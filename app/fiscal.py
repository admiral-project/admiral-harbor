import json

from app.branding import get_tax_rates
from app.extensions import db
from app.models import CustomerFiscalRequest, FiscalTreatmentType


def country_code(country):
    return (country or "").strip().upper()


def base_tax_percent(country):
    return get_tax_rates().get(country_code(country), 0)


def active_treatments(country, *, is_optional=None):
    code = country_code(country)
    if not code:
        return []
    query = db.session.query(FiscalTreatmentType).filter_by(
        country_code=code,
        is_active=True,
    )
    if is_optional is not None:
        query = query.filter_by(is_optional=is_optional)
    return query.order_by(FiscalTreatmentType.id.asc()).all()


def pending_requests(customer_email, country):
    code = country_code(country)
    if not code:
        return []
    return (
        db.session.query(CustomerFiscalRequest)
        .join(FiscalTreatmentType)
        .filter(
            CustomerFiscalRequest.customer_email == customer_email,
            CustomerFiscalRequest.status == "pending",
            FiscalTreatmentType.country_code == code,
            FiscalTreatmentType.is_active.is_(True),
        )
        .order_by(CustomerFiscalRequest.created_at.desc())
        .all()
    )


def approved_optional_treatments(customer_email, country):
    code = country_code(country)
    if not code:
        return []
    return (
        db.session.query(FiscalTreatmentType)
        .join(CustomerFiscalRequest)
        .filter(
            CustomerFiscalRequest.customer_email == customer_email,
            CustomerFiscalRequest.status == "approved",
            FiscalTreatmentType.country_code == code,
            FiscalTreatmentType.is_optional.is_(True),
            FiscalTreatmentType.is_active.is_(True),
        )
        .order_by(FiscalTreatmentType.id.asc())
        .all()
    )


def applies(country):
    return base_tax_percent(country) > 0 or bool(active_treatments(country))


def acceptance_snapshot(country):
    code = country_code(country)
    mandatory = active_treatments(code, is_optional=False)
    payload = {
        "country_code": code,
        "base_tax_percent": int(base_tax_percent(code)),
        "mandatory_treatments": [
            {
                "id": treatment.id,
                "name": treatment.name,
                "direction": treatment.direction,
                "percent": float(treatment.percent),
            }
            for treatment in mandatory
        ],
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def has_accepted_current_mandatory_terms(customer):
    code = country_code(getattr(customer, "country", ""))
    if not code or not applies(code):
        return True
    if base_tax_percent(code) <= 0 and not active_treatments(code, is_optional=False):
        return True
    expected = acceptance_snapshot(code)
    return (
        getattr(customer, "fiscal_acceptance_country_code", None) == code
        and getattr(customer, "fiscal_acceptance_snapshot_json", None) == expected
        and getattr(customer, "fiscal_accepted_at", None) is not None
    )


def contract_snapshot(customer, subtotal_cents):
    code = country_code(getattr(customer, "country", ""))
    tax_percent = int(base_tax_percent(code))
    tax_cents = int(subtotal_cents * tax_percent / 100)
    line_items = []
    if tax_cents:
        line_items.append(
            {
                "kind": "base_tax",
                "name": "Base tax",
                "direction": "+",
                "percent": tax_percent,
                "amount_cents": tax_cents,
                "is_optional": False,
            }
        )
    mandatory = active_treatments(code, is_optional=False)
    approved_optional = approved_optional_treatments(customer.email, code)
    applied_treatments = [*mandatory, *approved_optional]
    fiscal_adjustment_cents = 0
    for treatment in applied_treatments:
        amount_cents = int(subtotal_cents * float(treatment.percent) / 100)
        signed_amount = amount_cents if treatment.direction == "+" else -amount_cents
        fiscal_adjustment_cents += signed_amount
        line_items.append(
            {
                "kind": "treatment",
                "treatment_type_id": treatment.id,
                "name": treatment.name,
                "direction": treatment.direction,
                "percent": float(treatment.percent),
                "amount_cents": signed_amount,
                "is_optional": bool(treatment.is_optional),
            }
        )
    total_cents = subtotal_cents + tax_cents + fiscal_adjustment_cents
    payload = {
        "country_code": code,
        "subtotal_cents": subtotal_cents,
        "tax_percent": tax_percent,
        "tax_cents": tax_cents,
        "fiscal_adjustment_cents": fiscal_adjustment_cents,
        "total_cents": total_cents,
        "line_items": line_items,
    }
    return {
        "country_code": code,
        "tax_percent": tax_percent,
        "tax_cents": tax_cents,
        "fiscal_adjustment_cents": fiscal_adjustment_cents,
        "total_cents": total_cents,
        "snapshot_json": json.dumps(payload, sort_keys=True, separators=(",", ":")),
    }


def gate(customer):
    code = country_code(getattr(customer, "country", ""))
    configured = applies(code)
    pending = pending_requests(customer.email, code) if configured else []
    optional_types = active_treatments(code, is_optional=True) if configured else []
    requested_type_ids = {
        request_.treatment_type_id
        for request_ in db.session.query(CustomerFiscalRequest)
        .filter(
            CustomerFiscalRequest.customer_email == customer.email,
            CustomerFiscalRequest.status.in_(("pending", "approved")),
        )
        .all()
    }
    available_optional = [treatment for treatment in optional_types if treatment.id not in requested_type_ids]
    mandatory_types = active_treatments(code, is_optional=False) if configured else []
    mandatory_accepted = has_accepted_current_mandatory_terms(customer)
    requires_review = configured and (not mandatory_accepted or bool(pending))
    return {
        "country_code": code,
        "configured": configured,
        "mandatory_accepted": mandatory_accepted,
        "mandatory_types": mandatory_types,
        "pending_requests": pending,
        "available_optional_types": available_optional,
        "requires_review": requires_review,
    }
