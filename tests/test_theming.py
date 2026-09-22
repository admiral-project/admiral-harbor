# SPDX-License-Identifier: Apache-2.0

import pytest
from jinja2 import Environment, TemplateNotFound

from app.theming import ThemeError, ThemeLoader, configure_theme, load_theme

DEFAULT_CONFIG = (
    "theme: theme.css\n"
    "layout: layout.html\n"
    "templates: []\n"
    "assets:\n"
    "  css: [theme.css]\n"
    "  js: []\n"
    "  files: []\n"
)


def _write_theme(root, config=None, templates=()):
    root.mkdir()
    (root / "theme").mkdir()
    (root / "assets").mkdir()
    (root / "assets" / "theme.css").write_text("body { color: red; }", encoding="utf-8")
    (root / "theme" / "layout.html").write_text("{% block body %}{% endblock %}", encoding="utf-8")
    for name in templates:
        (root / "theme" / name).write_text(f"theme override {name}", encoding="utf-8")
    (root / "theme.yml").write_text(config or DEFAULT_CONFIG, encoding="utf-8")


def test_missing_theme_is_optional(tmp_path):
    assert load_theme(tmp_path / "missing") is None


def test_broken_theme_symlink_fails_fast(tmp_path):
    target = tmp_path / "theme"
    target.symlink_to(tmp_path / "missing")
    with pytest.raises(ThemeError):
        load_theme(target)


def test_theme_requires_theme_and_assets_directories(tmp_path):
    root = tmp_path / "theme"
    root.mkdir()
    (root / "theme.css").write_text("body { color: red; }", encoding="utf-8")
    (root / "theme.yml").write_text(DEFAULT_CONFIG, encoding="utf-8")
    with pytest.raises(ThemeError):
        load_theme(root)


def test_valid_theme_is_loaded(tmp_path):
    root = tmp_path / "theme"
    _write_theme(
        root, config=DEFAULT_CONFIG.replace("templates: []\n", "templates: [index.html]\n"), templates=("index.html",)
    )
    theme = load_theme(root)
    assert theme["root"] == root
    assert theme["templates_root"] == root / "theme"
    assert theme["assets_root"] == root / "assets"
    assert "theme.css" in theme["assets"]
    assert "index.html" in theme["templates"]
    assert "layout.html" in theme["templates"]


@pytest.mark.parametrize(
    "config",
    [
        "layout: layout.html\nassets: {css: [theme.css]}\n",
        "theme: missing.css\nlayout: layout.html\nassets: {css: [missing.css]}\n",
        "theme: theme.css\nlayout: ../layout.html\nassets: {css: [theme.css]}\n",
        "theme: theme.css\nlayout: layout.html\nassets: {css: [], js: [], files: []}\n",
        "theme: theme.css\nlayout: layout.html\nassets: {css: [theme.css], js: [], files: 7}\n",
        "theme: theme.css\nlayout: admin_layout.html\nassets: {css: [theme.css]}\n",
    ],
)
def test_invalid_theme_fails_fast(tmp_path, config):
    root = tmp_path / "theme"
    _write_theme(root, config=config)
    with pytest.raises(ThemeError):
        load_theme(root)


def test_admin_template_override_is_rejected(tmp_path):
    root = tmp_path / "theme"
    config = DEFAULT_CONFIG.replace("templates: []\n", "templates: [admin_dashboard.html]\n")
    _write_theme(root, config=config, templates=("admin_dashboard.html",))
    with pytest.raises(ThemeError):
        load_theme(root)


def test_asset_symlink_escape_is_rejected(tmp_path):
    root = tmp_path / "theme"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.css").write_text("body{}", encoding="utf-8")
    config = DEFAULT_CONFIG.replace("css: [theme.css]", "css: [link.css]")
    _write_theme(root, config=config)
    (root / "assets" / "link.css").symlink_to(outside / "secret.css")
    with pytest.raises(ThemeError):
        load_theme(root)


