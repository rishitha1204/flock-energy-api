import httpx
import pytest

from app.errors import (
    MeterNotFoundError,
    PortalAuthError,
    PortalConfigError,
    PortalTimeoutError,
    PortalUnavailableError,
    PortalUnexpectedResponseError,
)
from app.portal_client import PortalClient
from tests.conftest import TEST_USER


def client_with(handler, **kwargs):
    options = {"username": TEST_USER, "password": "x", **kwargs}
    return PortalClient(base_url="https://urja-ops.flockenergy.tech", transport=httpx.MockTransport(handler), **options)


def test_login_success_stores_cookie(portal_client, fake_portal):
    portal_client.login()
    assert portal_client.has_session()
    assert fake_portal.login_calls == 1


@pytest.mark.parametrize("status", [200, 401])
def test_login_failure_both_shapes(portal_client, fake_portal, status):
    fake_portal.login_failure_status = status
    portal_client.client.cookies.clear()
    bad = PortalClient(
        base_url="https://urja-ops.flockenergy.tech", username=TEST_USER, password="wrong",
        transport=httpx.MockTransport(fake_portal.handler),
    )
    with pytest.raises(PortalAuthError):
        bad.login()
    assert not bad.has_session()


def test_missing_credentials():
    client = client_with(lambda r: httpx.Response(200), username="", password="")
    with pytest.raises(PortalConfigError):
        client.login()


def test_login_200_without_cookie_is_rejected():
    client = client_with(lambda r: httpx.Response(200, json={"type": "redirect", "status": 303}))
    with pytest.raises(PortalUnexpectedResponseError):
        client.login()


def test_first_call_logs_in_automatically(portal_client, fake_portal):
    body = portal_client.search_meters("", 1)
    assert body["total"] == 3
    assert fake_portal.login_calls == 1


def test_expired_session_relogs_in_once_and_retries(portal_client, fake_portal):
    portal_client.search_meters("", 1)
    fake_portal.expire_session()  # portal now answers 200 + unauthorized
    body = portal_client.search_meters("", 1)
    assert body["total"] == 3
    assert fake_portal.login_calls == 2


def test_still_unauthorized_after_relogin_stops(fake_portal):
    def handler(request):
        if request.url.path == "/login":
            return fake_portal.handler(request)
        return httpx.Response(200, json={"error": "unauthorized", "message": "A valid session is required."})

    client = client_with(handler, password="correct-horse-test")
    with pytest.raises(PortalAuthError):
        client.search_meters("", 1)
    assert fake_portal.login_calls == 2  # initial login + exactly one retry login


def test_meter_not_found(portal_client):
    with pytest.raises(MeterNotFoundError):
        portal_client.get_energy("ZZZ999")
    with pytest.raises(MeterNotFoundError):
        portal_client.get_geo("ZZZ999")


def test_geo_and_energy(portal_client):
    assert portal_client.get_geo("J100000")["latitude"] == "26.938961002479868"
    assert len(portal_client.get_energy("J100000")) == 144


def test_timeout_maps_to_timeout_error():
    def handler(request):
        raise httpx.ConnectTimeout("slow")

    with pytest.raises(PortalTimeoutError):
        client_with(handler).login()


def test_connection_failure_maps_to_unavailable():
    def handler(request):
        raise httpx.ConnectError("down")

    with pytest.raises(PortalUnavailableError):
        client_with(handler).login()


def test_5xx_maps_to_unavailable():
    with pytest.raises(PortalUnavailableError):
        client_with(lambda r: httpx.Response(503, text="maintenance")).login()


def test_non_json_response_is_unexpected(fake_portal):
    def handler(request):
        if request.url.path == "/login":
            return fake_portal.handler(request)
        return httpx.Response(200, text="<html>surprise</html>")

    with pytest.raises(PortalUnexpectedResponseError):
        client_with(handler, password="correct-horse-test").search_meters("", 1)


def test_missing_data_key_is_unexpected(fake_portal):
    def handler(request):
        if request.url.path == "/login":
            return fake_portal.handler(request)
        return httpx.Response(200, json={"hello": "world"})

    with pytest.raises(PortalUnexpectedResponseError):
        client_with(handler, password="correct-horse-test").search_meters("", 1)


def test_meter_id_cannot_escape_the_path(portal_client):
    # '/' is percent-encoded, so this stays inside one path segment.
    assert portal_client._seg("../login") == "..%2Flogin"
