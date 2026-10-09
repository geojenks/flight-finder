"""Provider interface plus a small on-disk cache and HTTP helper."""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.request
from datetime import date
from pathlib import Path

from ..models import Option, Place


class ProviderError(RuntimeError):
    pass


class Provider:
    """Something that can price journeys between two places on a day."""

    name = "provider"
    modes: tuple[str, ...] = ()

    def search(self, origin: Place, destination: Place, day: date) -> list[Option]:
        raise NotImplementedError


def cache_dir() -> Path:
    root = os.environ.get("FLIGHTFINDER_CACHE") or os.path.join(
        os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache")), "flightfinder"
    )
    path = Path(root)
    path.mkdir(parents=True, exist_ok=True)
    return path


class DiskCache:
    """JSON cache keyed by string, so re-running a search doesn't re-query."""

    def __init__(self, namespace: str, ttl_seconds: int = 6 * 3600, enabled: bool = True):
        self.dir = cache_dir() / namespace
        self.ttl = ttl_seconds
        self.enabled = enabled

    def _path(self, key: str) -> Path:
        return self.dir / (hashlib.sha256(key.encode()).hexdigest()[:32] + ".json")

    def get(self, key: str):
        if not self.enabled:
            return None
        path = self._path(key)
        try:
            if time.time() - path.stat().st_mtime > self.ttl:
                return None
            return json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            return None

    def put(self, key: str, value) -> None:
        if not self.enabled:
            return
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self._path(key).with_suffix(".tmp")
        tmp.write_text(json.dumps(value), "utf-8")
        tmp.replace(self._path(key))


USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)


def get_json(url: str, timeout: float = 20.0):
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Accept-Language": "en-GB,en;q=0.9",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # network errors, HTTP errors, bad JSON
        raise ProviderError(f"{url}: {exc}") from exc
