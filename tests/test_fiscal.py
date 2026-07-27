# SPDX-FileCopyrightText: William Moreno Reyes <williamjmorenor@gmail.com>
# SPDX-License-Identifier: Apache-2.0

import json
from datetime import UTC, datetime

from app.extensions import db
from app.fiscal import (
    acceptance_snapshot,
    active_treatments,
    applies,
    approved_optional_treatments,
    base_tax_percent,
    contract_snapshot,
    country_code,
    gate,
    has_accepted_current_mandatory_terms,
    pending_requests,
)
from app.models import Customer, CustomerFiscalRequest, FiscalTreatmentType


def test_country_code():
    assert country_code("ni") == "NI"
    assert country_code("  us  ") == "US"
    assert country_code(None) == ""


def test_base_tax_percent(app):
    with app.test_request_context():
        # NI defaults to 15 in default settings
        from app.branding import set_tax_rates

        set_tax_rates({"NI": 15, "US": 5})
        assert base_tax_percent("ni") == 15
        assert base_tax_percent("us") == 5
        assert base_tax_percent("invalid") == 0


def test_active_treatments(app):
    with app.test_request_context():
        # Clear existing treatment types
        db.session.query(FiscalTreatmentType).delete()
        db.session.commit()

        # Add some treatment types
        t1 = FiscalTreatmentType(
            country_code="NI",
            name="IR Ret",
            direction="-",
            percent="10.0",
            is_optional=False,
            is_active=True,
        )
        t2 = FiscalTreatmentType(
            country_code="NI",
            name="VAT Exemption",
            direction="-",
            percent="15.0",
            is_optional=True,
            is_active=True,
        )
        t3 = FiscalTreatmentType(
            country_code="NI",
            name="Disabled Treatment",
            direction="+",
            percent="2.0",
            is_optional=False,
            is_active=False,
        )
        db.session.add_all([t1, t2, t3])
        db.session.commit()

        # Empty country code
        assert active_treatments("") == []

        # All active
        ni_active = active_treatments("NI")
        assert len(ni_active) == 2
        assert ni_active[0].name == "IR Ret"
        assert ni_active[1].name == "VAT Exemption"

        # Mandatory only
        ni_mandatory = active_treatments("NI", is_optional=False)
        assert len(ni_mandatory) == 1
        assert ni_mandatory[0].name == "IR Ret"

        # Optional only
        ni_optional = active_treatments("NI", is_optional=True)
        assert len(ni_optional) == 1
        assert ni_optional[0].name == "VAT Exemption"


def test_pending_requests(app):
    with app.test_request_context():
        db.session.query(CustomerFiscalRequest).delete()
        db.session.query(FiscalTreatmentType).delete()
        db.session.commit()

        treatment = FiscalTreatmentType(
            country_code="NI",
            name="VAT Exemption",
            direction="-",
            percent="15.0",
            is_optional=True,
            is_active=True,
        )
        db.session.add(treatment)
        db.session.commit()

        # Empty country code
        assert pending_requests("user@example.com", "") == []

        # No pending requests yet
        assert pending_requests("user@example.com", "NI") == []

        # Add a pending request
        req = CustomerFiscalRequest(
            customer_email="user@example.com",
            treatment_type_id=treatment.id,
            status="pending",
            evidence_path="doc.pdf",
        )
        db.session.add(req)
        db.session.commit()

        pending = pending_requests("user@example.com", "NI")
        assert len(pending) == 1
        assert pending[0].evidence_path == "doc.pdf"


def test_approved_optional_treatments(app):
    with app.test_request_context():
        db.session.query(CustomerFiscalRequest).delete()
        db.session.query(FiscalTreatmentType).delete()
        db.session.commit()

        treatment = FiscalTreatmentType(
            country_code="NI",
            name="VAT Exemption",
            direction="-",
            percent="15.0",
            is_optional=True,
            is_active=True,
        )
        db.session.add(treatment)
        db.session.commit()

        # Empty country code
        assert approved_optional_treatments("user@example.com", "") == []

        # No approved optional treatments yet
        assert approved_optional_treatments("user@example.com", "NI") == []

        # Add an approved request
        req = CustomerFiscalRequest(
            customer_email="user@example.com",
            treatment_type_id=treatment.id,
            status="approved",
            evidence_path="doc.pdf",
        )
        db.session.add(req)
        db.session.commit()

        approved = approved_optional_treatments("user@example.com", "NI")
        assert len(approved) == 1
        assert approved[0].name == "VAT Exemption"


