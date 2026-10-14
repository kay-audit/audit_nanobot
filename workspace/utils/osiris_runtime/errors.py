"""Infrastructure errors distinct from worker handler failures."""


class OsirisUnavailableError(RuntimeError):
    """The service could not become ready before its startup deadline."""


class OsirisStartupTimeoutError(OsirisUnavailableError, TimeoutError):
    """The startup deadline elapsed before READY."""


class OsirisRequestError(RuntimeError):
    """A ready worker failed while handling one request."""


class OsirisRequestTimeoutError(OsirisRequestError, TimeoutError):
    """One accepted request exceeded its separate execution deadline."""


class OsirisServiceNotFoundError(OsirisRequestError):
    """No trusted profile is registered with this name."""


class OsirisUnknownOperationError(OsirisRequestError, ValueError):
    """The service or worker does not support this operation."""


class OsirisPayloadError(OsirisRequestError, ValueError):
    """Invalid JSON, file reference, identity or size."""


class OsirisBusyError(OsirisUnavailableError):
    """Administrative stop would interrupt accepted requests."""


class OsirisResourcesUnavailableError(OsirisUnavailableError):
    """The SDK explicitly reports unavailable GPU resources."""
