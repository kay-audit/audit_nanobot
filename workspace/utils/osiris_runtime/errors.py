"""Infrastructure errors distinct from worker handler failures."""


class OsirisUnavailableError(RuntimeError):
    """The service could not become ready before its startup deadline."""


class OsirisStartupTimeoutError(OsirisUnavailableError, TimeoutError):
    """The startup deadline elapsed before READY."""


class OsirisRequestError(RuntimeError):
    """A ready worker failed while handling one request."""


class OsirisRequestTimeoutError(OsirisRequestError, TimeoutError):
    """One accepted request exceeded its separate execution deadline."""
