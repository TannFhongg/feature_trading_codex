"""Exchange-neutral execution errors used across adapters and orchestration."""


class ExecutionAdapterError(RuntimeError):
    """Base error for an exchange adapter execution failure."""


class AmbiguousExecutionError(ExecutionAdapterError):
    """An operation may have reached the exchange but cannot yet be resolved."""


class ExecutionIntentAlreadyRecordedError(RuntimeError):
    """The durable intent already exists and must be queried instead of submitted again."""
