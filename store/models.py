import uuid

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils.text import slugify


class Product(models.Model):
    name = models.CharField(max_length=140)
    slug = models.SlugField(max_length=160, unique=True, blank=True)
    category = models.CharField(max_length=80)
    description = models.TextField()
    dimensions = models.CharField(max_length=100)
    price = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    stock = models.PositiveIntegerField(default=0)
    emoji = models.CharField(max_length=8, default='📦')
    image = models.ImageField(upload_to='products/', blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(self.name) or 'producto'
            candidate = base_slug
            suffix = 2
            while Product.objects.filter(slug=candidate).exclude(pk=self.pk).exists():
                candidate = f'{base_slug}-{suffix}'
                suffix += 1
            self.slug = candidate[:160]
        super().save(*args, **kwargs)


class StoreSettings(models.Model):
    contact_email = models.EmailField(
        blank=True,
        help_text='Correo que recibirá consultas, dudas y reclamos.',
    )

    def __str__(self):
        return 'Configuración de la tienda'

    @classmethod
    def get_settings(cls):
        store_settings, _ = cls.objects.get_or_create(
            pk=1,
            defaults={'contact_email': settings.CONTACT_RECIPIENT_EMAIL},
        )
        return store_settings


class Inquiry(models.Model):
    class Topic(models.TextChoices):
        QUESTION = 'question', 'Consulta'
        COMPLAINT = 'complaint', 'Reclamo'
        OTHER = 'other', 'Otro'

    name = models.CharField(max_length=120)
    email = models.EmailField(max_length=254)
    topic = models.CharField(max_length=20, choices=Topic.choices)
    subject = models.CharField(max_length=160)
    message = models.TextField(max_length=5000)
    created_at = models.DateTimeField(auto_now_add=True)
    admin_reply = models.TextField(blank=True, max_length=5000)
    replied_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.get_topic_display()}: {self.subject}'


class CustomerProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='customer_profile',
    )
    phone = models.CharField(max_length=30, blank=True)
    phone_verified = models.BooleanField(default=False)
    phone_verification_code_hash = models.CharField(max_length=128, blank=True)
    phone_verification_expires_at = models.DateTimeField(null=True, blank=True)
    phone_verification_attempts = models.PositiveSmallIntegerField(default=0)
    phone_verification_sent_at = models.DateTimeField(null=True, blank=True)
    phone_verification_window_started_at = models.DateTimeField(null=True, blank=True)
    phone_verification_send_count = models.PositiveSmallIntegerField(default=0)
    rut = models.CharField(max_length=12, blank=True)
    delivery_address = models.CharField(max_length=250, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'Perfil de {self.user.username}'


class Order(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pendiente'
        CONFIRMED = 'confirmed', 'Confirmado'
        CANCELLED = 'cancelled', 'Cancelado'

    class PaymentStatus(models.TextChoices):
        NOT_STARTED = 'not_started', 'Sin iniciar'
        PENDING = 'pending', 'Pendiente'
        APPROVED = 'approved', 'Pagado'
        REJECTED = 'rejected', 'Rechazado'
        CANCELLED = 'cancelled', 'Cancelado'
        REFUNDED = 'refunded', 'Reembolsado'
        CHARGED_BACK = 'charged_back', 'Contracargo'

    customer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='container_orders',
    )
    customer_name = models.CharField(max_length=160)
    customer_email = models.EmailField(max_length=254)
    phone = models.CharField(max_length=30)
    rut = models.CharField(max_length=12, blank=True)
    delivery_address = models.CharField(max_length=250)
    notes = models.TextField(blank=True, max_length=1000)
    status = models.CharField(
        max_length=12,
        choices=Status.choices,
        default=Status.PENDING,
    )
    payment_status = models.CharField(
        max_length=16,
        choices=PaymentStatus.choices,
        default=PaymentStatus.NOT_STARTED,
    )
    mercadopago_external_reference = models.UUIDField(
        default=uuid.uuid4,
        editable=False,
        unique=True,
    )
    mercadopago_preference_id = models.CharField(max_length=100, blank=True)
    mercadopago_payment_id = models.CharField(max_length=64, blank=True)
    mercadopago_checkout_url = models.URLField(max_length=500, blank=True)
    payment_updated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'Pedido #{self.pk} - {self.customer_name}'

    @property
    def total(self):
        return sum(item.unit_price * item.quantity for item in self.items.all())


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name='order_items')
    product_name = models.CharField(max_length=140)
    unit_price = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])

    def __str__(self):
        return f'{self.quantity} x {self.product_name}'
