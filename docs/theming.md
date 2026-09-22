# Custom themes

Harbor can load a complete customer-facing theme from
`/opt/admiral/harbor/custom-theme`. The directory is optional: when it is
absent, Harbor uses its built-in templates and assets. If the directory exists
but is invalid, Harbor fails during application startup so a partial brand is
never served.

Set `HARBOR_CUSTOM_THEME_DIR` to use another location.

## Theme layout

```text
custom-theme/
├── theme.yml
├── layout.html
├── index.html
├── app_detail.html
├── theme.css
├── theme.js
└── assets/
    └── logo.svg
```

`theme.yml` must declare the main stylesheet and customer-facing layout:

```yaml
theme: theme.css
layout: layout.html

templates:
  - index.html
  - app_detail.html

assets:
  css:
    - theme.css
  js:
    - theme.js
  files:
    - assets/logo.svg
```

The declared files must exist, be readable regular files, and remain inside
the theme directory. Absolute paths, `..`, and links outside the theme are
rejected.

## Overrides and assets

Theme templates take precedence over the built-in templates by filename. A
template that is not included in the theme continues to use Harbor's built-in
version. The first release supports the public/catalog templates, customer
authentication templates, and all `client_*.html` templates. Administrative
templates are intentionally not overridden.

Only templates listed under `templates` (plus the declared `layout`) are
eligible for override. Other files in the theme directory are not loaded as
Jinja templates.

Declared assets are read-only and served at:

```text
/custom-theme/<path>
```

For example:

```jinja2
<link rel="stylesheet" href="{{ url_for('main.custom_theme_asset', filename='theme.css') }}">
<script src="{{ url_for('main.custom_theme_asset', filename='theme.js') }}" defer></script>
```

Only files declared in `theme.yml` are served. Files not declared are not
public, even when they exist in the theme directory.

## Startup validation

Harbor fails fast when an existing theme has any of these problems:

- missing or unreadable `theme.yml`;
- invalid YAML or a non-mapping configuration;
- missing `theme` or `layout` declarations;
- missing or unreadable declared files;
- invalid asset list types;
- unsafe paths or links outside the theme directory;
- a main stylesheet that is not declared in `assets.css`.

The startup error identifies the theme path and the invalid declaration. Fix
the theme or remove the directory to return to the built-in theme.

## Testing locally

Point the development configuration at a temporary theme directory:

```bash
HARBOR_CUSTOM_THEME_DIR=/tmp/harbor-theme python run.py
```

Keep the theme directory readable by the Harbor service account in packaged
deployments. The existing database, branding settings, routes, and customer
session behavior are unchanged by theme loading.
