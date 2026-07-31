#!/usr/bin/env python3
"""Cloud variant of the Odyssey seat tracker (runs on GitHub Actions).

Same logic as ~/odyssey-tracker/check_odyssey.py on Kushal's Mac: load AMC's
seat map for each target showtime in headless Chromium, count available
non-wheelchair seats, push to ntfy when any are open. State lives in state.json,
committed back to the repo by the workflow. ntfy topic comes from $NTFY_TOPIC.

Exits non-zero if every target check failed (e.g. AMC blocks the runner's IP),
so failed runs are visible in the Actions UI.
"""

import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

SEATS_URL = "https://www.amctheatres.com/showtimes/{sid}/seats"
# showtime IDs discovered 2026-07-30 from the Metreon showtimes page
TARGETS = [
    ("2026-08-07 10:00pm", "144696961"),
    ("2026-08-08 10:00am", "144696962"),
    ("2026-08-09 10:00am", "144696966"),
]
RENOTIFY_SECONDS = 2 * 3600
NTFY_TOPIC = os.environ["NTFY_TOPIC"]
STATE_FILE = Path(__file__).resolve().parent / "state.json"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
SEAT_CODE_RE = re.compile(r"\b[A-Z]{1,2}\d{1,2}$")


def log(msg):
    print(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {msg}", flush=True)


def notify(title, message, click_url=None, priority="urgent"):
    headers = {
        "Title": title.encode("ascii", "replace").decode(),
        "Priority": priority,
        "Tags": "movie_camera,cloud",
    }
    if click_url:
        headers["Click"] = click_url
    req = urllib.request.Request(
        f"https://ntfy.sh/{NTFY_TOPIC}", data=message.encode(), headers=headers
    )
    urllib.request.urlopen(req, timeout=15)


def count_open_seats(page, showtime_id):
    seats = []
    for attempt in range(2):
        page.goto(SEATS_URL.format(sid=showtime_id), wait_until="domcontentloaded",
                  timeout=60000)
        try:
            page.wait_for_function(
                """() => Array.from(document.querySelectorAll('[aria-label]'))
                    .filter(e => /\\b[A-Z]{1,2}\\d{1,2}$/.test(e.getAttribute('aria-label')))
                    .length > 50""",
                timeout=45000,
            )
        except Exception:
            pass
        labels = page.eval_on_selector_all(
            "[aria-label]", "els => els.map(e => e.getAttribute('aria-label'))"
        )
        seats = [l for l in labels if l and SEAT_CODE_RE.search(l)]
        if seats:
            break
    if not seats:
        raise RuntimeError("no seat elements found (page blocked or layout changed)")
    avail = [l for l in seats if not l.startswith("Occupied")]
    regular = [l for l in avail if "Wheelchair" not in l and "Companion" not in l]
    return len(regular), len(avail), len(seats)


def main():
    if datetime.now(timezone.utc) > datetime(2026, 8, 10, 7, tzinfo=timezone.utc):
        log("past Aug 9 2026 targets (PT); nothing to do — disable the workflow")
        return 0

    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    now = time.time()
    failures = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(user_agent=UA, viewport={"width": 1400, "height": 1000})
        page = ctx.new_page()
        for key, sid in TARGETS:
            prev = state.get(key, {})
            entry = dict(prev)
            try:
                regular, avail, total = count_open_seats(page, sid)
                entry["status"] = "open" if regular > 0 else "full"
                log(f"{key}: {regular} regular seats open "
                    f"({avail} incl. wheelchair, {total} total) [id {sid}]")
                just_opened = regular > 0 and prev.get("status") != "open"
                renotify = regular > 0 and now - prev.get("last_notified", 0) > RENOTIFY_SECONDS
                if just_opened or renotify:
                    notify(
                        "Odyssey IMAX 70mm - Metreon",
                        f"{key}: {regular} regular seat(s) OPEN - tap to pick seats",
                        click_url=SEATS_URL.format(sid=sid),
                    )
                    entry["last_notified"] = now
            except Exception as e:
                failures += 1
                log(f"{key}: check failed: {e}")
            state[key] = entry
        browser.close()

    STATE_FILE.write_text(json.dumps(state, indent=1))
    if failures == len(TARGETS):
        log("all targets failed — runner is likely blocked by AMC")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
