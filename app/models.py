"""Pydantic models.

Two groups:
  * Upstream* models describe what the PORTAL sends (camelCase names).
    They validate the portal's response so a shape change fails loudly.
  * Everything else is OUR public API (snake_case names).
"""
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


# --------------------------------------------------------- upstream shape
class UpstreamMeter(BaseModel):
    model_config = ConfigDict(extra="ignore")

    meter_id: str = Field(alias="meterId")
    # Only meterId is treated as required. The rest were present in every
    # sample seen, but nulls were not ruled out, so they are optional.
    serial_no: str | None = Field(default=None, alias="serialNo")
    make: str | None = None
    phase_type: str | None = Field(default=None, alias="phaseType")
    install_status: str | None = Field(default=None, alias="installStatus")
    dt_code: str | None = Field(default=None, alias="dtCode")


# ------------------------------------------------------------- public API
class ErrorResponse(BaseModel):
    error: str = Field(description="Short machine-readable error code")
    message: str = Field(description="Human-readable explanation")


class HealthResponse(BaseModel):
    status: str = "ok"


class LoginResponse(BaseModel):
    authenticated: bool


class Meter(BaseModel):
    meter_id: str
    serial_no: str | None = None
    make: str | None = None
    phase_type: str | None = None
    install_status: str | None = None
    dt_code: str | None = Field(
        default=None, description="Distribution transformer code, e.g. DT-001"
    )


class Pagination(BaseModel):
    page: int
    page_size: int = Field(description="Fixed at 20 by the portal")
    total: int
    total_pages: int


class MeterListResponse(BaseModel):
    data: list[Meter]
    pagination: Pagination


class Location(BaseModel):
    latitude: float
    longitude: float


class MeterDetail(Meter):
    location: Location | None = Field(
        default=None, description="null if the portal has no coordinates"
    )


class Reading(BaseModel):
    timestamp: datetime = Field(
        description="Local time as shown by the portal; the portal does not state a timezone"
    )
    kwh: float | None = None
    kvah: float | None = None
    volt_r: float | None = Field(default=None, description="R-phase voltage")


class ConsumptionUnits(BaseModel):
    kwh: str = "kWh"
    kvah: str = "kVAh"
    volt_r: str = "V"


class ConsumptionResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    meter_id: str
    date_from: date | None = Field(default=None, alias="from")
    date_to: date | None = Field(default=None, alias="to")
    count: int
    units: ConsumptionUnits = ConsumptionUnits()
    data: list[Reading]
