from functools import wraps
import json
import logging
import re
import secrets
import smtplib
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from uuid import UUID

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.hashers import check_password, make_password
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.core.mail import BadHeaderError, EmailMessage
from django.db import transaction
from django.db.models import BigIntegerField, Count, F, Q, Sum
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .forms import (
    CustomerRegistrationForm,
    CustomerProfileForm,
    InquiryForm,
    InquiryReplyForm,
    OrderCheckoutForm,
    PhoneVerificationForm,
    ProductForm,
    StoreSettingsForm,
    UserProfileForm,
)
from .models import CustomerProfile, Inquiry, Order, OrderItem, Product, StoreSettings
from .payments import MercadoPagoError, create_checkout_preference, get_payment, verify_webhook_signature
from .sms import SMSDeliveryError, send_sms

logger = logging.getLogger(__name__)
MAX_BASKET_LINES = 30
MAX_ITEM_QUANTITY = 1000
PHONE_CODE_TTL = timedelta(minutes=10)
PHONE_CODE_RESEND_DELAY = timedelta(seconds=60)
PHONE_CODE_SEND_WINDOW = timedelta(hours=1)
PHONE_CODE_MAX_SENDS = 3
PHONE_CODE_MAX_ATTEMPTS = 5


class OrderValidationError(Exception):
    pass


def checkout_initial(request):
    if not request.user.is_authenticated:
        return {}

    profile = CustomerProfile.objects.filter(user=request.user).first()
    return {
        'customer_name': request.user.get_full_name() or request.user.username,
        'delivery_address': profile.delivery_address if profile else '',
    }


def home(request):
    products = Product.objects.filter(is_active=True)
    return render(
        request,
        'index.html',
        {
            'products': products,
            'show_cart': True,
            'inquiry_form': InquiryForm(),
            'checkout_form': OrderCheckoutForm(initial=checkout_initial(request)),
            'contact_email': StoreSettings.get_settings().contact_email,
        },
    )


@require_http_methods(['GET', 'POST'])
def register(request):
    if request.user.is_authenticated:
        return redirect('home')

    form = CustomerRegistrationForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.save()
        login(request, user, backend='django.contrib.auth.backends.ModelBackend')
        messages.success(request, 'Tu cuenta de cliente quedó creada.')
        return redirect('home')

    return render(request, 'accounts/register.html', {'form': form})


@require_http_methods(['GET', 'POST'])
def login_view(request):
    if request.user.is_authenticated:
        return redirect('home')

    form = AuthenticationForm(request, data=request.POST or None)
    if request.method == 'POST' and form.is_valid():
        login(request, form.get_user())
        next_url = request.POST.get('next') or request.GET.get('next', '')
        if next_url and url_has_allowed_host_and_scheme(
            next_url,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ):
            return redirect(next_url)
        return redirect('home')

    return render(
        request,
        'accounts/login.html',
        {'form': form, 'next': request.GET.get('next', '')},
    )


@login_required
@require_http_methods(['GET', 'POST'])
def account(request):
    profile, _ = CustomerProfile.objects.get_or_create(user=request.user)
    old_phone = profile.phone
    form = UserProfileForm(request.POST or None, instance=request.user)
    profile_form = CustomerProfileForm(request.POST or None, instance=profile)
    user_form_valid = form.is_valid()
    profile_form_valid = profile_form.is_valid()
    if request.method == 'POST' and user_form_valid and profile_form_valid:
        with transaction.atomic():
            form.save()
            profile = profile_form.save(commit=False)
            phone_changed = profile.phone != old_phone
            if phone_changed:
                profile.phone_verified = False
                profile.phone_verification_code_hash = ''
                profile.phone_verification_expires_at = None
                profile.phone_verification_attempts = 0
            profile.save()
            profile_form.save_m2m()

        if phone_changed and profile.phone:
            try:
                issue_phone_verification(profile)
            except (PhoneVerificationError, SMSDeliveryError) as error:
                messages.error(request, str(error))
            else:
                messages.success(request, 'Te enviamos un código SMS de 6 dígitos para verificar el teléfono.')
        else:
            messages.success(request, 'Tus datos se actualizaron correctamente.')
        return redirect('account')

    return render(
        request,
        'accounts/account.html',
        {
            'form': form,
            'profile': profile,
            'profile_form': profile_form,
            'phone_verification_form': PhoneVerificationForm(),
        },
    )


