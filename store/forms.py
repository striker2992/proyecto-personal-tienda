import re

from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User

from .models import CustomerProfile, Inquiry, Product, StoreSettings

MAX_IMAGE_SIZE = 5 * 1024 * 1024


def normalize_phone_number(value):
    phone = value.strip()
    if not re.fullmatch(r'\+?[0-9()\-\s]+', phone):
        raise forms.ValidationError('Ingresa un teléfono móvil válido, por ejemplo +56912345678.')
    digits = re.sub(r'\D', '', phone)
    if phone.startswith('+') and 8 <= len(digits) <= 15:
        return f'+{digits}'
    if digits.startswith('56') and len(digits) == 11:
        return f'+{digits}'
    if digits.startswith('9') and len(digits) == 9:
        return f'+56{digits}'
    raise forms.ValidationError('Ingresa un teléfono móvil válido, por ejemplo +56912345678.')


def normalize_chilean_rut(value):
    compact_rut = re.sub(r'[.\-\s]', '', value).upper()
    if not compact_rut:
        return ''
    if not re.fullmatch(r'\d{7,8}[\dK]', compact_rut):
        raise forms.ValidationError('Ingresa un RUT chileno válido.')

    number, verifier = compact_rut[:-1], compact_rut[-1]
    checksum = sum(
        int(digit) * (index % 6 + 2)
        for index, digit in enumerate(reversed(number))
    )
    expected_verifier = 11 - checksum % 11
    if expected_verifier == 11:
        expected_verifier = '0'
    elif expected_verifier == 10:
        expected_verifier = 'K'
    else:
        expected_verifier = str(expected_verifier)
    if verifier != expected_verifier:
        raise forms.ValidationError('El dígito verificador del RUT no es válido.')
    return f'{number}-{verifier}'


class CustomerRegistrationForm(UserCreationForm):
    email = forms.EmailField(required=True, max_length=254)

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ('username', 'email')

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError('Ya existe una cuenta con ese correo electrónico.')
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data['email']
        user.is_staff = False
        user.is_superuser = False
        if commit:
            user.save()
        return user


class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = (
            'name',
            'category',
            'description',
            'dimensions',
            'price',
            'stock',
            'image',
            'emoji',
            'is_active',
        )
        widgets = {
            'description': forms.Textarea(attrs={'rows': 4}),
            'image': forms.ClearableFileInput(attrs={'accept': 'image/jpeg,image/png,image/webp'}),
        }

    def clean_image(self):
        image = self.cleaned_data.get('image')
        if image and image.size > MAX_IMAGE_SIZE:
            raise forms.ValidationError('La imagen no puede superar los 5 MB.')
        return image


class StoreSettingsForm(forms.ModelForm):
    class Meta:
        model = StoreSettings
        fields = ('contact_email',)


class InquiryForm(forms.ModelForm):
    website = forms.CharField(required=False, widget=forms.HiddenInput)

    class Meta:
        model = Inquiry
        fields = ('name', 'email', 'topic', 'subject', 'message')
        widgets = {
            'message': forms.Textarea(attrs={'rows': 5, 'maxlength': 5000}),
        }


class InquiryReplyForm(forms.Form):
    reply = forms.CharField(
        max_length=5000,
        widget=forms.Textarea(attrs={'rows': 4}),
        label='Respuesta al cliente',
    )


class UserProfileForm(forms.ModelForm):
    email = forms.EmailField(required=True, max_length=254)

    class Meta:
        model = User
        fields = ('username', 'first_name', 'last_name', 'email')

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        if User.objects.filter(email__iexact=email).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError('Ese correo ya está asociado a otra cuenta.')
        return email


class CustomerProfileForm(forms.ModelForm):
    phone = forms.CharField(
        required=False,
        max_length=30,
        label='Teléfono',
        help_text='Lo verificaremos con un código SMS de 6 dígitos.',
    )
    rut = forms.CharField(
        required=False,
        max_length=12,
        label='RUT',
        help_text='Se guardará normalizado, por ejemplo 12345678-5.',
    )

    class Meta:
        model = CustomerProfile
        fields = ('phone', 'rut', 'delivery_address')
        labels = {'delivery_address': 'Dirección de entrega habitual'}
        widgets = {
            'delivery_address': forms.TextInput(attrs={'maxlength': 250}),
        }

    def clean_phone(self):
        phone = self.cleaned_data['phone'].strip()
        return normalize_phone_number(phone) if phone else ''

    def clean_rut(self):
        return normalize_chilean_rut(self.cleaned_data['rut'].strip())

    def clean_delivery_address(self):
        return self.cleaned_data['delivery_address'].strip()


class OrderCheckoutForm(forms.Form):
    customer_name = forms.CharField(max_length=160, label='Nombre completo')
    delivery_address = forms.CharField(max_length=250, label='Dirección de entrega')
    notes = forms.CharField(
        required=False,
        max_length=1000,
        widget=forms.Textarea(attrs={'rows': 3}),
        label='Notas para el pedido',
    )

    def clean_delivery_address(self):
        return self.cleaned_data['delivery_address'].strip()


class PhoneVerificationForm(forms.Form):
    code = forms.CharField(
        min_length=6,
        max_length=6,
        label='Código de verificación',
        widget=forms.TextInput(attrs={
            'autocomplete': 'one-time-code',
            'inputmode': 'numeric',
            'pattern': '[0-9]{6}',
        }),
    )

    def clean_code(self):
        code = self.cleaned_data['code']
        if not re.fullmatch(r'\d{6}', code):
            raise forms.ValidationError('El código debe tener exactamente 6 dígitos.')
        return code
