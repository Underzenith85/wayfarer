"""Typed failures shared across application boundaries."""


class WayfarerError(Exception):
    code = "wayfarer_error"
    status = 400


class ValidationError(WayfarerError):
    code = "validation_error"


class NotFoundError(WayfarerError):
    code = "not_found"
    status = 404


class ConflictError(WayfarerError):
    code = "conflict"
    status = 409


class AuthenticationError(WayfarerError):
    code = "authentication_required"
    status = 401


class AuthorizationError(WayfarerError):
    code = "forbidden"
    status = 403


class ProviderError(WayfarerError):
    code = "provider_error"
    status = 502


class ProviderTimeoutError(ProviderError):
    code = "provider_timeout"
    status = 504


class StorageError(WayfarerError):
    code = "storage_error"
    status = 503
