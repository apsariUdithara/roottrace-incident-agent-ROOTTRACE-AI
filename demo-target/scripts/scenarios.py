"""The 8 seeded PayStream bugs, as data.

Each scenario is one "bad commit": a list of (file, old text, new text) edits
applied to the healthy service, plus the hidden test that must catch it.
Used by verify_scenarios.py now and by build_history.py later.
"""

SCENARIOS = [
    {
        "id": 1,
        "title": "Payment API 500s after deploy",
        "commit": "perf: raise DB pool size for peak traffic",
        "hidden_test": "test_scenario_1_pool.py",
        "edits": [("src/paystream/database/config.py",
                   'os.getenv("PAYSTREAM_DB_POOL_SIZE", "20")',
                   'os.getenv("PAYSTREAM_DB_POOL_SIZE", "100")')],
    },
    {
        "id": 2,
        "title": "Wrong totals on multi-item orders",
        "commit": "perf: simplify order total calculation",
        "hidden_test": "test_scenario_2_totals.py",
        "edits": [("src/paystream/payments/service.py",
                   '    total = sum((Decimal(str(i["price"])) * i["quantity"] for i in items), Decimal("0"))\n'
                   '    return total.quantize(CENT, rounding=ROUND_HALF_UP)\n',
                   '    return sum(float(i["price"]) * i["quantity"] for i in items)\n')],
    },
    {
        "id": 3,
        "title": "Users logged out early / 401 spike",
        "commit": "refactor: use utcnow for token expiry check",
        "hidden_test": "test_scenario_3_tokens.py",
        "edits": [("src/paystream/auth/tokens.py",
                   "    now = now or datetime.now(UTC)\n    return now >= token",
                   "    now = now or datetime.utcnow()\n    return now >= token")],
    },
    {
        "id": 4,
        "title": "Duplicate charges",
        "commit": "refactor: drop redundant lookup in create_charge",
        "hidden_test": "test_scenario_4_idempotency.py",
        "edits": [("src/paystream/payments/service.py",
                   "    if idempotency_key in store.by_key:\n"
                   "        return store.charges[store.by_key[idempotency_key]]\n",
                   "")],
    },
    {
        "id": 5,
        "title": "Webhook handler 500s",
        "commit": "refactor: tidy webhook metadata access",
        "hidden_test": "test_scenario_5_webhooks.py",
        "edits": [("src/paystream/payments/webhooks.py",
                   'metadata = data.get("metadata") or {}',
                   'metadata = data.get("metadata", {})')],
    },
    {
        "id": 6,
        "title": "Gateway retry storm",
        "commit": "perf: retry gateway calls immediately to cut checkout latency",
        "hidden_test": "test_scenario_6_backoff.py",
        "edits": [("src/paystream/payments/gateway.py",
                   "return [min(BACKOFF_CAP_SECONDS, BACKOFF_BASE_SECONDS * 2**i) for i in range(retries)]",
                   "return [0.0] * retries")],
    },
    {
        "id": 7,
        "title": "Config not loaded after rename",
        "commit": "chore: rename gateway key setting for consistency",
        "hidden_test": "test_scenario_7_settings.py",
        "edits": [("src/paystream/settings.py",
                   "PAYSTREAM_GATEWAY_API_KEY",
                   "PAYSTREAM_GATEWAY_KEY")],
    },
    {
        "id": 8,
        "title": "Slow user search / timeouts",
        "commit": "chore: drop duplicate indexes",
        "hidden_test": "test_scenario_8_index.py",
        "edits": [("src/paystream/database/migrations.py",
                   '    ("0004_index_payments_user", "CREATE INDEX ix_payments_user_id ON payments (user_id)"),\n]',
                   '    ("0004_index_payments_user", "CREATE INDEX ix_payments_user_id ON payments (user_id)"),\n'
                   '    ("0005_drop_duplicate_indexes", "DROP INDEX ix_users_name"),\n]')],
    },
]


def apply_edits(root, edits) -> None:
    """Apply a scenario's edits to a copy of PayStream at `root` (a pathlib.Path)."""
    for rel, old, new in edits:
        path = root / rel
        text = path.read_text(encoding="utf-8")
        if old not in text:
            raise ValueError(f"{rel}: text to replace not found: {old[:60]!r}")
        path.write_text(text.replace(old, new), encoding="utf-8")
