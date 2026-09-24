# BK Football Predictions — API

Django REST Framework API for customer accounts, time-limited package sales, permanent purchases, Relworx mobile-money payments, gated predictions, results, testimonials, and owner operations.

## Run locally

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py seed_demo
.\.venv\Scripts\python.exe manage.py runserver
```

The deterministic development seed creates this local-only owner account:

- Phone: `0700000000`
- Password: `BKowner2026!`

Change production credentials and never run the demo seed in production.

## Verification

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py test
```

Premium response fields are removed by the API unless the requester owns a verified paid purchase for the prediction's package. Package codes and links are snapshotted when Relworx confirms payment and remain available permanently.

## Relworx configuration

Copy the values from `.env.example` into the deployment environment. Configure the exact HTTPS value of `RELWORX_WEBHOOK_URL` as the Request Payment Webhook in the matching Relworx business account. A browser response never unlocks a package; only a valid signed webhook can complete a purchase.

Customer purchase endpoints are under `/api/v1/me/purchases/`. Relworx sends payment updates to `/api/v1/payments/relworx/webhook/`.

## Accounts and access

Registration accepts `first_name`, `surname`, `date_of_birth`, `phone`, `password`, and `password_confirm`. The API rejects anyone who has not reached their eighteenth birthday. Phone numbers are normalized before the database-level uniqueness check, and both password fields must match. Login accepts only `phone` and `password`. Browser authentication uses an HTTP-only Django session with CSRF protection; passwords are stored with Django's password hasher and are never returned by the API.

The current privileged role is **Product Owner**. Superusers have this role automatically, and non-superusers can be assigned to the Django `Product Owner` group. Other staff groups can be added later without receiving owner-control-room access by default.

The owner activity API is available at `GET /api/v1/owner/activities/`. It records authentication events and important account, purchase, payment, content, and administrative changes. The same read-only audit trail is available in Django Admin.

For local development, Django trusts `http://localhost:5173` and
`http://127.0.0.1:5173` as CSRF origins. In another environment, set
`CSRF_TRUSTED_ORIGINS` to a comma-separated list of complete frontend origins,
including `https://` in production. Keep `VITE_API_BASE_URL=/api` when using the
Vite proxy so session and CSRF cookies remain same-origin from the browser's
perspective.
