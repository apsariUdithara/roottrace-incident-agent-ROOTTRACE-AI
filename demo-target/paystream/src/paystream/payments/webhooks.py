"""Handles events the card gateway sends to /webhooks/gateway."""


def handle_gateway_event(event: dict) -> dict:
    """Extract what we need from a gateway event.

    `metadata` is optional: the gateway omits it, or sends null, for charges
    created outside PayStream (e.g. from the gateway dashboard).
    """
    data = event["data"]
    metadata = data.get("metadata") or {}
    return {
        "type": event["type"],
        "payment_id": data["id"],
        "order_id": metadata.get("order_id"),
    }
