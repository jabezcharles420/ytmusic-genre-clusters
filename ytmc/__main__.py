"""CLI: python -m ytmc <stage> [--authuser N]"""

from __future__ import annotations

import argparse
import sys

STAGES = ("fetch", "enrich", "previews", "tags", "embed", "cluster", "report",
          "site", "publish", "delete", "all")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ytmc", description="Cluster YouTube Music liked songs.")
    p.add_argument("stage", choices=STAGES)
    p.add_argument("--authuser", type=int, default=1,
                   help="Google account index in the browser (default 1)")
    p.add_argument("--workers", type=int, default=None)
    p.add_argument("--min-cluster-size", type=int, default=4,
                   help="HDBSCAN minimum cluster size (lower = more clusters)")
    p.add_argument("--merge-threshold", type=float, default=0.98,
                   help="merge clusters whose style centroids are this similar")
    p.add_argument("--publish", action="store_true",
                   help="create playlists in the account (stage 'all' skips them otherwise)")
    p.add_argument("--state", default=None,
                   help="state file for the delete stage, e.g. data/published-coarse-37.json")
    args = p.parse_args(argv)

    stage = args.stage
    workers = args.workers

    if stage in ("fetch", "all"):
        from . import fetch as m

        m.run(args.authuser)
    if stage in ("enrich", "all"):
        from . import enrich as m

        m.run(workers or 6)
    if stage in ("previews", "all"):
        from . import previews as m

        m.run(workers or 8)
    if stage in ("tags", "all"):
        from . import tags as m

        m.run(workers or 4)
    if stage in ("embed", "all"):
        from . import embed as m

        m.run(workers or 4)
    if stage in ("cluster", "all"):
        from . import cluster as m

        m.run(min_cluster_size=args.min_cluster_size, merge_threshold=args.merge_threshold)
    if stage in ("report", "all"):
        from . import report as m

        m.run()
    if stage in ("site", "all"):
        from . import site as m

        m.run()
    if stage == "publish" or (stage == "all" and args.publish):
        from . import publish as m

        m.run(args.authuser)
    if stage == "delete":
        from . import publish as m

        if not args.state:
            p.error("the delete stage needs --state <file>")
        m.delete(args.state, args.authuser)
    return 0


if __name__ == "__main__":
    sys.exit(main())
