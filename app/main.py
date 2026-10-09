"""FastAPI application: routes, dependency wiring and error handling."""
import logging
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse

from app.errors import (
    InvalidRequestError,
    MeterNotFoundError,
    PortalAuthError,
    PortalConfigError,
    PortalError,
    PortalTimeoutError,
    PortalUnavailableError,
    PortalUnexpectedResponseError,
)
from app.models import (
    ConsumptionResponse,
    ErrorResponse,
    HealthResponse,
    LoginResponse,
    MeterDetail,
    MeterListResponse,
)
from app.portal_client import PortalClient
from app.service import MeterService

logger = logging.getLogger("urja_api")

DESCRIPTION = (
    "A clean REST API in front of the legacy **Urja Meter Ops** portal. "
    "The service logs in to the portal itself (credentials come from the "
    "server's environment), so API callers never handle portal credentials."
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.portal = PortalClient()  # one shared session for the service
    yield
    app.state.portal.close()


app = FastAPI(
    title="Flock Energy - Urja Meter Ops API",
    version="1.0.0",
    description=DESCRIPTION,
    lifespan=lifespan,
)


def get_service(request: Request) -> MeterService:
    return MeterService(request.app.state.portal)


Service = Annotated[MeterService, Depends(get_service)]
MeterId = Annotated[
    str,
    Path(
        pattern=r"^[A-Za-z0-9_-]{1,32}$",
        description="Meter id, e.g. J100000 (letters, digits, '-' and '_' only)",
    ),
]

_UPSTREAM_ERRORS = {
    502: {"model": ErrorResponse, "description": "Portal login failed, or it sent an unexpected response"},
    503: {"model": ErrorResponse, "description": "Portal credentials are not configured on the server"},
    504: {"model": ErrorResponse, "description": "The portal timed out"},
}
_BAD_REQUEST = {400: {"model": ErrorResponse, "description": "Invalid input"}}
_NOT_FOUND = {404: {"model": ErrorResponse, "description": "Meter not found"}}


# ------------------------------------------------------------- routes
@app.get("/health", response_model=HealthResponse, tags=["system"])
def health() -> HealthResponse:
    """Is this service running? (Does not contact the portal.)"""
    return HealthResponse(status="ok")


@app.post(
    "/api/v1/auth/login",
    response_model=LoginResponse,
    responses=_UPSTREAM_ERRORS,
    tags=["auth"],
)
def auth_login(service: Service) -> LoginResponse:
    """Force the service to (re)log in to the portal.

    Takes no body: portal credentials stay on the server. Normally you never
    need this, because the service logs in automatically when required.
    """
    return LoginResponse(authenticated=service.login())


@app.get(
    "/api/v1/meters",
    response_model=MeterListResponse,
    responses={**_BAD_REQUEST, **_UPSTREAM_ERRORS},
    tags=["meters"],
)
def list_meters(
    service: Service,
    q: Annotated[str, Query(max_length=64, description="Search text (meter number or serial). Empty = all.")] = "",
    page: Annotated[int, Query(ge=1, description="Page number, starting at 1. Page size is fixed at 20.")] = 1,
) -> MeterListResponse:
    """List meters, 20 per page."""
    return service.list_meters(q, page)


@app.get(
    "/api/v1/meters/{meter_id}",
    response_model=MeterDetail,
    responses={**_BAD_REQUEST, **_NOT_FOUND, **_UPSTREAM_ERRORS},
    tags=["meters"],
)
def get_meter(meter_id: MeterId, service: Service) -> MeterDetail:
    """One meter's details plus its coordinates (if the portal has them)."""
    return service.get_meter(meter_id)


@app.get(
    "/api/v1/meters/{meter_id}/consumption",
    response_model=ConsumptionResponse,
    responses={**_BAD_REQUEST, **_NOT_FOUND, **_UPSTREAM_ERRORS},
    tags=["meters"],
)
def get_consumption(
    meter_id: MeterId,
    service: Service,
    date_from: Annotated[str | None, Query(alias="from", description="Start date, YYYY-MM-DD")] = None,
    date_to: Annotated[str | None, Query(alias="to", description="End date, YYYY-MM-DD")] = None,
) -> ConsumptionResponse:
    """Half-hourly readings (kWh, kVAh, R-phase voltage), oldest first."""
    return service.get_consumption(meter_id, date_from, date_to)


# ----------------------------------------------------- error handling
def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": code, "message": message})


@app.exception_handler(PortalError)
async def portal_error_handler(request: Request, exc: PortalError) -> JSONResponse:
    # Messages sent to the caller are fixed strings: no upstream details.
    logger.warning("Portal error: %s", type(exc).__name__)
    if isinstance(exc, MeterNotFoundError):
        return _error(404, "meter_not_found", "Meter not found.")
    if isinstance(exc, PortalConfigError):
        return _error(503, "not_configured", "Portal credentials are not configured on the server.")
    if isinstance(exc, PortalAuthError):
        return _error(502, "upstream_auth_failed", "The service could not authenticate with the portal.")
    if isinstance(exc, PortalTimeoutError):
        return _error(504, "upstream_timeout", "The portal did not respond in time.")
    if isinstance(exc, PortalUnavailableError):
        return _error(502, "upstream_unavailable", "The portal is unavailable.")
    if isinstance(exc, PortalUnexpectedResponseError):
        return _error(502, "upstream_unexpected_response", "The portal sent a response the service could not understand.")
    return _error(500, "internal_error", "Unexpected internal error.")


@app.exception_handler(InvalidRequestError)
async def invalid_request_handler(request: Request, exc: InvalidRequestError) -> JSONResponse:
    return _error(400, "invalid_request", str(exc))


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    # Only the location and the rule are reported, never the submitted value.
    problems = "; ".join(
        f"{'.'.join(str(p) for p in e['loc'] if p != 'query' and p != 'path')}: {e['msg']}"
        for e in exc.errors()
    )
    return _error(400, "validation_error", problems)


@app.exception_handler(Exception)
async def unexpected_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error")  # stack trace stays in the server log
    return _error(500, "internal_error", "Unexpected internal error.")


# ------------------------------------------------------------ OpenAPI
def custom_openapi() -> dict:
    """FastAPI's default schema advertises 422 for validation errors, but we
    return 400. Remove the 422 entries so the spec matches the behaviour."""
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title=app.title, version=app.version, description=app.description, routes=app.routes
    )
    for path_item in schema["paths"].values():
        for operation in path_item.values():
            operation.get("responses", {}).pop("422", None)
    components = schema.get("components", {}).get("schemas", {})
    components.pop("HTTPValidationError", None)
    components.pop("ValidationError", None)
    app.openapi_schema = schema
    return schema


app.openapi = custom_openapi
