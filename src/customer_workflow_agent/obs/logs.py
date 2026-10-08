"""Every log line carries the chat it belongs to: `... [chat=<id>] logger: message`.

The chat id lives in a ContextVar. The chat runner sets it for each graph run (LLM calls and
callbacks inherit it), and `ChatIdMiddleware` sets it for requests about one chat, so uvicorn's
access lines show it too.
"""

import logging
import re
from contextvars import ContextVar

chat_id_var: ContextVar[str] = ContextVar("chat_id", default="-")

FORMAT = "%(asctime)s %(levelname)-7s [chat=%(chat_id)s] %(name)s: %(message)s"
_QUIET = {"httpx": logging.WARNING, "aiosqlite": logging.WARNING}


class ChatIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.chat_id = chat_id_var.get()
        return True


def _adopt(handler: logging.Handler, formatter: logging.Formatter) -> None:
    handler.setFormatter(formatter)
    if not any(isinstance(f, ChatIdFilter) for f in handler.filters):
        handler.addFilter(ChatIdFilter())


def configure_logging(level: int = logging.INFO) -> None:
    """Use our format on the root logger and on uvicorn's own handlers. Safe to call again."""
    formatter = logging.Formatter(FORMAT, datefmt="%H:%M:%S")
    root = logging.getLogger()
    ours = [h for h in root.handlers if getattr(h, "_chat_log", False)]
    if not ours:
        handler = logging.StreamHandler()
        handler._chat_log = True  # type: ignore[attr-defined]
        root.addHandler(handler)
        ours = [handler]
    for h in ours:
        _adopt(h, formatter)
    root.setLevel(level)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        for h in logging.getLogger(name).handlers:
            _adopt(h, formatter)
    for name, quiet_level in _QUIET.items():
        logging.getLogger(name).setLevel(quiet_level)


_CHAT_PATH = re.compile(r"^/api/(?:chats|supervisor/approvals)/([0-9a-f]{32})(?:/|$)")


class ChatIdMiddleware:
    """Pure ASGI middleware: requests under /api/chats/<id> run with that chat id set."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        match = _CHAT_PATH.match(scope.get("path", "")) if scope["type"] == "http" else None
        if match is None:
            await self.app(scope, receive, send)
            return
        token = chat_id_var.set(match.group(1))
        try:
            await self.app(scope, receive, send)
        finally:
            chat_id_var.reset(token)