@login_required
def my_purchases(request):
    orders = Order.objects.filter(customer=request.user).prefetch_related('items')
    return render(request, 'accounts/purchases.html', {'orders': orders})


class PhoneVerificationError(Exception):
    pass


def issue_phone_verification(profile):
    now = timezone.now()
    with transaction.atomic():
        profile = CustomerProfile.objects.select_for_update().get(pk=profile.pk)
        if not profile.phone:
            raise PhoneVerificationError('Primero guarda un teléfono en tu cuenta.')
        if profile.phone_verified:
            raise PhoneVerificationError('Este teléfono ya está verificado.')
        if (
            profile.phone_verification_sent_at
            and now < profile.phone_verification_sent_at + PHONE_CODE_RESEND_DELAY
        ):
            raise PhoneVerificationError('Espera un minuto antes de solicitar otro código.')

        window_start = profile.phone_verification_window_started_at
        if not window_start or now >= window_start + PHONE_CODE_SEND_WINDOW:
            profile.phone_verification_window_started_at = now
            profile.phone_verification_send_count = 0
        if profile.phone_verification_send_count >= PHONE_CODE_MAX_SENDS:
            raise PhoneVerificationError('Alcanzaste el máximo de 3 códigos por hora. Inténtalo más tarde.')

        code = f'{secrets.randbelow(1_000_000):06d}'
        profile.phone_verification_code_hash = make_password(code)
        profile.phone_verification_expires_at = now + PHONE_CODE_TTL
        profile.phone_verification_attempts = 0
        profile.phone_verification_sent_at = now
        profile.phone_verification_send_count += 1
        profile.save(update_fields=[
            'phone_verification_code_hash',
            'phone_verification_expires_at',
            'phone_verification_attempts',
            'phone_verification_sent_at',
            'phone_verification_window_started_at',
            'phone_verification_send_count',
        ])

    send_sms(
        profile.phone,
        f'Tu código de verificación de ContainerPro es {code}. Vence en 10 minutos. No lo compartas.',
    )


@login_required
@require_http_methods(['POST'])
def phone_verification_send(request):
    profile, _ = CustomerProfile.objects.get_or_create(user=request.user)
    try:
        issue_phone_verification(profile)
    except (PhoneVerificationError, SMSDeliveryError) as error:
        messages.error(request, str(error))
    else:
        messages.success(request, 'Te enviamos un código SMS de 6 dígitos.')
    return redirect('account')


@login_required
@require_http_methods(['POST'])
def phone_verification_confirm(request):
    profile, _ = CustomerProfile.objects.get_or_create(user=request.user)
    form = PhoneVerificationForm(request.POST)
    if not form.is_valid():
        messages.error(request, 'Ingresa un código de exactamente 6 dígitos.')
        return redirect('account')

    now = timezone.now()
    with transaction.atomic():
        profile = CustomerProfile.objects.select_for_update().get(pk=profile.pk)
        if profile.phone_verified:
            messages.info(request, 'Este teléfono ya está verificado.')
        elif not profile.phone_verification_code_hash or not profile.phone_verification_expires_at:
            messages.error(request, 'Solicita primero un código de verificación.')
        elif now >= profile.phone_verification_expires_at:
            profile.phone_verification_code_hash = ''
            profile.phone_verification_expires_at = None
            profile.save(update_fields=[
                'phone_verification_code_hash',
                'phone_verification_expires_at',
            ])
            messages.error(request, 'El código venció. Solicita uno nuevo.')
        elif profile.phone_verification_attempts >= PHONE_CODE_MAX_ATTEMPTS:
            messages.error(request, 'Se agotaron los intentos. Solicita un código nuevo.')
        else:
            profile.phone_verification_attempts += 1
            if check_password(form.cleaned_data['code'], profile.phone_verification_code_hash):
                profile.phone_verified = True
                profile.phone_verification_code_hash = ''
                profile.phone_verification_expires_at = None
                profile.phone_verification_attempts = 0
                profile.save(update_fields=[
                    'phone_verified',
                    'phone_verification_code_hash',
                    'phone_verification_expires_at',
                    'phone_verification_attempts',
                ])
                messages.success(request, 'Teléfono verificado correctamente.')
            else:
                if profile.phone_verification_attempts >= PHONE_CODE_MAX_ATTEMPTS:
                    profile.phone_verification_code_hash = ''
                    profile.phone_verification_expires_at = None
                profile.save(update_fields=[
                    'phone_verification_attempts',
                    'phone_verification_code_hash',
                    'phone_verification_expires_at',
                ])
                messages.error(request, 'El código no es correcto. Revisa el SMS e inténtalo nuevamente.')

    return redirect('account')


