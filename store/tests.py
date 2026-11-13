import hashlib
import hmac
import json
from io import BytesIO
from tempfile import TemporaryDirectory
from urllib.parse import parse_qs
from unittest.mock import MagicMock, patch

from PIL import Image
from django.contrib.auth.models import User
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .forms import CustomerProfileForm, ProductForm
from .models import CustomerProfile, Inquiry, Order, OrderItem, Product, StoreSettings
from .sms import send_sms


class StoreAccountTests(TestCase):
    def test_builtin_django_admin_is_not_exposed(self):
        response = self.client.get('/admin/')

        self.assertEqual(response.status_code, 404)

    def test_public_registration_creates_customer_not_staff(self):
        response = self.client.post(
            reverse('register'),
            {
                'username': 'cliente',
                'email': 'cliente@example.com',
                'password1': 'UnPasswordSeguro_483!',
                'password2': 'UnPasswordSeguro_483!',
                'is_staff': 'on',
                'is_superuser': 'on',
            },
        )

        user = User.objects.get(username='cliente')
        self.assertRedirects(response, reverse('home'))
        self.assertEqual(self.client.session['_auth_user_id'], str(user.pk))
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)

    def test_seed_catalog_is_visible_on_storefront(self):
        response = self.client.get(reverse('home'))

        self.assertContains(response, 'Container 20 FT Standard')

    def test_contact_form_is_closed_by_default(self):
        response = self.client.get(reverse('home'))

        self.assertContains(response, '<details class="contact-disclosure" id="contact-disclosure">')
        self.assertNotContains(response, '<details class="contact-disclosure" id="contact-disclosure" open>')
        self.assertContains(response, 'Contáctanos')

    def test_why_choose_section_appears_after_contact(self):
        response = self.client.get(reverse('home'))
        content = response.content

        self.assertLess(
            content.index(b'<section class="section" id="contacto">'),
            content.index(b'<section class="section" id="soluciones">'),
        )
        self.assertLess(
            content.index(b'<section class="section" id="soluciones">'),
            content.index(b'<div class="cart-overlay"'),
        )

    def test_customer_cannot_open_admin_product_page(self):
        customer = User.objects.create_user(username='cliente', password='PasswordSeguro_483!')
        self.client.force_login(customer)

        response = self.client.get(reverse('product_create'))

        self.assertEqual(response.status_code, 403)

    def test_staff_can_add_product_and_it_appears_in_catalog(self):
        staff_user = User.objects.create_user(
            username='encargado',
            password='PasswordSeguro_483!',
            is_staff=True,
        )
        self.client.force_login(staff_user)

        response = self.client.post(
            reverse('product_create'),
            {
                'name': 'Container nuevo',
                'category': 'Almacenamiento',
                'description': 'Producto de prueba',
                'dimensions': '20 FT',
                'price': 2100000,
                'stock': 3,
                'emoji': '📦',
                'is_active': 'on',
            },
        )

        self.assertRedirects(response, reverse('product_create'))
        product = Product.objects.get(name='Container nuevo')
        self.assertEqual(product.slug, 'container-nuevo')
        self.assertContains(self.client.get(reverse('home')), 'Container nuevo')

    def test_product_management_post_requires_csrf_token(self):
        staff_user = User.objects.create_user(
            username='encargado',
            password='PasswordSeguro_483!',
            is_staff=True,
        )
        csrf_client = self.client_class(enforce_csrf_checks=True)
        csrf_client.force_login(staff_user)

        response = csrf_client.post(reverse('product_create'), {'name': 'Sin token'})

        self.assertEqual(response.status_code, 403)

    def test_staff_can_upload_product_image_and_set_inventory(self):
        staff_user = User.objects.create_user(
            username='encargado',
            password='PasswordSeguro_483!',
            is_staff=True,
        )
        self.client.force_login(staff_user)
        image_buffer = BytesIO()
        Image.new('RGB', (20, 20), color='green').save(image_buffer, format='PNG')

        with TemporaryDirectory() as media_directory, override_settings(MEDIA_ROOT=media_directory):
            response = self.client.post(
                reverse('product_create'),
                {
                    'name': 'Container con foto',
                    'category': 'Almacenamiento',
                    'description': 'Producto con imagen',
                    'dimensions': '20 FT',
                    'price': 2100000,
                    'stock': 2,
                    'emoji': '📦',
                    'is_active': 'on',
                    'image': SimpleUploadedFile(
                        'container.png',
                        image_buffer.getvalue(),
                        content_type='image/png',
                    ),
                },
            )

            self.assertRedirects(response, reverse('product_create'))
            product = Product.objects.get(name='Container con foto')
            self.assertEqual(product.stock, 2)
            self.assertTrue(product.image)
            self.assertTrue(product.image.storage.exists(product.image.name))

    def test_invalid_image_content_is_rejected(self):
        form = ProductForm(
            data={
                'name': 'Container inválido',
                'category': 'Almacenamiento',
                'description': 'Descripción',
                'dimensions': '20 FT',
                'price': 2100000,
                'stock': 1,
                'emoji': '📦',
                'is_active': 'on',
            },
            files={
                'image': SimpleUploadedFile(
                    'not-an-image.png',
                    b'this is not a valid image',
                    content_type='image/png',
                ),
            },
        )

        self.assertFalse(form.is_valid())
        self.assertIn('image', form.errors)

    def test_empty_stock_disables_adding_to_cart(self):
        product = Product.objects.get(slug='container-20-ft-standard')
        product.stock = 0
        product.save(update_fields=['stock'])

        response = self.client.get(reverse('home'))

        self.assertContains(response, 'Agotado')
        self.assertContains(response, 'data-stock="0"')
        self.assertContains(response, 'disabled')

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_customer_inquiry_is_emailed_to_admin_selected_address(self):
        StoreSettings.objects.update_or_create(
            pk=1,
            defaults={'contact_email': 'ventas@example.com'},
        )

        response = self.client.post(
            reverse('submit_inquiry'),
            {
                'name': 'Cliente',
                'email': 'cliente@example.com',
                'topic': Inquiry.Topic.COMPLAINT,
                'subject': 'Demora de entrega',
                'message': 'Necesito conocer el estado del despacho.',
                'website': '',
            },
        )

        self.assertRedirects(response, reverse('home'))
        inquiry = Inquiry.objects.get(subject='Demora de entrega')
        self.assertEqual(inquiry.topic, Inquiry.Topic.COMPLAINT)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['ventas@example.com'])
        self.assertEqual(mail.outbox[0].reply_to, ['cliente@example.com'])

    def test_login_is_blocked_after_three_failed_password_attempts(self):
        User.objects.create_user(username='cliente', password='PasswordSeguro_483!')
        for _ in range(3):
            self.client.post(
                reverse('login'),
                {'username': 'cliente', 'password': 'WrongPassword_483!'},
            )

        response = self.client.post(
            reverse('login'),
            {'username': 'cliente', 'password': 'PasswordSeguro_483!'},
        )

        self.assertEqual(response.status_code, 429)

    def test_failed_passwords_for_one_account_do_not_block_another_account(self):
        User.objects.create_user(username='cliente', password='PasswordSeguro_483!')
        User.objects.create_user(username='administrador', password='OtraClaveSegura_583!')
        for _ in range(3):
            self.client.post(
                reverse('login'),
                {'username': 'cliente', 'password': 'WrongPassword_483!'},
            )

        response = self.client.post(
            reverse('login'),
            {'username': 'administrador', 'password': 'OtraClaveSegura_583!'},
        )

        self.assertRedirects(response, reverse('home'))
        self.assertEqual(
            self.client.session['_auth_user_id'],
            str(User.objects.get(username='administrador').pk),
        )


