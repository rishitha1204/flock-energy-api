# PROTOCOL.md - how the Urja Meter Ops portal actually works

Everything here was observed by me in Chrome DevTools or by calling the portal
with httpx. Items I did **not** verify are marked **UNVERIFIED**.
Portal: `https://urja-ops.flockenergy.tech` (a SvelteKit app, judging by the
`_app/immutable/...` bundles and `__data.json` URLs).

## 1. Authentication

| | |
|---|---|
| Request | `POST /login` |
| Body | `application/x-www-form-urlencoded`, fields `email` and `password` (the field is `email`, not `username`) |
| Hidden form fields / CSRF token | None in the login form HTML |
| Headers the browser sent | `Origin: https://urja-ops.flockenergy.tech`, `Accept: application/json` |
| Success | HTTP 200, body `{"type":"redirect","status":303,"location":"/meters"}` (the 303 is *inside* the JSON, not a real redirect) |
| Session cookie | `__Secure-better-auth.session_token`, `Max-Age=3600; Path=/; HttpOnly; Secure; SameSite=Lax` |
| No token in the body | Authentication is cookie based |

**Bad credentials.** DevTools showed HTTP 401 with `{"type":"failure","status":401,"data":"..."}`.
Calling the same request with httpx returned **HTTP 200** with `type: "failure"` and `status: 401` in the body.
So a client must read the body, not only the HTTP status. The `data` field is a string in SvelteKit's compact format; I do not decode it.

**UNVERIFIED:** whether `Origin` and `Accept` are actually required (I send both to match the browser; I never tested without them).

## 2. Session behaviour

* The cookie has `Max-Age=3600` (1 hour). **UNVERIFIED:** whether activity extends it (sliding) or it is fixed from login.
* After signing out, `GET /portal/meters/search?q=&page=1` returned **HTTP 200** with
  `{"error":"unauthorized","message":"A valid session is required."}`. No 401. A client must inspect the JSON body.
* This was observed for a *signed-out* session. I did not observe a naturally *expired* one (would take an hour); I assume it looks the same. **UNVERIFIED.**

## 3. Meter list

`GET /portal/meters/search?q=<text>&page=<n>` returns JSON:

```json
{"data": [{"meterId":"J100000","serialNo":"SE33962","make":"HPL","phaseType":"single","installStatus":"Decommissioned","dtCode":"DT-001"}],
 "total": 403, "page": 1, "pageSize": 20}
```

* Pages 1 and 3 were confirmed. 403 meters / 20 per page.
* `?pageSize=5` was **ignored**: still 20 records and `pageSize: 20`. Page size is fixed.
* `q=J100000` returned exactly one meter (`total: 1`). `q=ZZZ999` returned `data: []`, `total: 0`.
* **UNVERIFIED:** whether `q` is an exact or substring match, which fields it searches (the UI says "meter number or serial"), behaviour for a page past the end, result ordering.
* Observed value styles: `phaseType` is a lowercase word (`single`, `three`); `installStatus` values seen: `Decommissioned`, `Installed`, `Faulty`; serials can contain `&` (e.g. `L&84997`).

## 4. Geo

`GET /portal/meters/{id}/geo` -> `{"data":{"latitude":"26.938961002479868","longitude":"75.83095696146852"}}`.
Coordinates are **strings**. **UNVERIFIED:** whether any meter lacks coordinates.

## 5. Consumption ("energy")

`GET /portal/meters/{id}/energy` -> `{"data":[{"timestamp":"23/06/2026 23:30","kwh":"48438.74","kvah":"52313.84","voltR":"226"}]}`

* All four values are **strings**. Timestamps are **day-first** `DD/MM/YYYY HH:MM`, with no timezone.
* Readings are 30 minutes apart. The UI table shows no date controls and its latest reading was 30/06/2026 23:30 (the data is a fixed historical set, not "live").
* Query parameters `from` and `to` (format `YYYY-MM-DD`) work, alone and together. Note the mismatch: input is ISO, output is day-first.
* `from=invalid-date` is **silently ignored** (normal dataset returned, no error). So callers get no feedback; our API validates dates itself.
* `from` later than `to` returns `{"data": []}`.
* Column headers in the UI are `KWH`, `KVAH`, `V(R)`. Units kWh / kVAh / V are my reading of those headers. **UNVERIFIED** beyond that.
* **UNVERIFIED:** whether `to` includes the whole end day, total row count per meter, any cap on rows, nulls/gaps (none seen in the rows I looked at).

## 6. Unknown meter

`GET /portal/meters/ZZZ999/energy` and `.../geo` -> `{"error":"not_found","message":"Meter not found"}`.
**UNVERIFIED:** the HTTP status of this response. The client therefore keys off the JSON body.

## 7. Meter detail page

The detail page's data comes from `GET /meters/J100000/__data.json?x-sveltekit-invalidated=001`.
This is SvelteKit page-data, **not** a REST API; its format is an internal, compacted structure.
It holds Meter ID, Serial No, Make, Phase Type, Installation Status, **Installation Type, Zone, Circle,
Division, Subdivision, Sub Station, Feeder, DT**. No separate `/api` endpoint for these was found.

**Not implemented yet:** I have not examined the raw `__data.json` body, so the service does not parse it.
Our `GET /api/v1/meters/{id}` therefore returns the list fields plus coordinates, and not the extra fields above.

## 8. Not investigated

* Transformers page / network hierarchy (likely source for a hierarchy endpoint).
* Rate limits, lockout after repeated failed logins, behaviour under concurrent sessions.
