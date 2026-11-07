#!/usr/bin/env python3
"""Fleet helpers for the seat-watch workflow (stdlib only, so they work on
self-hosted runners without the gh CLI).

  python3 fleet_count.py            -> prints active+queued seat-watch runs, excluding this run
  python3 fleet_count.py dispatch   -> workflow_dispatch one seat-watch run on this branch

Needs GH_TOKEN, GITHUB_REPOSITORY, GITHUB_RUN_ID and GITHUB_REF_NAME from the runner env.
"""
import json
import os
import sys
import urllib.request

API = "https://api.github.com"
REPO = os.environ["GITHUB_REPOSITORY"]
HEADERS = {
    "Authorization": f"Bearer {os.environ['GH_TOKEN']}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


def api(path, data=None):
    req = urllib.request.Request(API + path, headers=HEADERS,
                                 data=json.dumps(data).encode() if data is not None else None,
                                 method="POST" if data is not None else "GET")
    with urllib.request.urlopen(req, timeout=30) as r:
        body = r.read()
        return json.loads(body) if body else {}


def count_others():
    me = os.environ.get("GITHUB_RUN_ID")
    n = 0
    for status in ("in_progress", "queued"):
        runs = api(f"/repos/{REPO}/actions/workflows/watch.yml/runs?status={status}&per_page=50")
        n += sum(1 for r in runs.get("workflow_runs", []) if str(r["id"]) != me)
    return n


def dispatch():
    api(f"/repos/{REPO}/actions/workflows/watch.yml/dispatches",
        {"ref": os.environ.get("GITHUB_REF_NAME", "main")})


if __name__ == "__main__":
    if sys.argv[1:] == ["dispatch"]:
        dispatch()
    else:
        print(count_others())
