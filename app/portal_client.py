"""All communication with the legacy Urja portal lives in this file.

Responsibilities: log in, keep the session cookie, call the portal's JSON
endpoints, detect an expired session, log in again (once) and retry, and
turn every failure into one of our own exceptions (see errors.py).
"""
import threading
from typing import Any
from urllib.parse import quote

import httpx

from app import config
from app.errors import (
    MeterNotFoundError,
    PortalAuthError,
    PortalConfigError,
    PortalTimeoutError,
    PortalUnavailableError,
    PortalUnexpectedResponseError,
)

SESSION_COOKIE_NAME = "__Secure-better-auth.session_token"
JSON_HEADERS = {"Accept": "application/json"}


class PortalClient:
    def __init__(
        self,
        base_url: str | None = None,
        username: str | None = None,
        password: str | None = None,
        timeout: float | None = None,
        transport: httpx.BaseTransport | None = None,
    ):
        # Arguments exist so tests can inject fake credentials and a mock
        # transport. In normal use everything comes from config.py.
        self.base_url = (base_url or config.PORTAL_BASE_URL).rstrip("/")
        self._username = config.PORTAL_USERNAME if username is None else username
        self._password = config.PORTAL_PASSWORD if password is None else password
        self._login_lock = threading.Lock()
        # ONE client for the whole life of the service: its cookie jar is
        # the session. follow_redirects=False so surprises are visible.
        self.client = httpx.Client(
            base_url=self.base_url,
            follow_redirects=False,
            timeout=config.REQUEST_TIMEOUT_SECONDS if timeout is None else timeout,
            transport=transport,
        )

    def close(self) -> None:
        self.client.close()

    # ------------------------------------------------------------ login
    def login(self) -> None:
        """Log in. httpx stores the session cookie in self.client.cookies."""
        if not self._username or not self._password:
            raise PortalConfigError("Portal credentials are not configured")

        with self._login_lock:
            response = self._send(
                "POST",
                "/login",
                data={"email": self._username, "password": self._password},
                headers={**JSON_HEADERS, "Origin": self.base_url},
            )
            self._check_login_response(response)

    def _check_login_response(self, response: httpx.Response) -> None:
        if response.status_code >= 500:
            raise PortalUnavailableError(f"Portal returned HTTP {response.status_code}")
        if response.status_code not in (200, 401):
            raise PortalUnexpectedResponseError(
                f"Unexpected login status: {response.status_code}"
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise PortalUnexpectedResponseError("Login response was not valid JSON") from exc
        if not isinstance(body, dict):
            raise PortalUnexpectedResponseError("Login response had an unexpected shape")

        # Observed with httpx: bad credentials come back as HTTP 200 with
        # {"type": "failure", "status": 401} in the body (DevTools showed a
        # real 401). So we check both the status and the body.
        if response.status_code == 401 or body.get("type") == "failure":
            raise PortalAuthError("Portal rejected the credentials")
        if body.get("type") != "redirect":
            raise PortalUnexpectedResponseError("Login response had an unexpected shape")
        # HTTP 200 alone proves nothing: also require the session cookie.
        if not self.has_session():
            raise PortalUnexpectedResponseError("Login succeeded but no session cookie was set")

    def has_session(self) -> bool:
        """True if the cookie jar holds a session cookie (value never read)."""
        return self.client.cookies.get(SESSION_COOKIE_NAME) is not None

    # ---------------------------------------------------- public methods
    def search_meters(self, q: str, page: int) -> dict[str, Any]:
        body = self._get_json("/portal/meters/search", {"q": q, "page": page})
        if not isinstance(body.get("data"), list):
            raise PortalUnexpectedResponseError("Meter search had no 'data' list")
        return body

    def get_geo(self, meter_id: str) -> dict[str, Any]:
        body = self._get_json(f"/portal/meters/{self._seg(meter_id)}/geo")
        if not isinstance(body.get("data"), dict):
            raise PortalUnexpectedResponseError("Geo response had no 'data' object")
        return body["data"]

    def get_energy(
        self, meter_id: str, date_from: str | None = None, date_to: str | None = None
    ) -> list[dict[str, Any]]:
        params = {}
        if date_from:
            params["from"] = date_from
        if date_to:
            params["to"] = date_to
        body = self._get_json(f"/portal/meters/{self._seg(meter_id)}/energy", params)
        if not isinstance(body.get("data"), list):
            raise PortalUnexpectedResponseError("Energy response had no 'data' list")
        return body["data"]

    # ---------------------------------------------------------- internals
    @staticmethod
    def _seg(value: str) -> str:
        """Make a value safe to place inside a URL path."""
        return quote(value, safe="")

    def _get_json(self, path: str, params: dict | None = None) -> dict[str, Any]:
        """GET a portal JSON endpoint, handling session expiry.

        The portal reports 'no valid session' as HTTP 200 with
        {"error": "unauthorized"}, so we must look inside the body.
        """
        if not self.has_session():
            self.login()

        for attempt in (1, 2):
            response = self._send("GET", path, params=params, headers=JSON_HEADERS)
            body = self._parse_json(response)

            error = body.get("error")
            if error == "unauthorized":
                if attempt == 1:
                    self.login()  # log in again, then retry exactly once
                    continue
                raise PortalAuthError("Portal still reports no valid session after re-login")
            if error == "not_found":
                raise MeterNotFoundError("Meter not found")
            if error is not None:
                raise PortalUnexpectedResponseError("Portal returned an unrecognised error")
            if response.status_code != 200:
                raise PortalUnexpectedResponseError(
                    f"Unexpected portal status: {response.status_code}"
                )
            return body
        raise PortalAuthError("Portal session could not be established")  # defensive

    def _send(self, method: str, path: str, **kwargs) -> httpx.Response:
        """One HTTP call; network failures become our own exceptions."""
        try:
            return self.client.request(method, path, **kwargs)
        except httpx.TimeoutException as exc:
            raise PortalTimeoutError("The portal timed out") from exc
        except httpx.RequestError as exc:
            # Only the exception TYPE is kept: never request details.
            raise PortalUnavailableError(
                f"Could not reach the portal ({type(exc).__name__})"
            ) from exc

    @staticmethod
    def _parse_json(response: httpx.Response) -> dict[str, Any]:
        if response.status_code >= 500:
            raise PortalUnavailableError(f"Portal returned HTTP {response.status_code}")
        try:
            body = response.json()
        except ValueError as exc:
            raise PortalUnexpectedResponseError("Portal response was not valid JSON") from exc
        if not isinstance(body, dict):
            raise PortalUnexpectedResponseError("Portal response was not a JSON object")
        return body
