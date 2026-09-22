# Custom themes

Harbor can load a complete customer-facing theme from
`/opt/admiral/harbor/custom-theme`. The directory is optional: when it is
absent, Harbor uses its built-in templates and assets. If the directory exists
but is invalid, Harbor fails during application startup so a partial brand is
never served.

Set `HARBOR_CUSTOM_THEME_DIR` to use another location.

## Theme layout

Templates and static assets live in two separate directories: `theme/` holds
the Jinja templates and `assets/` holds the files published to browsers.

```text
custom-theme/
├── theme.yml
├── theme/
│   ├── layout.html
│   ├── index.html
│   └── app_detail.html
└── assets/
    ├── theme.css
    ├── theme.js
    └── logo.svg
```

`theme.yml` must declare the main stylesheet, the customer-facing layout, the
templates to override and the static assets:

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
    - logo.svg
```

Paths are relative to their owning directory: `layout` and `templates` resolve
inside `theme/`; the theme CSS and the `assets` lists resolve inside `assets/`.
Declared files must exist, be readable regular files, and stay inside their
directory. Absolute paths and `..` are rejected.

## Overrides and assets

Theme templates take precedence over the built-in templates by filename. A
template that is not included in the theme continues to use Harbor's built-in
version. Only customer-facing templates can be overridden: the catalog and app
detail pages, customer authentication templates, all `client_*.html` templates,
the shared `layout.html`, `confirm_email.html` and the mock PayPal page.
Declaring any other template — including any administrative template — fails at
startup, so the operator interface can never be branded from the theme
directory. This applies to both the `layout` and the `templates` declarations.

Only templates listed under `templates` (plus the declared `layout`) are loaded
from `theme/`. Jinja templates are never served over HTTP.

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
- a theme directory that is a broken symbolic link;
- missing `theme/` or `assets/` directories;
- missing `theme` or `layout` declarations;
- a layout or template that is not a customer-facing template;
- missing or unreadable declared files;
- invalid asset list types;
- unsafe paths or links outside the theme directory;
- a main stylesheet that is not declared in `assets.css`.

The startup error identifies the theme path and the invalid declaration. Fix
the theme or remove the directory to return to the built-in theme.

The schema migrator and the batch worker never render portal pages. They ignore
the custom theme entirely, so an invalid theme cannot prevent migrations or
worker runs. Set `HARBOR_SKIP_CUSTOM_THEME=1` to skip theme loading in any
process that should not validate it.

## Applying changes

The `theme.yml` declarations and the file sets are validated once at startup.
Changing the directory contents or `theme.yml` requires a Harbor restart; the
running process does not re-read the declarations.

## Testing locally

Point the development configuration at a temporary theme directory:

```bash
HARBOR_CUSTOM_THEME_DIR=/tmp/harbor-theme python run.py
```

Keep the theme directory readable by the Harbor service account in packaged
deployments. The existing database, branding settings, routes, and customer
session behavior are unchanged by theme loading.