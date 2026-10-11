"""Stage 7 — create one private playlist per cluster in YouTube Music."""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

from .util import DATA, read_json, write_json

BATCH = 25
SKIP_SMALLER_THAN = 4


def _fingerprint(video_ids: list[str]) -> str:
    return hashlib.sha1("\n".join(sorted(video_ids)).encode()).hexdigest()[:12]


def delete(state_file: str, authuser: int = 1) -> int:
    """Delete every playlist recorded in a published-state file.

    Only IDs recorded in that file are touched, so this can never reach
    playlists created by hand or by an earlier generation of this tool.
    """
    state = read_json(Path(state_file), {}) or {}
    if not state:
        raise SystemExit(f"no playlists recorded in {state_file}")
    from . import auth

    yt = auth.client(authuser)
    removed = 0
    for name, entry in state.items():
        playlist_id = entry.get("playlistId")
        if not playlist_id:
            continue
        try:
            yt.delete_playlist(playlist_id)
            removed += 1
            print(f"  deleted {name!r} ({playlist_id})")
        except Exception as exc:
            print(f"  ! could not delete {name!r}: {str(exc)[:90]}")
        time.sleep(0.3)
    print(f"delete: removed {removed}/{len(state)} playlists from {state_file}")
    return removed


def run(authuser: int = 1, dry_run: bool = False) -> dict:
    clusters = read_json(DATA / "clusters.json", {}) or {}
    if not clusters:
        raise SystemExit("no data/clusters.json — run the cluster stage first")
    library = read_json(DATA / "library.json", {}) or {}
    titles = {t["videoId"]: t for t in library.get("tracks", [])}
    state_path = DATA / "published.json"
    state = read_json(state_path, {}) or {}

    targets = [c for c in clusters["clusters"] if c["size"] >= SKIP_SMALLER_THAN]
    plan = [(c, [v for v in c["videoIds"] if v in titles and v not in state.get(c["name"], {}).get("videoIds", [])])
            for c in targets]
    print(f"publish: {len(targets)} playlists, {sum(len(v) for _, v in plan)} items")
    if dry_run:
        for c, vids in plan:
            print(f"  {c['name']:<28} {c['size']:>4} songs ({len(vids)} new)")
        return {}

    from . import auth

    yt = auth.client(authuser)
    created = 0
    for c, vids in plan:
        fingerprint = _fingerprint(c["videoIds"])
        entry = state.get(c["name"])
        if entry and entry.get("fingerprint") not in (None, fingerprint):
            # Same name, different songs: a re-cluster with new settings. Start a
            # fresh playlist rather than silently merging two generations.
            entry.setdefault("superseded", []).append(entry.get("playlistId"))
            entry = None
        if entry is None:
            desc = (f"Auto-clustered by sound. Styles: {', '.join(c['styles'][:4])}. "
                    f"Tags: {', '.join(c['tags'][:6])}. "
                    f"Mean BPM {c['bpm'] or 'n/a'}. Source: Liked Music.")
            playlist_id = yt.create_playlist(f"{c['name'].title()} · {c['size']}", desc[:1000],
                                             privacy_status="PRIVATE")
            if isinstance(playlist_id, dict):  # some clients wrap the id
                playlist_id = playlist_id.get("playlistId") or playlist_id.get("id")
            entry = {"playlistId": playlist_id, "videoIds": [], "fingerprint": fingerprint}
            if c["name"] in state:
                entry["superseded"] = state[c["name"]].get("superseded", [])
            state[c["name"]] = entry
            created += 1
            print(f"  created {c['name']!r} -> {playlist_id}")
        for i in range(0, len(vids), BATCH):
            chunk = vids[i:i + BATCH]
            try:
                yt.add_playlist_items(entry["playlistId"], chunk)
                entry["videoIds"].extend(chunk)
            except Exception as exc:
                print(f"  ! {c['name']} batch {i//BATCH}: {str(exc)[:100]}")
            time.sleep(0.4)
        write_json(state_path, state)

    print(f"publish: {created} new playlists, {len(state)} total tracked -> data/published.json")
    return state
