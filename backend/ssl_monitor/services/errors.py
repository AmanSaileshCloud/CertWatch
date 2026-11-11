"""Errors raised by the monitor / catalog services.

Plain ``ValueError`` subclasses, so the services stay framework-free; the API maps
``NotFound`` → 404 and ``InvalidInput`` → 422 (``DuplicateNameError`` → 409).
"""

from __future__ import annotations


class ServiceError(ValueError):
    """Base for monitor and catalog service errors."""


class NotFound(ServiceError):
    pass


class InvalidInput(ServiceError):
    pass


class MonitorNotFound(NotFound):
    pass


class CustomerNotFound(NotFound):
    pass


class GroupNotFound(NotFound):
    pass


class InvalidMonitor(InvalidInput):
    pass


class BlockedTarget(InvalidMonitor):
    """The URL points at a private/internal address (SSRF guard)."""
