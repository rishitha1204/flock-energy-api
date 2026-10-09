# Study guide

## File by file

### app/config.py
**Does:** loads `.env` and exposes settings. **Why:** no hardcoded secrets. **Key line:** `Path(__file__).resolve().parent.parent / ".env"` builds an absolute path so it works from any folder. **Connects to:** read by portal_client.py. **Likely question:** "Why an explicit path?" Default lookup failed on my machine; an explicit path removes guessing.

### app/errors.py
**Does:** our exception types. **Why:** each failure becomes a specific HTTP code in one place (main.py). **Key idea:** `PortalTimeoutError` subclasses `PortalUnavailableError`, so handlers must check the timeout first.

### app/portal_client.py
**Does:** all talking to the portal. **Key parts:** `login()` (form POST, checks status AND body AND cookie); `_get_json()` (login if no cookie, detect `{"error":"unauthorized"}`, re-login, retry once); `_send()` (turns network errors into our exceptions). **Example:** `search_meters("", 1)` -> `{"data":[...],"total":403,...}`. **Likely questions:** why a lock; why `follow_redirects=False`; why check the cookie exists after login.

### app/models.py
**Does:** Pydantic models. `UpstreamMeter` mirrors the portal (camelCase aliases); `Meter`, `MeterListResponse`, `Reading`, `ConsumptionResponse` are our public shapes. **Why:** validation, documentation, stable output. **Key line:** `Field(alias="from")` lets the JSON key be `from` although `from` is a Python keyword.

### app/service.py
**Does:** the rules. `to_float`, `parse_energy_timestamp` (`%d/%m/%Y %H:%M`), `parse_iso_date`, `MeterService.list_meters/get_meter/get_consumption`. **Why:** keeps cleaning out of routes and out of the HTTP client. **Example:** `"48438.74"` -> `48438.74`; `"23/06/2026 23:30"` -> `2026-06-23T23:30:00`.

### app/main.py
**Does:** routes, the lifespan (create/close the shared `PortalClient`), exception handlers, custom OpenAPI. **Key ideas:** `Depends(get_service)` supplies the service (tests override it); handlers return fixed messages so nothing leaks.

### tests/
`conftest.py` has `FakePortal`, a scripted imitation of the observed portal (including 200+unauthorized and a per-login session token). Other files test client, service and API. `test_committed_openapi_matches_code` stops openapi.json drifting.

## Request flow (beginner version)

1. **User** calls `GET /api/v1/meters/J100000/consumption?from=2026-06-01`.
2. **FastAPI** checks the id pattern; bad input -> 400.
3. **Route** calls `MeterService.get_consumption`.
4. **Service** validates dates (YYYY-MM-DD, from <= to), asks the client.
5. **PortalClient** has no session cookie yet, so it **logs in** (`POST /login`); httpx stores the cookie.
6. It calls `GET /portal/meters/J100000/energy?from=2026-06-01`. If the body says `unauthorized`, it logs in again once and retries.
7. **Legacy portal** returns `{"data":[{"timestamp":"01/06/2026 00:00","kwh":"...",...}]}` (all strings).
8. **Service** parses and normalises: floats, datetimes, sorted.
9. **Pydantic** builds `ConsumptionResponse`; FastAPI serialises to JSON for the **user**.
If anything fails, an exception travels up and `main.py` turns it into `{"error","message"}` with the right status.

## What I must be able to explain without Claude

* Why the portal needs a session, and how the cookie jar keeps it.
* Why HTTP 200 does not mean success here (login and "unauthorized").
* The retry-once logic and why it stops.
* Why dates are validated by us, and the day-first vs ISO mismatch.
* The error-to-status mapping, and why bad credentials are 502.
* Why tests use a mock, and what the mock cannot prove.
* What is unverified (list in INTERVIEW_NOTES #28) and why meter detail is thin.
* How to run it and the one live check you still have to do.
