from paystream.payments.webhooks import handle_gateway_event


def test_event_with_order_metadata():
    event = {"type": "charge.succeeded", "data": {"id": "ch_1", "metadata": {"order_id": "ord_9"}}}
    assert handle_gateway_event(event) == {"type": "charge.succeeded", "payment_id": "ch_1", "order_id": "ord_9"}
