# SPDX-FileCopyrightText: William Moreno Reyes <williamjmorenor@gmail.com>
# SPDX-License-Identifier: Apache-2.0

import json
import logging

import requests
import yaml
from flask import current_app

logger = logging.getLogger("admiral-harbor")


class AdmiralAPIError(RuntimeError):
    pass


def _verify():
    ca_file = current_app.config.get("ADMIRAL_CA_FILE", "")
    if ca_file:
        return ca_file
    if current_app.config.get("ADMIRAL_INSECURE_SKIP_VERIFY"):
        return False
    return True  # system CA bundle by default


def _headers(customer_id=None):
    token = current_app.config.get("ADMIRAL_HARBOR_API_TOKEN") or current_app.config["ADMIRAL_ADMIN_TOKEN"]
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
        "X-Admiral-Operator": "admiral-harbor",
    }
    if customer_id:
        headers["X-Admiral-Customer-ID"] = customer_id
    return headers


def _request(method, path, payload=None, params=None, timeout=60, customer_id=None):
    url = current_app.config["ADMIRAL_API_URL"] + path
    try:
        response = requests.request(
            method,
            url,
            headers=_headers(customer_id),
            json=payload,
            params=params,
            timeout=timeout,
            verify=_verify(),
        )
    except requests.RequestException as exc:
        logger.error(
            "admirald request failed",
            extra={"path": path, "error": str(exc), "method": method},
        )
        raise AdmiralAPIError(str(exc)) from exc

    if response.ok:
        if not response.content:
            return None
        return response.json()

    message = response.text
    try:
        message = response.json().get("error", message)
    except ValueError:
        pass
    raise AdmiralAPIError(message)


def list_apps():
    return _request("GET", "/api/v1/apps", timeout=30)


def get_app(slug):
    app = _request("GET", f"/api/v1/apps/{slug}", timeout=30)
    app["tiers"] = normalize_tiers(parse_tiers_from_yaml(app.get("raw_yaml", "")))
    app["requires_billing"] = any(not t.get("free") and t["price_monthly_cents"] > 0 for t in app["tiers"])
    return app


def normalize_tiers(raw_tiers):
    tiers = []
    for name, tier in raw_tiers.items():
        price_monthly = tier.get("price_monthly", 0) or 0
        is_free = bool(tier.get("free", False))
        tiers.append(
            {
                "name": name,
                "cpu": tier.get("cpu"),
                "memory": tier.get("memory"),
                "storage": tier.get("storage"),
                "price_monthly": price_monthly,
                "price_monthly_cents": int(float(price_monthly) * 100),
                "free": is_free,
                "backups": tier.get("backups") or {},
            }
        )
    tiers.sort(key=lambda item: (item["price_monthly_cents"], item["name"]))
    return tiers


def parse_tiers_from_yaml(raw_yaml):
    try:
        document = yaml.safe_load(raw_yaml) or {}
    except yaml.YAMLError:
        return {}
    if not isinstance(document, dict) or not isinstance(document.get("tiers"), dict):
        return {}
    return {str(name): values for name, values in document["tiers"].items() if isinstance(values, dict)}


def list_customer_apps(customer_id):
    result = _request("GET", "/api/v1/customer-apps", params={"customer_id": customer_id}, timeout=30)
    if result is None:
        return []
    return result


def get_customer_app(instance_id, customer_id=None):
    return _request("GET", f"/api/v1/customer-apps/{instance_id}", timeout=30, customer_id=customer_id)


def get_instance_inspect(instance_id):
    return _request("GET", f"/api/admin/instances/{instance_id}/inspect", timeout=30)


def get_instance_credentials(instance_id, customer_id=None):
    result = _request("GET", f"/api/v1/customer-apps/{instance_id}/credentials", timeout=30, customer_id=customer_id)
    if result is None:
        return []
    return result


def provision_app(app_slug, tier_name, customer_id):
    return _request(
        "POST",
        "/api/v1/customer-apps",
        payload={
            "app_definition_name": app_slug,
            "tier_name": tier_name,
            "customer_id": customer_id,
        },
        customer_id=customer_id,
    )


def action(instance_id, action_name, tier=None, service=None, customer_id=None):
    payload = {"instance_id": instance_id, "action": action_name}
    if tier:
        payload["tier"] = tier
    if service:
        payload["service"] = service
    return _request("POST", "/api/v1/customer-apps/action", payload=payload, customer_id=customer_id)


def list_backups(instance_id):
    response = _request("GET", "/api/v1/backups", params={"instance_id": instance_id}, timeout=30)
    if isinstance(response, dict) and "items" in response:
        return response["items"] or []
    return response or []


def get_backup(backup_id):
    return _request("GET", f"/api/v1/backups/{backup_id}", timeout=30)


def get_operation(operation_id):
    return _request("GET", "/api/v1/operations", params={"id": operation_id}, timeout=30)


def restore_backup(backup_id, instance_id, service, source=None, verify_checksum=True):
    return _request(
        "POST",
        "/api/v1/backups/restore",
        payload={
            "backup_id": backup_id,
            "target_app_id": instance_id,
            "service": service,
            "source": source or {},
            "verify_checksum": verify_checksum,
            "restore_mode": "replace",
        },
    )


def get_catalog():
    return _request("GET", "/api/v1/apps/catalog", timeout=30)


def validate_provisioning(app_slug, tier_id, expected_revision=0, expected_checksum=""):
    return _request(
        "POST",
        f"/api/v1/apps/{app_slug}/validate-provisioning",
        payload={
            "tier_id": tier_id,
            "expected_revision": expected_revision,
            "expected_checksum": expected_checksum,
        },
        timeout=30,
    )


def update_availability(app_slug, availability, reason=""):
    return _request(
        "PATCH",
        f"/api/v1/apps/{app_slug}/availability",
        payload={
            "availability": availability,
            "reason": reason,
        },
        timeout=30,
    )


def dump_json(data):
    return json.dumps(data, sort_keys=True, separators=(",", ":"))
