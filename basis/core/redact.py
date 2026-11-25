"""URLs in error, log and queue text, cut down to their scheme and host (TypeScript SDK: src/core/redact.ts). An RPC URL can
carry an API key in its path, query or user info (``https://rpc.example.com/v2/KEY``), and the HTTP libraries put the
whole URL in their error text."""

import re

_URL = re.compile(r"\b(?:https?|wss?)://[^\s'\"<>`]*", re.IGNORECASE)
_TAIL = re.compile(r"[)\]},.;:!?]+$")
_PATH = re.compile(r"(\burl: )/[^\s'\"<>`()]*", re.IGNORECASE)


def _cut(m: "re.Match[str]") -> str:
    u = m.group(0)
    tail = _TAIL.search(u)
    end = tail.group(0) if tail else ""
    scheme, rest = u[:len(u) - len(end)].split("://", 1)
    authority = re.split(r"[/?#]", rest, maxsplit=1)[0]
    return f"{scheme}://{authority.rsplit('@', 1)[-1]}{end}"


def redact_urls(text: object) -> str:
    """``text`` (``str()`` of it) with every URL cut down to its scheme and host (``https://rpc.example.com/v2/KEY``
    reads ``https://rpc.example.com``) and a bare path after ``url:`` replaced by ``[path removed]``. TS:
    ``redactUrls``."""
    return _PATH.sub(r"\1[path removed]", _URL.sub(_cut, str(text)))
