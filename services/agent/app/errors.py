class TurnError(Exception):
    """A turn ended in a known way. `code` maps to config/messages/errors.yaml
    and to the web app's `errors.<code>` strings. A guard block carries the deciding
    policy's own messages and `policy_id@version` (README §10.1: `block` returns the
    policy's message_ar/en to the user)."""

    def __init__(
        self,
        code: str,
        *,
        retryable: bool = False,
        detail: str | None = None,
        message_ar: str | None = None,
        message_en: str | None = None,
        policy_id: str | None = None,
    ):
        super().__init__(detail or code)
        self.code = code
        self.retryable = retryable
        self.message_ar = message_ar
        self.message_en = message_en
        self.policy_id = policy_id
