#!/usr/bin/env python3
"""Bulk-assign (or remove) a taste category for every item under a folder.

One-off helper: "everything under this folder, recursively, is Sci-Fi".
It talks to a running Metascan server over HTTP and needs nothing else from
the repository, so it can be deleted without touching anything.

Usage:
    python scripts/assign_category.py FOLDER --category NAME
        [--remove] [--dry-run] [--no-create] [--server http://localhost:8700]

Sends ``Authorization: Bearer $METASCAN_API_KEY`` when that variable is set.
Exit codes: 0 done; 1 server unreachable or answered with an error; 2 the
category does not exist and --no-create was given.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Dict, List, Optional

import httpx

CHUNK = 500
DEFAULT_SERVER = "http://localhost:8700"


def _components(path: str) -> List[str]:
    return [p for p in path.replace("\\", "/").rstrip("/").split("/")]


def _has_drive(path: str) -> bool:
    return len(path) >= 2 and path[0].isalpha() and path[1] == ":"


def is_under(path: str, folder: str) -> bool:
    """True when ``path`` lies inside ``folder`` (compared by components)."""
    fold = _components(folder)
    parts = _components(path)
    if _has_drive(path) or _has_drive(folder):
        fold = [c.lower() for c in fold]
        parts = [c.lower() for c in parts]
    return len(parts) > len(fold) and parts[: len(fold)] == fold


def _headers() -> Dict[str, str]:
    key = os.environ.get("METASCAN_API_KEY", "")
    return {"Authorization": f"Bearer {key}"} if key else {}


def _find_category(
    client: httpx.Client, name: str, headers: Dict[str, str]
) -> Optional[int]:
    resp = client.get("/api/categories", headers=headers)
    resp.raise_for_status()
    wanted = name.casefold()
    for cat in resp.json():
        if str(cat["name"]).casefold() == wanted:
            return int(cat["id"])
    return None


def _run(client: httpx.Client, args: argparse.Namespace) -> int:
    headers = _headers()
    resp = client.get("/api/media", params={"include_hidden": "true"}, headers=headers)
    resp.raise_for_status()
    paths = [
        m["file_path"] for m in resp.json() if is_under(m["file_path"], args.folder)
    ]
    n = len(paths)

    if args.dry_run:
        print(f"{n} items under {args.folder} (dry run, nothing changed)")
        return 0

    cat_id = _find_category(client, args.category, headers)
    if cat_id is None:
        if args.no_create or args.remove:
            print(f"error: no category named {args.category!r}", file=sys.stderr)
            return 2
        created = client.post(
            "/api/categories", json={"name": args.category}, headers=headers
        )
        created.raise_for_status()
        cat_id = int(created.json()["id"])

    total = 0
    url = f"/api/categories/{cat_id}/items"
    for i in range(0, n, CHUNK):
        chunk = paths[i : i + CHUNK]
        if args.remove:
            r = client.request(
                "DELETE", url, json={"file_paths": chunk}, headers=headers
            )
            r.raise_for_status()
            total += int(r.json()["removed"])
        else:
            r = client.post(
                url, json={"file_paths": chunk, "source": "bulk"}, headers=headers
            )
            r.raise_for_status()
            total += int(r.json()["added"])
    verb = "removed" if args.remove else "added"
    print(f"{n} items under {args.folder}; {verb} {total}")
    return 0


def main(
    argv: Optional[List[str]] = None, client: Optional[httpx.Client] = None
) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("folder", help="folder whose whole tree is affected")
    p.add_argument("--category", required=True, help="category name")
    p.add_argument("--remove", action="store_true", help="remove instead of add")
    p.add_argument("--dry-run", action="store_true", help="count only, no writes")
    p.add_argument(
        "--no-create", action="store_true", help="fail if the category is missing"
    )
    p.add_argument("--server", default=DEFAULT_SERVER)
    args = p.parse_args(argv)

    own = client is None
    http = client or httpx.Client(base_url=args.server, timeout=120.0)
    try:
        return _run(http, args)
    except httpx.HTTPStatusError as exc:
        print(
            f"error: server answered {exc.response.status_code} for "
            f"{exc.request.method} {exc.request.url.path}",
            file=sys.stderr,
        )
        return 1
    except httpx.HTTPError as exc:
        print(f"error: cannot reach the server: {exc}", file=sys.stderr)
        return 1
    finally:
        if own:
            http.close()


if __name__ == "__main__":
    sys.exit(main())
