"""Generate one day of structured PayStream logs per scenario. Deterministic and free.

Each file covers 60 minutes before and 60 minutes after the bad deploy: normal
traffic with some unrelated noise, plus the scenario's own symptoms starting a
few minutes after the deploy.

Run from the repo root: python demo-target/scripts/make_logs.py
Writes demo-target/fixtures/logs/<date>.jsonl
"""
import json
import random
from datetime import datetime, timedelta
from pathlib import Path

from scenarios import SCENARIOS

OUT = Path(__file__).resolve().parents[1] / "fixtures" / "logs"
ROUTES = ["/orders/total", "/payments", "/webhooks/gateway", "/users/search", "/health"]


def line(ts, level, route, status, latency, message, error_type=None, rng=None, trace=None):
    return {
        "ts": ts.isoformat(timespec="milliseconds") + "Z",
        "level": level,
        "service": "paystream-api",
        "instance": f"api-{rng.randint(1, 5)}",
        "route": route,
        "status": status,
        "latency_ms": latency,
        "message": message,
        "error_type": error_type,
        "trace_id": trace or f"{rng.getrandbits(64):016x}",
    }


def normal(ts, rng):
    route = rng.choices(ROUTES, weights=[30, 25, 15, 10, 20])[0]
    roll = rng.random()
    if roll < 0.01:
        return line(ts, "WARN", route, 400, rng.randint(5, 20), "request validation failed: missing field", "RequestValidationError", rng)
    if roll < 0.015:
        return line(ts, "WARN", "/health", 200, rng.randint(800, 1500), "slow health check", None, rng)
    return line(ts, "INFO", route, 200, rng.randint(15, 180), "request completed", None, rng)


def symptom(sid, ts, rng):
    """Zero or more log lines showing this scenario's problem at time ts."""
    if sid == 1 and rng.random() < 0.22:
        return [line(ts, "ERROR", "/payments", 500, rng.randint(5000, 5040),
                     "OperationalError: too many connections (FATAL: sorry, too many clients already); "
                     "pool wait exceeded DB_POOL_TIMEOUT_SECONDS=5", "OperationalError", rng)]
    if sid == 2 and rng.random() < 0.15:
        a, b = rng.choice([("0.10", "0.20"), ("1.10", "2.20"), ("0.70", "0.10")])
        bad = float(a) + float(b)
        return [line(ts, "WARN", "/orders/total", 200, rng.randint(20, 60),
                     f"reconciliation mismatch: order total {bad!r} does not match gateway amount "
                     f"{(float(a) * 100 + float(b) * 100) / 100:.2f}", "ReconciliationMismatch", rng)]
    if sid == 3 and rng.random() < 0.6:
        route = rng.choice(["/payments", "/orders/total", "/users/search"])
        return [line(ts, "WARN", route, 401, rng.randint(3, 10),
                     "token rejected: can't compare offset-naive and offset-aware datetimes", "InvalidToken", rng)]
    if sid == 4 and rng.random() < 0.12:
        order = f"ord_{rng.randint(10000, 99999)}"
        trace = f"{rng.getrandbits(64):016x}"
        first, second = f"ch_{rng.getrandbits(48):012x}", f"ch_{rng.getrandbits(48):012x}"
        return [
            line(ts, "INFO", "/payments", 200, rng.randint(80, 200), f"charge created {first} idempotency_key={order}", None, rng, trace),
            line(ts + timedelta(seconds=2), "INFO", "/payments", 200, rng.randint(80, 200), f"charge created {second} idempotency_key={order}", None, rng, trace),
            line(ts + timedelta(minutes=3), "WARN", "/payments", 200, 0,
                 f"reconciliation: duplicate charges {first}, {second} for {order}", "DuplicateCharge", rng),
        ]
    if sid == 5 and rng.random() < 0.1:
        return [line(ts, "ERROR", "/webhooks/gateway", 500, rng.randint(5, 15),
                     "AttributeError: 'NoneType' object has no attribute 'get' "
                     "(payments/webhooks.py in handle_gateway_event; event charge.refunded from dashboard)",
                     "AttributeError", rng)]
    if sid == 6 and rng.random() < 0.08:
        trace = f"{rng.getrandbits(64):016x}"
        burst = [line(ts + timedelta(milliseconds=3 * i), "WARN", "/payments", 200, 2,
                      f"gateway unavailable, retrying (attempt {i + 1}/5) wait=0.0s", "GatewayUnavailable", rng, trace)
                 for i in range(5)]
        return burst + [line(ts + timedelta(milliseconds=20), "ERROR", "/payments", 502, 21,
                             "gateway returned 429 Too Many Requests: merchant rate limit exceeded", "GatewayRateLimited", rng, trace)]
    if sid == 7 and rng.random() < 0.25:
        return [line(ts, "ERROR", "/payments", 500, rng.randint(2, 6),
                     "MissingSetting: environment variable PAYSTREAM_GATEWAY_KEY is not set", "MissingSetting", rng)]
    if sid == 8 and rng.random() < 0.3:
        latency = rng.randint(4000, 9500)
        status, level = (504, "ERROR") if latency > 8000 else (200, "WARN")
        return [line(ts, level, "/users/search", status, latency,
                     f"slow query {latency}ms: SELECT id, email, name FROM users WHERE name = ? (full table scan)",
                     "SlowQuery", rng)]
    return []


def make(scenario):
    rng = random.Random(scenario["id"])
    deploy = datetime.fromisoformat(scenario["deploy_at"])
    ts, end, out = deploy - timedelta(minutes=60), deploy + timedelta(minutes=60), []
    out.append(line(deploy, "INFO", "-", 0, 0, f"deploy started: release {scenario['release']} (rolling, 5 instances)", "Deploy", rng))
    out.append(line(deploy + timedelta(minutes=10), "INFO", "-", 0, 0, f"deploy finished: release {scenario['release']}", "Deploy", rng))
    while ts < end:
        out.append(normal(ts, rng))
        if ts > deploy + timedelta(minutes=6):
            out.extend(symptom(scenario["id"], ts, rng))
        ts += timedelta(seconds=rng.randint(4, 12))
    out.sort(key=lambda r: r["ts"])
    return out


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for s in SCENARIOS:
        rows = make(s)
        path = OUT / f"{s['deploy_at'][:10]}.jsonl"
        path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        errors = sum(r["level"] != "INFO" for r in rows)
        print(f"scenario {s['id']}: {path.name}  {len(rows)} lines, {errors} warnings/errors")
