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
    that a resource was not found. `resource_type`/`resource_identifier` are
    kept as attributes, not folded only into the message string -- a caller
    (a route's error handler, a test) reads them directly instead of parsing
    `str(exc)`.
    """

    def __init__(self, resource_type: str, resource_identifier: str) -> None:
        self.resource_type = resource_type
        self.resource_identifier = resource_identifier
        super().__init__(f"{resource_type} with ID {resource_identifier} not found")


class ResourceAlreadyExistsError(ApplicationError, ABC):
    """A resource that was expected not to exist yet already does.

    `ResourceNotFoundError`'s sibling: same two attributes, same reason for
    keeping them (a caller reads `resource_type`/`resource_identifier`
    directly), the other side of the same question -- "does this resource
    exist" -- rather than a new shape.
    """

    def __init__(self, resource_type: str, resource_identifier: str) -> None:
        self.resource_type = resource_type
        self.resource_identifier = resource_identifier
        super().__init__(f"{resource_type} with ID {resource_identifier} already exists")


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
