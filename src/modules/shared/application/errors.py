from abc import ABC
from typing import Any


class ApplicationError(Exception, ABC):
    """Base for every rule this application refuses to break.

    Application errors are not validation errors. A validation error says the
    caller sent something malformed; an application error says the operation
    would have violated an invariant of the business, and would have been wrong
    even if every field parsed perfectly.
    """


class ResourceNotFoundError(ApplicationError, ABC):
    """A resource was not found.

    This is a user-facing error, not a system error. It is used to indicate
    that a resource was not found.
    """

    def __init__(self, resource_type: str, resource_identifier: str) -> None:
        super().__init__(f"{resource_type} with ID {resource_identifier} not found")


class IntegrationError(Exception, ABC):
    """Wraps a third-party/infrastructure exception that crossed a port boundary.

    An adapter (a repository, a gateway) that catches an exception it does not
    recognize as one of its own typed conditions raises this instead of
    letting the raw infrastructure exception (a SQLAlchemy `IntegrityError`, a
    driver-level connection error) leak past the port -- the application and
    domain layers must never depend on what library is behind a port.
    """

    def __init__(self, code: str, cause: Exception, message: str, metadata: dict[str, Any]) -> None:
        self.code = code
        self.cause = cause
        self.metadata = metadata

        super().__init__(f"{code}: {message} (caused by {cause})")
