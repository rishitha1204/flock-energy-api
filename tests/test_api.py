import json
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from app.main import app, get_service
from app.portal_client import PortalClient
from app.service import MeterService
from tests.conftest import TEST_PASSWORD


def test_health():
    assert TestClient(app).get("/health").json() == {"status": "ok"}


def test_login_endpoint(api):
    response = api.post("/api/v1/auth/login")
    assert response.status_code == 200
    assert response.json() == {"authenticated": True}


def test_list_meters(api):
    response = api.get("/api/v1/meters", params={"page": 1})
    assert response.status_code == 200
    body = response.json()
    assert body["pagination"] == {"page": 1, "page_size": 20, "total": 3, "total_pages": 1}
    assert body["data"][0]["meter_id"] == "J100000"


def test_list_meters_bad_page(api):
    response = api.get("/api/v1/meters", params={"page": 0})
    assert response.status_code == 400
    assert response.json()["error"] == "validation_error"


def test_get_meter(api):
    body = api.get("/api/v1/meters/J100000").json()
    assert body["serial_no"] == "SE33962"
    assert set(body["location"]) == {"latitude", "longitude"}


def test_get_meter_not_found(api):
    response = api.get("/api/v1/meters/ZZZ999")
    assert response.status_code == 404
    assert response.json() == {"error": "meter_not_found", "message": "Meter not found."}


def test_invalid_meter_id_is_400(api):
    response = api.get("/api/v1/meters/bad%20id!")
    assert response.status_code == 400


def test_consumption(api):
    response = api.get("/api/v1/meters/J100000/consumption", params={"from": "2026-06-01", "to": "2026-06-02"})
    assert response.status_code == 200
    body = response.json()
    assert body["from"] == "2026-06-01" and body["to"] == "2026-06-02"
    assert body["count"] == 96
    assert body["units"] == {"kwh": "kWh", "kvah": "kVAh", "volt_r": "V"}
    assert body["data"][0]["timestamp"] == "2026-06-01T00:00:00"


def test_consumption_invalid_date(api):
    response = api.get("/api/v1/meters/J100000/consumption", params={"from": "invalid-date"})
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"


def test_consumption_reversed_range(api):
    response = api.get("/api/v1/meters/J100000/consumption", params={"from": "2026-06-10", "to": "2026-06-01"})
    assert response.status_code == 400


def test_consumption_meter_not_found(api):
    assert api.get("/api/v1/meters/ZZZ999/consumption").status_code == 404


def _api_with_transport(handler, username="tester@example.com", password=TEST_PASSWORD):
    portal = PortalClient(base_url="https://urja-ops.flockenergy.tech", username=username, password=password,
                          transport=httpx.MockTransport(handler))
    app.dependency_overrides[get_service] = lambda: MeterService(portal)
    return TestClient(app)


def test_upstream_timeout_is_504():
    def handler(request):
        raise httpx.ReadTimeout("slow")
    try:
        assert _api_with_transport(handler).get("/api/v1/meters").status_code == 504
    finally:
        app.dependency_overrides.clear()


def test_upstream_down_is_502():
    def handler(request):
        raise httpx.ConnectError("down")
    try:
        response = _api_with_transport(handler).get("/api/v1/meters")
        assert response.status_code == 502 and response.json()["error"] == "upstream_unavailable"
    finally:
        app.dependency_overrides.clear()


def test_bad_credentials_is_502_and_leaks_nothing(fake_portal):
    try:
        client = _api_with_transport(fake_portal.handler, password="wrong-password-test")
        response = client.get("/api/v1/meters")
        assert response.status_code == 502
        assert response.json()["error"] == "upstream_auth_failed"
        assert "wrong-password-test" not in response.text
    finally:
        app.dependency_overrides.clear()


def test_missing_credentials_is_503():
    try:
        response = _api_with_transport(lambda r: httpx.Response(200), username="", password="").get("/api/v1/meters")
        assert response.status_code == 503
    finally:
        app.dependency_overrides.clear()


def test_malformed_upstream_is_502(fake_portal):
    def handler(request):
        if request.url.path == "/login":
            return fake_portal.handler(request)
        return httpx.Response(200, text="<html>oops</html>")
    try:
        assert _api_with_transport(handler).get("/api/v1/meters").status_code == 502
    finally:
        app.dependency_overrides.clear()


def test_unexpected_exception_is_generic_500(service, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("secret internal detail")
    monkeypatch.setattr(MeterService, "list_meters", boom)
    app.dependency_overrides[get_service] = lambda: service
    try:
        response = TestClient(app, raise_server_exceptions=False).get("/api/v1/meters")
        assert response.status_code == 500
        assert "secret internal detail" not in response.text
    finally:
        app.dependency_overrides.clear()


def test_committed_openapi_matches_code():
    committed = json.loads((Path(__file__).resolve().parent.parent / "openapi.json").read_text(encoding="utf-8"))
    assert committed == app.openapi()
