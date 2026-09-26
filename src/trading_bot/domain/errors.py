"""Domain-specific exceptions."""


class DomainValidationError(ValueError):
    """Raised when a domain object would violate a trading invariant."""
