# SPDX-FileCopyrightText: William Moreno Reyes <williamjmorenor@gmail.com>
# SPDX-License-Identifier: Apache-2.0

import re

_LOCAL_PART_RE = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+\Z", re.ASCII)
_DOMAIN_LABEL_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\Z", re.ASCII)


def is_valid_email(value):
    """Return whether value is a bounded, syntactically valid mailbox address."""
    if not isinstance(value, str):
        return False
    address = value.strip()
    if len(address) > 254 or address.count("@") != 1:
        return False

    local, domain = address.rsplit("@", 1)
    if (
        not local
        or len(local) > 64
        or local.startswith(".")
        or local.endswith(".")
        or ".." in local
        or not _LOCAL_PART_RE.fullmatch(local)
    ):
        return False

    labels = domain.split(".")
    if len(labels) < 2 or any(not _DOMAIN_LABEL_RE.fullmatch(label) for label in labels):
        return False
    return len(domain) <= 253
