# SPDX-License-Identifier: Apache-2.0

import pytest

from app.theming import ThemeError, ThemeLoader, load_theme
from jinja2 import Environment, TemplateNotFound


def _write_theme(root, config=None):
    root.mkdir()
    (root / "theme.css").write_text("body { color: red; }", encoding="utf-8")
    (root / "layout.html").write_text("{% block body %}{% endblock %}", encoding="utf-8")
    (root / "theme.yml").write_text(
        config
        or "theme: theme.css\nlayout: layout.html\ntemplates: []\nassets:\n  css: [theme.css]\n  js: []\n  files: []\n",
        encoding="utf-8",
    )


def test_missing_theme_is_optional(tmp_path):
    assert load_theme(tmp_path / "missing") is None


def test_valid_theme_is_loaded(tmp_path):
    theme_dir = tmp_path / "theme"
    _write_theme(theme_dir)
    theme = load_theme(theme_dir)
    assert theme["root"] == theme_dir
    assert "theme.css" in theme["declared"]


@pytest.mark.parametrize(
    "config",
    [
        "layout: layout.html\nassets: {css: [theme.css]}\n",
        "theme: missing.css\nlayout: layout.html\nassets: {css: [missing.css]}\n",
        "theme: theme.css\nlayout: ../layout.html\nassets: {css: [theme.css]}\n",
        "theme: theme.css\nlayout: layout.html\nassets: {css: [], js: [], files: []}\n",
    ],
)
def test_invalid_theme_fails_fast(tmp_path, config):
    theme_dir = tmp_path / "theme"
    _write_theme(theme_dir, config)
    with pytest.raises(ThemeError):
        load_theme(theme_dir)


def test_asset_must_be_declared(tmp_path):
    theme_dir = tmp_path / "theme"
    _write_theme(theme_dir)
    theme = load_theme(theme_dir)
    assert "secret.txt" not in theme["declared"]


def test_only_declared_templates_are_loadable(tmp_path):
    theme_dir = tmp_path / "theme"
    _write_theme(theme_dir)
    (theme_dir / "undeclared.html").write_text("secret", encoding="utf-8")
    theme = load_theme(theme_dir)
    loader = ThemeLoader(str(theme_dir), theme["templates"])
    environment = Environment(loader=loader)
    with pytest.raises(TemplateNotFound):
        environment.get_template("undeclared.html")
