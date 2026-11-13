import hashlib
import hmac
import json
import logging
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from django.conf import settings
from django.urls import reverse

logger = logging.getLogger(__name__)
API_URL = 'https://api.mercadopago.com'


class MercadoPagoError(Exception):
    pass


def _public_base_url():
    base_url = settings.MP_PUBLIC_BASE_URL.rstrip('/')
    parsed_url = urlsplit(base_url)
    if (
        parsed_url.scheme != 'https'
        or not parsed_url.netloc
        or parsed_url.username
        or parsed_url.password
        or parsed_url.query
        or parsed_url.fragment
    ):
        raise MercadoPagoError(
            'Configura MP_PUBLIC_BASE_URL con el dominio público HTTPS de la tienda.',
        )
    return base_url


def _api_request(path, method, payload=None):
    access_token = settings.MP_ACCESS_TOKEN.strip()
    if not access_token:
        raise MercadoPagoError('Falta configurar MP_ACCESS_TOKEN en el servidor.')

    body = json.dumps(payload).encode('utf-8') if payload is not None else None
    request = Request(
        f'{API_URL}{path}',
        data=body,
        headers={
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        },
        method=method,
    )
    try:
        with urlopen(request, timeout=10) as response:
            result = json.loads(response.read())
    except HTTPError as error:
        logger.warning('Mercado Pago respondió HTTP %s a %s.', error.code, path)
        raise MercadoPagoError(
            'Mercado Pago no pudo procesar la solicitud. Revisa las credenciales y vuelve a intentar.',
        ) from error
    except (URLError, TimeoutError, OSError) as error:
        logger.warning('No se pudo conectar con Mercado Pago en %s.', path)
        raise MercadoPagoError(
            'No se pudo conectar con Mercado Pago. Inténtalo nuevamente.',
        ) from error
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        logger.warning('Mercado Pago devolvió una respuesta inválida en %s.', path)
        raise MercadoPagoError('Mercado Pago devolvió una respuesta inválida.') from error

    if not isinstance(result, dict):
        raise MercadoPagoError('Mercado Pago devolvió una respuesta inválida.')
    return result


def create_checkout_preference(order):
    base_url = _public_base_url()
    items = [
        {
            'id': str(item.product_id),
            'title': item.product_name[:256],
            'quantity': item.quantity,
            'currency_id': 'CLP',
            'unit_price': item.unit_price,
        }
        for item in order.items.order_by('pk')
    ]
    if not items:
        raise MercadoPagoError('El pedido no tiene productos para cobrar.')

    return_url = f'{base_url}{reverse("payment_return", args=[order.pk])}'
    payload = {
        'items': items,
        'payer': {
            'name': order.customer_name,
            'email': order.customer_email,
        },
        'external_reference': str(order.mercadopago_external_reference),
        'back_urls': {
            'success': return_url,
            'pending': return_url,
            'failure': return_url,
        },
        'auto_return': 'approved',
        'notification_url': f'{base_url}{reverse("mercadopago_webhook")}',
        'metadata': {'order_id': order.pk},
    }
    if order.rut:
        payload['payer']['identification'] = {
            'type': 'RUT',
            'number': order.rut,
        }

    result = _api_request('/checkout/preferences', 'POST', payload)
    preference_id = result.get('id')
    checkout_url_key = 'sandbox_init_point' if settings.MP_USE_SANDBOX else 'init_point'
    checkout_url = result.get(checkout_url_key)
    if not preference_id or not isinstance(checkout_url, str):
        raise MercadoPagoError('Mercado Pago no devolvió los datos para iniciar el pago.')

    parsed_checkout_url = urlsplit(checkout_url)
    checkout_host = (parsed_checkout_url.hostname or '').lower()
    if (
        parsed_checkout_url.scheme != 'https'
        or not (
            checkout_host == 'mercadopago.com'
            or checkout_host.endswith('.mercadopago.com')
            or checkout_host == 'mercadopago.cl'
            or checkout_host.endswith('.mercadopago.cl')
        )
    ):
        raise MercadoPagoError('Mercado Pago devolvió una dirección de pago no válida.')

    return str(preference_id), checkout_url


def get_payment(payment_id):
    if not re.fullmatch(r'\d{1,20}', str(payment_id)):
        raise MercadoPagoError('El identificador del pago no es válido.')
    return _api_request(f'/v1/payments/{payment_id}', 'GET')


def verify_webhook_signature(signature, request_id, data_id, secret):
    if not signature or not request_id or not data_id or not secret:
        return False

    fields = {}
    for part in signature.split(','):
        key, separator, value = part.strip().partition('=')
        if separator:
            fields[key] = value

    timestamp = fields.get('ts', '')
    version = fields.get('v1', '')
    if not re.fullmatch(r'\d+', timestamp) or not re.fullmatch(r'[0-9a-fA-F]{64}', version):
        return False

    manifest = f'id:{str(data_id).lower()};request-id:{request_id};ts:{timestamp};'
    expected_signature = hmac.new(
        secret.encode('utf-8'),
        manifest.encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected_signature, version.lower())
