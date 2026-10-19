# PayStream

A small payments API: order totals, idempotent charges, card gateway calls with retries, gateway webhooks, session tokens and user search.

> **Demo target for [RootTrace AI](https://github.com/apsariUdithara/RootTrace-AI).** PayStream is a fictional service with a fictional team. Each release tag `v1.4.1` to `v1.4.8` contains one realistic production bug that RootTrace investigates and fixes. `main` is healthy.

## Run the tests

```bash
pip install -r requirements.txt
pytest -q
```

## Layout

| Path | Contents |
|---|---|
| `src/paystream/` | The service: `payments/`, `auth/`, `users/`, `database/`, `settings.py`, `app.py` |
| `tests/` | Test suite |
| `docs/` | Architecture, database, payments, authentication, deployment and API docs |
| `deploy/production.env` | Production configuration (secret values are vault references) |
