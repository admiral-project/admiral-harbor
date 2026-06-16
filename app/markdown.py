# SPDX-FileCopyrightText: William Moreno Reyes <williamjmorenor@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from functools import lru_cache

from markupsafe import Markup

try:
    import mistune
except ImportError as exc:  # pragma: no cover - packaging ensures this dependency
    raise RuntimeError(
        "mistune is required to render Harbor markdown descriptions"
    ) from exc


@lru_cache(maxsize=1)
def _renderer():
    return mistune.create_markdown(
        renderer=mistune.HTMLRenderer(escape=True),
        plugins=["strikethrough", "table"],
    )


def render_markdown(value):
    if value is None:
        return Markup("")
    text = str(value)
    if not text.strip():
        return Markup("")
    return Markup(_renderer()(text))
