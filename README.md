# EduHope / Bold Foundation

Django site for Bold Foundation, with public learner, mentor, partnership, contact, and event-support forms; staff event and application management; and an M-Pesa mentor registration payment flow.

## Requirements

- Python 3.13
- Dependencies in `requirements.txt`
- SQLite for local development, or PostgreSQL through `DATABASE_URL`

## Local setup

In PowerShell:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
```

If `.env` does not already exist, copy the example with `Copy-Item .env.example .env`. Edit `.env` and set a private `SECRET_KEY`. Keep `.env` out of version control. For local work, the example file configures SQLite and M-Pesa sandbox placeholders. Set actual sandbox credentials and a reachable HTTPS callback URL before testing Daraja callbacks; callback secrets must be long, random, and private.

Initialize and run the site:

```powershell
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

The public site is at `http://127.0.0.1:8000/`. Uploaded event media is stored under `media/`; static files are collected into `staticfiles/`.

## Environment variables

Copy the names and descriptions from `.env.example`. The key variables are:

| Variable | Purpose |
| --- | --- |
| `SECRET_KEY` | Private Django signing key; never commit or reuse an exposed value. |
| `DEBUG` | Enable debug mode only for local development. |
| `ALLOWED_HOSTS` | Comma-separated hostnames served by Django. |
| `CSRF_TRUSTED_ORIGINS` | Comma-separated scheme-qualified trusted browser origins. |
| `DATABASE_URL` | Optional database URL; omitted locally to use SQLite. |
| `MPESA_ENVIRONMENT` | `sandbox` or `production`. |
| `MPESA_CONSUMER_KEY`, `MPESA_CONSUMER_SECRET` | Daraja OAuth credentials. |
| `MPESA_SHORTCODE`, `MPESA_PASSKEY` | Daraja shortcode and STK password inputs. |
| `MPESA_CALLBACK_URL` | Public HTTPS callback endpoint; the application appends its token parameter. |
| `MPESA_CALLBACK_SECRET` | Private callback token appended to Daraja's callback URL and checked on receipt. |

When `DEBUG=False`, HTTPS redirection, secure session/CSRF cookies, and one-year HSTS are enabled. Set the Render `RENDER_EXTERNAL_HOSTNAME` or provide the host and origin lists explicitly.

## Tests and lint

Run the Django suite and linter:

```powershell
python manage.py test
ruff check Edulink eduapp manage.py
```

The `Django CI` GitHub Actions workflow installs dependencies, applies migrations, runs the test suite, and runs Ruff on pushes and pull requests.

## Mentor M-Pesa flow

1. A mentor application is validated and saved with `payment_pending` status.
2. The applicant enters a Kenyan mobile number. The site requests an STK push through Daraja using a cached OAuth token.
3. A transaction remains pending until Daraja's STK query confirms `ResultCode == 0` for the same checkout ID, or a valid callback confirms the amount and receipt.
4. The callback URL includes the configured secret token. Daraja callbacks are acknowledged, but malformed, mismatched, or duplicate callbacks do not approve a payment.
5. Only confirmed payments move the mentor application to `pending_review`.

Use the Safaricom sandbox before configuring production credentials. Do not mark a payment successful based only on the browser redirect or an unverified callback.

## Render deployment

The repository includes `render.yaml` for the staging web service and PostgreSQL database. Render builds static assets and applies database migrations on startup. Configure private service environment variables in the Render dashboard:

- Generate a new `SECRET_KEY`; do not copy it from a source file or local `.env`.
- Set `DEBUG` to `false` and verify the host and CSRF trusted origins.
- Set the Daraja environment and credentials, callback URL, and a random `MPESA_CALLBACK_SECRET`.
- For Daraja callbacks, ensure the callback URL is public HTTPS and that the secret is configured before initiating payments.

The existing Render staging service is not production. Confirm the deployment and environment settings before directing real users or money to it.
