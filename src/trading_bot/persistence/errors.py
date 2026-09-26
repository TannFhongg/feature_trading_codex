"""Persistence errors that do not expose SQL statements or account data."""


class LedgerError(RuntimeError):
    """Base error for persistent execution-ledger failures."""


class LedgerConflictError(LedgerError):
    """Raised when a uniqueness or lifecycle invariant would be violated."""
