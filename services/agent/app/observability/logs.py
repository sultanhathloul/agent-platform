"""Library loggers that could carry a server's or a document's text (G8, ADR-0234).

The MCP SDK, httpx and httpcore log at INFO and DEBUG what they send and receive, and the SDK logs
exceptions whose messages and tracebacks may quote a server's answer. Their loggers are set to
WARNING, and a filter on each drops a record's arguments, exception and stack: what is left is
the message template, which those libraries write themselves. The platform's own logs name codes
and type names only."""

import logging

QUIETED = ("mcp", "httpx", "httpcore")
WITHHELD = "message withheld (G8): see the logger, module and line"


def _quieted(name: str) -> bool:
    return any(name == prefix or name.startswith(f"{prefix}.") for prefix in QUIETED)


def scrub(record: logging.LogRecord) -> logging.LogRecord:
    """Keeps a record's level, logger, place in the code and %-style template; drops what it was
    formatted with, its exception and its stack. A message without arguments may have been built
    with an f-string around a server's text, so it is withheld whole."""
    if record.args:
        record.msg = str(record.msg)
        record.args = None
    else:
        record.msg = WITHHELD
    record.exc_info = None
    record.exc_text = None
    record.stack_info = None
    return record


_installed = False


def quiet_libraries() -> None:
    """Idempotent. Their loggers (children included, through the effective level) log WARNING and
    above; every record they create is scrubbed where it is made, by the record factory, so no
    handler (a test's capture included) ever sees the arguments or the traceback."""
    global _installed
    for name in QUIETED:
        logging.getLogger(name).setLevel(logging.WARNING)
    if _installed:
        return
    previous = logging.getLogRecordFactory()

    def factory(*args, **kwargs) -> logging.LogRecord:
        record = previous(*args, **kwargs)
        return scrub(record) if _quieted(record.name) else record

    logging.setLogRecordFactory(factory)
    _installed = True