def test_applies_and_acceptance_snapshot(app):
    with app.test_request_context():
        # Setup tax rate
        from app.branding import set_tax_rates

        set_tax_rates({"NI": 15, "US": 0})

        db.session.query(FiscalTreatmentType).delete()
        db.session.commit()

        # Applies because of base tax percent > 0
        assert applies("NI") is True
        # US base tax is 0, no active treatment types, so applies is False
        assert applies("US") is False

        # Add active treatment for US
        t_us = FiscalTreatmentType(
            country_code="US",
            name="US Spec",
            direction="+",
            percent="5.0",
            is_optional=False,
            is_active=True,
        )
        db.session.add(t_us)
        db.session.commit()

        assert applies("US") is True

        # Snapshot of NI
        snap_ni = json.loads(acceptance_snapshot("NI"))
        assert snap_ni["country_code"] == "NI"
        assert snap_ni["base_tax_percent"] == 15
        assert snap_ni["mandatory_treatments"] == []


def test_has_accepted_current_mandatory_terms(app):
    with app.test_request_context():
        # Setup tax rate
        from app.branding import set_tax_rates

        set_tax_rates({"NI": 15})

        db.session.query(FiscalTreatmentType).delete()
        db.session.commit()

        cust = Customer(
            email="test_cust@example.com",
            country="NI",
            fiscal_acceptance_country_code=None,
            fiscal_acceptance_snapshot_json=None,
            fiscal_accepted_at=None,
        )

        # Base tax exists, but snapshot is missing
        assert has_accepted_current_mandatory_terms(cust) is False

        # If country is empty or applies(country) is False
        cust_empty = Customer(email="empty@example.com", country="")
        assert has_accepted_current_mandatory_terms(cust_empty) is True

        # Set correct snapshot values
        expected_snap = acceptance_snapshot("NI")
        cust.fiscal_acceptance_country_code = "NI"
        cust.fiscal_acceptance_snapshot_json = expected_snap
        cust.fiscal_accepted_at = datetime.now(UTC)

        assert has_accepted_current_mandatory_terms(cust) is True


def test_contract_snapshot_and_gate(app):
    with app.test_request_context():
        from app.branding import set_tax_rates

        set_tax_rates({"NI": 15})

        db.session.query(CustomerFiscalRequest).delete()
        db.session.query(FiscalTreatmentType).delete()
        db.session.commit()

        mandatory = FiscalTreatmentType(
            country_code="NI",
            name="IR Ret",
            direction="-",
            percent="10.0",
            is_optional=False,
            is_active=True,
        )
        optional = FiscalTreatmentType(
            country_code="NI",
            name="VAT Exemption",
            direction="-",
            percent="15.0",
            is_optional=True,
            is_active=True,
        )
        db.session.add_all([mandatory, optional])
        db.session.commit()

        cust = Customer(
            email="test_cust_2@example.com",
            country="NI",
            fiscal_acceptance_country_code="NI",
            fiscal_acceptance_snapshot_json=acceptance_snapshot("NI"),
            fiscal_accepted_at=datetime.now(UTC),
        )

        # 10000 cents subtotal (100 USD)
        # tax (15%) = 1500 cents
        # mandatory treatment (IR Ret -10%) = -1000 cents
        # total_cents = 10000 + 1500 - 1000 = 10500 cents
        snap = contract_snapshot(cust, 10000)
        assert snap["country_code"] == "NI"
        assert snap["tax_percent"] == 15
        assert snap["tax_cents"] == 1500
        assert snap["fiscal_adjustment_cents"] == -1000
        assert snap["total_cents"] == 10500

        # Now test gate function
        g = gate(cust)
        assert g["country_code"] == "NI"
        assert g["configured"] is True
        assert g["mandatory_accepted"] is True
        assert len(g["mandatory_types"]) == 1
        assert len(g["available_optional_types"]) == 1
        assert g["requires_review"] is False
