class TurnError(Exception):
    """A turn ended in a known way. `code` maps to config/messages/errors.yaml
    and to the web app's `errors.<code>` strings."""

    def __init__(self, code: str, *, retryable: bool = False, detail: str | None = None):
        super().__init__(detail or code)
        self.code = code
        self.retryable = retryable
