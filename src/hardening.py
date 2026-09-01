"""
hardening.py — auth, rate limiting, and response caching for the API.

All three are opt-in via env vars, so local dev with no .env changes is
unchanged. Tests enable them by setting env / patching helpers and clearing
stores between cases.

- API_TOKEN: if set, /query, /query/stream, /chat require
  Authorization: Bearer <token>. /health and /schema stay public so probes
  and the Streamlit sidebar keep working without a secret.

- RATE_LIMIT: e.g. "10/minute", "5/second", "100/hour". Sliding window per
  IP, in-memory. Disabled when unset/empty/invalid. Returns 429 with
  Retry-After when exceeded.

- CACHE_TTL: seconds (int). When >0, identical /query questions hit an
  in-memory LRU cache. Disabled by default. /chat is never cached (history
  matters) and /query/stream is not cached either - streaming is about
  progress, not repeat cost.
"""

from __future__ import annotations

import os
import re
import secrets
import threading
import time
from collections import OrderedDict
from typing import Optional

from fastapi import Header, HTTPException, Request

# --- auth ------------------------------------------------------------------

def _get_api_token() -> str:
    return os.environ.get("API_TOKEN", "").strip()


def require_auth(authorization: Optional[str] = Header(None)) -> None:
    """
    FastAPI dependency. No-op when API_TOKEN is unset; otherwise enforces
    Bearer <token> with a timing-safe comparison.
    """
    expected = _get_api_token()
    if not expected:
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    provided = authorization[7:].strip()
    if not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="invalid token")


# --- rate limiting ---------------------------------------------------------

_RATE_RE = re.compile(r"^\s*(\d+)\s*/\s*(second|minute|hour)\s*$", re.IGNORECASE)
_WINDOW: dict[str, int] = {"second": 1, "minute": 60, "hour": 3600}

_rate_store: dict[str, list[float]] = {}
_rate_lock = threading.Lock()


def _get_rate_limit() -> tuple[int, int] | None:
    raw = os.environ.get("RATE_LIMIT", "").strip()
    if not raw:
        return None
    m = _RATE_RE.match(raw)
    if not m:
        return None
    limit = int(m.group(1))
    window = _WINDOW[m.group(2).lower()]
    if limit <= 0:
        return None
    return limit, window


def check_rate_limit(request: Request) -> None:
    """
    FastAPI dependency. No-op when RATE_LIMIT is unset/invalid.
    Sliding window per IP; raises 429 with Retry-After on excess.
    """
    parsed = _get_rate_limit()
    if parsed is None:
        return
    limit, window = parsed
    # Prefer X-Forwarded-For when behind a proxy, else the direct peer.
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        ip = forwarded.split(",")[0].strip() or "unknown"
    elif request.client is not None:
        ip = request.client.host
    else:
        ip = "unknown"

    now = time.time()
    with _rate_lock:
        timestamps = _rate_store.get(ip, [])
        # keep only inside window
        timestamps = [t for t in timestamps if now - t < window]
        if len(timestamps) >= limit:
            retry_after = int(window - (now - timestamps[0])) + 1
            raise HTTPException(
                status_code=429,
                detail="rate limit exceeded",
                headers={"Retry-After": str(retry_after)},
            )
        timestamps.append(now)
        _rate_store[ip] = timestamps


def clear_rate_limit_store() -> None:
    """Helper for tests — reset between cases."""
    with _rate_lock:
        _rate_store.clear()


# --- caching ---------------------------------------------------------------

_cache: OrderedDict[str, tuple[object, float]] = OrderedDict()
_cache_lock = threading.Lock()
_DEFAULT_MAX_SIZE = 128


def _get_cache_ttl() -> int:
    raw = os.environ.get("CACHE_TTL", "").strip()
    if not raw:
        return 0
    try:
        ttl = int(raw)
    except ValueError:
        return 0
    return ttl if ttl > 0 else 0


def _get_cache_max_size() -> int:
    raw = os.environ.get("CACHE_MAX_SIZE", "").strip()
    if not raw:
        return _DEFAULT_MAX_SIZE
    try:
        size = int(raw)
    except ValueError:
        return _DEFAULT_MAX_SIZE
    return size if size > 0 else _DEFAULT_MAX_SIZE


def _cache_key(question: str) -> str:
    # strip only — case and wording matter for SQL generation, so no lower()
    return (question or "").strip()


def get_cached(question: str):
    ttl = _get_cache_ttl()
    if ttl <= 0:
        return None
    key = _cache_key(question)
    now = time.time()
    with _cache_lock:
        item = _cache.get(key)
        if item is None:
            return None
        response, inserted = item
        if now - inserted > ttl:
            _cache.pop(key, None)
            return None
        # move to end for LRU
        _cache.move_to_end(key)
        return response


def set_cached(question: str, response) -> None:
    ttl = _get_cache_ttl()
    if ttl <= 0:
        return
    key = _cache_key(question)
    max_size = _get_cache_max_size()
    with _cache_lock:
        _cache[key] = (response, time.time())
        _cache.move_to_end(key)
        while len(_cache) > max_size:
            _cache.popitem(last=False)


def clear_cache() -> None:
    """Helper for tests."""
    with _cache_lock:
        _cache.clear()
