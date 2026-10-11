"""Stage 3 — download the 30s previews that feed the audio model.

Deezer preview links are Akamai-signed with an `exp=` parameter, so a link
minted during enrichment is usually dead by the time previews are fetched. Any
expired (or rejected) Deezer link is re-minted from /track/{id} and retried.
"""

from __future__ import annotations

import concurrent.futures as cf
import json
import re
import time
from pathlib import Path

from .util import DATA, PREVIEWS, ensure_dirs, make_client, read_json, write_json

MIN_BYTES = 20_000
_EXPIRY = re.compile(r"hdnea=exp=(\d+)")


def _target(video_id: str, kind: str) -> Path:
    return PREVIEWS / f"{video_id}.{'m4a' if kind == 'm4a' else 'mp3'}"


def _expired(url: str) -> bool:
    match = _EXPIRY.search(url)
    return bool(match) and int(match.group(1)) <= time.time() + 30


def _refresh(api, rec: dict) -> str | None:
    track_id = rec.get("deezer_track_id")
    if not track_id:
        return None
    data = api.get_json(f"https://api.deezer.com/track/{track_id}")
    return (data or {}).get("preview") or None


def _fetch(api, url: str, path: Path) -> tuple[bool, str]:
    r = api.get(url, stream=True)
    if r.status_code != 200:
        return False, f"HTTP {r.status_code}"
    tmp = path.with_suffix(path.suffix + ".part")
    with open(tmp, "wb") as fh:
        for chunk in r.iter_content(1 << 16):
            fh.write(chunk)
    if tmp.stat().st_size < MIN_BYTES:
        tmp.unlink(missing_ok=True)
        return False, "suspiciously small"
    tmp.rename(path)
    return True, ""


def _download(api, rec: dict) -> dict:
    vid = rec["videoId"]
    path = _target(vid, rec.get("preview_kind", "mp3"))
    if path.exists() and path.stat().st_size >= MIN_BYTES:
        return {"videoId": vid, "ok": True, "path": str(path), "cached": True}

    url = rec.get("preview")
    if not url:
        return {"videoId": vid, "ok": False, "reason": "no preview url"}
    if _expired(url):
        url = _refresh(api, rec) or url

    reason = "unknown"
    for attempt in (0, 1):
        try:
            ok, reason = _fetch(api, url, path)
        except Exception as exc:
            ok, reason = False, str(exc)[:120]
        if ok:
            return {"videoId": vid, "ok": True, "path": str(path),
                    "bytes": path.stat().st_size}
        if attempt == 0:  # signed links expire mid-run; mint a fresh one and retry
            fresh = _refresh(api, rec)
            if fresh and fresh != url:
                url = fresh
                rec["preview"] = fresh
                continue
            break
    return {"videoId": vid, "ok": False, "reason": reason}


def run(workers: int = 6) -> dict[str, str]:
    """Returns {videoId: preview path} for everything that downloaded."""
    ensure_dirs()
    records = (read_json(DATA / "enriched.json", {}) or {}).get("tracks", [])
    if not records:
        raise SystemExit("no data/enriched.json — run the enrich stage first")

    api = make_client()
    results: list[dict] = []
    with cf.ThreadPoolExecutor(max_workers=workers) as pool:
        for res in pool.map(lambda r: _download(api, r), records):
            results.append(res)

    ok = [r for r in results if r["ok"]]
    index = {r["videoId"]: r["path"] for r in ok}
    failures = [r for r in results if not r["ok"]]
    write_json(DATA / "previews.json", {"ok": len(ok), "total": len(results), "index": index,
                                        "failures": failures})
    # refreshed links are worth keeping for a re-run
    write_json(DATA / "enriched.json", {"tracks": records})
    print(f"previews: {len(ok)}/{len(results)} saved to data/previews/")
    if failures:
        reasons: dict[str, int] = {}
        for r in failures:
            key = r["reason"][:40]
            reasons[key] = reasons.get(key, 0) + 1
        print("  top failure reasons:",
              json.dumps(dict(sorted(reasons.items(), key=lambda x: -x[1])[:5])))
    return index
