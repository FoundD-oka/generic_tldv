from fastapi.testclient import TestClient


def test_health_reports_loading_before_model_ready(load_app):
    main = load_app()
    main.model_holder["model"] = None
    client = TestClient(main.app)

    resp = client.get("/health")

    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "loading"
    assert body["model"] == "ecapa-tdnn"


def test_health_reports_healthy_once_model_ready(load_app):
    main = load_app()
    main.model_holder["model"] = object()  # any non-None sentinel
    client = TestClient(main.app)

    resp = client.get("/health")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "healthy"
    assert body["model"] == "ecapa-tdnn"
