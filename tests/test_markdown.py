# SPDX-FileCopyrightText: William Moreno Reyes <williamjmorenor@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from markupsafe import Markup
from app.markdown import render_markdown


def test_render_markdown_basic():
    # Test bold, italics, links
    res = render_markdown("Hello **world**!")
    assert "<strong>world</strong>" in res

    res = render_markdown("This is *italic* text.")
    assert "<em>italic</em>" in res

    res = render_markdown("[Google](https://google.com)")
    assert '<a href="https://google.com">Google</a>' in res


def test_render_markdown_empty_and_none():
    # None input should return empty Markup
    assert render_markdown(None) == Markup("")
    # Empty string should return empty Markup
    assert render_markdown("") == Markup("")
    # Whitespace string should return empty Markup
    assert render_markdown("   \n  ") == Markup("")


def test_render_markdown_xss_protection():
    # Raw HTML tags should be escaped
    bad_input = "<script>alert('xss')</script>"
    res = render_markdown(bad_input)
    assert "<script>" not in res
    assert "&lt;script&gt;" in res


def test_render_markdown_strikethrough_and_table():
    # Test plugins (strikethrough)
    res = render_markdown("~~deleted~~")
    assert "<del>deleted</del>" in res

    # Test plugins (table)
    table_input = "| Header 1 | Header 2 |\n| -------- | -------- |\n| Cell 1   | Cell 2   |\n"
    res = render_markdown(table_input)
    assert "<table>" in res
    assert "<th>Header 1</th>" in res
    assert "<td>Cell 1</td>" in res
