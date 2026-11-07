#!/usr/bin/env python3
"""Cloud seat checker: alerts when a regular seat opens for the showtimes in
targets.json (currently Dune: Part Three, IMAX 70mm, AMC Metreon 16).

Each run self-mints an AMC session from the runner's own IP with headless
Chromium, then polls one showtime per minute over plain HTTP with those
cookies. AMC_COOKIES (a repo secret, optional, historically uploaded from
Kushal's Mac) is only a fallback if self-minting is blocked. state.json is
committed back by the workflow. After CONSEC_FAIL_ALERT consecutive failed
cycles one "checks failing" alert is pushed; success resets the cycle.

Targets come from targets.json, written by discover_showtimes.py.
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
TARGETS_FILE = Path(__file__).resolve().parent / "targets.json"
_TARGETS_DATA = json.loads(TARGETS_FILE.read_text())
TARGETS = [(t["label"], t["sid"]) for t in _TARGETS_DATA["targets"] if t.get("sid")]
MOVIE_NAME = _TARGETS_DATA["config"]["movie_name"]
RETIRE_AFTER = datetime.fromisoformat(_TARGETS_DATA["config"]["retire_after_utc"])
RENOTIFY_SECONDS = 24 * 3600
# Kushal only wants alerts for middle rows F-M (single-letter rows, any seat number).
MID_ROW_RE = re.compile(r"^[F-M]\d{1,2}$")
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
    codes = [SEAT_CODE_RE.search(l).group(0) for l in regular]
    mid_codes = [c for c in codes if MID_ROW_RE.match(c)]
    return len(regular), len(avail), len(seats), codes, mid_codes


def check_cycle(state, cookie_hdr, verbose, only=None):
    now = time.time()
    run_failed = False

    targets = [only] if only else TARGETS
    for key, sid in targets:
        prev = state.get(key, {})
        entry = dict(prev)
        try:
            regular, avail, total, codes, mid_codes = fetch_counts(sid, cookie_hdr)
        except Exception as e:
            log(f"{key}: check failed: {e}")
            run_failed = True
            state[key] = entry
            continue
        mid = len(mid_codes)
        entry["status"] = "open" if mid > 0 else "full"
        changed = [regular, avail, mid] != prev.get("last_counts")
        entry["last_counts"] = [regular, avail, mid]
        if verbose or changed:
            log(f"{key}: {mid} F-M seats open ({regular} regular any row, "
                f"{avail} incl. wheelchair, {total} total)")
        just_opened = mid > 0 and prev.get("status") != "open"
        renotify = mid > 0 and now - prev.get("last_notified", 0) > RENOTIFY_SECONDS
        if just_opened or renotify:
            notify(
                f"{MOVIE_NAME} IMAX 70mm - Metreon",
                f"{key}: {mid} F-M seat(s) OPEN: {', '.join(mid_codes[:8])} - tap to buy",
                click_url=SEATS_URL.format(sid=sid),
            )
            entry["last_notified"] = now
        state[key] = entry

    if run_failed:
        fails = state.get("_consecutive_failures", 0) + 1
        state["_consecutive_failures"] = fails
        if fails == CONSEC_FAIL_ALERT:
            notify(
                "Cloud checker: checks failing",
                "Cloud seat checks have failed for ~2h (self-mint blocked and no "
                "working AMC_COOKIES). Check the seat-watch Actions logs.",
                priority="high",
            )
    else:
        state["_consecutive_failures"] = 0

    STATE_FILE.write_text(json.dumps(state, indent=1))
    return run_failed


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
    if datetime.now(timezone.utc) > RETIRE_AFTER:
        log("past targets; disable the workflow")
        return 0
    if not TARGETS:
        log("targets.json has no targets yet; run the discover-showtimes workflow")
        return 0

    cookies = mint_cookies_in_runner() or json.loads(os.environ.get("AMC_COOKIES") or "[]")
    cookie_hdr = "; ".join(
        f"{c['name']}={c['value']}" for c in cookies if "amctheatres.com" in c.get("domain", "")
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
    deaf_streak = 0
    remints = 0
    for i in range(LOOP_MINUTES):
        target = TARGETS[i % len(TARGETS)]
        failed = check_cycle(state, cookie_hdr, verbose=(i == 0), only=target)
        deaf_streak = deaf_streak + 1 if failed else 0
        if deaf_streak >= 4 and remints < 2:
            log("session deaf; re-minting mid-run")
            fresh = mint_cookies_in_runner()
            if fresh:
                cookie_hdr = "; ".join(f"{c['name']}={c['value']}" for c in fresh
                                       if "amctheatres.com" in c["domain"])
            remints += 1
            deaf_streak = 0
        if i < LOOP_MINUTES - 1:
            time.sleep(60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
