"""Exceptions used inside the service.

The portal client raises PortalError subclasses; main.py turns each one into
a clean HTTP response. None of these messages may contain secrets.
"""


class PortalError(Exception):
    """Base class for everything that goes wrong talking to the portal."""


class PortalConfigError(PortalError):
    """Portal credentials are missing from the environment."""


class PortalAuthError(PortalError):
    """The portal rejected our credentials, or still says 'unauthorized'
    after we logged in again."""


class PortalUnavailableError(PortalError):
    """Could not reach the portal, or it answered with a 5xx error."""


class PortalTimeoutError(PortalUnavailableError):
    """The portal took too long to answer."""


class PortalUnexpectedResponseError(PortalError):
    """The portal answered, but not in a shape we recognise."""


class MeterNotFoundError(PortalError):
    """The portal said the meter does not exist."""


class InvalidRequestError(Exception):
    """The caller of OUR api sent something invalid (e.g. from > to)."""
