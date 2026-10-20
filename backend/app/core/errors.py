class ModuleError(Exception):
    """Safe, public error message; never put secrets or source code in message."""

    def __init__(
        self, code: str, message: str, *, status_code: int = 502, retryable: bool = False
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable
