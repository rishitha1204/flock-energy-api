# Flock Energy Urja Meter Ops API

## Overview

The Urja Meter Ops portal is a legacy web app with no API. This project is a small
**FastAPI service** that logs in to the portal on your behalf, reads its data, cleans it
(strings to numbers, day-first timestamps to ISO) and serves it as documented REST JSON.
Callers never touch the portal or its credentials.

Status: all tests pass against a **mocked** portal. A full run of every endpoint against the live portal is listed under [Testing](#testing) as a manual check.

## Architecture

```
caller -> FastAPI routes (app/main.py) -> MeterService (app/service.py)
       -> PortalClient (app/portal_client.py) -> httpx session -> legacy portal
```

* **main.py**: routes, validation, error-to-HTTP mapping, OpenAPI.
* **service.py**: input validation (dates), normalisation, building responses.
* **portal_client.py**: *all* portal I/O: login, cookie session, expiry detection, retry-once.
* **models.py**: Pydantic models (portal shapes and our public shapes).
* **errors.py**: our exceptions. **config.py**: environment settings.

## Project Structure

```
app/            main.py service.py portal_client.py models.py errors.py config.py
tests/          conftest.py (fake portal) test_portal_client.py test_service.py test_api.py
docs/           STUDY_GUIDE.md
PROTOCOL.md     how the portal really behaves
openapi.json    exported API description
REFLECTION.md   INTERVIEW_NOTES.md
```

## Requirements

Python 3.10+. Packages are in `requirements.txt`.

## Installation

```
python -m venv .venv
.venv\Scripts\activate          (Windows cmd)     # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env          # macOS/Linux: cp .env.example .env
```

## Configuration

Edit `.env` (never committed):

| Variable | Meaning |
|---|---|
| `PORTAL_BASE_URL` | Portal URL (default `https://urja-ops.flockenergy.tech`) |
| `PORTAL_USERNAME` | Portal login email (from the assignment email) |
| `PORTAL_PASSWORD` | Portal password (from the assignment email) |
| `PORTAL_TIMEOUT_SECONDS` | Optional, default 30 |

## Running Locally

```
uvicorn app.main:app --reload
```

## API Documentation

Interactive Swagger UI: <http://127.0.0.1:8000/docs> - schema: <http://127.0.0.1:8000/openapi.json>.
The checked-in [`openapi.json`](openapi.json) is generated from the code; a test fails if they drift apart.
Regenerate it with:

```
python -c "import json; from app.main import app; open('openapi.json','w',encoding='utf-8').write(json.dumps(app.openapi(), indent=2)+'\n')"
```

## Endpoints

| Method and path | Purpose |
|---|---|
| `GET /health` | Service is up (does not call the portal) |
| `POST /api/v1/auth/login` | Force a portal (re)login. No body: credentials stay on the server |
| `GET /api/v1/meters?q=&page=1` | List meters, 20 per page (`q` searches the portal) |
| `GET /api/v1/meters/{meter_id}` | Meter fields plus coordinates |
| `GET /api/v1/meters/{meter_id}/consumption?from=YYYY-MM-DD&to=YYYY-MM-DD` | Half-hourly readings, oldest first |

No hierarchy endpoint: see [Intentional Omissions](#intentional-omissions).

## Example Requests

The JSON below shows the response *format* our code produces, using values from the portal
samples recorded in PROTOCOL.md. They are illustrative, not pasted from a live run.

```
curl "http://127.0.0.1:8000/api/v1/meters?page=1"
```
```json
{"data":[{"meter_id":"J100000","serial_no":"SE33962","make":"HPL","phase_type":"single","install_status":"Decommissioned","dt_code":"DT-001"}],
 "pagination":{"page":1,"page_size":20,"total":403,"total_pages":21}}
```

```
curl "http://127.0.0.1:8000/api/v1/meters/J100000"
```
```json
{"meter_id":"J100000","serial_no":"SE33962","make":"HPL","phase_type":"single","install_status":"Decommissioned","dt_code":"DT-001",
 "location":{"latitude":26.938961002479868,"longitude":75.83095696146852}}
```

```
curl "http://127.0.0.1:8000/api/v1/meters/J100000/consumption?from=2026-06-01&to=2026-06-02"
```
```json
{"meter_id":"J100000","from":"2026-06-01","to":"2026-06-02","count":96,
 "units":{"kwh":"kWh","kvah":"kVAh","volt_r":"V"},
 "data":[{"timestamp":"2026-06-01T00:00:00","kwh":1234.5,"kvah":1300.25,"volt_r":226.0}]}
```
(The numbers inside `data` are placeholders to show the types.)

## Authentication

Callers of *this* API need no authentication (a deliberate scope decision, see trade-offs).
The service logs in to the portal itself with `.env` credentials, keeps the session cookie in one
`httpx.Client`, and logs in again automatically once if the portal says the session is invalid.

## Error Handling

Every error is `{"error": "<code>", "message": "<text>"}`. Messages are fixed strings: no
passwords, cookies, stack traces or upstream details.

| Status | `error` | When |
|---|---|---|
| 400 | `validation_error` / `invalid_request` | Bad page, bad meter id, bad date, `from` > `to` |
| 404 | `meter_not_found` | Portal says the meter does not exist |
| 502 | `upstream_auth_failed` | Portal rejected our credentials, or still unauthorized after re-login |
| 502 | `upstream_unavailable` / `upstream_unexpected_response` | Portal unreachable, 5xx, or an answer we cannot parse |
| 503 | `not_configured` | Credentials missing in `.env` |
| 504 | `upstream_timeout` | Portal too slow |
| 500 | `internal_error` | Anything unexpected (details only in the server log) |

## Testing

```
pytest
```

55 tests run against a **fake portal** (`httpx.MockTransport`), so they are fast, repeatable and
need no credentials. They cover login success/failure (both response shapes seen), expiry and
retry-once, search, empty search, not found, geo, energy, date validation, timeouts, malformed
responses, error mapping and that the committed `openapi.json` matches the code.

Limitation: the fake encodes *my understanding* of the portal. **Manual live check** (do this once
with real credentials in `.env`): start the server, then call `/api/v1/auth/login`, `/api/v1/meters`,
`/api/v1/meters/J100000`, `/api/v1/meters/J100000/consumption?from=2026-06-01&to=2026-06-02`,
`/api/v1/meters/ZZZ999`, and confirm the shapes match PROTOCOL.md.

## Assumptions

* The session expires like a signed-out session does (200 + `unauthorized` body). UNVERIFIED.
* Meter ids look like `J100000`; the id pattern allows letters, digits, `-`, `_` (max 32).
* Energy timestamps are local portal time; no timezone is stated, so we return naive ISO datetimes.
* Units kWh / kVAh / V come from the UI column headers.
* Search by id returns the exact meter on page 1.

## Design Decisions and Trade-offs

* **Portal credentials stay server-side**; `POST /auth/login` takes no body.
* **One persistent `httpx.Client`**: its cookie jar *is* the session. We never copy the cookie.
* **Body-based auth detection** because the portal returns 200 for "unauthorized".
* **Retry once, then fail**: avoids infinite loops if credentials are wrong.
* **We do not forward `pageSize`**: the portal ignores it, so exposing it would mislead.
* **We validate dates ourselves**: the portal silently ignores bad dates.
* **Sync routes**: FastAPI runs them in a thread pool; simple and fine for this size.
* **Strict parsing**: one unreadable energy row fails the request (502) rather than being silently dropped.
* **Validation errors are 400, not FastAPI's default 422**, and the OpenAPI schema is adjusted to match.
* **Meter detail uses search + geo** because the only source for the other detail fields is SvelteKit's `__data.json` (internal page data, not a REST API).

## Intentional Omissions

* Extra detail fields (Installation Type, Zone, Circle, Division, Subdivision, Sub Station, Feeder): need the raw `__data.json` format examined first.
* Network hierarchy (Transformers page not investigated), caching, local index, web client.
* Authentication on our own API, rate limiting, Docker, a live integration test suite.

## Future Improvements

Parse `__data.json` for full meter detail; investigate the Transformers page for a hierarchy
endpoint; cache meter lists with a short TTL; add pagination or streaming for large energy ranges;
a marked live integration test; per-caller auth and rate limits; structured logging.

## Protocol Documentation

See [PROTOCOL.md](PROTOCOL.md) (confirmed vs UNVERIFIED behaviour).

## OpenAPI

See [openapi.json](openapi.json) and `/docs` when running.

## Reflection

See [REFLECTION.md](REFLECTION.md). Interview prep notes: [INTERVIEW_NOTES.md](INTERVIEW_NOTES.md), [docs/STUDY_GUIDE.md](docs/STUDY_GUIDE.md).