def staff_required(view_func):
    @wraps(view_func)
    def check_staff(request, *args, **kwargs):
        if not request.user.is_staff:
            raise PermissionDenied
        return view_func(request, *args, **kwargs)

    return login_required(check_staff, login_url='login')


@staff_required
@require_http_methods(['GET', 'POST'])
def product_create(request):
    form = ProductForm(request.POST or None, request.FILES or None)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Producto añadido al catálogo con su inventario.')
        return redirect('product_create')

    products = Product.objects.all()
    store_settings = StoreSettings.get_settings()
    settings_form = StoreSettingsForm(instance=store_settings)
    return render(
        request,
        'store/product_form.html',
        {
            'form': form,
            'products': products,
            'settings_form': settings_form,
            'inquiries': Inquiry.objects.all()[:50],
        },
    )


@staff_required
@require_http_methods(['GET', 'POST'])
def product_edit(request, product_id):
    product = get_object_or_404(Product, pk=product_id)
    form = ProductForm(request.POST or None, request.FILES or None, instance=product)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Producto actualizado.')
        return redirect('product_create')

    return render(
        request,
        'store/product_form.html',
        {
            'form': form,
            'products': Product.objects.all(),
            'settings_form': StoreSettingsForm(instance=StoreSettings.get_settings()),
            'inquiries': Inquiry.objects.all()[:50],
            'editing_product': True,
        },
    )


@staff_required
@require_http_methods(['POST'])
def contact_settings_update(request):
    settings = StoreSettings.get_settings()
    form = StoreSettingsForm(request.POST, instance=settings)
    if form.is_valid():
        form.save()
        messages.success(request, 'Correo de consultas actualizado.')
    else:
        messages.error(request, 'No se pudo guardar el correo: revisa el formato.')
    return redirect('product_create')


@staff_required
@require_http_methods(['POST'])
def product_toggle(request, product_id):
    product = get_object_or_404(Product, pk=product_id)
    product.is_active = not product.is_active
    product.save(update_fields=['is_active', 'updated_at'])
    messages.success(
        request,
        'Producto publicado.' if product.is_active else 'Producto oculto del catálogo.',
    )
    return redirect('product_create')


@require_http_methods(['POST'])
def submit_inquiry(request):
    form = InquiryForm(request.POST)
    if form.is_valid():
        if form.cleaned_data.get('website'):
            return redirect('home')

        recipient = StoreSettings.get_settings().contact_email.strip()
        if not recipient:
            messages.error(
                request,
                'El negocio todavía no configuró el correo para recibir consultas. Inténtalo más tarde.',
            )
            return redirect('home')

        inquiry = form.save()
        try:
            sent_count = EmailMessage(
                subject=f'ContainerPro - {inquiry.get_topic_display()}: {inquiry.subject}',
                body=(
                    f'Nombre: {inquiry.name}\n'
                    f'Correo de respuesta: {inquiry.email}\n'
                    f'Tema: {inquiry.get_topic_display()}\n\n'
                    f'{inquiry.message}'
                ),
                from_email=None,
                to=[recipient],
                reply_to=[inquiry.email],
            ).send(fail_silently=False)
        except (BadHeaderError, OSError, smtplib.SMTPException):
            logger.exception('No se pudo enviar la consulta %s por correo.', inquiry.pk)
            messages.error(
                request,
                'Guardamos tu mensaje, pero no se pudo enviar el correo. Por favor, inténtalo más tarde.',
            )
            return redirect('home')

        if sent_count != 1:
            logger.error('El backend de correo no aceptó la consulta %s.', inquiry.pk)
            messages.error(
                request,
                'Guardamos tu mensaje, pero el servicio de correo no lo aceptó. Inténtalo más tarde.',
            )
            return redirect('home')

        messages.success(request, 'Tu mensaje fue enviado al equipo. Gracias por contactarnos.')
        return redirect('home')

    messages.error(request, 'Revisa el formulario: hay datos obligatorios o no válidos.')
    return render(
        request,
        'index.html',
        {
            'products': Product.objects.filter(is_active=True),
            'show_cart': True,
            'inquiry_form': form,
            'checkout_form': OrderCheckoutForm(initial=checkout_initial(request)),
            'contact_email': StoreSettings.get_settings().contact_email,
            'contact_open': True,
        },
        status=400,
    )


