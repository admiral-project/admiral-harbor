# SPDX-License-Identifier: Apache-2.0

"""External theme discovery, validation, and Jinja integration."""

import os
from pathlib import Path

import yaml
from flask import Flask
from jinja2 import ChoiceLoader, FileSystemLoader, TemplateNotFound


class ThemeError(RuntimeError):
    """Raised when an installed Harbor theme cannot be used safely."""


# Templates rendered by customer-facing routes. Administrative templates are
# never overridable; a theme declaring one fails fast at startup so the
# operator interface can never be branded from the theme directory.
CUSTOMER_TEMPLATES = frozenset(
    {
        "app_detail.html",
        "auth_login.html",
        "auth_profile.html",
        "auth_register.html",
        "client_billing.html",
        "client_billing_cancel_confirm.html",
        "client_billing_return_confirm.html",
        "client_dashboard.html",
        "client_fiscal_request_new.html",
        "client_fiscal_requests.html",
        "client_help.html",
        "client_instance_detail.html",
        "client_profile.html",
        "client_provision_confirmation.html",
        "client_receipt.html",
        "client_subscription_cancel.html",
        "client_subscription_detail.html",
        "client_subscriptions_list.html",
        "client_subscription_upgrade.html",
        "client_support_create.html",
        "client_support_detail.html",
        "client_support_list.html",
        "confirm_email.html",
        "index.html",
        "layout.html",
        "mock_paypal_approve.html",
    }
)


class ThemeLoader(FileSystemLoader):
    """Jinja loader that exposes only templates explicitly declared by a theme."""

    def __init__(self, searchpath, declared_templates):
        super().__init__(searchpath)
        self.declared_templates = frozenset(declared_templates)

    def get_source(self, environment, template):
        if template not in self.declared_templates:
            raise TemplateNotFound(template)
        return super().get_source(environment, template)


def _safe_file(root, relative, label):
    if not isinstance(relative, str) or not relative.strip():
        raise ThemeError(f"theme.yml {label} must be a non-empty relative path")
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        raise ThemeError(f"theme.yml {label} contains an unsafe path: {relative}")
    candidate = (root / path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ThemeError(f"theme.yml {label} escapes the theme directory: {relative}") from exc
    if not candidate.is_file() or not os.access(candidate, os.R_OK):
        raise ThemeError(f"theme.yml {label} is missing or unreadable: {relative}")
    return candidate


def _require_dir(root, relative, label):
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ThemeError(f"theme.yml {label} escapes the theme directory: {relative}") from exc
    if not candidate.is_dir() or not os.access(candidate, os.R_OK | os.X_OK):
        raise ThemeError(f"Custom theme is missing a readable {label} directory: {candidate}")
    return candidate


def load_theme(theme_dir):
    """Return validated theme metadata, or None when no theme is installed."""
    entry = Path(theme_dir)
    if not entry.exists():
        if entry.is_symlink():
            raise ThemeError(f"Custom theme directory is a broken symbolic link: {entry}")
        return None
    root = entry.resolve()
    if not root.is_dir() or not os.access(root, os.R_OK | os.X_OK):
        raise ThemeError(f"Custom theme directory is not readable: {root}")

    config_path = root / "theme.yml"
    if not config_path.is_file() or not os.access(config_path, os.R_OK):
        raise ThemeError(f"Custom theme is missing a readable theme.yml: {config_path}")
    try:
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ThemeError(f"Unable to read custom theme configuration {config_path}: {exc}") from exc
    if not isinstance(config, dict):
        raise ThemeError("theme.yml must contain a mapping")

    templates_root = _require_dir(root, "theme", "theme")
    assets_root = _require_dir(root, "assets", "assets")

    css = config.get("theme")
    layout = config.get("layout")
    for value, label in ((layout, "layout"), (css, "theme")):
        if not isinstance(value, str) or not value.strip():
            raise ThemeError(f"theme.yml {label} must be a non-empty relative path")

    if Path(layout).name not in CUSTOMER_TEMPLATES:
        raise ThemeError(f"theme.yml layout is not a customer-facing template: {layout}")
    _safe_file(templates_root, layout, "layout")
    _safe_file(assets_root, css, "theme")

    templates = config.get("templates", [])
    if not isinstance(templates, list) or not all(isinstance(item, str) for item in templates):
        raise ThemeError("theme.yml templates must be a list of paths")
    declared_templates = {layout}
    for item in templates:
        if Path(item).name not in CUSTOMER_TEMPLATES:
            raise ThemeError(f"theme.yml template is not available for override: {item}")
        _safe_file(templates_root, item, "templates")
        declared_templates.add(item)

    assets = config.get("assets", {})
    if not isinstance(assets, dict):
        raise ThemeError("theme.yml assets must be a mapping")
    declared_assets = set()
    for key in ("css", "js", "files"):
        values = assets.get(key, [])
        if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
            raise ThemeError(f"theme.yml assets.{key} must be a list of paths")
        for item in values:
            _safe_file(assets_root, item, f"assets.{key}")
            declared_assets.add(item)
    if css not in assets.get("css", []):
        raise ThemeError("theme.yml must declare the theme CSS in assets.css")
    return {
        "root": root,
        "templates_root": templates_root,
        "assets_root": assets_root,
        "assets": frozenset(declared_assets),
        "templates": frozenset(declared_templates),
    }


def configure_theme(app: Flask):
    theme = load_theme(app.config["HARBOR_CUSTOM_THEME_DIR"])
    if theme is not None:
        app.jinja_loader = ChoiceLoader(
            [ThemeLoader(str(theme["templates_root"]), theme["templates"]), app.jinja_loader]
        )
    app.extensions["harbor_theme"] = theme
    return theme
