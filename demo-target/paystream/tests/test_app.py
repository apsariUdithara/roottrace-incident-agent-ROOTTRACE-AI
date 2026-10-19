from decimal import Decimal

from fastapi.testclient import TestClient

from paystream.app import app

client = TestClient(app)


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


def test_order_total_endpoint():
    resp = client.post("/orders/total", json=[{"price": "4.50", "quantity": 2}])
    assert Decimal(resp.json()["total"]) == Decimal("9.00")


def test_payment_endpoint():
    resp = client.post("/payments", json={"amount": "5.00"}, headers={"Idempotency-Key": "abc"})
    assert resp.status_code == 200
    assert resp.json()["amount"] == "5.00"
