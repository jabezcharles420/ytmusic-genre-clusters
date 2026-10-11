"""YouTube Music auth derived from the local Microsoft Edge cookie store (macOS).

Chromium-family browsers encrypt cookie values with AES-128-CBC. The key is
PBKDF2-HMAC-SHA1(password, "saltysalt", 1003, 16) where `password` lives in the
login keychain. Chrome >= 130 prefixes the plaintext with SHA256(host_key).
"""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
import subprocess
import tempfile
import time
from pathlib import Path

from Crypto.Cipher import AES

EDGE_DIR = Path.home() / "Library/Application Support/Microsoft Edge"
KEYCHAIN_SERVICE = "Microsoft Edge Safe Storage"
KEYCHAIN_ACCOUNT = "Microsoft Edge"
YT_ORIGIN = "https://music.youtube.com"

# Cookies Google's web clients actually need; keeps the header small.
KEEP = {
    "SID", "HSID", "SSID", "APISID", "SAPISID",
    "__Secure-1PSID", "__Secure-3PSID",
    "__Secure-1PAPISID", "__Secure-3PAPISID",
    "__Secure-1PSIDTS", "__Secure-3PSIDTS",
    "SIDCC", "__Secure-1PSIDCC", "__Secure-3PSIDCC",
    "LOGIN_INFO", "PREF", "SOCS", "VISITOR_INFO1_LIVE", "CONSENT",
}


def _keychain_key() -> bytes:
    pw = subprocess.run(
        ["security", "find-generic-password", "-w",
         "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT],
        capture_output=True, text=True, check=True,
    ).stdout.strip().encode()
    return hashlib.pbkdf2_hmac("sha1", pw, b"saltysalt", 1003, 16)


def _decrypt(blob: bytes, key: bytes) -> str:
    if blob[:3] in (b"v10", b"v11"):
        blob = blob[3:]
    plain = AES.new(key, AES.MODE_CBC, b" " * 16).decrypt(blob)
    plain = plain[: -plain[-1]]  # strip PKCS#7
    # Chrome >= 130 prepends SHA256(host_key); real values are ASCII.
    if plain[:32] and not plain[:32].isascii() and plain[32:].isascii():
        plain = plain[32:]
    return plain.decode("utf-8", "replace")


def _cookie_db() -> tuple[Path, str]:
    """Newest Edge profile whose cookie DB is non-trivial."""
    candidates: list[tuple[float, Path]] = []
    for prof in EDGE_DIR.iterdir():
        if not prof.is_dir():
            continue
        for rel in ("Network/Cookies", "Cookies"):
            db = prof / rel
            if db.is_file():
                candidates.append((db.stat().st_mtime, db))
    if not candidates:
        raise FileNotFoundError(f"no cookie DB under {EDGE_DIR}")
    candidates.sort(reverse=True)
    mtime, db = candidates[0]
    return db, db.parent.name if db.parent.name == "Network" else db.parent.name


def browser_identity() -> tuple[str, str]:
    """(profile_name, user_agent) for the newest profile."""
    versions = []
    for name in ("Last Version", "RunningChromeVersion"):
        f = EDGE_DIR / name
        if f.is_file():
            versions.append(f.read_text().strip())
    ver = versions[0] if versions else "140.0.0.0"
    ua = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        f"(KHTML, like Gecko) Chrome/{ver} Safari/537.36 Edg/{ver}"
    )
    return ver, ua


def _expired(expires_utc: int) -> bool:
    if not expires_utc:  # session cookie
        return False
    unix = expires_utc / 1_000_000 - 11_644_473_600
    return unix < time.time()


def domain_matches(host_key: str, host: str) -> bool:
    """RFC 6265 domain matching, as a browser would apply it."""
    if host_key.startswith("."):
        bare = host_key[1:]
        return host == bare or host.endswith(host_key)
    return host == host_key


def edge_cookies(host: str = "music.youtube.com") -> dict[str, str]:
    """Cookies the browser would send to `host`, newest profile first."""
    db, _ = _cookie_db()
    key = _keychain_key()
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / "Cookies"
        shutil.copy2(db, tmp)
        for side in ("-wal", "-journal"):
            if db.with_name(db.name + side).exists():
                shutil.copy2(db.with_name(db.name + side), Path(str(tmp) + side))
        con = sqlite3.connect(tmp)
        try:
            rows = con.execute(
                "select host_key, name, path, expires_utc, encrypted_value from cookies"
            ).fetchall()
        finally:
            con.close()

    # most specific path first, then exact host over parent domain
    rows.sort(key=lambda r: (len(r[2] or ""), len(r[0])), reverse=True)
    out: dict[str, str] = {}
    for host_key, name, path, expires, blob in rows:
        if name not in KEEP or _expired(expires) or not blob:
            continue
        if not domain_matches(host_key, host):
            continue
        value = _decrypt(bytes(blob), key)
        if value and name not in out:
            out[name] = value
    missing = {"SAPISID", "SID"} - out.keys()
    if missing:
        raise RuntimeError(f"Edge cookies lack {sorted(missing)}; is Edge logged in to YouTube?")
    return out


def yt_headers(authuser: int = 0) -> dict[str, str]:
    """Headers that ytmusicapi accepts directly (browser auth type)."""
    cookies = edge_cookies()
    cookies.setdefault("SOCS", "CAI")
    ts = int(time.time())
    sapisid = cookies["SAPISID"]
    digest = hashlib.sha1(f"{ts} {sapisid} {YT_ORIGIN}".encode()).hexdigest()
    _, ua = browser_identity()
    return {
        "accept": "*/*",
        "content-type": "application/json",
        "origin": YT_ORIGIN,
        "x-origin": YT_ORIGIN,
        "x-goog-authuser": str(authuser),
        "user-agent": ua,
        "cookie": "; ".join(f"{k}={v}" for k, v in cookies.items()),
        "authorization": f"SAPISIDHASH {ts}_{digest}",
    }


def client(authuser: int = 0):
    from ytmusicapi import YTMusic

    return YTMusic(yt_headers(authuser))
