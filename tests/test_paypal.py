# SPDX-FileCopyrightText: William Moreno Reyes <williamjmorenor@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import patch, MagicMock
import pytest
import requests

from app.paypal import (
    PayPalError,
    _resolve_secret,
    _base_url,
    _api_url,
    _get_access_token,
    create_subscription,
    capture_subscription,
    get_subscription,
    cancel_subscription,
    refund_last_sale,
    verify_webhook_signature,
)
from app.secrets_manager import SecretsManager


def test_resolve_secret_decryption_error(app):
    with app.app_context():
        # Test decryption error
        with patch("app.extensions.secrets.decrypt", side_effect=SecretsManager.EncryptionError("decrypt failed")):
            secret = _resolve_secret("some_value")
            assert secret == "some_value"


def test_resolve_secret_none_or_empty(app):
    with app.app_context():
        assert _resolve_secret(None) is None
        assert _resolve_secret("") == ""


def test_base_url_invalid_mode(app):
    with app.app_context():
        with patch("app.paypal._db_paypal_config", return_value={"mode": "invalid"}):
            with pytest.raises(PayPalError) as excinfo:
                _base_url()
            assert "Invalid PayPal mode" in str(excinfo.value)


def test_api_url(app):
    with app.app_context():
        with patch("app.paypal._db_paypal_config", return_value={"mode": "mock"}):
            url = _api_url()
            assert "/mock-paypal" in url


def test_get_access_token_missing_credentials(app):
    with app.app_context():
        with patch(
            "app.paypal._db_paypal_config", return_value={"mode": "sandbox", "client_id": "", "client_secret": ""}
        ):
            with pytest.raises(PayPalError) as excinfo:
                _get_access_token()
            assert "Client ID and Client Secret are required" in str(excinfo.value)


def test_get_access_token_request_exception(app):
    with app.app_context():
        with patch(
            "app.paypal._db_paypal_config",
            return_value={"mode": "sandbox", "client_id": "id", "client_secret": "secret"},
        ):
            with patch("requests.post", side_effect=requests.RequestException("connection failed")):
                with pytest.raises(PayPalError) as excinfo:
                    _get_access_token()
                assert "PayPal authentication failed" in str(excinfo.value)


def test_create_subscription_non_mock_success(app):
    with app.app_context():
        with patch(
            "app.paypal._db_paypal_config",
            return_value={"mode": "sandbox", "client_id": "id", "client_secret": "secret"},
        ):
            with patch("app.paypal._get_access_token", return_value="token"):
                mock_response = MagicMock()
                mock_response.json.return_value = {"id": "SUB-123"}
                mock_response.raise_for_status = MagicMock()
                with patch("requests.post", return_value=mock_response) as mock_post:
                    res = create_subscription(
                        "plan_id", "http://return", "http://cancel", custom_id="cust", amount_cents=1000
                    )
                    assert res["id"] == "SUB-123"
                    mock_post.assert_called_once()


def test_create_subscription_missing_plan_id(app):
    with app.app_context():
        with patch(
            "app.paypal._db_paypal_config",
            return_value={"mode": "sandbox", "client_id": "id", "client_secret": "secret"},
        ):
            with pytest.raises(PayPalError) as excinfo:
                create_subscription("", "http://return", "http://cancel")
            assert "PayPal plan ID is required" in str(excinfo.value)


def test_create_subscription_request_exception(app):
    with app.app_context():
        with patch(
            "app.paypal._db_paypal_config",
            return_value={"mode": "sandbox", "client_id": "id", "client_secret": "secret"},
        ):
            with patch("app.paypal._get_access_token", return_value="token"):
                with patch("requests.post", side_effect=requests.RequestException("failed")):
                    with pytest.raises(PayPalError) as excinfo:
                        create_subscription("plan_id", "http://return", "http://cancel")
                    assert "Failed to create PayPal subscription" in str(excinfo.value)


def test_capture_subscription_non_mock_success(app):
    with app.app_context():
        with patch(
            "app.paypal._db_paypal_config",
            return_value={"mode": "sandbox", "client_id": "id", "client_secret": "secret"},
        ):
            with patch("app.paypal._get_access_token", return_value="token"):
                mock_response = MagicMock()
                mock_response.json.return_value = {"status": "ACTIVE"}
                with patch("requests.post", return_value=mock_response):
                    res = capture_subscription("sub_id")
                    assert res["status"] == "ACTIVE"


def test_capture_subscription_request_exception(app):
    with app.app_context():
        with patch(
            "app.paypal._db_paypal_config",
            return_value={"mode": "sandbox", "client_id": "id", "client_secret": "secret"},
        ):
            with patch("app.paypal._get_access_token", return_value="token"):
                with patch("requests.post", side_effect=requests.RequestException("failed")):
                    with pytest.raises(PayPalError) as excinfo:
                        capture_subscription("sub_id")
                    assert "Failed to activate PayPal subscription" in str(excinfo.value)


def test_get_subscription_non_mock_success(app):
    with app.app_context():
        with patch(
            "app.paypal._db_paypal_config",
            return_value={"mode": "sandbox", "client_id": "id", "client_secret": "secret"},
        ):
            with patch("app.paypal._get_access_token", return_value="token"):
                mock_response = MagicMock()
                mock_response.json.return_value = {"id": "sub_id", "status": "ACTIVE"}
                with patch("requests.get", return_value=mock_response):
                    res = get_subscription("sub_id")
                    assert res["status"] == "ACTIVE"


