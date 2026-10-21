# SPDX-FileCopyrightText: William Moreno Reyes <williamjmorenor@gmail.com>
# SPDX-License-Identifier: Apache-2.0

import re
from functools import lru_cache
from html import unescape
from urllib.parse import unquote

from markupsafe import Markup

try:
    import mistune
except ImportError as exc:  # pragma: no cover - packaging ensures this dependency
    raise RuntimeError("mistune is required to render Harbor markdown descriptions") from exc


_SCHEME_RE = re.compile(r"^([a-z][a-z0-9+.-]*):", re.IGNORECASE)
_SAFE_SCHEMES = {"http", "https", "mailto"}


def _unsafe_link(url):
    """Reject executable schemes after browser-like URL decoding."""
    normalized = str(url)
    for _ in range(10):
        decoded = unescape(unquote(normalized))
        if decoded == normalized:
            break
        normalized = decoded

    # Browsers ignore ASCII whitespace and controls when parsing URL schemes.
    normalized = "".join(character for character in normalized if not character.isspace() and ord(character) >= 0x20)
    match = _SCHEME_RE.match(normalized)
    if match and match.group(1).lower() not in _SAFE_SCHEMES:
        return True

    # Reject network-path links so generated external links remain explicit.
    return normalized.startswith(("//", "\\\\"))


class _SafeHTMLRenderer(mistune.HTMLRenderer):
    def link(self, text, url, title=None):
        if _unsafe_link(url):
            url = "#harmful-link"
        return super().link(text, url, title)


@lru_cache(maxsize=1)
def _renderer():
    return mistune.create_markdown(
        renderer=_SafeHTMLRenderer(escape=True),
        plugins=["strikethrough", "table"],
    )


def render_markdown(value):
    if value is None:
        return Markup("")
    text = str(value)
    if not text.strip():
        return Markup("")
    return Markup(_renderer()(text))  # nosec B704 - mistune escapes HTML