def test_asset_must_be_declared(tmp_path):
    root = tmp_path / "theme"
    _write_theme(root)
    (root / "assets" / "secret.txt").write_text("secret", encoding="utf-8")
    theme = load_theme(root)
    assert "secret.txt" not in theme["assets"]


def test_only_declared_templates_are_loadable(tmp_path):
    root = tmp_path / "theme"
    _write_theme(root)
    (root / "theme" / "undeclared.html").write_text("secret", encoding="utf-8")
    theme = load_theme(root)
    loader = ThemeLoader(str(theme["templates_root"]), theme["templates"])
    environment = Environment(loader=loader)
    with pytest.raises(TemplateNotFound):
        environment.get_template("undeclared.html")
    assert environment.get_template("layout.html").render() == ""


def test_theme_render_does_not_expose_templates(tmp_path):
    root = tmp_path / "theme"
    _write_theme(root, templates=("index.html",))
    theme = load_theme(root)
    assert "index.html" not in theme["assets"]


@pytest.fixture
def themed_app(app, tmp_path):
    root = tmp_path / "theme"
    _write_theme(root, templates=("index.html",))
    (root / "theme" / "index.html").write_text("THEMED-CATALOG", encoding="utf-8")
    (root / "assets" / "theme.js").write_text("console.log('theme');", encoding="utf-8")
    config = (
        "theme: theme.css\n"
        "layout: layout.html\n"
        "templates: [index.html]\n"
        "assets:\n"
        "  css: [theme.css]\n"
        "  js: [theme.js]\n"
        "  files: []\n"
    )
    (root / "theme.yml").write_text(config, encoding="utf-8")
    app.config["HARBOR_CUSTOM_THEME_DIR"] = str(root)
    configure_theme(app)
    return app


def test_themed_app_serves_declared_assets(themed_app):
    for filename, body in (("theme.css", b"body { color: red; }"), ("theme.js", b"console.log")):
        response = themed_app.test_client().get(f"/custom-theme/{filename}")
        assert response.status_code == 200
        assert body in response.data


def test_themed_app_rejects_undeclared_assets(themed_app):
    client = themed_app.test_client()
    assert client.get("/custom-theme/secret.txt").status_code == 404
    assert client.get("/custom-theme/index.html").status_code == 404
    assert client.get("/custom-theme/../theme.yml").status_code == 404


def test_themed_app_overrides_customer_template(themed_app):
    response = themed_app.test_client().get("/")
    assert response.status_code == 200
    assert b"THEMED-CATALOG" in response.data


def test_plain_app_serves_no_theme_assets(app):
    assert app.extensions["harbor_theme"] is None
    client = app.test_client()
    assert client.get("/custom-theme/theme.css").status_code == 404


def test_branding_skips_for_migrate_and_worker(monkeypatch):
    monkeypatch.delenv("HARBOR_MIGRATE", raising=False)
    monkeypatch.delenv("HARBOR_SKIP_CUSTOM_THEME", raising=False)
    from app import _serves_web_branding

    assert _serves_web_branding() is True
    monkeypatch.setenv("HARBOR_MIGRATE", "1")
    assert _serves_web_branding() is False
    monkeypatch.delenv("HARBOR_MIGRATE")
    monkeypatch.setenv("HARBOR_SKIP_CUSTOM_THEME", "1")
    assert _serves_web_branding() is False


def test_migrate_mode_skips_invalid_theme(tmp_path, monkeypatch):
    root = tmp_path / "broken"
    root.mkdir()
    (root / "theme.yml").write_text(": [unclosed", encoding="utf-8")
    monkeypatch.setenv("HARBOR_CUSTOM_THEME_DIR", str(root))
    monkeypatch.setenv("HARBOR_MIGRATE", "1")
    from app import create_app

    app = create_app()
    assert app.extensions.get("harbor_theme") is None