class StoreOrderTests(TestCase):
    def setUp(self):
        self.customer = User.objects.create_user(
            username='cliente-pedido',
            email='cliente@example.com',
            password='PasswordSeguro_483!',
        )
        self.profile = CustomerProfile.objects.create(
            user=self.customer,
            phone='+56912345678',
            phone_verified=True,
            rut='12345678-5',
            delivery_address='Casa 123',
        )
        self.product = Product.objects.create(
            name='Container de prueba',
            category='Almacenamiento',
            description='Producto de prueba',
            dimensions='20 FT',
            price=2500000,
            stock=3,
        )
        self.checkout_data = {
            'customer_name': 'Cliente de prueba',
            'delivery_address': 'Calle de prueba 123',
            'notes': '',
        }

    def post_order(self, quantities, **extra_data):
        data = {
            **self.checkout_data,
            'items': json.dumps([
                {'id': product_id, 'quantity': quantity}
                for product_id, quantity in quantities.items()
            ]),
            **extra_data,
        }
        return self.client.post(reverse('order_create'), data)

    def test_order_uses_database_price_and_reserves_stock(self):
        self.client.force_login(self.customer)

        response = self.post_order(
            {self.product.pk: 2},
            items=json.dumps([{'id': self.product.pk, 'quantity': 2, 'price': 1}]),
        )

        order = Order.objects.get()
        item = order.items.get()
        self.assertRedirects(response, reverse('order_detail', args=[order.pk]))
        self.assertEqual(order.customer, self.customer)
        self.assertEqual(order.customer_name, 'Cliente de prueba')
        self.assertEqual(order.customer_email, self.customer.email)
        self.assertEqual(order.phone, self.profile.phone)
        self.assertEqual(order.rut, '12345678-5')
        self.assertEqual(item.unit_price, self.product.price)
        self.assertEqual(Product.objects.get(pk=self.product.pk).stock, 1)

    def test_checkout_prefills_saved_customer_contact_details(self):
        self.client.force_login(self.customer)

        response = self.client.get(reverse('home'))

        self.assertContains(response, 'value="Casa 123"')
        self.assertNotContains(response, 'name="phone"')
        self.assertNotContains(response, 'name="rut"')
        self.assertNotContains(response, 'name="customer_email"')

    def test_customer_cannot_override_account_contact_details_in_checkout(self):
        self.client.force_login(self.customer)

        self.post_order(
            {self.product.pk: 1},
            customer_email='attacker@example.com',
            phone='+56900000000',
            rut='11111111-1',
        )

        order = Order.objects.get()
        self.assertEqual(order.customer_email, self.customer.email)
        self.assertEqual(order.phone, self.profile.phone)
        self.assertEqual(order.rut, self.profile.rut)

    def test_order_requires_verified_phone(self):
        self.profile.phone_verified = False
        self.profile.save(update_fields=['phone_verified'])
        self.client.force_login(self.customer)

        response = self.post_order({self.product.pk: 1})

        self.assertRedirects(response, reverse('account'))
        self.assertFalse(Order.objects.exists())

    def test_order_rejects_insufficient_stock_without_partial_changes(self):
        second_product = Product.objects.create(
            name='Segundo container',
            category='Almacenamiento',
            description='Otro producto',
            dimensions='40 FT',
            price=3500000,
            stock=1,
        )
        self.client.force_login(self.customer)

        response = self.post_order({self.product.pk: 1, second_product.pk: 2})

        self.assertRedirects(response, reverse('home'))
        self.assertFalse(Order.objects.exists())
        self.assertEqual(Product.objects.get(pk=self.product.pk).stock, 3)
        self.assertEqual(Product.objects.get(pk=second_product.pk).stock, 1)

    def test_customer_cannot_view_another_customers_order(self):
        other_customer = User.objects.create_user(username='otro-cliente')
        order = Order.objects.create(
            customer=other_customer,
            customer_name='Otro cliente',
            customer_email='otro@example.com',
            phone='12345678',
            delivery_address='Otra dirección',
        )
        OrderItem.objects.create(
            order=order,
            product=self.product,
            product_name=self.product.name,
            unit_price=self.product.price,
            quantity=1,
        )
        self.client.force_login(self.customer)

        response = self.client.get(reverse('order_detail', args=[order.pk]))
        purchases_response = self.client.get(reverse('my_purchases'))

        self.assertEqual(response.status_code, 403)
        self.assertEqual(purchases_response.status_code, 200)
        self.assertNotContains(purchases_response, f'Pedido #{order.pk}')

    def test_customer_can_render_order_detail_and_separate_purchases_page(self):
        order = Order.objects.create(
            customer=self.customer,
            customer_name='Cliente de prueba',
            customer_email='cliente@example.com',
            phone='12345678',
            delivery_address='Calle de prueba 123',
        )
        OrderItem.objects.create(
            order=order,
            product=self.product,
            product_name=self.product.name,
            unit_price=self.product.price,
            quantity=2,
        )
        self.client.force_login(self.customer)

        detail_response = self.client.get(reverse('order_detail', args=[order.pk]))
        account_response = self.client.get(reverse('account'))
        purchases_response = self.client.get(reverse('my_purchases'))

        self.assertEqual(detail_response.status_code, 200)
        self.assertContains(detail_response, 'Pedido registrado')
        self.assertContains(detail_response, '$5000000')
        self.assertEqual(account_response.status_code, 200)
        self.assertContains(account_response, 'href="/cuenta/compras/">Mis compras</a>')
        self.assertNotContains(account_response, 'orders-section')
        self.assertNotContains(account_response, 'Pedido #')
        self.assertEqual(purchases_response.status_code, 200)
        self.assertContains(purchases_response, 'Mis compras')
        self.assertContains(purchases_response, f'Pedido #{order.pk}')

    def test_cancelling_order_restores_stock_only_once_and_cannot_reopen(self):
        admin = User.objects.create_user(username='admin-pedidos', is_staff=True)
        order = Order.objects.create(
            customer=self.customer,
            customer_name='Cliente de prueba',
            customer_email='cliente@example.com',
            phone='12345678',
            delivery_address='Calle de prueba 123',
        )
        OrderItem.objects.create(
            order=order,
            product=self.product,
            product_name=self.product.name,
            unit_price=self.product.price,
            quantity=2,
        )
        self.product.stock = 1
        self.product.save(update_fields=['stock'])
        self.client.force_login(admin)

        self.client.post(
            reverse('order_update_status', args=[order.pk]),
            {'status': Order.Status.CANCELLED},
        )
        self.client.post(
            reverse('order_update_status', args=[order.pk]),
            {'status': Order.Status.CANCELLED},
        )
        response = self.client.post(
            reverse('order_update_status', args=[order.pk]),
            {'status': Order.Status.PENDING},
        )

        self.assertRedirects(response, reverse('sales_dashboard'))
        self.assertEqual(Product.objects.get(pk=self.product.pk).stock, 3)
        self.assertEqual(Order.objects.get(pk=order.pk).status, Order.Status.CANCELLED)


