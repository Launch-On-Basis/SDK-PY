"""Unknown keys in a dict argument (TS: src/core/opts.ts).

Keyword arguments refuse an unknown name on their own (TypeError). The dicts a caller hands over (a profile payload, an
agent config, the ``slippage_bps`` / ``sync`` / ``addresses`` client options) are checked here instead: a key outside
the known ones raises ``ValidationError(field)`` naming it and, when one is close, the key it most likely meant. Same
rule and words as TS ``optsOf`` / ``checkKeys``."""

from typing import Any, List, Optional, Sequence

from .errors import ValidationError


def _norm_key(s: str) -> str:
    """ASCII lower case without ``_`` and ``-``: ``fraction_bps``, ``FractionBps`` and ``fractionBps`` meet. Same in TS."""
    return "".join(c.lower() if "A" <= c <= "Z" else c for c in s if c not in "_-")


def _edit_distance(a: str, b: str) -> int:
    """Optimal string alignment distance (insert, delete, replace, or swap two neighbours: one edit each). Same in TS."""
    d = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a) + 1):
        d[i][0] = i
    for j in range(len(b) + 1):
        d[0][j] = j
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            v = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                v = min(v, d[i - 2][j - 2] + 1)
            d[i][j] = v
    return d[len(a)][len(b)]


def closest_key(key: Any, known: Sequence[str]) -> Optional[str]:
    """The known key a mistyped one most likely meant: the same key in another case or with ``_`` / ``-``
    (``fraction_bps`` -> ``fractionBps``), else the nearest by edits when at most 2 edits and at most a third of the
    longer key away (``amout`` -> ``amount``), the first in list order on a tie; None when none is that close. Same
    rule as TS ``closestKey``."""
    k = _norm_key(str(key))
    for c in known:
        if _norm_key(c) == k:
            return c
    best: Optional[str] = None
    best_d = None
    for c in known:
        dist = _edit_distance(k, _norm_key(c))
        if best_d is None or dist < best_d:
            best, best_d = c, dist
    if best is None or best_d is None:
        return None
    return best if best_d <= 2 and best_d * 3 <= max(len(k), len(_norm_key(best))) else None


def unknown_keys_message(field: str, unknown: Sequence[Any], known: Sequence[str]) -> str:
    """The words of an unknown-key refusal, the same as TS ``unknownKeysMessage``: ``options has an unknown key 'amout'
    (did you mean 'amount'?). Known keys: amount.``"""
    named: List[str] = []
    for k in unknown:
        near = closest_key(k, known)
        named.append(f"'{k}'" if near is None else f"'{k}' (did you mean '{near}'?)")
    what = "an unknown key" if len(unknown) == 1 else "unknown keys"
    tail = f"Known keys: {', '.join(known)}." if known else "It takes no keys."
    return f"{field} has {what} {', '.join(named)}. {tail}"


def did_you_mean(unknown: Sequence[Any], known: Sequence[str]) -> str:
    """The hint the older unknown-key refusals (``slippage_bps``, ``sync``, ``addresses``) end with: `` Did you mean
    'trade'?`` for one unknown key with a close known one, `` Did you mean 'a' for 'x', 'b' for 'y'?`` for several, ''
    when none is close. Same as TS ``didYouMean``."""
    pairs = [(k, n) for k, n in ((k, closest_key(k, known)) for k in unknown) if n is not None]
    if not pairs:
        return ""
    if len(unknown) == 1:
        return f" Did you mean '{pairs[0][1]}'?"
    return " Did you mean " + ", ".join(f"'{n}' for '{k}'" for k, n in pairs) + "?"


def check_keys(d: Any, known: Sequence[str], field: str, label: Optional[str] = None) -> None:
    """Raises ``ValidationError(field)`` when the dict ``d`` has a key outside ``known``; a key set to None counts (a
    misspelt key is refused whatever its value). ``label`` names the dict in the message (default ``field``). Anything
    that is not a dict is left to the caller's own check. TS: ``checkKeys`` (``optsOf`` for an options object)."""
    if not isinstance(d, dict):
        return
    unknown = [k for k in d if k not in known]
    if unknown:
        raise ValidationError(field, unknown_keys_message(label or field, unknown, known))
