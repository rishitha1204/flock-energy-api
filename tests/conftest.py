"""Shared test setup: a fake portal that imitates the behaviour we OBSERVED.

Nothing here touches the real portal. Credentials and cookie values are
obviously fake test strings.
"""
from datetime import datetime, timedelta
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app, get_service
from app.portal_client import SESSION_COOKIE_NAME, PortalClient
from app.service import MeterService

TEST_USER = "tester@example.com"
TEST_PASSWORD = "correct-horse-test"

METERS = [
    {"meterId": "J100000", "serialNo": "SE33962", "make": "HPL", "phaseType": "single", "installStatus": "Decommissioned", "dtCode": "DT-001"},
    {"meterId": "J100001", "serialNo": "GE84132", "make": "L&T", "phaseType": "single", "installStatus": "Installed", "dtCode": "DT-002"},
    {"meterId": "J100005", "serialNo": "HP63682", "make": "Allied", "phaseType": "three", "installStatus": "Faulty", "dtCode": "DT-006"},
]


def make_energy_rows(days=("2026-06-01", "2026-06-02", "2026-06-03")):
    """30-minute rows as strings, in the portal's day-first format."""
    rows = []
    for day in days:
        start = datetime.strptime(day, "%Y-%m-%d")
        for i in range(48):
            ts = start + timedelta(minutes=30 * i)
            rows.append({
                "timestamp": ts.strftime("%d/%m/%Y %H:%M"),
                "kwh": f"{1000 + len(rows) * 0.25:.2f}",
                "kvah": f"{1100 + len(rows) * 0.25:.2f}",
                "voltR": "226",
            })
    return rows


class FakePortal:
    """Scripted stand-in for the portal. Use .handler as a MockTransport."""

    def __init__(self):
        self.valid_token = None
        self.token_counter = 0
        self.login_calls = 0
        self.energy_rows = make_energy_rows()
        self.login_failure_status = 200  # we saw 200+failure body with httpx

    def expire_session(self):
        self.valid_token = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/login":
            return self._login(request)
        if not self._has_valid_cookie(request):
            return httpx.Response(200, json={"error": "unauthorized", "message": "A valid session is required."})
        if path == "/portal/meters/search":
            return self._search(request)
        parts = path.strip("/").split("/")  # portal / meters / {id} / geo|energy
        if len(parts) == 4 and parts[1] == "meters":
            meter_id, kind = parts[2], parts[3]
            if meter_id not in {m["meterId"] for m in METERS}:
                return httpx.Response(200, json={"error": "not_found", "message": "Meter not found"})
            if kind == "geo":
                return httpx.Response(200, json={"data": {"latitude": "26.938961002479868", "longitude": "75.83095696146852"}})
            if kind == "energy":
                return httpx.Response(200, json={"data": self._filter_energy(request)})
        return httpx.Response(404, text="nope")

    def _login(self, request):
        self.login_calls += 1
        form = parse_qs(request.content.decode())
        if form.get("email") == [TEST_USER] and form.get("password") == [TEST_PASSWORD]:
            self.token_counter += 1
            self.valid_token = f"fake-session-{self.token_counter}"
            cookie = f"{SESSION_COOKIE_NAME}={self.valid_token}; Max-Age=3600; Path=/; HttpOnly; Secure; SameSite=Lax"
            return httpx.Response(200, json={"type": "redirect", "status": 303, "location": "/meters"}, headers={"set-cookie": cookie})
        return httpx.Response(self.login_failure_status, json={"type": "failure", "status": 401})

    def _has_valid_cookie(self, request):
        return self.valid_token is not None and f"{SESSION_COOKIE_NAME}={self.valid_token}" in request.headers.get("cookie", "")

    def _search(self, request):
        q = request.url.params.get("q", "").lower()
        page = int(request.url.params.get("page", "1"))
        hits = [m for m in METERS if q in m["meterId"].lower() or q in m["serialNo"].lower()]
        start = (page - 1) * 20
        return httpx.Response(200, json={"data": hits[start:start + 20], "total": len(hits), "page": page, "pageSize": 20})

    def _filter_energy(self, request):
        # The 'to' day being inclusive is this fake's choice, not a portal fact.
        d_from, d_to = request.url.params.get("from"), request.url.params.get("to")
        if not d_from and not d_to:
            return self.energy_rows
        rows = []
        for row in self.energy_rows:
            day = datetime.strptime(row["timestamp"], "%d/%m/%Y %H:%M").strftime("%Y-%m-%d")
            if (d_from and day < d_from) or (d_to and day > d_to):
                continue
            rows.append(row)
        return rows


@pytest.fixture
def fake_portal():
    return FakePortal()


@pytest.fixture
def portal_client(fake_portal):
    client = PortalClient(
        base_url="https://urja-ops.flockenergy.tech",
        username=TEST_USER,
        password=TEST_PASSWORD,
        transport=httpx.MockTransport(fake_portal.handler),
    )
    yield client
    client.close()


@pytest.fixture
def service(portal_client):
    return MeterService(portal_client)


@pytest.fixture
def api(service):
    app.dependency_overrides[get_service] = lambda: service
    yield TestClient(app)
    app.dependency_overrides.clear()
