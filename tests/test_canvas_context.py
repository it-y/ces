from app.canvas import context


def test_explicit_canvas_id_has_priority():
    context.set_last_opened_canvas("fallback")
    context.bind_canvas_client("client-1", "bound")
    assert context.resolve_canvas_id("explicit", "client-1") == "explicit"


def test_client_binding_has_priority_over_global_fallback():
    context.set_last_opened_canvas("fallback")
    context.bind_canvas_client("client-1", "bound")
    assert context.resolve_canvas_id("", "client-1") == "bound"


def test_legacy_request_uses_last_opened_canvas():
    context.set_last_opened_canvas("fallback")
    assert context.resolve_canvas_id(None, None) == "fallback"
