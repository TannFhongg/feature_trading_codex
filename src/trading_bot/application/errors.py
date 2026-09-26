"""Application runtime and control-plane errors."""


class ApplicationError(RuntimeError):
    """Base error for P6 orchestration failures."""


class InvalidStateTransitionError(ApplicationError):
    """A control command is not valid from the current lifecycle state."""


class RuntimeDependencyError(ApplicationError):
    """A supervised dependency stopped or failed unexpectedly."""


class EventBackpressureError(ApplicationError):
    """A bounded runtime queue rejected an event and the runtime failed closed."""


class ControlAuthenticationError(ApplicationError):
    """A control request did not present an authorized credential."""


class ControlCommandError(ApplicationError):
    """An authenticated control command failed after being durably audited."""