class MercadoPagoIntegrationTests(TestCase):
    def setUp(self):
        self.customer = User.objects.create_user(username='cliente-mp', password='PasswordSeguro_483!')
        self.product = Product.objects.create(
            name='Container para Mercado Pago',
            category='Almacenamiento',
            description='Producto de prueba',
            dimensions='20 FT',
            price=2500000,
            stock=2,
        )
        self.order = Order.objects.create(
            customer=self.customer,
            customer_name='Cliente Mercado Pago',
            customer_email='cliente@example.com',
            phone='+56 9 1234 5678',
            rut='12345678-5',
            delivery_address='Santiago',
        )
        OrderItem.objects.create(
            order=self.order,
            product=self.product,
            product_name=self.product.name,
            unit_price=self.product.price,
            quantity=2,
        )
        self.client.force_login(self.customer)

    @override_settings(
        MP_ACCESS_TOKEN='TEST-access-token',
        MP_WEBHOOK_SECRET='webhook-secret',
        MP_PUBLIC_BASE_URL='https://tienda.example.com',
        MP_USE_SANDBOX=True,
    )
    @patch('store.payments.urlopen')
    def test_customer_is_redirected_to_sandbox_preference_with_server_prices(self, mocked_urlopen):
        api_response = MagicMock()
        api_response.__enter__.return_value = api_response
        api_response.read.return_value = json.dumps({
            'id': 'preference-123',
            'sandbox_init_point': 'https://sandbox.mercadopago.com/checkout/pay?pref_id=preference-123',
            'init_point': 'https://www.mercadopago.cl/checkout/v1/redirect?pref_id=preference-123',
        }).encode()
        mocked_urlopen.return_value = api_response

        response = self.client.post(reverse('payment_create', args=[self.order.pk]))

        request = mocked_urlopen.call_args.args[0]
        payload = json.loads(request.data)
        self.order.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response['Location'],
            'https://sandbox.mercadopago.com/checkout/pay?pref_id=preference-123',
        )
        self.assertEqual(payload['items'][0]['unit_price'], self.product.price)
        self.assertEqual(payload['items'][0]['quantity'], 2)
        self.assertEqual(payload['payer']['identification']['number'], '12345678-5')
        self.assertEqual(
            payload['external_reference'],
            str(self.order.mercadopago_external_reference),
        )
        self.assertEqual(self.order.payment_status, Order.PaymentStatus.PENDING)
        self.assertEqual(self.order.mercadopago_preference_id, 'preference-123')

    @override_settings(
        MP_ACCESS_TOKEN='TEST-access-token',
        MP_WEBHOOK_SECRET='webhook-secret',
        MP_PUBLIC_BASE_URL='https://tienda.example.com',
    )
    @patch('store.views.get_payment')
    def test_signed_webhook_confirms_only_the_verified_payment(self, mocked_get_payment):
        payment_id = '987654321'
        request_id = 'request-456'
        timestamp = '1790000000'
        secret = 'webhook-secret'
        manifest = f'id:{payment_id};request-id:{request_id};ts:{timestamp};'
        signature = hmac.new(
            secret.encode(),
            manifest.encode(),
            hashlib.sha256,
        ).hexdigest()
        mocked_get_payment.return_value = {
            'id': payment_id,
            'external_reference': str(self.order.mercadopago_external_reference),
            'transaction_amount': str(self.order.total),
            'currency_id': 'CLP',
            'status': 'approved',
        }

        response = self.client.post(
            f'{reverse("mercadopago_webhook")}?type=payment&data.id={payment_id}',
            data=json.dumps({'type': 'payment', 'data': {'id': payment_id}}),
            content_type='application/json',
            HTTP_X_SIGNATURE=f'ts={timestamp},v1={signature}',
            HTTP_X_REQUEST_ID=request_id,
        )

        self.order.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.order.payment_status, Order.PaymentStatus.APPROVED)
        self.assertEqual(self.order.mercadopago_payment_id, payment_id)
        mocked_get_payment.assert_called_once_with(payment_id)

    @override_settings(MP_WEBHOOK_SECRET='webhook-secret')
    @patch('store.views.get_payment')
    def test_webhook_does_not_confirm_payment_with_wrong_amount_or_currency(self, mocked_get_payment):
        payment_id = '987654321'
        request_id = 'request-456'
        timestamp = '1790000000'
        manifest = f'id:{payment_id};request-id:{request_id};ts:{timestamp};'
        signature = hmac.new(
            b'webhook-secret',
            manifest.encode(),
            hashlib.sha256,
        ).hexdigest()

        for payment_details in (
            {
                'transaction_amount': str(self.order.total + 1),
                'currency_id': 'CLP',
            },
            {
                'transaction_amount': str(self.order.total),
                'currency_id': 'USD',
            },
        ):
            with self.subTest(payment_details=payment_details):
                mocked_get_payment.return_value = {
                    'id': payment_id,
                    'external_reference': str(self.order.mercadopago_external_reference),
                    'status': 'approved',
                    **payment_details,
                }

                response = self.client.post(
                    f'{reverse("mercadopago_webhook")}?type=payment&data_id={payment_id}',
                    data=json.dumps({'type': 'payment', 'data': {'id': payment_id}}),
                    content_type='application/json',
                    HTTP_X_SIGNATURE=f'ts={timestamp},v1={signature}',
                    HTTP_X_REQUEST_ID=request_id,
                )

                self.order.refresh_from_db()
                self.assertEqual(response.status_code, 200)
                self.assertEqual(self.order.payment_status, Order.PaymentStatus.NOT_STARTED)

    @override_settings(MP_WEBHOOK_SECRET='webhook-secret')
    @patch('store.views.get_payment')
    def test_webhook_rejects_invalid_signature_without_fetching_payment(self, mocked_get_payment):
        response = self.client.post(
            f'{reverse("mercadopago_webhook")}?type=payment&data.id=987654321',
            data=json.dumps({'type': 'payment', 'data': {'id': '987654321'}}),
            content_type='application/json',
            HTTP_X_SIGNATURE='ts=1790000000,v1=invalid',
            HTTP_X_REQUEST_ID='request-456',
        )

        self.assertEqual(response.status_code, 401)
        mocked_get_payment.assert_not_called()

    def test_browser_return_does_not_mark_order_as_paid(self):
        response = self.client.get(
            f'{reverse("payment_return", args=[self.order.pk])}?status=approved',
        )

        self.order.refresh_from_db()
        self.assertRedirects(response, reverse('order_detail', args=[self.order.pk]))
        self.assertEqual(self.order.payment_status, Order.PaymentStatus.NOT_STARTED)


class StoreManagementTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='admin-gestion', is_staff=True)
        self.client.force_login(self.admin)

    def test_staff_can_edit_product_details(self):
        product = Product.objects.create(
            name='Container antes',
            category='Almacenamiento',
            description='Descripción anterior',
            dimensions='20 FT',
            price=2000000,
            stock=2,
        )

        response = self.client.post(
            reverse('product_edit', args=[product.pk]),
            {
                'name': 'Container editado',
                'category': 'Oficina',
                'description': 'Descripción actualizada',
                'dimensions': '40 FT',
                'price': 3000000,
                'stock': 5,
                'emoji': '📦',
                'is_active': 'on',
            },
        )

        self.assertRedirects(response, reverse('product_create'))
        product.refresh_from_db()
        self.assertEqual(product.name, 'Container editado')
        self.assertEqual(product.price, 3000000)
        self.assertEqual(product.stock, 5)

    def test_staff_pages_render(self):
        product = Product.objects.create(
            name='Container panel',
            category='Almacenamiento',
            description='Producto para renderizar',
            dimensions='20 FT',
            price=2000000,
            stock=2,
        )
        order = Order.objects.create(
            customer_name='Cliente panel',
            customer_email='panel@example.com',
            phone='12345678',
            delivery_address='Dirección panel',
            status=Order.Status.CONFIRMED,
        )
        OrderItem.objects.create(
            order=order,
            product=product,
            product_name=product.name,
            unit_price=product.price,
            quantity=2,
        )

        edit_response = self.client.get(reverse('product_edit', args=[product.pk]))
        dashboard_response = self.client.get(reverse('sales_dashboard'))

        self.assertEqual(edit_response.status_code, 200)
        self.assertContains(edit_response, 'Editar producto')
        self.assertEqual(dashboard_response.status_code, 200)
        self.assertContains(dashboard_response, 'Ventas y pedidos')
        self.assertContains(dashboard_response, 'Cliente panel')
        self.assertContains(dashboard_response, '$4000000')

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_staff_can_reply_to_and_delete_inquiry(self):
        inquiry = Inquiry.objects.create(
            name='Cliente',
            email='cliente@example.com',
            topic=Inquiry.Topic.QUESTION,
            subject='Consulta de entrega',
            message='¿Cuándo se entrega?',
        )

        response = self.client.post(
            reverse('inquiry_reply', args=[inquiry.pk]),
            {'reply': 'Coordinaremos el despacho contigo.'},
        )

        self.assertRedirects(response, reverse('product_create'))
        inquiry.refresh_from_db()
        self.assertEqual(inquiry.admin_reply, 'Coordinaremos el despacho contigo.')
        self.assertIsNotNone(inquiry.replied_at)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['cliente@example.com'])

        response = self.client.post(reverse('inquiry_delete', args=[inquiry.pk]))

        self.assertRedirects(response, reverse('product_create'))
        self.assertFalse(Inquiry.objects.filter(pk=inquiry.pk).exists())

    def test_customer_profile_is_editable_by_its_owner(self):
        customer = User.objects.create_user(
            username='cliente-antiguo',
            email='antes@example.com',
            password='PasswordSeguro_483!',
        )
        self.client.force_login(customer)

        with patch('store.views.send_sms') as mocked_send_sms:
            with patch('store.views.secrets.randbelow', return_value=123456):
                response = self.client.post(
                    reverse('account'),
                    {
                        'username': 'cliente-nuevo',
                        'first_name': 'Nombre',
                        'last_name': 'Apellido',
                        'email': 'nuevo@example.com',
                        'phone': '+56 9 1234 5678',
                        'rut': '12.345.678-5',
                        'delivery_address': 'Av. Principal 123',
                    },
                )

        self.assertRedirects(response, reverse('account'))
        customer.refresh_from_db()
        profile = CustomerProfile.objects.get(user=customer)
        self.assertEqual(customer.username, 'cliente-nuevo')
        self.assertEqual(customer.email, 'nuevo@example.com')
        self.assertEqual(profile.phone, '+56912345678')
        self.assertFalse(profile.phone_verified)
        self.assertEqual(profile.rut, '12345678-5')
        self.assertEqual(profile.delivery_address, 'Av. Principal 123')
        self.assertNotEqual(profile.phone_verification_code_hash, '123456')
        mocked_send_sms.assert_called_once()

        verify_response = self.client.post(
            reverse('phone_verification_confirm'),
            {'code': '123456'},
        )

        self.assertRedirects(verify_response, reverse('account'))
        profile.refresh_from_db()
        self.assertTrue(profile.phone_verified)
        self.assertEqual(profile.phone_verification_code_hash, '')

    @patch('store.views.send_sms')
    @patch('store.views.secrets.randbelow', return_value=123456)
    def test_phone_verification_code_is_limited_to_five_attempts(
        self,
        mocked_randbelow,
        mocked_send_sms,
    ):
        customer = User.objects.create_user(
            username='cliente-sms',
            email='sms@example.com',
            password='PasswordSeguro_483!',
        )
        CustomerProfile.objects.create(user=customer, phone='+56912345678')
        self.client.force_login(customer)

        send_response = self.client.post(reverse('phone_verification_send'))
        self.assertRedirects(send_response, reverse('account'))
        mocked_send_sms.assert_called_once()

        for _ in range(5):
            self.client.post(
                reverse('phone_verification_confirm'),
                {'code': '999999'},
            )

        profile = CustomerProfile.objects.get(user=customer)
        self.assertFalse(profile.phone_verified)
        self.assertEqual(profile.phone_verification_attempts, 5)
        self.assertEqual(profile.phone_verification_code_hash, '')

        self.client.post(
            reverse('phone_verification_confirm'),
            {'code': '123456'},
        )
        profile.refresh_from_db()
        self.assertFalse(profile.phone_verified)
        mocked_randbelow.assert_called_once()

    def test_customer_profile_rejects_invalid_rut_checksum(self):
        form = CustomerProfileForm(data={
            'phone': '+56 9 1234 5678',
            'rut': '12.345.678-9',
            'delivery_address': 'Av. Principal 123',
        })

        self.assertFalse(form.is_valid())
        self.assertIn('rut', form.errors)


class SMSDeliveryTests(TestCase):
    @override_settings(
        TWILIO_ACCOUNT_SID='AC123',
        TWILIO_AUTH_TOKEN='test-auth-token',
        TWILIO_FROM_NUMBER='+15005550006',
        TWILIO_MESSAGING_SERVICE_SID='',
    )
    @patch('store.sms.urlopen')
    def test_sms_uses_twilio_form_api_and_e164_recipient(self, mocked_urlopen):
        api_response = MagicMock()
        api_response.__enter__.return_value = api_response
        api_response.read.return_value = json.dumps({'sid': 'SM123'}).encode()
        mocked_urlopen.return_value = api_response

        send_sms('+56912345678', 'Código: 123456')

        request = mocked_urlopen.call_args.args[0]
        payload = parse_qs(request.data.decode('utf-8'))
        self.assertEqual(request.method, 'POST')
        self.assertEqual(payload['To'], ['+56912345678'])
        self.assertEqual(payload['From'], ['+15005550006'])
        self.assertEqual(payload['Body'], ['Código: 123456'])
        self.assertTrue(request.get_header('Authorization').startswith('Basic '))
