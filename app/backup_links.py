# SPDX-FileCopyrightText: William Moreno Reyes <williamjmorenor@gmail.com>
# SPDX-License-Identifier: Apache-2.0

import hashlib
import hmac
import time
from urllib.parse import urlencode

from flask import current_app


def _canonical_payload(backup_id, customer_id, expires_at):
    return f"{backup_id}|{customer_id}|{expires_at}".encode()


def _signing_key():
    return str(current_app.config["SECRET_KEY"]).encode("utf-8")


def build_backup_download_query(backup_id, customer_id, now=None):
    if now is None:
        now = int(time.time())
    expires_at = now + int(current_app.config.get("HARBOR_BACKUP_DOWNLOAD_TTL_SECONDS", 600))
    signature = hmac.new(
        _signing_key(),
        _canonical_payload(backup_id, customer_id, expires_at),
        hashlib.sha256,
    ).hexdigest()
    return urlencode({"customer_id": customer_id, "expires": expires_at, "signature": signature})


def verify_backup_download_signature(backup_id, customer_id, expires_at, signature, now=None):
    try:
        expires = int(expires_at)
    except (TypeError, ValueError):
        return False
    if now is None:
        now = int(time.time())
    if expires < now:
        return False
    expected = hmac.new(
        _signing_key(),
        _canonical_payload(backup_id, customer_id, expires),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, str(signature or ""))
