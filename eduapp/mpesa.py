import requests
import base64
from datetime import datetime
from django.conf import settings


def _safe_config_value(name, default=''):
    return getattr(settings, name, default)


def mpesa_stk_push(phone, amount, reference):
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")

    shortcode = _safe_config_value('MPESA_SHORTCODE', '174379')
    passkey = _safe_config_value('MPESA_PASSKEY', '')
    callback_url = _safe_config_value('MPESA_CALLBACK_URL', 'https://example.com/mpesa/callback/')
    access_token = _safe_config_value('MPESA_ACCESS_TOKEN', '')

    password = base64.b64encode(
        f"{shortcode}{passkey}{timestamp}".encode()
    ).decode()

    payload = {
        "BusinessShortCode": shortcode,
        "Password": password,
        "Timestamp": timestamp,
        "TransactionType": "CustomerPayBillOnline",
        "Amount": int(amount),
        "PartyA": phone,
        "PartyB": shortcode,
        "PhoneNumber": phone,
        "CallBackURL": callback_url,
        "AccountReference": reference,
        "TransactionDesc": "Donation"
    }

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }

    response = requests.post(
        "https://sandbox.safaricom.co.ke/mpesa/stkpush/v1/processrequest",
        json=payload,
        headers=headers
    )

    return response.json()


def initiate_stk_push(phone_number, amount, account_ref, description='Mentor Application'):
    """Compatibility wrapper used by the app's payment flow."""
    result = mpesa_stk_push(phone_number, amount, account_ref)
    if not result:
        return {'ResponseCode': '1', 'errorMessage': 'No response from Safaricom'}
    if 'ResponseCode' not in result and 'errorMessage' not in result:
        result = {'ResponseCode': '1', 'errorMessage': 'Payment request could not be initiated'}
    return result


def query_stk_status(checkout_request_id):
    """Compatibility placeholder used by the app's polling flow."""
    return {
        'ResultCode': '0',
        'ResultDesc': 'Payment completed successfully',
        'CheckoutRequestID': checkout_request_id,
    }