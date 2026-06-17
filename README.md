# admiral-harbor

Customer portal and commercial operations app for the Admiral platform.

`admiral-harbor` is in active development. It is part of the normal single-node installation flow, but it is not production ready.

The codebase provides:

- customer registration, login, and profile management
- subscription and billing workflows
- application catalog browsing
- dashboard and instance views
- support incident flows
- admin workspace for operators
- catalog synchronization from `admirald`
- periodic reconciliation of overdue subscriptions and instance state

## Entry Points

The packaged and source entry points are:

- `admiral-harbor.service` - web application service
- `admiral-harbor-worker.service` - reconciliation worker
- `admiral-harbor-worker.timer` - periodic worker trigger
- `admiral-harbor-catalog-sync.service` - catalog sync job
- `admiral-harbor-catalog-sync.timer` - periodic catalog sync trigger
- `harbor-gunicorn` - gunicorn wrapper used by the web service
- `harborctl` - CLI wrapper for catalog and operator maintenance tasks
- `run.py` - direct Flask app entry point for local development
- `worker.py` - reconciliation worker entry point
- `cli.py` - catalog sync CLI (`sync`, `status`, `list`)

## Runtime Model

`admiral-harbor` runs as a Flask application backed by SQLAlchemy and Alembic.

- The default development database is SQLite at `sqlite:///harbor.db`.
- Production should use PostgreSQL via `HARBOR_DATABASE_URL`.
- Secrets are encrypted at rest through `HARBOR_ENCRYPTION_KEY`.
- CSRF protection and security headers are enabled by default.
- Login throttling is enforced through the Flask rate limit layer.
- The app performs an initial catalog sync handshake on startup.

## Installation

The RPM installs:

- `/usr/bin/harborctl`
- `/usr/bin/harbor-gunicorn`
- `/etc/admiral/harbor.env`
- the systemd units listed above

The web service binds to `127.0.0.1:5001` behind gunicorn and is expected to sit behind the Admiral TLS terminator.

## Configuration

`admiral-harbor` is configured through `/etc/admiral/harbor.env` in packaged installs.

Important variables:

- `HARBOR_DATABASE_URL` - database connection string, required in production
- `HARBOR_SECRET_KEY` - Flask secret key, required in production
- `HARBOR_ENCRYPTION_KEY` - master key for encrypted secrets, required in production
- `ADMIRAL_API_URL` - `admirald` API endpoint
- `ADMIRAL_ADMIN_TOKEN` - shared service token for Admiral APIs
- `ADMIRAL_CA_FILE` - CA bundle for TLS verification
- `ADMIRAL_INSECURE_SKIP_VERIFY` - test-only TLS bypass
- `HARBOR_BOOTSTRAP_ADMIN_USER` / `HARBOR_BOOTSTRAP_ADMIN_PASSWORD` - initial admin account
- `HARBOR_EXTERNAL_URL` - public portal URL
- `HARBOR_UPLOAD_DIR` - upload storage directory
- `HARBOR_MAX_BACKUP_UPLOAD_BYTES` - upload size limit
- `HARBOR_PAYPAL_CLIENT_ID` / `HARBOR_PAYPAL_CLIENT_SECRET` / `HARBOR_PAYPAL_WEBHOOK_ID` - PayPal integration
- `HARBOR_SMTP_*` - outbound email settings

The configuration defaults are intentionally safe for local development, but production must override the secret and database settings.

## Local Development

```bash
python run.py
python worker.py
python cli.py status
python cli.py sync
python cli.py list
```

## Security Notes

- Customer and admin authentication are separated.
- Password resets, login flows, and CSRF tokens are handled inside the app.
- Secrets are stored encrypted, not in plaintext.
- Overdue subscription handling is explicit and auditable.
- The worker only performs policy decisions that have already been encoded in application state.

See `AGENTS.md` for the broader Admiral architecture and commit guidelines.
