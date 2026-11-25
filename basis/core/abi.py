"""Bundled ABI loader. Files are ``basis/abis/<Name>.json``."""

import json
import os
from functools import lru_cache
from typing import Any, List

_ABI_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "abis")


@lru_cache(maxsize=None)
def _load(name: str) -> tuple:
    path = os.path.join(_ABI_DIR, f"{name}.json")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    abi = data if isinstance(data, list) else data.get("abi")
    if not abi:
        raise ValueError(f"[basis-sdk] ABI {name} is empty or missing: {path}")
    return tuple(abi)


def load_abi(name: str) -> List[Any]:
    """ABI by SDK name (e.g. "Swap", "MainToken"). Accepts both a bare list and {"abi": [...]}; fails on empty."""
    return list(_load(name))
