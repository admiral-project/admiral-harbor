# SPDX-FileCopyrightText: William Moreno Reyes <williamjmorenor@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""
admiral-harbor dev mode — runs mock admirald API + real harbor app.

Starts two servers on different ports:
  - Mock admirald API  → http://127.0.0.1:9090
  - Real harbor app    → http://127.0.0.1:5001

Harbor's admiral_client talks to the mock API, so no real backend setup needed.

Usage:
    python dev_run.py
"""

import os
import sys
import time
import secrets
import logging
import threading
from datetime import UTC, datetime, timedelta

from flask import Flask, redirect, request, jsonify, abort

# ── Configuration ──────────────────────────────────────────────────────────

HOST = os.environ.get("ADMIRAL_MOCK_HOST", "127.0.0.1")
MOCK_PORT = int(os.environ.get("ADMIRAL_MOCK_PORT", "9090"))
HARBOR_HOST = os.environ.get("HARBOR_HTTP_ADDR", "127.0.0.1")
HARBOR_PORT = int(os.environ.get("HARBOR_HTTP_PORT", "5001"))
DEBUG = os.environ.get("DEV_RUN_DEBUG", "0") == "1"
SHARED_TOKEN = os.environ.get("ADMIRAL_ADMIN_TOKEN", "dev-token")

logging.basicConfig(
    level=logging.DEBUG if DEBUG else logging.INFO,
    format="[admirald-mock] %(levelname)s %(message)s",
)
log = logging.getLogger("admirald-mock")

# ── Mock App Factory ───────────────────────────────────────────────────────

mock_app = Flask("admirald-mock")

# ── Mock Data ──────────────────────────────────────────────────────────────

NOW = datetime.now(UTC)

CATALOG_APPS = [
    {
        "name": "whoami",
        "display_name": "Whoami",
        "description": "Lightweight HTTP echo server for testing and health checks",
        "status": "active",
        "created_at": "2026-01-01T00:00:00Z",
    },
    {
        "name": "ghost",
        "display_name": "Ghost",
        "description": "Professional publishing platform for content creators",
        "status": "active",
        "created_at": "2026-01-01T00:00:00Z",
    },
    {
        "name": "twenty-crm",
        "display_name": "Twenty CRM",
        "description": "Open source customer relationship management",
        "status": "active",
        "created_at": "2026-01-15T00:00:00Z",
    },
    {
        "name": "n8n",
        "display_name": "n8n",
        "description": "Workflow automation with visual builder",
        "status": "active",
        "created_at": "2026-02-01T00:00:00Z",
    },
]

APP_YAMLS = {
    "whoami": """name: whoami
description: Lightweight HTTP echo server for testing and health checks
version: "1.0"
services:
  web:
    image: docker.io/admiral/whoami:1.0
    ports:
      - 8080
    health:
      path: /health
tiers:
  starter:
    cpu: 0.5
    memory: 512M
    storage: 5G
    price_monthly: 0
    backups:
      database:
        schedule: "@daily"
        retention_days: 7
  business:
    cpu: 2
    memory: 4G
    storage: 20G
    price_monthly: 29.99
    backups:
      database:
        schedule: "@hourly"
        retention_days: 30
      volumes:
        schedule: "@daily"
        retention_days: 14
""",
    "ghost": """name: ghost
description: Professional publishing platform for content creators
version: "5.0"
services:
  web:
    image: docker.io/admiral/ghost:5.0
    ports:
      - 2368
    health:
      path: /ghost/
  db:
    image: docker.io/library/mariadb:11
tiers:
  starter:
    cpu: 0.5
    memory: 1G
    storage: 10G
    price_monthly: 9.99
    backups:
      database:
        schedule: "@daily"
        retention_days: 7
  business:
    cpu: 2
    memory: 4G
    storage: 50G
    price_monthly: 49.99
    backups:
      database:
        schedule: "@hourly"
        retention_days: 30
      volumes:
        schedule: "@daily"
        retention_days: 14
  enterprise:
    cpu: 4
    memory: 8G
    storage: 100G
    price_monthly: 149.99
    backups:
      database:
        schedule: "@hourly"
        retention_days: 90
      volumes:
        schedule: "@hourly"
        retention_days: 90
""",
    "twenty-crm": """name: twenty-crm
description: Open source customer relationship management
version: "0.30"
services:
  web:
    image: docker.io/admiral/twenty-crm:0.30
    ports:
      - 3000
    health:
      path: /health
  db:
    image: docker.io/library/postgres:16
