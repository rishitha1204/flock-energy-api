# Interview notes

Each answer: **Concept** (plain language), then **Here** (how it applies to this project).
Where I could not verify something, I say so: say the same in the interview.

**60-second explanation.** The utility's portal has no API. I built a FastAPI service that logs in
to the portal, fetches meter lists, geo and consumption, cleans the data, and serves it as documented
REST JSON. It handles the cookie session, re-logs in once if the session dies, validates input the
portal ignores, and maps failures to sensible HTTP codes. Tests use a mocked portal.

## 1-2. Project and architecture
**Concept:** an API wrapper (adapter) hides an awkward system behind a clean interface.
**Here:** routes (main.py) -> service (service.py) -> PortalClient (portal_client.py) -> portal. Each layer has one job: HTTP, rules/cleaning, portal I/O.

## 3. Why FastAPI?
**Concept:** a Python web framework that validates input from type hints and generates OpenAPI docs.
**Here:** the brief needs `openapi.json` and `/docs`; FastAPI gives both from the same code, plus Pydantic models.

## 4. Why httpx?
**Concept:** an HTTP client library. **Here:** `httpx.Client` keeps cookies between calls, supports timeouts and `MockTransport` (used in all tests).

## 5. Why a persistent `httpx.Client`?
**Concept:** a client object owns a cookie jar and connection pool. **Here:** the jar *is* our login session; a new client per request would lose it and log in every time.

## 6. Why BeautifulSoup is in requirements but unused
**Honest answer:** the portal's data came from JSON endpoints, so no HTML parsing was needed. It is only relevant if I parse HTML later. (I could drop it from requirements.)

## 7. Why this structure?
Separation of concerns: I can test parsing without HTTP, and swap how the portal is read without touching the routes.

## 8-9. Authentication and cookies
**Concept:** a session cookie is a ticket the server gives after login; the client must send it back. **Here:** `POST /login` with form fields `email`/`password`, plus `Origin` and `Accept` headers (copied from the browser; I did not test whether they are required). Success = HTTP 200 + `type: "redirect"` + the cookie `__Secure-better-auth.session_token` present. I never read or log the value.

## 10. Why not manage the cookie manually?
Manual copying is fragile, risks leaking it into logs, and ignores attributes (Secure, expiry). httpx's jar does it correctly.

## 11. Session expiry
**Concept:** sessions are temporary. **Here:** cookie `Max-Age=3600`. The portal signals a bad session with HTTP **200** and `{"error":"unauthorized"}`, so `_get_json` inspects the body, logs in again, and retries. Unverified: I only saw the signed-out version.

## 12. Why retry only once?
If the second attempt is still unauthorized the problem isn't an expired session (maybe bad credentials), and looping would hammer the portal.

## 13-16. Pagination, search, geo, consumption
Page size is fixed at 20 (the portal ignored `pageSize=5`); we compute `total_pages`. Search passes `q` through. Geo gives string coordinates, converted to floats. Consumption rows are strings with day-first timestamps; we convert to floats and ISO datetimes, sorted oldest-first.

## 17-18. Why validate dates ourselves?
The portal silently ignores `from=invalid-date`, so a typo would return the wrong data with no warning. We require `YYYY-MM-DD` and reject `from > to` with 400 (the portal just returns an empty list).

## 19. Invalid meters
Portal answers `{"error":"not_found"}` (HTTP status not verified), so we key off the body and return 404. Meter ids are also restricted by regex and URL-encoded before use in a path.

## 20. Upstream error mapping
Timeout -> 504; unreachable/5xx -> 502; bad/unparseable response -> 502; rejected credentials -> 502 (it is *our* configuration, not the caller's fault); missing config -> 503. Messages are fixed strings.

## 21-22. FastAPI validation and Pydantic
Type hints and `Query(ge=1)`/`Path(pattern=...)` reject bad input before our code runs. We convert FastAPI's 422 to 400 with a custom handler. Pydantic models define response shapes, validate the portal's data, and feed OpenAPI.

## 23. Why mock the portal in tests?
Fast, repeatable, no credentials, can simulate failures (timeouts, expiry) on demand, and does not hit a live system. Cost: the mock only knows what I believe, so a manual live check is still needed.

## 24-26. Legacy integration, wrapper, REST vs portal API
Legacy integration = making an old system usable by new ones. A wrapper translates. The portal's internal JSON is shaped for its own frontend (camelCase, strings, 200-for-errors); ours is designed for consumers (snake_case, numbers, proper status codes).

## 27. Security
Credentials in `.env` only (git-ignored); no secrets in logs/errors; cookie never read; path inputs validated; read-only GETs (plus the login POST). Our API itself is unauthenticated: a known gap.

## 28. What is NOT confirmed
Whether `Origin`/`Accept` are needed; sliding vs fixed expiry; natural expiry response; upstream status for not_found; whether search is substring; whether `to` includes its whole day; timezone; energy row count; meter detail fields from `__data.json`.

## 29-30. Likely questions and honest answers
1. **"Why is bad-password a 502, not 401?"** The caller isn't wrong, our server's portal login is; 401 would blame the caller.
2. **"What if two requests hit expiry at once?"** Both may log in; a lock serialises the login calls but one is redundant. Harmless, not optimal; I'd recheck the session inside the lock.
3. **"Biggest challenge?"** Login: DevTools said 401, httpx saw 200+body failure. I isolated it with a bare request.
4. **"Biggest mistake?"** Trusting the DevTools status code.
5. **"Would this scale?"** One shared session, sync threads, no cache: fine for a few users; for more I'd add caching, a session pool, rate limits.
6. **"Why no hierarchy?"** I didn't investigate the Transformers page; I'd rather ship a verified core.
7. **"Why is detail thin?"** The extra fields live only in SvelteKit's `__data.json`, an internal format I haven't parsed.
8. **"How do you know it works on the live portal?"** Automated tests are mocked; I did a manual live run (do this and state the real result).