@staff_required
@require_http_methods(['POST'])
def inquiry_reply(request, inquiry_id):
    inquiry = get_object_or_404(Inquiry, pk=inquiry_id)
    form = InquiryReplyForm(request.POST)
    if not form.is_valid():
        messages.error(request, 'Escribe una respuesta válida antes de enviarla.')
        return redirect('product_create')

    reply = form.cleaned_data['reply'].strip()
    if not reply:
        messages.error(request, 'La respuesta no puede quedar vacía.')
        return redirect('product_create')

    inquiry.admin_reply = reply
    inquiry.replied_at = None
    try:
        sent_count = EmailMessage(
            subject=f'Re: {inquiry.subject}',
            body=f'Hola {inquiry.name},\n\n{reply}\n\nSaludos,\nContainerPro',
            from_email=None,
            to=[inquiry.email],
        ).send(fail_silently=False)
    except (BadHeaderError, OSError, smtplib.SMTPException):
        inquiry.save(update_fields=['admin_reply', 'replied_at'])
        logger.exception('No se pudo enviar la respuesta a la consulta %s.', inquiry.pk)
        messages.error(request, 'La respuesta quedó guardada, pero no se pudo enviar por correo.')
        return redirect('product_create')

    if sent_count != 1:
        inquiry.save(update_fields=['admin_reply', 'replied_at'])
        messages.error(request, 'La respuesta quedó guardada, pero el servicio de correo no la aceptó.')
        return redirect('product_create')

    inquiry.replied_at = timezone.now()
    inquiry.save(update_fields=['admin_reply', 'replied_at'])
    messages.success(request, 'Respuesta enviada al cliente.')
    return redirect('product_create')


@staff_required
@require_http_methods(['POST'])
def inquiry_delete(request, inquiry_id):
    inquiry = get_object_or_404(Inquiry, pk=inquiry_id)
    inquiry.delete()
    messages.success(request, 'Consulta eliminada.')
    return redirect('product_create')


@login_required
@require_http_methods(['GET', 'POST'])
def order_create(request):
    if request.method == 'GET':
        return redirect('home')

    profile = CustomerProfile.objects.filter(user=request.user).first()
    if not request.user.email:
        messages.error(request, 'Agrega tu correo desde Mi cuenta antes de hacer un pedido.')
        return redirect('account')
    if not profile or not profile.phone_verified:
        messages.error(request, 'Agrega y verifica tu teléfono desde Mi cuenta antes de hacer un pedido.')
        return redirect('account')

    form = OrderCheckoutForm(request.POST)
    if not form.is_valid():
        messages.error(request, 'Completa los datos de contacto y entrega correctamente.')
        return redirect('home')

    try:
        basket = json.loads(request.POST.get('items', ''))
        if not isinstance(basket, list) or not basket or len(basket) > MAX_BASKET_LINES:
            raise OrderValidationError('El carrito está vacío o contiene demasiados productos.')

        quantities = {}
        for line in basket:
            if not isinstance(line, dict):
                raise OrderValidationError('No se pudo validar el carrito.')
            product_id = line.get('id')
            quantity = line.get('quantity')
            if (
                not str(product_id).isdigit()
                or not isinstance(quantity, int)
                or isinstance(quantity, bool)
                or quantity < 1
                or quantity > MAX_ITEM_QUANTITY
            ):
                raise OrderValidationError('Hay cantidades inválidas en el carrito.')
            product_id = int(product_id)
            if product_id in quantities:
                raise OrderValidationError('El carrito contiene productos duplicados.')
            quantities[product_id] = quantity

        with transaction.atomic():
            products = list(
                Product.objects.select_for_update()
                .filter(pk__in=quantities, is_active=True)
                .order_by('pk')
            )
            products_by_id = {product.pk: product for product in products}
            if len(products_by_id) != len(quantities):
                raise OrderValidationError('Uno de los productos ya no está disponible.')

            order_items = []
            for product_id, quantity in quantities.items():
                product = products_by_id[product_id]
                updated = Product.objects.filter(
                    pk=product_id,
                    is_active=True,
                    stock__gte=quantity,
                ).update(stock=F('stock') - quantity)
                if updated != 1:
                    raise OrderValidationError(
                        f'No hay existencias suficientes de {product.name}. Revisa el carrito.',
                    )

                order_items.append(
                    OrderItem(
                        product=product,
                        product_name=product.name,
                        unit_price=product.price,
                        quantity=quantity,
                    )
                )

            order = Order.objects.create(
                customer=request.user,
                customer_name=form.cleaned_data['customer_name'].strip(),
                customer_email=request.user.email.strip().lower(),
                phone=profile.phone,
                rut=profile.rut,
                delivery_address=form.cleaned_data['delivery_address'].strip(),
                notes=form.cleaned_data['notes'].strip(),
            )
            for item in order_items:
                item.order = order
            OrderItem.objects.bulk_create(order_items)
    except (json.JSONDecodeError, OrderValidationError) as error:
        messages.error(request, str(error) or 'No se pudo registrar el pedido.')
        return redirect('home')

    messages.success(
        request,
        'Pedido registrado. Desde el detalle podrás iniciar el pago en línea si Mercado Pago está configurado.',
    )
    return redirect('order_detail', order_id=order.pk)


