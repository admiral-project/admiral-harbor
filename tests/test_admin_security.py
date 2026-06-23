import pytest
from app.models import HarborAdminUser
from app.extensions import db
from argon2 import PasswordHasher
from flask import get_flashed_messages

ph = PasswordHasher()

def test_admin_login_rate_limit(client, app):
    """Test that admin login is rate limited."""
    with app.app_context():
        # Ensure we have an admin user
        if not db.session.query(HarborAdminUser).filter_by(username="admin").first():
            admin = HarborAdminUser(
                username="admin",
                display_name="Admin",
                password_hash=ph.hash("secret")
            )
            db.session.add(admin)
            db.session.commit()

    # Fail login 5 times
    for _ in range(5):
        response = client.post("/admin/login", data={
            "username": "admin",
            "password": "wrong-password"
        }, follow_redirects=True)
        assert response.status_code == 200

    # 6th attempt should be rate limited
    response = client.post("/admin/login", data={
        "username": "admin",
        "password": "secret"
    }, follow_redirects=False)
    assert response.status_code == 429

    # Check that it redirects to login page with 429
    assert response.headers["Location"] == "/admin/login"