tiers:
  starter:
    cpu: 1
    memory: 2G
    storage: 10G
    price_monthly: 19.99
    backups:
      database:
        schedule: "@daily"
        retention_days: 7
  business:
    cpu: 2
    memory: 4G
    storage: 50G
    price_monthly: 79.99
    backups:
      database:
        schedule: "@hourly"
        retention_days: 30
      volumes:
        schedule: "@daily"
        retention_days: 14
  enterprise:
    cpu: 4
    memory: 8G
    storage: 200G
    price_monthly: 199.99
    backups:
      database:
        schedule: "@hourly"
        retention_days: 90
      volumes:
        schedule: "@hourly"
        retention_days: 90
""",
    "n8n": """name: n8n
description: Workflow automation with visual builder
version: "1.0"
services:
  web:
    image: docker.io/admiral/n8n:1.0
    ports:
      - 5678
    health:
      path: /health
  db:
    image: docker.io/library/postgres:16
tiers:
  starter:
    cpu: 0.5
    memory: 1G
    storage: 5G
    price_monthly: 0
    backups:
      database:
        schedule: "@daily"
        retention_days: 7
  business:
    cpu: 2
    memory: 4G
    storage: 20G
    price_monthly: 39.99
    backups:
      database:
        schedule: "@hourly"
        retention_days: 30
      volumes:
        schedule: "@daily"
        retention_days: 14
