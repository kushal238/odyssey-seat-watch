#!/usr/bin/env python3
"""Standalone seat poller for friends' laptops ("fleet of friends").

Zero setup beyond Python + Playwright, no accounts, no secrets:

    pip install playwright && playwright install chromium
    python3 friend_watch.py --name "Priya"

What it does: loads the AMC seat page for each target showtime in a real
headless Chromium, ONE page load per minute total (rotating across targets),
images/fonts blocked, and pushes a phone alert with the seat codes when a
regular (non-wheelchair) seat opens. Targets, movie and ntfy topic are pulled
from the repo's targets.json (re-fetched every 30 min) so the organiser can
change them centrally. Exits by itself after the retire date.

Rate discipline is the whole point: never run two of these on the same
connection, and never lower --poll below 60. If AMC blocks or rate-limits this
IP the script backs off (up to 30 min) and tells you; it does not retry harder.
"""

import argparse
import json
import random
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

TARGETS_URL = ("https://raw.githubusercontent.com/kushal238/odyssey-seat-watch/"
               "main/targets.json")
SEATS_URL = "https://www.amctheatres.com/showtimes/{sid}/seats"
SEAT_CODE_RE = re.compile(r"\b[A-Z]{1,2}\d{1,2}$")
BLOCK_TITLES = ("attention required", "access denied", "just a moment")


def log(msg):
    print(f"{datetime.now().strftime('%H:%M:%S')} {msg}", flush=True)


def fetch_targets(path_or_url):
    if path_or_url.startswith("http"):
        with urllib.request.urlopen(path_or_url, timeout=20) as r:
            return json.loads(r.read())
    return json.loads(Path(path_or_url).read_text())


def notify(topic, title, message, click_url=None, priority="urgent"):
    headers = {"Title": title.encode("ascii", "replace").decode(),
               "Priority": priority, "Tags": "movie_camera,busts_in_silhouette"}
    if click_url:
        headers["Click"] = click_url
    req = urllib.request.Request(f"https://ntfy.sh/{topic}", data=message.encode(),
                                 headers=headers)
    try:
        urllib.request.urlopen(req, timeout=15)
    except Exception as e:
        log(f"ntfy failed: {e}")


def read_seats(page, sid):
    """Load a seat page; return (regular_open_codes, total_seats) or raise."""
    resp = page.goto(SEATS_URL.format(sid=sid), wait_until="domcontentloaded",
                     timeout=60000)
    status = resp.status if resp else 0
    if status == 429:
        raise RuntimeError("429 rate limited")
    title = (page.title() or "").lower()
    if status == 403 or any(t in title for t in BLOCK_TITLES):
        raise RuntimeError(f"blocked (HTTP {status}, title {title!r})")
    page.wait_for_function(
        """() => Array.from(document.querySelectorAll('[aria-label]'))
            .filter(e => /\\b[A-Z]{1,2}\\d{1,2}$/.test(e.getAttribute('aria-label')))
            .length > 20""",
        timeout=45000,
    )
    labels = page.evaluate(
        "() => Array.from(document.querySelectorAll('[aria-label]'))"
        ".map(e => e.getAttribute('aria-label'))")
    seats = [l for l in labels if SEAT_CODE_RE.search(l)]
    avail = [l for l in seats if not l.startswith("Occupied")]
    regular = [l for l in avail if "Wheelchair" not in l and "Companion" not in l]
    return [SEAT_CODE_RE.search(l).group(0) for l in regular], len(seats)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="a friend", help="shows up in alerts, e.g. your first name")
    ap.add_argument("--targets", default=TARGETS_URL, help="targets.json URL or path")
    ap.add_argument("--poll", type=int, default=None, help="seconds between page loads (min 60)")
    ap.add_argument("--once", action="store_true", help="check every target once and exit")
    args = ap.parse_args()

    data = fetch_targets(args.targets)
    cfg = data["config"]
    topic = cfg["ntfy_topic"]
    poll = max(60, args.poll or cfg.get("poll_seconds", 60))
    renotify = cfg.get("renotify_seconds", 7200)
    retire = datetime.fromisoformat(cfg["retire_after_utc"])
    targets = [t for t in data["targets"] if t.get("sid")]
    if not targets:
        log("targets.json has no showtimes with AMC ids yet; ask the organiser, then rerun")
        return 1
    log(f"{cfg['movie_name']}: watching {len(targets)} showtime(s) every {poll}s as {args.name!r}")
    for t in targets:
        log(f"  {t['label']}  [{t.get('format', '?')}]  {SEATS_URL.format(sid=t['sid'])}")

    from playwright.sync_api import sync_playwright

    state = {}            # label -> {"open": bool, "last_notified": ts}
    backoff = 0           # seconds; grows on block/429, resets on success
    last_refresh = time.time()
    i = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1400, "height": 1000})
        ctx.route("**/*", lambda route: route.abort()
                  if route.request.resource_type in ("image", "media", "font")
                  else route.continue_())
        page = ctx.new_page()
        if not args.once:
            delay = random.randint(0, poll - 1)   # stagger vs other friends
            log(f"starting in {delay}s")
            time.sleep(delay)

        while True:
            if datetime.now(timezone.utc) > retire:
                log("past the last showtime; done. Thanks for helping!")
                break
            if time.time() - last_refresh > 1800:
                try:
                    data = fetch_targets(args.targets)
                    fresh = [t for t in data["targets"] if t.get("sid")]
                    if fresh and [t["sid"] for t in fresh] != [t["sid"] for t in targets]:
                        targets = fresh
                        log(f"targets updated: {[t['label'] for t in targets]}")
                except Exception as e:
                    log(f"could not refresh targets ({e}); keeping current list")
                last_refresh = time.time()

            t = targets[i % len(targets)]
            i += 1
            label, sid = t["label"], t["sid"]
            prev = state.setdefault(label, {"open": False, "last_notified": 0})
            try:
                codes, total = read_seats(page, sid)
                backoff = 0
                now = time.time()
                log(f"{label}: {len(codes)} regular open / {total} seats"
                    + (f"  -> {', '.join(codes[:8])}" if codes else ""))
                if codes and (not prev["open"] or now - prev["last_notified"] > renotify):
                    notify(topic, f"{cfg['movie_name']} IMAX 70mm - Metreon",
                           f"{label}: {len(codes)} seat(s) OPEN: {', '.join(codes[:8])}"
                           f" - tap to buy (seen by {args.name})",
                           click_url=SEATS_URL.format(sid=sid))
                    prev["last_notified"] = now
                prev["open"] = bool(codes)
            except Exception as e:
                msg = str(e).split("\n")[0][:160]
                log(f"{label}: check failed: {msg}")
                if "429" in msg or "blocked" in msg:
                    backoff = min(max(backoff * 2, 120), 1800)
                    log(f"AMC is limiting this connection; backing off {backoff}s "
                        "(this is expected now and then — do NOT restart faster)")
                elif "Target" in msg or "closed" in msg:
                    page = ctx.new_page()   # browser hiccup; fresh tab

            if args.once and i >= len(targets):
                break
            time.sleep(poll + backoff)
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
