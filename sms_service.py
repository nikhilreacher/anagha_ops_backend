import json
import os
from urllib import error, request


def sms_enabled() -> bool:
    return bool(os.getenv("SMS_GATEWAY_URL"))


def normalize_indian_phone(phone: str | None) -> str | None:
    raw = "".join(ch for ch in (phone or "") if ch.isdigit() or ch == "+")
    if not raw:
        return None
    if raw.startswith("+"):
        return raw
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) == 10:
        return f"+91{digits}"
    if len(digits) == 12 and digits.startswith("91"):
        return f"+{digits}"
    return None


def build_credit_sms(shop_name: str, bill_no: str, balance: float) -> str:
    return (
        f"Anagha Enterprises(HUL): Credit added for {shop_name}. "
        f"Bill No: {bill_no}. Outstanding amount: Rs. {balance:,.2f}. "
        "For questions, please contact our office."
    )


def build_payment_sms(shop_name: str, applied_amount: float, remaining_amount: float) -> str:
    return (
        f"Anagha Enterprises(HUL): Payment received from {shop_name}. "
        f"Amount received: Rs. {applied_amount:,.2f}. "
        f"Remaining outstanding: Rs. {remaining_amount:,.2f}. Thank you."
    )


def send_sms(phone: str | None, message: str):
    if not sms_enabled():
        return {"sent": False, "reason": "sms_not_configured"}

    to_number = normalize_indian_phone(phone)
    if not to_number:
        return {"sent": False, "reason": "invalid_shop_phone"}

    gateway_url = os.getenv("SMS_GATEWAY_URL", "")
    api_key = os.getenv("SMS_GATEWAY_API_KEY", "")
    device_id = os.getenv("SMS_GATEWAY_DEVICE_ID", "")

    payload = {
        "phone": to_number,
        "message": message,
    }
    if api_key:
        payload["api_key"] = api_key
    if device_id:
        payload["device_id"] = device_id

    req = request.Request(
        gateway_url,
        data=json.dumps(payload).encode(),
        method="POST",
        headers={
            "Content-Type": "application/json",
        },
    )

    try:
        with request.urlopen(req, timeout=15) as response:
            response_text = response.read().decode(errors="ignore")
            return {
                "sent": True,
                "to": to_number,
                "response": response_text[:500],
            }
    except error.HTTPError as exc:
        detail = exc.read().decode(errors="ignore")
        return {
            "sent": False,
            "reason": "sms_gateway_http_error",
            "detail": detail[:500],
        }
    except Exception as exc:
        return {
            "sent": False,
            "reason": "sms_gateway_request_failed",
            "detail": str(exc),
        }


def send_credit_added_sms(shop_name: str, phone: str | None, bill_no: str, balance: float):
    return send_sms(phone, build_credit_sms(shop_name, bill_no, balance))


def send_payment_received_sms(shop_name: str, phone: str | None, applied_amount: float, remaining_amount: float):
    return send_sms(phone, build_payment_sms(shop_name, applied_amount, remaining_amount))
