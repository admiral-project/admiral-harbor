# SPDX-FileCopyrightText: William Moreno Reyes <williamjmorenor@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from datetime import UTC, date, datetime


def format_billing_date(value, fallback="N/A"):
    """Render current ISO date strings and legacy date/datetime values safely."""
    if value is None:
        return fallback
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()

    rendered = str(value).strip()
    if not rendered:
        return fallback
    try:
        return date.fromisoformat(rendered).isoformat()
    except ValueError:
        try:
            return datetime.fromisoformat(rendered.replace("Z", "+00:00")).date().isoformat()
        except ValueError:
            return rendered


def conversation_timestamp_key(value):
    if value is None:
        return float("-inf")
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.timestamp()
