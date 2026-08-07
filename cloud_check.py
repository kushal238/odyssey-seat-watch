#!/usr/bin/env python3
"""Cloud seat checker for The Odyssey IMAX 70mm at AMC Metreon 16.

Fast-path only: plain HTTP with cookies minted on Kushal's Mac (uploaded as the
AMC_COOKIES secret whenever the local tracker refreshes them). Verified
2026-08-04 that these cookies work from datacenter IPs. Runs on a GitHub
Actions cron; state.json is committed back by the workflow.

If cookies go stale the checks fail; after CONSEC_FAIL_ALERT consecutive
failures one "wake the Mac" alert is pushed, and success resets the cycle.
"""

import gzip
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

SEATS_URL = "https://www.amctheatres.com/showtimes/{sid}/seats"
TARGETS = [
    ("2026-08-07 10:00pm", "144696961"),
    ("2026-08-08 10:00am", "144696962"),
    ("2026-08-09 10:00am", "144696966"),
]
RENOTIFY_SECONDS = 2 * 3600
CONSEC_FAIL_ALERT = 120  # ~2h of failed 1-min cycles before the stale-cookie alert
LOOP_MINUTES = int(os.environ.get("LOOP_MINUTES", "27"))
NTFY_TOPIC = os.environ["NTFY_TOPIC"]
STATE_FILE = Path(__file__).resolve().parent / "state.json"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
LABEL_RE = re.compile(r'aria-label="([^"]+)"')
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


def fetch_counts(sid, cookie_hdr):
    req = urllib.request.Request(
        SEATS_URL.format(sid=sid),
        headers={
            "User-Agent": UA,
            "Cookie": cookie_hdr,
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
    html = raw.decode("utf-8", "replace")
    if "queue.amctheatres.com" in html or "queue-it" in html.lower():
        raise RuntimeError("queue page - cookies stale")
    seats = [l for l in LABEL_RE.findall(html) if SEAT_CODE_RE.search(l)]
    if not seats:
        raise RuntimeError("no seats in HTML")
    avail = [l for l in seats if not l.startswith("Occupied")]
    regular = [l for l in avail if "Wheelchair" not in l and "Companion" not in l]
    return len(regular), len(avail), len(seats)


def check_cycle(state, cookie_hdr, verbose):
    now = time.time()
    run_failed = False

    for key, sid in TARGETS:
        prev = state.get(key, {})
        entry = dict(prev)
        try:
            regular, avail, total = fetch_counts(sid, cookie_hdr)
        except Exception as e:
            log(f"{key}: check failed: {e}")
            run_failed = True
            state[key] = entry
            continue
        entry["status"] = "open" if regular > 0 else "full"
        changed = [regular, avail] != prev.get("last_counts")
        entry["last_counts"] = [regular, avail]
        if verbose or changed:
            log(f"{key}: {regular} regular seats open ({avail} incl. wheelchair, {total} total)")
        just_opened = regular > 0 and prev.get("status") != "open"
        renotify = regular > 0 and now - prev.get("last_notified", 0) > RENOTIFY_SECONDS
        if just_opened or renotify:
            notify(
                "Odyssey IMAX 70mm - Metreon",
                f"{key}: {regular} regular seat(s) OPEN - tap to pick seats",
                click_url=SEATS_URL.format(sid=sid),
            )
            entry["last_notified"] = now
        state[key] = entry

    if run_failed:
        fails = state.get("_consecutive_failures", 0) + 1
        state["_consecutive_failures"] = fails
        if fails == CONSEC_FAIL_ALERT:
            notify(
                "Cloud checker: cookies stale",
                "Cloud seat checks failing ~2h. Wake the Mac (plug in, lid open) "
                "so it can mint fresh cookies.",
                priority="high",
            )
    else:
        state["_consecutive_failures"] = 0

    STATE_FILE.write_text(json.dumps(state, indent=1))


def mint_cookies_in_runner():
    """Try to earn a fresh session from this runner's own IP (rotates per job).
    Returns a cookie list on success, None if the bot wall blocks the runner."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(user_agent=UA,
                                      viewport={"width": 1400, "height": 1000})
            ctx.route("**/*", lambda route: route.abort()
                      if route.request.resource_type in ("image", "media", "font")
                      else route.continue_())
            page = ctx.new_page()
            page.goto(SEATS_URL.format(sid=TARGETS[0][1]),
                      wait_until="domcontentloaded", timeout=60000)
            page.wait_for_function(
                """() => Array.from(document.querySelectorAll('[aria-label]'))
                    .filter(e => /\\b[A-Z]{1,2}\\d{1,2}$/.test(e.getAttribute('aria-label')))
                    .length > 50""",
                timeout=45000,
            )
            cookies = ctx.cookies()
            browser.close()
            log("self-minted fresh cookies from runner IP")
            return cookies
    except Exception as e:
        log(f"self-mint failed ({type(e).__name__}); falling back to secret cookies")
        return None


def main():
    if datetime.now(timezone.utc) > datetime(2026, 8, 10, 7, tzinfo=timezone.utc):
        log("past targets; disable the workflow")
        return 0

    cookies = mint_cookies_in_runner() or json.loads(os.environ["AMC_COOKIES"])
    cookie_hdr = "; ".join(
        f"{c['name']}={c['value']}" for c in cookies if "amctheatres.com" in c["domain"]
    )
    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}

    if os.environ.get("TEST_PING") == "1":
        notify("Cloud test ping", "This check ran on GitHub's servers, not your Mac. "
               "Cloud checks are live at ~1/min.", priority="high")
        log("test ping sent")

    # stagger parallel runs so their 60s cycles interleave across runner IPs
    stagger = int(os.environ.get("GITHUB_RUN_ID", "0")) % 45
    log(f"stagger offset: {stagger}s")
    time.sleep(stagger)
    for i in range(LOOP_MINUTES):
        check_cycle(state, cookie_hdr, verbose=(i == 0))
        if i < LOOP_MINUTES - 1:
            time.sleep(60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
