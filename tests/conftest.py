# SPDX-FileCopyrightText: William Moreno Reyes <williamjmorenor@gmail.com>
# SPDX-License-Identifier: Apache-2.0

import tempfile

import pytest

from argon2 import PasswordHasher

from app import create_app
from app import admiral_client
from app.extensions import db
from app.models import CatalogApp, Customer, CustomerApp, HarborAdminUser, Subscription


@pytest.fixture
def app():
    app = create_app()
    app.config.update(
        TESTING=True,
        SECRET_KEY="test-secret",
        SQLALCHEMY_DATABASE_URI="sqlite://",
        ADMIRAL_API_URL="https://admirald.test:8443",
        ADMIRAL_ADMIN_TOKEN="test-token",
        ADMIRAL_CA_FILE="",
        HARBOR_UPLOAD_DIR=tempfile.mkdtemp(prefix="admiral-harbor-tests-"),
        HARBOR_BOOTSTRAP_ADMIN_USER="testadmin",
        HARBOR_BOOTSTRAP_ADMIN_PASSWORD="secret",
    )
    return app


@pytest.fixture
def client(app):
    with app.app_context():
        db.drop_all()
        db.create_all()
        if db.session.query(CatalogApp).count() == 0:
            db.session.add_all(
                [
                    CatalogApp(
                        upstream_app_id="wordpress",
                        name="WordPress",
                        one_liner="Managed publishing for teams that want to ship content fast.",
                        description_md="Managed **WordPress** hosting with [backups](https://example.com) and updates.",
                        catalog_enabled=True,
                        sort_order=1,
                        sync_status="synced",
                        upstream_present=True,
                        upstream_availability="available",
                        upstream_revision=1,
                    ),
                    CatalogApp(
                        upstream_app_id="gitea",
                        name="Gitea",
                        one_liner="Private code hosting with managed operations.",
                        description_md="Git hosting for internal teams and agencies.",
                        catalog_enabled=True,
                        sort_order=2,
                        sync_status="synced",
                        upstream_present=True,
                        upstream_availability="available",
                        upstream_revision=1,
                    ),
                ]
            )
            db.session.commit()
        if db.session.query(Customer).count() == 0:
            db.session.add(
                Customer(
                    email="user@example.com",
                    public_id="hcus_testuser",
                    display_name="Acme Studios",
                    password_hash=PasswordHasher().hash("secret"),
                    signup_status="active",
                    terms_policy_version="overdue-policy-v1",
                )
            )
            db.session.commit()
        HarborAdminUser.ensure_default_admin(username="testadmin", password="secret")
        if db.session.query(Subscription).count() == 0:
            subscription = Subscription(
                customer_email="user@example.com",
                app_slug="wordpress",
                status="active",
                monthly_price_cents=2500,
                tier_name="starter",
                instance_id="inst_123",
                paypal_subscription_id="paypal_sub_1",
            )
            db.session.add(subscription)
            db.session.commit()
            db.session.add(
                CustomerApp(
                    subscription_id=subscription.id,
                    customer_email="user@example.com",
                    instance_id="inst_123",
                    app_slug="wordpress",
                    domain="wordpress.example.com",
                    status="running",
                    backup_status="ok",
                    storage_status="ok",
                    tier_name="starter",
                    next_billing_at="2026-07-15",
                )
            )
            db.session.commit()
    admiral_client.list_customer_apps = lambda customer_id: [
        {
            "id": "inst_123",
            "customer_id": customer_id,
            "app_definition_name": "wordpress",
            "tier_name": "starter",
            "technical_status": "running",
            "storage_state": "ok",
        }
    ]
    admiral_client.get_app = lambda slug: {
        "name": slug,
        "raw_yaml": "tiers:\n  starter:\n    cpu: 1\n    memory: 1G\n    storage: 10G\n    price_monthly: 25\n",
        "tiers": [
            {
                "name": "starter",
                "cpu": 1,
                "memory": "1G",
                "storage": "10G",
                "price_monthly": 25,
                "price_monthly_cents": 2500,
                "backups": {},
            }
        ],
        "requires_billing": True,
    }
    admiral_client.list_backups = lambda instance_id: [
        {
            "id": "bk_123",
            "status": "succeeded",
            "backup_type": "database",
            "created_at": "2026-06-04T00:00:00Z",
        }
    ]
    admiral_client.get_customer_app = lambda instance_id: {
        "id": instance_id,
        "technical_status": "running",
        "storage_state": "ok",
    }
    admiral_client.action = lambda instance_id, action_name, tier=None, service=None: {
        "operation_id": f"op_{action_name}",
        "status": "queued",
    }
    admiral_client.restore_backup = (
        lambda backup_id, instance_id, service, source=None, verify_checksum=True: {
            "operation_id": "op_restore",
            "status": "queued",
        }
    )
    admiral_client.provision_app = lambda app_slug, tier_name, customer_id: {
        "operation_id": "op_provision",
        "instance_id": "inst_provision",
        "status": "queued",
    }
    admiral_client.get_instance_inspect = lambda instance_id: {
        "containers": [
            {"name": "app", "image": "wordpress:latest", "state": "running"},
            {"name": "db", "image": "mariadb:10", "state": "running"},
        ],
        "volumes": [
            {
                "name": "wp-data",
                "mountpoint": "/var/lib/containers/storage/volumes/wp-data",
            }
        ],
        "inspected_at": "2026-06-17T00:00:00Z",
    }
    return app.test_client()
