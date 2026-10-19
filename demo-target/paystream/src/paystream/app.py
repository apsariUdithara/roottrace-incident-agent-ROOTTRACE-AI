"""PayStream HTTP API."""
from decimal import Decimal

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from paystream.payments.service import ChargeStore, calculate_total, create_charge
from paystream.payments.webhooks import handle_gateway_event

app = FastAPI(title="PayStream", version="1.4.0")
charges = ChargeStore()


class Item(BaseModel):
    price: str
    quantity: int


class ChargeRequest(BaseModel):
    amount: str
    currency: str = "USD"


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/orders/total")
def order_total(items: list[Item]) -> dict:
    return {"total": str(calculate_total([i.model_dump() for i in items]))}


@app.post("/payments")
def create_payment(req: ChargeRequest, idempotency_key: str = Header(...)) -> dict:
    charge = create_charge(charges, Decimal(req.amount), req.currency, idempotency_key)
    return {"id": charge.id, "amount": str(charge.amount), "currency": charge.currency}


@app.post("/webhooks/gateway")
def gateway_webhook(event: dict) -> dict:
    try:
        return handle_gateway_event(event)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=f"missing field {exc}") from exc
