# SPDX-FileCopyrightText: William Moreno Reyes <williamjmorenor@gmail.com>
# SPDX-License-Identifier: Apache-2.0

import sys
from urllib.parse import urlparse

import requests
from flask import current_app


def _candidates():
    url = current_app.config["ADMIRAL_API_URL"]
    parsed = urlparse(url)
    port = parsed.port
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    scheme = parsed.scheme
    ca = current_app.config.get("ADMIRAL_CA_FILE", "")
    insecure = current_app.config.get("ADMIRAL_INSECURE_SKIP_VERIFY", False)
    verify = ca if ca else not insecure
    return scheme, port, verify


def handle_ping():
    scheme, port, verify = _candidates()
    token = current_app.config["ADMIRAL_ADMIN_TOKEN"]

    addrs = ["127.0.0.1", "10.99.0.1"]
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Admiral-Operator": "admiral-harbor",
    }

    for addr in addrs:
        url = f"{scheme}://{addr}:{port}/api/v1/status"
        try:
            resp = requests.get(
                url,
                headers=headers,
                verify=verify,
                timeout=10,
            )
            resp.raise_for_status()
            elapsed = resp.elapsed.total_seconds()
            print(f"\u2713 admirald reachable at {url} ({elapsed:.3f}s)")
            return
        except requests.exceptions.ConnectionError:
            continue
        except requests.exceptions.Timeout:
            continue
        except requests.HTTPError as e:
            print(f"\u2717 admirald at {url} returned HTTP {e.response.status_code}: {e.response.text.strip()}")
            sys.exit(1)

    print(f"\u2717 admirald unreachable at {addrs} port {port} (tried 127.0.0.1 and 10.99.0.1)")
    sys.exit(1)
