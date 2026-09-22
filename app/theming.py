# SPDX-License-Identifier: Apache-2.0

"""External theme discovery, validation, and Jinja integration."""

import os
from pathlib import Path

import yaml
from flask import Flask
from jinja2 import ChoiceLoader, FileSystemLoader, TemplateNotFound


class ThemeError(RuntimeError):
    """Raised when an installed Harbor theme cannot be used safely."""


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


def load_theme(theme_dir):
    """Return validated theme metadata, or None when no theme is installed."""
    root = Path(theme_dir).resolve()
    if not root.exists():
        return None
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

    css = config.get("theme")
    layout = config.get("layout")
    _safe_file(root, css, "theme")
    _safe_file(root, layout, "layout")

    templates = config.get("templates", [])
    assets = config.get("assets", {})
    if not isinstance(templates, list) or not all(isinstance(item, str) for item in templates):
        raise ThemeError("theme.yml templates must be a list of paths")
    if not isinstance(assets, dict):
        raise ThemeError("theme.yml assets must be a mapping")
    declared = {css, layout}
    declared_templates = {layout}
    for item in templates:
        _safe_file(root, item, "templates")
        declared.add(item)
        declared_templates.add(item)
    for key in ("css", "js", "files"):
        values = assets.get(key, [])
        if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
            raise ThemeError(f"theme.yml assets.{key} must be a list of paths")
        for item in values:
            _safe_file(root, item, f"assets.{key}")
            declared.add(item)
    if css not in assets.get("css", []):
        raise ThemeError("theme.yml must declare the theme CSS in assets.css")
    return {
        "root": root,
        "config": config,
        "declared": frozenset(declared),
        "templates": frozenset(declared_templates),
    }


def configure_theme(app: Flask):
    theme = load_theme(app.config["HARBOR_CUSTOM_THEME_DIR"])
    if theme is not None:
        app.jinja_loader = ChoiceLoader([ThemeLoader(str(theme["root"]), theme["templates"]), app.jinja_loader])
    app.extensions["harbor_theme"] = theme
    return theme
