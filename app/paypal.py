# SPDX-FileCopyrightText: William Moreno Reyes <williamjmorenor@gmail.com>
# SPDX-License-Identifier: Apache-2.0

import json
import logging
import os
import uuid
from base64 import b64encode
from datetime import UTC, datetime, timedelta

import requests
from flask import current_app

logger = logging.getLogger("admiral-harbor")


class PayPalError(RuntimeError):
    pass


def _resolve_secret(value):
    """Decrypt a stored secret if encrypted; otherwise return as-is."""
    from app.extensions import secrets as ext_secrets
    from app.secrets_manager import SecretsManager

    if ext_secrets is None or not value:
        return value
    try:
        decrypted = ext_secrets.decrypt(value)
        if decrypted != value:
            return decrypted
    except SecretsManager.EncryptionError:
        logger.error("Could not decrypt PayPal client_secret")
        return value
    return value


def _db_paypal_config():
    """Return the persisted PayPal config, with env only as bootstrap fallback."""
    from app.extensions import db
    from app.models import HarborPayPalConfig

    cfg = db.session.query(HarborPayPalConfig).first()
    if cfg is not None:
        return {
            "mode": cfg.mode,
            "client_id": cfg.client_id or "",
            "client_secret": _resolve_secret(cfg.client_secret or ""),
            "webhook_id": cfg.webhook_id or "",
        }
    return {
        "mode": current_app.config.get("HARBOR_PAYPAL_MODE", "mock"),
        "client_id": current_app.config.get("HARBOR_PAYPAL_CLIENT_ID", ""),
        "client_secret": current_app.config.get("HARBOR_PAYPAL_CLIENT_SECRET", ""),
        "webhook_id": current_app.config.get("HARBOR_PAYPAL_WEBHOOK_ID", ""),
    }


_PAYPAL_BASE_URLS = {
    "sandbox": "https://api-m.sandbox.paypal.com",
    "live": "https://api-m.paypal.com",
}


def _base_url():
    """Resolve the fixed PayPal REST API endpoint for the configured mode."""
    mode = _db_paypal_config()["mode"]
    try:
        return _PAYPAL_BASE_URLS[mode]
    except KeyError as exc:
        raise PayPalError(f"Invalid PayPal mode: {mode!r}") from exc


def _api_url():
    pp = _db_paypal_config()
    if pp["mode"] == "mock":
        return _external_url() + "/mock-paypal"
    return _base_url()


def _external_url():
    """Return the portal external URL, preferring DB setting over env."""
    from app.settings import get_external_url

    return get_external_url()


def paypal_mode():
    """Return the effective mode shared by checkout, callbacks and API calls."""
    return _db_paypal_config()["mode"]


def is_mock_mode():
    return paypal_mode() == "mock"


def _is_mock():
    """Backward-compatible internal alias."""
    return is_mock_mode()


