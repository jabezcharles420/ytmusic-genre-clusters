"""Shared helpers: paths, text normalisation, polite HTTP."""

from __future__ import annotations

import json
import re
import threading
import time
import unicodedata
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
PREVIEWS = DATA / "previews"

_BRACKET = re.compile(r"\([^)]*\)|\[[^\]]*\]")
_NONWORD = re.compile(r"[^a-z0-9]+")
_LOCK = threading.Lock()
_LAST: dict[str, float] = {}


def norm(text: str) -> str:
    """Aggressively normalise a title/artist for matching."""
    s = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    s = _BRACKET.sub(" ", s)
    s = _NONWORD.sub(" ", s)
    return " ".join(s.split())


def tokens(text: str) -> set[str]:
    return set(norm(text).split())


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def ensure_dirs() -> None:
    DATA.mkdir(exist_ok=True)
    PREVIEWS.mkdir(exist_ok=True)


# Deezer geo-blocks this network, so its calls are tunnelled through the SOCKS
# proxy opened on the `ciu` host (api.deezer.com + preview CDNs). Apple's Search
# API throttles near ~57 requests/36s (measured), hence its slower interval.
SOCKS = {"http": "socks5h://127.0.0.1:1080", "https": "socks5h://127.0.0.1:1080"}
DEEZER_HOSTS = ("api.deezer.com", "dzcdn.net")


def make_client(min_interval: float = 0.15) -> "HttpClient":
    return HttpClient(min_interval=min_interval, retries=4,
                      retry_status=frozenset({403, 429, 500, 502, 503}),
                      proxies=SOCKS, proxy_hosts=DEEZER_HOSTS,
                      host_intervals={"itunes.apple.com": 0.7})


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text())


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1, ensure_ascii=False))


class HttpClient:
    """Session with per-host rate limiting, optional per-host proxying, and retries."""

    def __init__(self, min_interval: float = 0.12, retries: int = 3,
                 retry_status: frozenset[int] = frozenset({429, 500, 502, 503}),
                 proxies: dict[str, str] | None = None,
                 proxy_hosts: tuple[str, ...] = (),
                 host_intervals: dict[str, float] | None = None) -> None:
        self.session = requests.Session()
        self.min_interval = min_interval
        self.retries = retries
        self.retry_status = retry_status
        self.proxies = proxies
        self.proxy_hosts = proxy_hosts
        self.host_intervals = host_intervals or {}

    def _proxies_for(self, host: str) -> dict[str, str] | None:
        if self.proxies and any(host.endswith(s) for s in self.proxy_hosts):
            return self.proxies
        return None

    def _throttle(self, host: str) -> None:
        interval = self.host_intervals.get(host, self.min_interval)
        with _LOCK:
            wait = interval - (time.time() - _LAST.get(host, 0.0))
            if wait > 0:
                time.sleep(wait)
            _LAST[host] = time.time()

    def get(self, url: str, **kw):
        host = url.split("/")[2]
        kw.setdefault("timeout", 30)
        proxies = self._proxies_for(host)
        last: Exception | None = None
        for attempt in range(self.retries):
            self._throttle(host)
            try:
                r = self.session.get(url, proxies=proxies, **kw)
            except requests.RequestException as exc:
                last = exc
                time.sleep(1.5 * (attempt + 1))
                continue
            if r.status_code in self.retry_status:
                last = RuntimeError(f"HTTP {r.status_code}")
                time.sleep(min(2.0 * (attempt + 1) + 3.0 * (r.status_code == 403), 20.0))
                continue
            return r
        raise last if last else RuntimeError("request failed")

    def get_json(self, url: str, **kw):
        r = self.get(url, **kw)
        if r.status_code != 200:
            return None
        try:
            return r.json()
        except ValueError:
            return None