@login_required
def order_detail(request, order_id):
    order = get_object_or_404(
        Order.objects.prefetch_related('items'),
        pk=order_id,
    )
    if not request.user.is_staff and order.customer_id != request.user.pk:
        raise PermissionDenied
    payment_ready = all((
        settings.MP_ACCESS_TOKEN,
        settings.MP_WEBHOOK_SECRET,
        settings.MP_PUBLIC_BASE_URL,
    ))
    return render(
        request,
        'store/order_detail.html',
        {'order': order, 'payment_ready': payment_ready},
    )


@login_required
@require_http_methods(['POST'])
def payment_create(request, order_id):
    order = get_object_or_404(Order, pk=order_id)
    if order.customer_id != request.user.pk:
        raise PermissionDenied
    if order.status == Order.Status.CANCELLED:
        messages.error(request, 'No se puede pagar un pedido cancelado.')
        return redirect('order_detail', order_id=order.pk)
    if order.payment_status == Order.PaymentStatus.APPROVED:
        messages.info(request, 'Este pedido ya figura como pagado.')
        return redirect('order_detail', order_id=order.pk)
    if not all((
        settings.MP_ACCESS_TOKEN,
        settings.MP_WEBHOOK_SECRET,
        settings.MP_PUBLIC_BASE_URL,
    )):
        messages.error(
            request,
            'Mercado Pago todavía no está configurado. No se inició ningún cobro.',
        )
        return redirect('order_detail', order_id=order.pk)

    try:
        with transaction.atomic():
            order = get_object_or_404(
                Order.objects.select_for_update().prefetch_related('items'),
                pk=order_id,
            )
            if order.customer_id != request.user.pk:
                raise PermissionDenied
            if order.status == Order.Status.CANCELLED:
                messages.error(request, 'No se puede pagar un pedido cancelado.')
                return redirect('order_detail', order_id=order.pk)
            if order.payment_status == Order.PaymentStatus.APPROVED:
                messages.info(request, 'Este pedido ya figura como pagado.')
                return redirect('order_detail', order_id=order.pk)
            if (
                order.payment_status == Order.PaymentStatus.PENDING
                and order.mercadopago_checkout_url
            ):
                return redirect(order.mercadopago_checkout_url)

            preference_id, checkout_url = create_checkout_preference(order)
            order.mercadopago_preference_id = preference_id
            order.mercadopago_checkout_url = checkout_url
            order.payment_status = Order.PaymentStatus.PENDING
            order.payment_updated_at = timezone.now()
            order.save(
                update_fields=[
                    'mercadopago_preference_id',
                    'mercadopago_checkout_url',
                    'payment_status',
                    'payment_updated_at',
                ],
            )
    except MercadoPagoError as error:
        logger.warning('No se pudo iniciar el pago del pedido %s.', order_id, exc_info=True)
        messages.error(request, str(error))
        return redirect('order_detail', order_id=order_id)

    return redirect(checkout_url)