def _get_access_token():
    pp = _db_paypal_config()
    client_id = pp["client_id"]
    client_secret = pp["client_secret"]
    if not client_id or not client_secret:
        raise PayPalError("PayPal Client ID and Client Secret are required")
    base_url = _base_url()
    auth = b64encode(f"{client_id}:{client_secret}".encode()).decode()
    try:
        resp = requests.post(
            f"{base_url}/v1/oauth2/token",
            headers={"Authorization": f"Basic {auth}"},
            data={"grant_type": "client_credentials"},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["access_token"]
    except requests.RequestException as exc:
        logger.error("PayPal auth failed", extra={"error": str(exc)})
        raise PayPalError(f"PayPal authentication failed: {exc}") from exc


def create_subscription(
    plan_id,
    return_url,
    cancel_url,
    custom_id=None,
    amount_cents=None,
    currency="USD",
):
    if _is_mock():
        sub_id = f"MOCK-SUB-{uuid.uuid4().hex[:16]}"
        approval_url = f"{_api_url()}/approve?subscription_id={sub_id}&return_url={return_url}&cancel_url={cancel_url}"
        return {
            "id": sub_id,
            "status": "APPROVAL_PENDING",
            "links": [{"rel": "approve", "href": approval_url}],
        }

    if not plan_id:
        raise PayPalError("PayPal plan ID is required")

    token = _get_access_token()
    base_url = _base_url()
    body = {
        "plan_id": plan_id,
        "application_context": {
            "return_url": return_url,
            "cancel_url": cancel_url,
        },
    }
    if custom_id:
        body["custom_id"] = custom_id
    if amount_cents is not None:
        body["plan_override"] = {
            "billing_cycles": [
                {
                    "sequence": 1,
                    "pricing_scheme": {
                        "fixed_price": {
                            "value": f"{amount_cents / 100:.2f}",
                            "currency_code": currency,
                        }
                    },
                }
            ]
        }
    try:
        resp = requests.post(
            f"{base_url}/v1/billing/subscriptions",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as exc:
        logger.error("PayPal create subscription failed", extra={"error": str(exc)})
        raise PayPalError(f"Failed to create PayPal subscription: {exc}") from exc


def capture_subscription(subscription_id):
    if _is_mock():
        return {
            "id": subscription_id,
            "status": "ACTIVE",
            "start_time": "2026-06-08T00:00:00Z",
            "billing_info": {
                "next_billing_time": "2026-07-08T00:00:00Z",
            },
        }
    token = _get_access_token()
    base_url = _base_url()
    try:
        resp = requests.post(
            f"{base_url}/v1/billing/subscriptions/{subscription_id}/activate",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as exc:
        logger.error("PayPal activate subscription failed", extra={"error": str(exc)})
        raise PayPalError(f"Failed to activate PayPal subscription: {exc}") from exc


def get_subscription(subscription_id):
    if _is_mock():
        return {
            "id": subscription_id,
            "status": "ACTIVE",
            "start_time": "2026-06-08T00:00:00Z",
            "billing_info": {
                "next_billing_time": "2026-07-08T00:00:00Z",
            },
        }
    token = _get_access_token()
    base_url = _base_url()
    try:
        resp = requests.get(
            f"{base_url}/v1/billing/subscriptions/{subscription_id}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as exc:
        logger.error("PayPal get subscription failed", extra={"error": str(exc)})
        raise PayPalError(f"Failed to get PayPal subscription: {exc}") from exc


def cancel_subscription(subscription_id, reason="Cancelled by customer"):
    """Cancel a PayPal subscription.

    PayPal returns 204 No Content on success. Raises PayPalError on failure.
    The cancellation is a no-op in mock mode.
    """
    if _is_mock():
        return
    token = _get_access_token()
    base_url = _base_url()
    try:
        resp = requests.post(
            f"{base_url}/v1/billing/subscriptions/{subscription_id}/cancel",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={"reason": reason[:128]},
            timeout=30,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.error("PayPal cancel subscription failed", extra={"error": str(exc)})
        raise PayPalError(f"Failed to cancel PayPal subscription: {exc}") from exc


def refund_last_sale(subscription_id):
    """Refund the most recent captured payment for a PayPal subscription.

    Used when setup_command fails and the customer must be reimbursed.
    The function:
      1. Lists transactions for the subscription (last 30 days).
      2. Picks the most recent PAYMENT.CAPTURE.COMPLETED transaction.
      3. Issues a full refund via POST /v2/payments/captures/{id}/refund.

    Returns the refund transaction ID on success, or None in mock mode
    or when no capturable transaction is found.

    Raises PayPalError if the PayPal API returns an error.
    """
    if _is_mock():
        logger.info(
            "refund_last_sale: mock mode, no real refund for %s",
            subscription_id,
        )
        return None

    token = _get_access_token()
    base_url = _base_url()

    start_time = (datetime.now(UTC) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    end_time = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        resp = requests.get(
            f"{base_url}/v1/billing/subscriptions/{subscription_id}/transactions",
            headers={"Authorization": f"Bearer {token}"},
            params={"start_time": start_time, "end_time": end_time},
            timeout=30,
        )
        resp.raise_for_status()
        transactions = resp.json().get("transactions", [])
    except requests.RequestException as exc:
        logger.error(
            "PayPal list transactions failed for %s: %s",
            subscription_id,
            exc,
        )
        raise PayPalError(f"Failed to list PayPal transactions: {exc}") from exc

    capture_id = None
    for tx in reversed(transactions):
        status = tx.get("status", "")
        if status == "COMPLETED":
            capture_id = tx.get("id")
            break

    if capture_id is None:
        logger.warning(
            "refund_last_sale: no completed capture found for %s",
            subscription_id,
        )
        return None

    try:
        refund_resp = requests.post(
            f"{base_url}/v2/payments/captures/{capture_id}/refund",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={},
            timeout=30,
        )
        refund_resp.raise_for_status()
        refund_data = refund_resp.json()
        logger.info(
            "Refund issued for capture %s on subscription %s: %s",
            capture_id,
            subscription_id,
            refund_data.get("id", ""),
        )
        return refund_data.get("id")
    except requests.RequestException as exc:
        logger.error("PayPal refund failed for capture %s: %s", capture_id, exc)
        raise PayPalError(f"Failed to refund PayPal capture {capture_id}: {exc}") from exc


def verify_webhook_signature(headers, body):
    if _is_mock():
        mock_token = os.environ.get("HARBOR_MOCK_WEBHOOK_TOKEN", "")
        req_token = headers.get("X-Admiral-Webhook-Test", "")
        if not mock_token or req_token != mock_token:
            logger.warning("mock webhook rejected: missing or mismatched X-Admiral-Webhook-Test header")
            return False
        return True
    webhook_id = _db_paypal_config()["webhook_id"]
    if not webhook_id:
        logger.warning("PayPal webhook ID not configured")
        return False
    try:
        webhook_event = json.loads(body)
    except (TypeError, json.JSONDecodeError):
        logger.warning("PayPal webhook rejected: body is not valid JSON")
        return False
    if not isinstance(webhook_event, dict):
        logger.warning("PayPal webhook rejected: event must be a JSON object")
        return False
    token = _get_access_token()
    base_url = _base_url()
    verification = {
        "auth_algo": headers.get("PAYPAL-AUTH-ALGO", ""),
        "cert_url": headers.get("PAYPAL-CERT-URL", ""),
        "transmission_id": headers.get("PAYPAL-TRANSMISSION-ID", ""),
        "transmission_sig": headers.get("PAYPAL-TRANSMISSION-SIG", ""),
        "transmission_time": headers.get("PAYPAL-TRANSMISSION-TIME", ""),
        "webhook_id": webhook_id,
        # The PayPal postback API schema requires webhook_event to be a JSON
        # object. Raw-body CRC32 preservation applies to local cryptographic
        # verification, not to this postback request.
        "webhook_event": webhook_event,
    }
    try:
        resp = requests.post(
            f"{base_url}/v1/notifications/verify-webhook-signature",
            headers={"Authorization": f"Bearer {token}"},
            json=verification,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json().get("verification_status") == "SUCCESS"
    except requests.RequestException as exc:
        logger.error("PayPal webhook verify failed", extra={"error": str(exc)})
        return False
