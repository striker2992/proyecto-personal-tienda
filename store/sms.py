import base64
import json
import logging
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings

logger = logging.getLogger(__name__)


class SMSDeliveryError(Exception):
    pass


def send_sms(phone, message):
    account_sid = settings.TWILIO_ACCOUNT_SID.strip()
    auth_token = settings.TWILIO_AUTH_TOKEN.strip()
    messaging_service_sid = settings.TWILIO_MESSAGING_SERVICE_SID.strip()
    from_number = settings.TWILIO_FROM_NUMBER.strip()
    if not account_sid or not auth_token or not (messaging_service_sid or from_number):
        raise SMSDeliveryError(
            'Falta configurar Twilio para enviar SMS. Revisa las variables TWILIO_* del servidor.',
        )
    if not re.fullmatch(r'\+[1-9]\d{7,14}', phone):
        raise SMSDeliveryError('El teléfono no tiene un formato internacional válido.')

    data = {'To': phone, 'Body': message}
    if messaging_service_sid:
        data['MessagingServiceSid'] = messaging_service_sid
    else:
        data['From'] = from_number
    credentials = base64.b64encode(
        f'{account_sid}:{auth_token}'.encode('utf-8'),
    ).decode('ascii')
    request = Request(
        f'https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json',
        data=urlencode(data).encode('utf-8'),
        headers={
            'Authorization': f'Basic {credentials}',
            'Content-Type': 'application/x-www-form-urlencoded',
            'Accept': 'application/json',
        },
        method='POST',
    )
    try:
        with urlopen(request, timeout=10) as response:
            result = json.loads(response.read())
    except HTTPError as error:
        logger.warning('Twilio respondió HTTP %s al solicitar el envío de un SMS.', error.code)
        raise SMSDeliveryError('Twilio no pudo enviar el SMS. Revisa la configuración e inténtalo más tarde.') from error
    except (URLError, TimeoutError, OSError) as error:
        logger.warning('No se pudo conectar con Twilio para enviar un SMS.')
        raise SMSDeliveryError('No se pudo conectar con Twilio. Inténtalo más tarde.') from error
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        logger.warning('Twilio devolvió una respuesta inválida al solicitar un SMS.')
        raise SMSDeliveryError('Twilio devolvió una respuesta inválida.') from error

    if not isinstance(result, dict) or not result.get('sid'):
        raise SMSDeliveryError('Twilio no confirmó la solicitud de envío del SMS.')