@login_required
@require_http_methods(['GET'])
def payment_return(request, order_id):
    order = get_object_or_404(Order, pk=order_id)
    if order.customer_id != request.user.pk:
        raise PermissionDenied
    messages.info(
        request,
        'Volviste desde Mercado Pago. El estado se confirmará mediante una notificación segura del servidor.',
    )
    return redirect('order_detail', order_id=order.pk)


@csrf_exempt
@require_http_methods(['POST'])
def mercadopago_webhook(request):
    if not settings.MP_WEBHOOK_SECRET:
        logger.error('Se recibió una notificación de Mercado Pago sin MP_WEBHOOK_SECRET configurado.')
        return HttpResponse(status=503)

    try:
        notification = json.loads(request.body or b'{}')
    except (json.JSONDecodeError, UnicodeDecodeError):
        return HttpResponseBadRequest('Notificación inválida.')
    if not isinstance(notification, dict):
        return HttpResponseBadRequest('Notificación inválida.')

    topic = (
        request.GET.get('type')
        or notification.get('type')
        or request.GET.get('topic')
        or notification.get('topic')
    )
    if topic != 'payment':
        return HttpResponse(status=200)

    data = notification.get('data')
    if not isinstance(data, dict):
        data = {}
    payment_id = (
        request.GET.get('data.id')
        or request.GET.get('data_id')
        or request.GET.get('id')
        or data.get('id')
    )
    if not payment_id or not re.fullmatch(r'\d{1,20}', str(payment_id)):
        return HttpResponseBadRequest('Falta el identificador de pago.')
    payment_id = str(payment_id)

    if not verify_webhook_signature(
        request.headers.get('x-signature', ''),
        request.headers.get('x-request-id', ''),
        payment_id,
        settings.MP_WEBHOOK_SECRET,
    ):
        logger.warning('Firma inválida en notificación de Mercado Pago.')
        return HttpResponse(status=401)

    try:
        payment = get_payment(payment_id)
    except MercadoPagoError:
        logger.exception('No se pudo consultar el pago %s informado por Mercado Pago.', payment_id)
        return HttpResponse(status=503)

    if str(payment.get('id', '')) != payment_id:
        logger.warning('Mercado Pago devolvió un identificador de pago inesperado.')
        return HttpResponse(status=200)

    external_reference = payment.get('external_reference')
    try:
        external_reference = UUID(str(external_reference))
    except (TypeError, ValueError, AttributeError):
        logger.warning('Pago %s sin una referencia de pedido válida.', payment_id)
        return HttpResponse(status=200)

    try:
        transaction_amount = Decimal(str(payment.get('transaction_amount', '')))
    except (InvalidOperation, ValueError):
        logger.warning('Monto inválido en el pago %s.', payment_id)
        return HttpResponse(status=200)

    provider_status_map = {
        'pending': Order.PaymentStatus.PENDING,
        'in_process': Order.PaymentStatus.PENDING,
        'in_mediation': Order.PaymentStatus.PENDING,
        'authorized': Order.PaymentStatus.PENDING,
        'approved': Order.PaymentStatus.APPROVED,
        'rejected': Order.PaymentStatus.REJECTED,
        'cancelled': Order.PaymentStatus.CANCELLED,
        'refunded': Order.PaymentStatus.REFUNDED,
        'charged_back': Order.PaymentStatus.CHARGED_BACK,
    }
    provider_status = payment.get('status')
    new_payment_status = (
        provider_status_map.get(provider_status)
        if isinstance(provider_status, str)
        else None
    )
    if not new_payment_status:
        logger.warning('Estado desconocido recibido para el pago %s.', payment_id)
        return HttpResponse(status=200)

    with transaction.atomic():
        try:
            order = Order.objects.select_for_update().get(
                mercadopago_external_reference=external_reference,
            )
        except Order.DoesNotExist:
            logger.warning('Pago %s refiere a un pedido inexistente.', payment_id)
            return HttpResponse(status=200)

        if (
            payment.get('currency_id') != 'CLP'
            or not transaction_amount.is_finite()
            or transaction_amount != Decimal(order.total)
        ):
            logger.error('Monto o moneda no coinciden para el pago %s y pedido %s.', payment_id, order.pk)
            return HttpResponse(status=200)

        if (
            order.payment_status == Order.PaymentStatus.APPROVED
            and new_payment_status not in (
                Order.PaymentStatus.APPROVED,
                Order.PaymentStatus.REFUNDED,
                Order.PaymentStatus.CHARGED_BACK,
            )
        ):
            return HttpResponse(status=200)
        if (
            order.payment_status in (
                Order.PaymentStatus.REFUNDED,
                Order.PaymentStatus.CHARGED_BACK,
            )
            and new_payment_status not in (
                Order.PaymentStatus.REFUNDED,
                Order.PaymentStatus.CHARGED_BACK,
            )
        ):
            return HttpResponse(status=200)
        if (
            order.payment_status == Order.PaymentStatus.APPROVED
            and new_payment_status == Order.PaymentStatus.APPROVED
            and order.mercadopago_payment_id
            and order.mercadopago_payment_id != payment_id
        ):
            logger.critical(
                'Se detectó un segundo pago aprobado (%s) para el pedido %s; requiere revisión y posible reembolso.',
                payment_id,
                order.pk,
            )
            return HttpResponse(status=200)

        if order.status == Order.Status.CANCELLED and new_payment_status == Order.PaymentStatus.APPROVED:
            logger.critical(
                'Se aprobó el pago %s después de cancelar el pedido %s; requiere revisión.',
                payment_id,
                order.pk,
            )
        order.payment_status = new_payment_status
        order.mercadopago_payment_id = payment_id
        order.payment_updated_at = timezone.now()
        order.save(
            update_fields=[
                'payment_status',
                'mercadopago_payment_id',
                'payment_updated_at',
            ],
        )

    return HttpResponse(status=200)