""",
}

CUSTOMER_APPS = [
    {
        "id": "inst_a1b2c3d4",
        "customer_id": "hcus_a1b2c3d4e5f6",
        "app_definition_name": "whoami",
        "tier_name": "starter",
        "commercial_status": "active",
        "technical_status": "running",
        "storage_state": "ok",
        "created_at": (NOW - timedelta(days=45)).isoformat() + "Z",
    },
    {
        "id": "inst_e5f6g7h8",
        "customer_id": "hcus_a1b2c3d4e5f6",
        "app_definition_name": "ghost",
        "tier_name": "business",
        "commercial_status": "active",
        "technical_status": "running",
        "storage_state": "ok",
        "created_at": (NOW - timedelta(days=30)).isoformat() + "Z",
    },
    {
        "id": "inst_i9j0k1l2",
        "customer_id": "hcus_b2c3d4e5f6a1",
        "app_definition_name": "twenty-crm",
        "tier_name": "enterprise",
        "commercial_status": "active",
        "technical_status": "paused",
        "storage_state": "ok",
        "created_at": (NOW - timedelta(days=20)).isoformat() + "Z",
    },
    {
        "id": "inst_m3n4o5p6",
        "customer_id": "hcus_c3d4e5f6a1b2",
        "app_definition_name": "n8n",
        "tier_name": "business",
        "commercial_status": "active",
        "technical_status": "running",
        "storage_state": "ok",
        "created_at": (NOW - timedelta(days=10)).isoformat() + "Z",
    },
    {
        "id": "inst_q7r8s9t0",
        "customer_id": "hcus_d4e5f6a1b2c3",
        "app_definition_name": "whoami",
        "tier_name": "starter",
        "commercial_status": "suspended",
        "technical_status": "error",
        "storage_state": "critical",
        "created_at": (NOW - timedelta(days=60)).isoformat() + "Z",
    },
]

BACKUPS = [
    {
        "id": "bkp_a1b2c3d4",
        "instance_id": "inst_a1b2c3d4",
        "backup_type": "database",
        "status": "succeeded",
        "size_bytes": 47185920,
        "checksum_sha256": "a1b2c3d4e5f67890abcdef1234567890abcdef1234567890abcdef1234567890",
        "created_at": (NOW - timedelta(hours=4)).isoformat() + "Z",
        "completed_at": (NOW - timedelta(hours=3, minutes=55)).isoformat() + "Z",
    },
    {
        "id": "bkp_a2b2c3d4",
        "instance_id": "inst_a1b2c3d4",
        "backup_type": "database",
        "status": "succeeded",
        "size_bytes": 46137344,
        "created_at": (NOW - timedelta(days=1)).isoformat() + "Z",
        "completed_at": (NOW - timedelta(days=1, hours=0, minutes=-5)).isoformat() + "Z",
    },
    {
        "id": "bkp_e5f6g7h8",
        "instance_id": "inst_e5f6g7h8",
        "backup_type": "database",
        "status": "succeeded",
        "size_bytes": 241172480,
        "created_at": (NOW - timedelta(hours=2)).isoformat() + "Z",
        "completed_at": (NOW - timedelta(hours=1, minutes=50)).isoformat() + "Z",
    },
    {
        "id": "bkp_i9j0k1l2",
        "instance_id": "inst_i9j0k1l2",
        "backup_type": "database",
        "status": "succeeded",
        "size_bytes": 933232640,
        "created_at": (NOW - timedelta(days=14)).isoformat() + "Z",
        "completed_at": (NOW - timedelta(days=14, hours=0, minutes=-3)).isoformat() + "Z",
    },
    {
        "id": "bkp_u1v2w3x4",
        "instance_id": "inst_m3n4o5p6",
        "backup_type": "database",
        "status": "failed",
        "size_bytes": 0,
        "created_at": (NOW - timedelta(hours=1)).isoformat() + "Z",
        "completed_at": None,
    },
    {
        "id": "bkp_q7r8s9t0",
        "instance_id": "inst_q7r8s9t0",
        "backup_type": "database",
        "status": "running",
        "size_bytes": 0,
        "created_at": NOW.isoformat() + "Z",
        "completed_at": None,
    },
]

OPERATIONS = [
    {
        "id": "inst_a1b2c3d4e5f6",
        "action": "provision_app",
        "status": "succeeded",
        "instance_id": "inst_m3n4o5p6",
        "created_at": (NOW - timedelta(days=10)).isoformat() + "Z",
        "updated_at": (NOW - timedelta(days=10, minutes=-5)).isoformat() + "Z",
        "error_message": "",
    },
    {
        "id": "inst_e5f6g7h8i9j0",
        "action": "backup_database",
        "status": "succeeded",
        "instance_id": "inst_e5f6g7h8",
        "created_at": (NOW - timedelta(hours=2)).isoformat() + "Z",
        "updated_at": (NOW - timedelta(hours=2, minutes=-2)).isoformat() + "Z",
        "error_message": "",
    },
    {
        "id": "inst_i9j0k1l2m3n4",
        "action": "pause_app",
        "status": "succeeded",
        "instance_id": "inst_i9j0k1l2",
        "created_at": (NOW - timedelta(days=3)).isoformat() + "Z",
        "updated_at": (NOW - timedelta(days=3, minutes=-1)).isoformat() + "Z",
        "error_message": "",
    },
    {
        "id": "inst_q7r8s9t0u1v2",
        "action": "backup_database",
        "status": "running",
        "instance_id": "inst_q7r8s9t0",
        "created_at": NOW.isoformat() + "Z",
        "updated_at": NOW.isoformat() + "Z",
        "error_message": "",
    },
]

# In-memory mutable stores (copies of above for mutation during dev)
_apps_store = [
    {
        **app,
        "raw_yaml": APP_YAMLS.get(app["name"], ""),
        "availability": app.get("availability", "available"),
        "revision": app.get("revision", 1),
        "checksum": app.get("checksum", f"dev-{app['name']}-checksum"),
    }
    for app in CATALOG_APPS
]
_instances_store = list(CUSTOMER_APPS)
_backups_store = list(BACKUPS)
_operations_store = list(OPERATIONS)
_instance_counter = [0]


def _next_instance_id():
    _instance_counter[0] += 1
    return f"inst_{secrets.token_hex(8)}"


def _next_inst_id():
    return f"inst_{secrets.token_hex(8)}"


def _find_app(slug):
    return next((a for a in _apps_store if a["name"] == slug), None)


def _find_instance(instance_id):
    return next((i for i in _instances_store if i["id"] == instance_id), None)


# ── Request logging ────────────────────────────────────────────────────────


@mock_app.before_request
def _log_request():
    log.debug("%s %s", request.method, request.path)


# ── Auth helper ────────────────────────────────────────────────────────────


def _require_shared_token():
    token = request.headers.get("X-Admiral-Token", "")
    if token != SHARED_TOKEN:
        abort(401, description="invalid token")


# ── API v1 endpoints ───────────────────────────────────────────────────────


@mock_app.route("/health")
@mock_app.route("/api/v1/health")
def health():
    return jsonify({"status": "healthy"})


@mock_app.route("/api/v1/status")
def v1_status():
    _require_shared_token()
    return jsonify({"status": "healthy", "database": "connected"})


@mock_app.route("/api/v1/apps")
def v1_apps_list():
    _require_shared_token()
    return jsonify(_apps_store)


@mock_app.route("/api/v1/apps/<slug>/validate-provisioning", methods=["POST"])
def v1_app_validate_provisioning(slug):
    _require_shared_token()
    app = _find_app(slug)
    data = request.get_json(silent=True) or {}
    tier_id = data.get("tier_id", "")
    if not app:
        return jsonify({"valid": False, "reason": "app_not_found"})
    if app.get("availability", "available") != "available":
        return (
            jsonify(
                {
                    "valid": False,
                    "app_id": slug,
                    "reason": "app_not_available",
                    "revision": app.get("revision", 0),
                    "checksum": app.get("checksum", ""),
                }
            ),
            200,
        )
    if tier_id not in APP_YAMLS.get(slug, ""):
        return (
            jsonify(
                {
                    "valid": False,
                    "app_id": slug,
                    "reason": "tier_not_found",
                    "revision": app.get("revision", 0),
                    "checksum": app.get("checksum", ""),
                }
            ),
            200,
        )
    expected_revision = int(data.get("expected_revision") or 0)
    if expected_revision > 0 and expected_revision != app.get("revision", 0):
        return (
            jsonify(
                {
                    "valid": False,
                    "app_id": slug,
                    "tier_id": tier_id,
                    "reason": "revision_mismatch",
                    "revision": app.get("revision", 0),
                    "checksum": app.get("checksum", ""),
                }
            ),
            200,
        )
    expected_checksum = data.get("expected_checksum", "")
    if expected_checksum and expected_checksum != app.get("checksum", ""):
        return (
            jsonify(
                {
                    "valid": False,
                    "app_id": slug,
                    "tier_id": tier_id,
                    "reason": "checksum_mismatch",
                    "revision": app.get("revision", 0),
                    "checksum": app.get("checksum", ""),
                }
            ),
            200,
        )
    return jsonify(
        {
            "valid": True,
            "app_id": slug,
            "tier_id": tier_id,
            "revision": app.get("revision", 0),
            "checksum": app.get("checksum", ""),
        }
    )


@mock_app.route("/api/v1/apps/<slug>/availability", methods=["PATCH"])
def v1_app_availability(slug):
    _require_shared_token()
    app = _find_app(slug)
    if not app:
        return jsonify({"error": "App not found"}), 404
    data = request.get_json(silent=True) or {}
    availability = str(data.get("availability", "")).strip().lower()
    if availability not in {"available", "unavailable"}:
        return jsonify({"error": "invalid availability"}), 400
    app["availability"] = availability
    app["last_availability_reason"] = data.get("reason", "")
    return jsonify({"success": True, "app_id": slug, "availability": availability})


@mock_app.route("/api/v1/apps/<slug>")
def v1_app_detail(slug):
    _require_shared_token()
    app = _find_app(slug)
    if not app:
        return jsonify({"error": "App definition not found"}), 404
    result = dict(app)
    return jsonify(result)


@mock_app.route("/api/v1/customer-apps")
def v1_customer_apps_list():
    _require_shared_token()
    customer_id = request.args.get("customer_id", "")
    result = _instances_store
    if customer_id:
        result = [i for i in _instances_store if i["customer_id"] == customer_id]
    return jsonify(result)


@mock_app.route("/api/v1/customer-apps/<instance_id>")
def v1_customer_app_detail(instance_id):
    _require_shared_token()
    inst = _find_instance(instance_id)
    if not inst:
        return jsonify({"error": "instance not found"}), 404
    return jsonify(inst)


@mock_app.route("/api/v1/customer-apps/<instance_id>/credentials")
def v1_customer_app_credentials(instance_id):
    _require_shared_token()
    if not _find_instance(instance_id):
        return jsonify({"error": "instance not found"}), 404
    return jsonify(
        [
            {"service": "web", "name": "ADMIN_URL", "value": "https://example.test"},
            {"service": "web", "name": "ADMIN_PASSWORD", "value": "dev-password"},
        ]
    )


@mock_app.route("/api/v1/customer-apps", methods=["POST"])
def v1_customer_apps_provision():
    _require_shared_token()
    data = request.get_json(silent=True) or {}
    app_slug = data.get("app_definition_name", "")
    tier_name = data.get("tier_name", "")
    customer_id = data.get("customer_id", "")

    if not app_slug or not tier_name or not customer_id:
        return (
            jsonify({"error": "app_definition_name, tier_name and customer_id are required"}),
            400,
        )

    instance_id = _next_instance_id()
    inst_id = _next_inst_id()

    new_instance = {
        "id": instance_id,
        "customer_id": customer_id,
        "app_definition_name": app_slug,
        "tier_name": tier_name,
        "commercial_status": "active",
        "technical_status": "provisioning",
        "storage_state": "ok",
        "created_at": NOW.isoformat() + "Z",
    }
    _instances_store.append(new_instance)

    new_op = {
        "id": inst_id,
        "action": "provision_app",
        "status": "running",
        "instance_id": instance_id,
        "created_at": NOW.isoformat() + "Z",
        "updated_at": NOW.isoformat() + "Z",
        "error_message": "",
    }
    _operations_store.append(new_op)

    return (
        jsonify({"operation_id": inst_id, "instance_id": instance_id, "status": "queued"}),
        202,
    )


@mock_app.route("/api/v1/customer-apps/action", methods=["POST"])
def v1_customer_app_action():
    _require_shared_token()
    data = request.get_json(silent=True) or {}
    instance_id = data.get("instance_id", "")
    action_name = data.get("action", "")

    if not instance_id or not action_name:
        return jsonify({"error": "instance_id and action are required"}), 400

    inst = next((i for i in _instances_store if i["id"] == instance_id), None)
    if not inst:
        return jsonify({"error": "instance not found"}), 404

    inst_id = _next_inst_id()
    new_op = {
        "id": inst_id,
        "action": f"{action_name}_app",
        "status": "running",
        "instance_id": instance_id,
        "created_at": NOW.isoformat() + "Z",
        "updated_at": NOW.isoformat() + "Z",
        "error_message": "",
    }
    _operations_store.append(new_op)

    return jsonify({"operation_id": inst_id, "status": "queued"}), 202


@mock_app.route("/api/v1/backups")
def v1_backups_list():
    _require_shared_token()
    instance_id = request.args.get("instance_id", "")
    result = _backups_store
    if instance_id:
        result = [b for b in _backups_store if b["instance_id"] == instance_id]
    return jsonify({"items": result, "page": 1, "page_size": len(result), "total": len(result)})


@mock_app.route("/api/v1/backups/<backup_id>")
def v1_backup_detail(backup_id):
    _require_shared_token()
    backup = next((b for b in _backups_store if b["id"] == backup_id), None)
    if not backup:
        return jsonify({"error": "backup not found"}), 404
    return jsonify(backup)


@mock_app.route("/api/v1/backups/restore", methods=["POST"])
def v1_backups_restore():
    _require_shared_token()
    data = request.get_json(silent=True) or {}
    if not data.get("backup_id") or not data.get("target_app_id"):
        return jsonify({"error": "backup_id and target_app_id are required"}), 400

    inst_id = _next_inst_id()
    new_op = {
        "id": inst_id,
        "action": "restore_backup",
        "status": "running",
        "instance_id": data["target_app_id"],
        "created_at": NOW.isoformat() + "Z",
        "updated_at": NOW.isoformat() + "Z",
        "error_message": "",
    }
    _operations_store.append(new_op)

    return jsonify({"operation_id": inst_id, "status": "queued"}), 202


@mock_app.route("/api/v1/operations")
def v1_operations_list():
    _require_shared_token()
    inst_id = request.args.get("id", "")
    if inst_id:
        op = next((o for o in _operations_store if o["id"] == inst_id), None)
        if not op:
            return jsonify({"error": "operation not found"}), 404
        return jsonify(op)
    return jsonify(_operations_store)


@mock_app.route("/api/admin/instances/<instance_id>/inspect")
def admin_instance_inspect(instance_id):
    _require_shared_token()
    inst = _find_instance(instance_id)
    if not inst:
        return jsonify({"error": "Instance not found"}), 404
    return jsonify(
        {
            "instance_id": instance_id,
            "containers": [
                {
                    "name": f"admiral-{instance_id}-web",
                    "image": f"docker.io/admiral/{inst['app_definition_name']}:latest",
                    "state": inst.get("technical_status", "unknown"),
                }
            ],
            "volumes": [
                {
                    "name": f"{instance_id}-data",
                    "mountpoint": f"/var/lib/containers/storage/volumes/{instance_id}-data",
                }
            ],
            "inspected_at": datetime.now(UTC).isoformat() + "Z",
        }
    )


# ── Mock PayPal ────────────────────────────────────────────────────────────


@mock_app.route("/mock-paypal/approve")
def mock_paypal_approve():
    subscription_id = request.args.get("subscription_id", "")
    return_url = request.args.get("return_url", "")
    if not return_url and not subscription_id:
        return jsonify({"error": "missing subscription_id or return_url"}), 400
    log.info("Mock PayPal: approving subscription %s", subscription_id)
    from urllib.parse import urlencode, urlparse, parse_qs, urlunparse

    parsed = list(urlparse(return_url))
    query = dict(parse_qs(parsed[4]))
    query["token"] = subscription_id
    parsed[4] = urlencode(query, doseq=True)
    return redirect(urlunparse(parsed))


# ── Main ───────────────────────────────────────────────────────────────────


def _print_banner(mock_port, harbor_port):
    print("*" * 60)
    print("  admiral-harbor DEV MODE")
    print("*" * 60)
    print()
    print(f"  Mock admirald API:  http://127.0.0.1:{mock_port}")
    print(f"  Harbor portal:      http://127.0.0.1:{harbor_port}")
    print()
    print("  Register a customer at /auth/register")
    print("  Admin login: admin / secret at /admin/login")
    print("  PayPal mode: mock (auto-approves at /mock-paypal/approve)")
    print()
    print("  Press Ctrl+C to stop.")
    print("*" * 60)


def _wait_for_mock(host, port, timeout=10):
    import urllib.request

    start = time.time()
    while time.time() - start < timeout:
        try:
            req = urllib.request.Request(
                f"http://{host}:{port}/api/v1/status",
                headers={"X-Admiral-Token": SHARED_TOKEN},
            )
            resp = urllib.request.urlopen(req, timeout=1)  # nosec - controlled dev mock
            if resp.status == 200:
                return True
        except Exception:
            pass
        time.sleep(0.2)
    return False


def _fleet_simulation():
    while True:
        time.sleep(5)
        now = datetime.now(UTC)
        for op in _operations_store:
            if op["status"] != "running":
                continue
            try:
                created = datetime.fromisoformat(op["created_at"].replace("Z", ""))
            except (ValueError, TypeError):
                continue
            if (now - created).total_seconds() < 5:
                continue
            op["status"] = "succeeded"
            op["updated_at"] = now.isoformat() + "Z"
            inst = next((i for i in _instances_store if i["id"] == op["instance_id"]), None)
            if inst is None:
                continue
            if op["action"] == "provision_app":
                inst["technical_status"] = "running"
            elif op["action"] == "restore_backup":
                inst["technical_status"] = "paused"
            elif op["action"] in ("pause", "pause_app"):
                inst["technical_status"] = "paused"
            elif op["action"] in ("resume", "resume_app"):
                inst["technical_status"] = "running"
            elif op["action"] in ("deprovision", "deprovision_app"):
                inst["technical_status"] = "deprovisioned"


def _worker_loop():
    from app import create_app as _create_worker_app
    from worker import (
        _generate_invoices,
        _enforce_payment_policy,
        _reconcile_paypal_subscriptions,
        _sync_remote_instances,
    )

    worker_app = _create_worker_app()
    while True:
        time.sleep(60)
        with worker_app.app_context():
            _generate_invoices(worker_app)
            _enforce_payment_policy(worker_app)
            _reconcile_paypal_subscriptions(worker_app)
            _sync_remote_instances(worker_app)


def main():
    os.environ["ADMIRAL_API_URL"] = f"http://{HOST}:{MOCK_PORT}"
    os.environ.setdefault("ADMIRAL_ADMIN_TOKEN", SHARED_TOKEN)
    os.environ.setdefault("HARBOR_SECRET_KEY", "dev-secret-key-change-in-production")
    os.environ.setdefault("HARBOR_DATABASE_URL", "sqlite:///harbor.db")
    os.environ["HARBOR_PAYPAL_MODE"] = "mock"
    os.environ["ADMIRAL_INSECURE_SKIP_VERIFY"] = "1"

    mock_daemon = threading.Thread(
        target=lambda: mock_app.run(host=HOST, port=MOCK_PORT, debug=False, use_reloader=False),
        daemon=True,
    )
    mock_daemon.start()

    fleet_sim = threading.Thread(target=_fleet_simulation, daemon=True)
    fleet_sim.start()

    worker_th = threading.Thread(target=_worker_loop, daemon=True)
    worker_th.start()

    if not _wait_for_mock(HOST, MOCK_PORT):
        print("ERROR: mock admirald API did not start in time")
        sys.exit(1)

    from app import create_app

    harbor = create_app()

    _print_banner(MOCK_PORT, HARBOR_PORT)
    harbor.run(host=HARBOR_HOST, port=HARBOR_PORT, debug=DEBUG)


if __name__ == "__main__":
    main()
