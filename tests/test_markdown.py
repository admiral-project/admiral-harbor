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


def test_render_markdown_rejects_encoded_and_mixed_case_executable_urls():
    for bad_url in (
        "javascript%3Aalert%281%29",
        "JaVaScRiPt:alert(1)",
        "java%73cript%3Aalert%281%29",
        "javascript%253Aalert%25281%2529",
        "java%0ascript%3Aalert(1)",
        "javascript&colon;alert(1)",
        "//untrusted.example/path",
    ):
        res = render_markdown(f"[probe]({bad_url})")
        assert "javascript" not in res.lower()
        assert 'href="#harmful-link"' in res

    assert '<a href="https://example.com">ok</a>' in render_markdown("[ok](https://example.com)")
    assert '<a href="/apps/example">relative</a>' in render_markdown("[relative](/apps/example)")
    assert '<a href="mailto:support@example.com">email</a>' in render_markdown("[email](mailto:support@example.com)")


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
