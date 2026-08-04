# SPDX-FileCopyrightText: William Moreno Reyes <williamjmorenor@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from urllib.parse import parse_qs, urlparse

from app.backup_links import build_backup_download_query, verify_backup_download_signature


def test_backup_download_signature_is_scoped_and_expires(app):
    with app.app_context():
        query = parse_qs(build_backup_download_query("bk_1", "customer_a", now=1000))
        expires = query["expires"][0]
        signature = query["signature"][0]

        assert verify_backup_download_signature("bk_1", "customer_a", expires, signature, now=1001)
        assert not verify_backup_download_signature("bk_1", "customer_b", expires, signature, now=1001)
        assert not verify_backup_download_signature("bk_1", "customer_a", expires, signature, now=int(expires) + 1)


def test_backup_download_query_is_url_encoded(app):
    with app.app_context():
        query = build_backup_download_query("bk/1", "customer@example.com", now=1000)
        parsed = parse_qs(urlparse(f"https://example.test/?{query}").query)

        assert parsed["customer_id"] == ["customer@example.com"]
