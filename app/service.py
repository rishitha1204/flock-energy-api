"""Business logic: validate input, call PortalClient, normalise the result.

The portal sends strings ("48438.74") and day-first timestamps
("23/06/2026 23:30"). This is where they become floats and datetimes.
"""
import math
import re
from datetime import date, datetime
from typing import Any

from pydantic import ValidationError

from app.errors import (
    InvalidRequestError,
    MeterNotFoundError,
    PortalUnexpectedResponseError,
)
from app.models import (
    ConsumptionResponse,
    Location,
    Meter,
    MeterDetail,
    MeterListResponse,
    Pagination,
    Reading,
    UpstreamMeter,
)
from app.portal_client import PortalClient

# Observed in the portal's energy data, e.g. "23/06/2026 23:30".
ENERGY_TIMESTAMP_FORMAT = "%d/%m/%Y %H:%M"
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ------------------------------------------------------------ helpers
def clean_text(value: Any) -> str | None:
    """Trim whitespace; turn empty strings into None."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def to_float(value: Any) -> float | None:
    """'48438.74' -> 48438.74. Blank -> None. Garbage -> error."""
    text = clean_text(value)
    if text is None:
        return None
    try:
        number = float(text)
    except ValueError as exc:
        raise PortalUnexpectedResponseError("Portal sent a non-numeric value") from exc
    if not math.isfinite(number):
        raise PortalUnexpectedResponseError("Portal sent a non-finite number")
    return number


def to_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise PortalUnexpectedResponseError("Portal sent a non-integer value") from exc


def parse_iso_date(value: str | None, name: str) -> date | None:
    """Accept exactly YYYY-MM-DD. The portal silently ignores bad dates,
    so we must reject them ourselves."""
    if value is None or value == "":
        return None
    if not _ISO_DATE.match(value):
        raise InvalidRequestError(f"'{name}' must be a date in YYYY-MM-DD format")
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise InvalidRequestError(f"'{name}' is not a real calendar date") from exc


def parse_energy_timestamp(value: Any) -> datetime:
    try:
        return datetime.strptime(str(value).strip(), ENERGY_TIMESTAMP_FORMAT)
    except ValueError as exc:
        raise PortalUnexpectedResponseError("Portal sent an unreadable timestamp") from exc


def to_meter(raw: Any) -> Meter:
    try:
        upstream = UpstreamMeter.model_validate(raw)
    except ValidationError as exc:
        raise PortalUnexpectedResponseError("Meter record had an unexpected shape") from exc
    return Meter(
        meter_id=clean_text(upstream.meter_id) or "",
        serial_no=clean_text(upstream.serial_no),
        make=clean_text(upstream.make),
        phase_type=clean_text(upstream.phase_type),
        install_status=clean_text(upstream.install_status),
        dt_code=clean_text(upstream.dt_code),
    )


def to_reading(raw: Any) -> Reading:
    if not isinstance(raw, dict):
        raise PortalUnexpectedResponseError("Energy row was not an object")
    return Reading(
        timestamp=parse_energy_timestamp(raw.get("timestamp")),
        kwh=to_float(raw.get("kwh")),
        kvah=to_float(raw.get("kvah")),
        volt_r=to_float(raw.get("voltR")),
    )


# ------------------------------------------------------------ service
class MeterService:
    def __init__(self, portal: PortalClient):
        self.portal = portal

    def login(self) -> bool:
        self.portal.login()
        return True

    def list_meters(self, q: str, page: int) -> MeterListResponse:
        body = self.portal.search_meters(q.strip(), page)
        meters = [to_meter(item) for item in body["data"]]
        page_size = to_int(body.get("pageSize"))
        total = to_int(body.get("total"))
        total_pages = -(-total // page_size) if page_size > 0 else 0  # ceiling
        return MeterListResponse(
            data=meters,
            pagination=Pagination(
                page=to_int(body.get("page")),
                page_size=page_size,
                total=total,
                total_pages=total_pages,
            ),
        )

    def get_meter(self, meter_id: str) -> MeterDetail:
        # The portal has no clean "one meter" JSON endpoint, so we search for
        # the id and keep only an exact match (search might be a substring
        # match: UNVERIFIED), then add coordinates from the geo endpoint.
        body = self.portal.search_meters(meter_id, 1)
        matches = [m for m in map(to_meter, body["data"]) if m.meter_id.lower() == meter_id.lower()]
        if not matches:
            raise MeterNotFoundError("Meter not found")
        meter = matches[0]

        location = None
        try:
            geo = self.portal.get_geo(meter.meter_id)
            lat, lon = to_float(geo.get("latitude")), to_float(geo.get("longitude"))
            if lat is not None and lon is not None:
                location = Location(latitude=lat, longitude=lon)
        except MeterNotFoundError:
            location = None  # meter exists but has no coordinates
        return MeterDetail(**meter.model_dump(), location=location)

    def get_consumption(
        self, meter_id: str, date_from: str | None, date_to: str | None
    ) -> ConsumptionResponse:
        d_from = parse_iso_date(date_from, "from")
        d_to = parse_iso_date(date_to, "to")
        if d_from and d_to and d_from > d_to:
            raise InvalidRequestError("'from' must not be later than 'to'")

        rows = self.portal.get_energy(
            meter_id,
            d_from.isoformat() if d_from else None,
            d_to.isoformat() if d_to else None,
        )
        readings = sorted((to_reading(r) for r in rows), key=lambda r: r.timestamp)
        return ConsumptionResponse(
            meter_id=meter_id,
            date_from=d_from,
            date_to=d_to,
            count=len(readings),
            data=readings,
        )
