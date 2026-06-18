# SPDX-FileCopyrightText: William Moreno Reyes <williamjmorenor@gmail.com>
# SPDX-License-Identifier: Apache-2.0

import json
import logging
import uuid
from base64 import b64encode

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
    """Return PayPal config from DB, falling back to env vars (current_app.config)."""
    from app.models import HarborPayPalConfig
    from app.extensions import db

    cfg = db.session.query(HarborPayPalConfig).first()
    if cfg is not None and cfg.mode != "mock":
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


def _api_url():
    pp = _db_paypal_config()
    if pp["mode"] == "mock":
        return _external_url() + "/mock-paypal"
    return current_app.config.get(
        "HARBOR_PAYPAL_BASE_URL", "https://api-m.sandbox.paypal.com"
    )


def _external_url():
    """Return the portal external URL, preferring DB setting over env."""
    from app.settings import get_external_url

    return get_external_url()


def _is_mock():
    return _db_paypal_config()["mode"] == "mock"


def _get_access_token():
    pp = _db_paypal_config()
    client_id = pp["client_id"]
    client_secret = pp["client_secret"]
    if not client_id or not client_secret:
        return "mock-token"
    base_url = current_app.config["HARBOR_PAYPAL_BASE_URL"]
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


def create_subscription(plan_id, return_url, cancel_url, custom_id=None):
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
    base_url = current_app.config["HARBOR_PAYPAL_BASE_URL"]
    body = {
        "plan_id": plan_id,
        "application_context": {
            "return_url": return_url,
            "cancel_url": cancel_url,
        },
    }
    if custom_id:
        body["custom_id"] = custom_id
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
    base_url = current_app.config["HARBOR_PAYPAL_BASE_URL"]
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
    base_url = current_app.config["HARBOR_PAYPAL_BASE_URL"]
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


def verify_webhook_signature(headers, body):
    if _is_mock():
        return True
    webhook_id = _db_paypal_config()["webhook_id"]
    if not webhook_id:
        logger.warning("PayPal webhook ID not configured")
        return False
    token = _get_access_token()
    base_url = current_app.config["HARBOR_PAYPAL_BASE_URL"]
    verification = {
        "auth_algo": headers.get("PAYPAL-AUTH-ALGO", ""),
        "cert_url": headers.get("PAYPAL-CERT-URL", ""),
        "transmission_id": headers.get("PAYPAL-TRANSMISSION-ID", ""),
        "transmission_sig": headers.get("PAYPAL-TRANSMISSION-SIG", ""),
        "transmission_time": headers.get("PAYPAL-TRANSMISSION-TIME", ""),
        "webhook_id": webhook_id,
        "webhook_event": json.loads(body) if isinstance(body, (str, bytes)) else body,
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
