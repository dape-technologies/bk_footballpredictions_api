# BK Football Predictions — API

Django REST Framework API for customer accounts, packages, manual subscriptions, gated predictions, results, testimonials, and owner operations.

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

Premium response fields are removed by the API unless the requester owns an active, unexpired subscription to the prediction's package.
