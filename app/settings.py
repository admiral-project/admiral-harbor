# SPDX-FileCopyrightText: William Moreno Reyes <williamjmorenor@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Harbor commercial settings — persisted in database via HarborMeta.

All settings not required for deployment should live here with admin UI forms,
so the portal admin never needs shell access.
"""

from app.models import HarborMeta

# ── Key constants ──────────────────────────────────────────────────────────

SMTP_FROM_KEY = "harbor_smtp_from"
EXTERNAL_URL_KEY = "harbor_external_url"
MAX_BACKUP_UPLOAD_BYTES_KEY = "harbor_max_backup_upload_bytes"
OVERDUE_POLICY_VERSION_KEY = "harbor_overdue_policy_version"
OVERDUE_SUSPEND_AFTER_DAYS_KEY = "harbor_overdue_suspend_after_days"
OVERDUE_DEPROVISION_AFTER_DAYS_KEY = "harbor_overdue_deprovision_after_days"
OVERDUE_LAST_BACKUP_RETENTION_DAYS_KEY = "harbor_overdue_last_backup_retention_days"

# ── Defaults (used when DB has no value) ──────────────────────────────────

_DEFAULTS = {
    SMTP_FROM_KEY: "noreply@example.com",
    EXTERNAL_URL_KEY: "https://localhost:5000",
    MAX_BACKUP_UPLOAD_BYTES_KEY: "536870912",
    OVERDUE_POLICY_VERSION_KEY: "overdue-policy-v1",
    OVERDUE_SUSPEND_AFTER_DAYS_KEY: "5",
    OVERDUE_DEPROVISION_AFTER_DAYS_KEY: "10",
    OVERDUE_LAST_BACKUP_RETENTION_DAYS_KEY: "15",
}


# ── Helpers ────────────────────────────────────────────────────────────────


def get_setting(key, default=None):
    """Read a setting from DB. Falls back to _DEFAULTS then caller default."""
    value = HarborMeta.get(key)
    if value is not None:
        return value
    if key in _DEFAULTS:
        return _DEFAULTS[key]
    return default


def set_setting(key, value):
    """Persist a setting in the DB."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return
    HarborMeta.set(key, str(value).strip())


def get_all_settings():
    """Return a dict of every known commercial setting."""
    out = {}
    for key in _DEFAULTS:
        out[key] = get_setting(key)
    return out


def set_smtp_from(email):
    set_setting(SMTP_FROM_KEY, email)


def get_smtp_from():
    return get_setting(SMTP_FROM_KEY)


def set_external_url(url):
    set_setting(EXTERNAL_URL_KEY, url)


def get_external_url():
    return get_setting(EXTERNAL_URL_KEY)


def set_max_backup_upload_bytes(value):
    set_setting(MAX_BACKUP_UPLOAD_BYTES_KEY, value)


def get_max_backup_upload_bytes():
    return int(get_setting(MAX_BACKUP_UPLOAD_BYTES_KEY))


def set_overdue_policy_version(version):
    set_setting(OVERDUE_POLICY_VERSION_KEY, version)


def get_overdue_policy_version():
    return get_setting(OVERDUE_POLICY_VERSION_KEY)


def set_overdue_suspend_after_days(days):
    set_setting(OVERDUE_SUSPEND_AFTER_DAYS_KEY, str(int(days)))


def get_overdue_suspend_after_days():
    return int(get_setting(OVERDUE_SUSPEND_AFTER_DAYS_KEY))


def set_overdue_deprovision_after_days(days):
    set_setting(OVERDUE_DEPROVISION_AFTER_DAYS_KEY, str(int(days)))


def get_overdue_deprovision_after_days():
    return int(get_setting(OVERDUE_DEPROVISION_AFTER_DAYS_KEY))


def set_overdue_last_backup_retention_days(days):
    set_setting(OVERDUE_LAST_BACKUP_RETENTION_DAYS_KEY, str(int(days)))


def get_overdue_last_backup_retention_days():
    return int(get_setting(OVERDUE_LAST_BACKUP_RETENTION_DAYS_KEY))


def overdue_policy_dict():
    """Return the overdue policy dict (same shape as config.py version)."""
    return {
        "policy_version": get_overdue_policy_version(),
        "grace_before_suspend_days": get_overdue_suspend_after_days(),
        "additional_grace_before_deprovision_days": get_overdue_deprovision_after_days(),
        "last_backup_retention_days": get_overdue_last_backup_retention_days(),
        "requires_acceptance_at_signup": True,
    }
