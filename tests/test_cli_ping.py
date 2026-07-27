from unittest.mock import MagicMock, patch

import pytest
import requests

from app.cli.ping import handle_ping


def test_handle_ping_success(app):
    app.config["ADMIRAL_HARBOR_API_TOKEN"] = "harbor-token"
    with patch("requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.elapsed.total_seconds.return_value = 0.1
        mock_get.return_value = mock_resp

        with app.app_context():
            handle_ping()

        mock_get.assert_called_once()
        call_args = mock_get.call_args
        assert "/api/v1/harbor_ping" in call_args[0][0]


def test_handle_ping_failure(app):
    app.config["ADMIRAL_HARBOR_API_TOKEN"] = "harbor-token"
    with patch("requests.get") as mock_get:
        mock_resp = MagicMock()

        error_response = MagicMock()
        error_response.status_code = 500
        error_response.text = "Internal Error"

        http_error = requests.HTTPError("Fail")
        http_error.response = error_response

        mock_resp.raise_for_status.side_effect = http_error
        mock_get.return_value = mock_resp

        with app.app_context():
            with pytest.raises(SystemExit) as excinfo:
                handle_ping()
            assert excinfo.value.code == 1


def test_handle_ping_unreachable(app):
    app.config["ADMIRAL_HARBOR_API_TOKEN"] = "harbor-token"
    with patch("requests.get", side_effect=requests.exceptions.ConnectionError()), app.app_context():
        with pytest.raises(SystemExit) as excinfo:
            handle_ping()
        assert excinfo.value.code == 1


def test_handle_ping_missing_token(app):
    app.config["ADMIRAL_HARBOR_API_TOKEN"] = ""
    with app.app_context():
        with pytest.raises(SystemExit) as excinfo:
            handle_ping()
        assert excinfo.value.code == 1