def test_get_subscription_request_exception(app):
    with app.app_context():
        with patch(
            "app.paypal._db_paypal_config",
            return_value={"mode": "sandbox", "client_id": "id", "client_secret": "secret"},
        ):
            with patch("app.paypal._get_access_token", return_value="token"):
                with patch("requests.get", side_effect=requests.RequestException("failed")):
                    with pytest.raises(PayPalError) as excinfo:
                        get_subscription("sub_id")
                    assert "Failed to get PayPal subscription" in str(excinfo.value)


def test_cancel_subscription_non_mock_success(app):
    with app.app_context():
        with patch(
            "app.paypal._db_paypal_config",
            return_value={"mode": "sandbox", "client_id": "id", "client_secret": "secret"},
        ):
            with patch("app.paypal._get_access_token", return_value="token"):
                mock_response = MagicMock()
                with patch("requests.post", return_value=mock_response) as mock_post:
                    cancel_subscription("sub_id", "Reason")
                    mock_post.assert_called_once()


def test_cancel_subscription_request_exception(app):
    with app.app_context():
        with patch(
            "app.paypal._db_paypal_config",
            return_value={"mode": "sandbox", "client_id": "id", "client_secret": "secret"},
        ):
            with patch("app.paypal._get_access_token", return_value="token"):
                with patch("requests.post", side_effect=requests.RequestException("failed")):
                    with pytest.raises(PayPalError) as excinfo:
                        cancel_subscription("sub_id")
                    assert "Failed to cancel PayPal subscription" in str(excinfo.value)


def test_refund_last_sale_non_mock_success(app):
    with app.app_context():
        with patch(
            "app.paypal._db_paypal_config",
            return_value={"mode": "sandbox", "client_id": "id", "client_secret": "secret"},
        ):
            with patch("app.paypal._get_access_token", return_value="token"):
                # Mock transaction list
                mock_get = MagicMock()
                mock_get.json.return_value = {
                    "transactions": [
                        {"status": "COMPLETED", "id": "capture_123"},
                    ]
                }
                # Mock refund response
                mock_post = MagicMock()
                mock_post.json.return_value = {"id": "refund_999"}

                with patch("requests.get", return_value=mock_get), patch("requests.post", return_value=mock_post):
                    refund_id = refund_last_sale("sub_id")
                    assert refund_id == "refund_999"


def test_refund_last_sale_no_capture_found(app):
    with app.app_context():
        with patch(
            "app.paypal._db_paypal_config",
            return_value={"mode": "sandbox", "client_id": "id", "client_secret": "secret"},
        ):
            with patch("app.paypal._get_access_token", return_value="token"):
                mock_get = MagicMock()
                mock_get.json.return_value = {"transactions": [{"status": "FAILED", "id": "capture_123"}]}
                with patch("requests.get", return_value=mock_get):
                    refund_id = refund_last_sale("sub_id")
                    assert refund_id is None


def test_refund_last_sale_list_exception(app):
    with app.app_context():
        with patch(
            "app.paypal._db_paypal_config",
            return_value={"mode": "sandbox", "client_id": "id", "client_secret": "secret"},
        ):
            with patch("app.paypal._get_access_token", return_value="token"):
                with patch("requests.get", side_effect=requests.RequestException("failed")):
                    with pytest.raises(PayPalError) as excinfo:
                        refund_last_sale("sub_id")
                    assert "Failed to list PayPal transactions" in str(excinfo.value)


def test_refund_last_sale_refund_exception(app):
    with app.app_context():
        with patch(
            "app.paypal._db_paypal_config",
            return_value={"mode": "sandbox", "client_id": "id", "client_secret": "secret"},
        ):
            with patch("app.paypal._get_access_token", return_value="token"):
                mock_get = MagicMock()
                mock_get.json.return_value = {"transactions": [{"status": "COMPLETED", "id": "capture_123"}]}
                with (
                    patch("requests.get", return_value=mock_get),
                    patch("requests.post", side_effect=requests.RequestException("refund error")),
                ):
                    with pytest.raises(PayPalError) as excinfo:
                        refund_last_sale("sub_id")
                    assert "Failed to refund PayPal capture" in str(excinfo.value)


def test_verify_webhook_signature_no_webhook_id(app):
    with app.app_context():
        with patch("app.paypal._db_paypal_config", return_value={"mode": "sandbox", "webhook_id": ""}):
            assert verify_webhook_signature({}, "{}") is False


def test_verify_webhook_signature_invalid_json(app):
    with app.app_context():
        with patch("app.paypal._db_paypal_config", return_value={"mode": "sandbox", "webhook_id": "web_id"}):
            assert verify_webhook_signature({}, "invalid json") is False


def test_verify_webhook_signature_not_dict(app):
    with app.app_context():
        with patch("app.paypal._db_paypal_config", return_value={"mode": "sandbox", "webhook_id": "web_id"}):
            assert verify_webhook_signature({}, "[]") is False


def test_verify_webhook_signature_api_exception(app):
    with app.app_context():
        with patch(
            "app.paypal._db_paypal_config",
            return_value={"mode": "sandbox", "client_id": "id", "client_secret": "secret", "webhook_id": "web_id"},
        ):
            with patch("app.paypal._get_access_token", return_value="token"):
                with patch("requests.post", side_effect=requests.RequestException("failed")):
                    assert verify_webhook_signature({}, "{}") is False


def test_verify_webhook_signature_success(app):
    with app.app_context():
        with patch(
            "app.paypal._db_paypal_config",
            return_value={"mode": "sandbox", "client_id": "id", "client_secret": "secret", "webhook_id": "web_id"},
        ):
            with patch("app.paypal._get_access_token", return_value="token"):
                mock_resp = MagicMock()
                mock_resp.json.return_value = {"verification_status": "SUCCESS"}
                with patch("requests.post", return_value=mock_resp):
                    assert verify_webhook_signature({}, "{}") is True
