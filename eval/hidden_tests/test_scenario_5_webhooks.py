from paystream.payments.webhooks import handle_gateway_event


def test_event_without_metadata_is_handled():
    event = {"type": "charge.succeeded", "data": {"id": "ch_2", "metadata": None}}
    assert handle_gateway_event(event)["order_id"] is None
