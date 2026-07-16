import pytest
from unittest.mock import patch, MagicMock
import requests
from app.admiral_client import (
    get_app,
    normalize_tiers,
    parse_tiers_from_yaml,
    AdmiralAPIError,
    _request,
    _headers,
    provision_app,
)
from flask import current_app


def test_headers(app):
    with app.app_context():
        current_app.config["ADMIRAL_ADMIN_TOKEN"] = "test-token"
        headers = _headers()
        assert headers["Authorization"] == "Bearer test-token"
        assert headers["Content-Type"] == "application/json"


def test_request_success(app):
    with patch("requests.request") as mock_req:
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.content = b'{"status": "ok"}'
        mock_resp.json.return_value = {"status": "ok"}
        mock_req.return_value = mock_resp

        with app.app_context():
            result = _request("GET", "/test")
            assert result == {"status": "ok"}


def test_provision_app_sends_customer_identity(app):
    with patch("requests.request") as mock_req:
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.content = b'{"status": "queued"}'
        mock_resp.json.return_value = {"status": "queued"}
        mock_req.return_value = mock_resp

        with app.app_context():
            provision_app("test-app", "starter", "customer_001")

    assert mock_req.call_args.kwargs["headers"]["X-Admiral-Customer-ID"] == "customer_001"


def test_request_http_error(app):
    with patch("requests.request") as mock_req:
        mock_resp = MagicMock()
        mock_resp.ok = False
        mock_resp.text = "Error message"
        mock_resp.json.side_effect = ValueError()
        mock_req.return_value = mock_resp

        with app.app_context():
            with pytest.raises(AdmiralAPIError) as excinfo:
                _request("GET", "/error")
            assert "Error message" in str(excinfo.value)


def test_request_exception(app):
    with patch("requests.request", side_effect=requests.RequestException("Conn error")):
        with app.app_context():
            with pytest.raises(AdmiralAPIError) as excinfo:
                _request("GET", "/fail")
            assert "Conn error" in str(excinfo.value)


def test_parse_tiers_from_yaml():
    yaml = """
tiers:
  starter:
    cpu: 1
    memory: 512Mi
    price_monthly: 5
    free: false
  free_tier:
    cpu: 0.5
    price_monthly: 0
    free: true
"""
    tiers = parse_tiers_from_yaml(yaml)
    assert "starter" in tiers
    assert tiers["starter"]["cpu"] == 1.0
    assert tiers["starter"]["price_monthly"] == 5.0
    assert tiers["starter"]["free"] is False
    assert tiers["starter"]["memory"] == "512Mi"

    assert "free_tier" in tiers
    assert tiers["free_tier"]["cpu"] == 0.5
    assert tiers["free_tier"]["free"] is True


def test_parse_tiers_from_yaml_handles_quoted_values_and_invalid_documents():
    tiers = parse_tiers_from_yaml('tiers:\n  starter:\n    price_monthly: "12.50"\n    free: false\n')
    assert tiers["starter"]["price_monthly"] == "12.50"
    assert tiers["starter"]["free"] is False
    assert parse_tiers_from_yaml("tiers: [not-a-tier-map]") == {}
    assert parse_tiers_from_yaml("tiers: [") == {}


def test_normalize_tiers():
    raw_tiers = {
        "b": {"price_monthly": 10, "cpu": 2},
        "a": {"price_monthly": 5, "cpu": 1},
    }
    normalized = normalize_tiers(raw_tiers)
    # Should be sorted by price then name
    assert normalized[0]["name"] == "a"
    assert normalized[1]["name"] == "b"
    assert normalized[0]["price_monthly_cents"] == 500


def test_get_app(app):
    mock_app_data = {"name": "test-app", "raw_yaml": "tiers:\n  t1:\n    price_monthly: 0\n    free: true"}
    with patch("app.admiral_client._request", return_value=mock_app_data):
        with app.app_context():
            app_info = get_app("test-app")
            assert app_info["name"] == "test-app"
            assert "tiers" in app_info
            assert app_info["requires_billing"] is False