@staff_required
def sales_dashboard(request):
    counts = Order.objects.aggregate(
        total=Count('pk'),
        pending=Count('pk', filter=Q(status=Order.Status.PENDING)),
        confirmed=Count('pk', filter=Q(status=Order.Status.CONFIRMED)),
        cancelled=Count('pk', filter=Q(status=Order.Status.CANCELLED)),
    )
    confirmed_total = OrderItem.objects.filter(
        order__status=Order.Status.CONFIRMED,
    ).aggregate(
        total=Sum(
            F('unit_price') * F('quantity'),
            output_field=BigIntegerField(),
        ),
    )['total'] or 0
    orders = (
        Order.objects.annotate(
            order_total=Sum(
                F('items__unit_price') * F('items__quantity'),
                output_field=BigIntegerField(),
            ),
        )
        .prefetch_related('items')
        .order_by('-created_at')[:100]
    )
    return render(
        request,
        'store/sales_dashboard.html',
        {
            'counts': counts,
            'confirmed_total': confirmed_total,
            'orders': orders,
            'status_choices': Order.Status.choices,
        },
    )


@staff_required
@require_http_methods(['POST'])
def order_update_status(request, order_id):
    new_status = request.POST.get('status')
    valid_statuses = {status for status, _ in Order.Status.choices}
    if new_status not in valid_statuses:
        messages.error(request, 'Estado de pedido no válido.')
        return redirect('sales_dashboard')

    with transaction.atomic():
        order = get_object_or_404(Order.objects.select_for_update(), pk=order_id)
        if order.status == Order.Status.CANCELLED and new_status != Order.Status.CANCELLED:
            messages.error(request, 'Un pedido cancelado no se puede reabrir.')
            return redirect('sales_dashboard')
        if (
            new_status == Order.Status.CANCELLED
            and order.payment_status == Order.PaymentStatus.APPROVED
        ):
            messages.error(
                request,
                'No canceles desde aquí un pedido pagado. Primero debes gestionar su reembolso en Mercado Pago.',
            )
            return redirect('sales_dashboard')
        if new_status == Order.Status.CANCELLED and order.status != Order.Status.CANCELLED:
            for item in order.items.all():
                Product.objects.filter(pk=item.product_id).update(
                    stock=F('stock') + item.quantity,
                )
        order.status = new_status
        order.save(update_fields=['status', 'updated_at'])

    messages.success(request, f'Pedido #{order.pk} actualizado.')
    return redirect('sales_dashboard')
