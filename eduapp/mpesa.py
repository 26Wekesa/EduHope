import base64
import logging
from datetime import datetime
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


def _api_base_url():
    environment = settings.MPESA_ENVIRONMENT.lower()
    if environment == 'sandbox':
        return 'https://sandbox.safaricom.co.ke'
    if environment == 'production':
        return 'https://api.safaricom.co.ke'
    raise ValueError('MPESA_ENVIRONMENT must be sandbox or production')


def _access_token():
    consumer_key = settings.MPESA_CONSUMER_KEY
    consumer_secret = settings.MPESA_CONSUMER_SECRET
    if not consumer_key or not consumer_secret:
        raise ValueError('M-Pesa consumer credentials are not configured')

    response = requests.get(
        f'{_api_base_url()}/oauth/v1/generate?grant_type=client_credentials',
        auth=(consumer_key, consumer_secret),
        timeout=15,
    )
    response.raise_for_status()
    token = response.json().get('access_token')
    if not token:
        raise ValueError('Daraja did not return an access token')
    return token


def _timestamp():
    return datetime.now(ZoneInfo('Africa/Nairobi')).strftime('%Y%m%d%H%M%S')


def _password(timestamp):
    shortcode = settings.MPESA_SHORTCODE
    passkey = settings.MPESA_PASSKEY
    if not shortcode or not passkey:
        raise ValueError('M-Pesa shortcode and passkey are not configured')
    return base64.b64encode(f'{shortcode}{passkey}{timestamp}'.encode()).decode()


def mpesa_stk_push(phone, amount, reference, description='Donation'):
    timestamp = _timestamp()
    shortcode = settings.MPESA_SHORTCODE
    callback_url = urlparse(settings.MPESA_CALLBACK_URL)
    if callback_url.scheme != 'https' or not callback_url.hostname:
        raise ValueError('M-Pesa callback URL must be a public HTTPS URL')
    if callback_url.hostname.endswith(('.example', '.invalid', '.localhost', '.test')):
        raise ValueError('Replace the example M-Pesa callback URL with a public HTTPS URL')

    payload = {
        "BusinessShortCode": shortcode,
        "Password": _password(timestamp),
        "Timestamp": timestamp,
        "TransactionType": settings.MPESA_TRANSACTION_TYPE,
        "Amount": int(amount),
        "PartyA": phone,
        "PartyB": shortcode,
        "PhoneNumber": phone,
        "CallBackURL": settings.MPESA_CALLBACK_URL,
        "AccountReference": reference,
        "TransactionDesc": description,
    }

    response = requests.post(
        f'{_api_base_url()}/mpesa/stkpush/v1/processrequest',
        json=payload,
        headers={
            'Authorization': f'Bearer {_access_token()}',
            'Content-Type': 'application/json',
        },
        timeout=15,
    )
    response.raise_for_status()
    return response.json()


def initiate_stk_push(phone_number, amount, account_ref, description='Mentor Application'):
    try:
        result = mpesa_stk_push(phone_number, amount, account_ref, description)
    except (requests.RequestException, ValueError):
        logger.exception('M-Pesa STK Push request failed')
        return {'ResponseCode': '1', 'errorMessage': 'Payment request could not be initiated'}
    if not result or ('ResponseCode' not in result and 'errorMessage' not in result):
        return {'ResponseCode': '1', 'errorMessage': 'Payment request could not be initiated'}
    return result


def query_stk_status(checkout_request_id):
    timestamp = _timestamp()
    payload = {
        'BusinessShortCode': settings.MPESA_SHORTCODE,
        'Password': _password(timestamp),
        'Timestamp': timestamp,
        'CheckoutRequestID': checkout_request_id,
    }
    response = requests.post(
        f'{_api_base_url()}/mpesa/stkpushquery/v1/query',
        json=payload,
        headers={'Authorization': f'Bearer {_access_token()}'},
        timeout=15,
    )
    response.raise_for_status()
    return response.json()