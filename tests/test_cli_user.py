import pytest
from unittest.mock import patch
from app.cli.user import cmd_create_admin, cmd_create_customer, cmd_list, cmd_set_password, cmd_toggle_active
from app.models import HarborAdminUser, Customer
from app.extensions import db


def test_cmd_list_table(app):
    with app.app_context():
        # Just ensure it doesn't crash
        cmd_list("table")


def test_cmd_list_json(app, capsys):
    with app.app_context():
        cmd_list("json")
        captured = capsys.readouterr()
        assert '"admins":' in captured.out
        assert '"customers":' in captured.out


def test_cmd_toggle_active(app):
    with app.app_context():
        # Clear and create an admin
        db.session.query(HarborAdminUser).delete()
        admin = HarborAdminUser(username="testadmin", display_name="Test Admin", password_hash="hash", is_active=True)
        db.session.add(admin)
        db.session.commit()

        cmd_toggle_active("testadmin")

        admin = HarborAdminUser.query.filter_by(username="testadmin").first()
        assert admin.is_active is False


def test_cmd_toggle_active_not_found(app):
    with app.app_context():
        with pytest.raises(SystemExit):
            cmd_toggle_active("nonexistent")


def test_cmd_set_password_success(app):
    with app.app_context():
        db.session.query(HarborAdminUser).delete()
        admin = HarborAdminUser(username="testadmin", display_name="Test Admin", password_hash="oldhash")
        db.session.add(admin)
        db.session.commit()

        with patch("getpass.getpass", side_effect=["newpass", "newpass"]):
            cmd_set_password("testadmin")

        admin = HarborAdminUser.query.filter_by(username="testadmin").first()
        assert admin.password_hash != "oldhash"


def test_cmd_create_admin_success(app):
    with app.app_context():
        db.session.query(HarborAdminUser).delete()
        db.session.commit()

        with patch("getpass.getpass", side_effect=["pass123", "pass123"]):
            cmd_create_admin("newadmin")

        assert HarborAdminUser.query.filter_by(username="newadmin").first() is not None


def test_cmd_create_customer_success(app):
    with app.app_context():
        db.session.query(Customer).delete()
        db.session.commit()

        with patch("getpass.getpass", side_effect=["pass123", "pass123"]):
            cmd_create_customer("cust@test.com", "Test Customer", "US")

        assert Customer.query.filter_by(email="cust@test.com").first() is not None
