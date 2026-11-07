#!/usr/bin/env python3
"""Seat checker that reads Fandango's seat map instead of AMC's.

Why: since Nov 2026 AMC's Cloudflare hard-blocks datacenter IPs (error 1020),
but Fandango sells the same AMC reserved seats and serves its pages to GitHub
runners. Verified 2026-11-07 from an Anthropic sandbox: the Metreon listing
and the seat selection page both load; the seat picker fetches availability
JSON from /transaction/ticketing/seatpicker/Default.aspx?action=availability
with seat status A/H/O/R/S and type standard/premium/wheelchair/companion.

Per cycle this loads ONE seat selection page in headless Chromium (images,
fonts and media blocked), captures that availability response, and counts
open non-wheelchair seats. Same alert semantics as cloud_check.py: push on the
transition to open, re-push every RENOTIFY_SECONDS while open, seat codes in
the message. One page load per minute per IP, same rate discipline as before.

Targets come from targets.json; each needs a "fandango" object:
  {"tid": "AANEM", "mid": "245188", "sdate": "2026-12-25", "t": "11:00"}
and may carry "sid" (the AMC showtime id) for the tap-to-buy link.

DRY_RUN=1: one pass over every target, verbose dump of what the availability
JSON looks like (so the parser can be checked against reality), no ntfy, no
state write.
"""

import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGETS_FILE = HERE / "targets.json"
STATE_FILE = HERE / "state.json"
DATA = json.loads(TARGETS_FILE.read_text())
CFG = DATA["config"]
TARGETS = [t for t in DATA["targets"] if t.get("fandango")]
MOVIE_NAME = CFG["movie_name"]
RETIRE_AFTER = datetime.fromisoformat(CFG["retire_after_utc"])
RENOTIFY_SECONDS = CFG.get("renotify_seconds", 7200)
CONSEC_FAIL_ALERT = 120
LOOP_MINUTES = int(os.environ.get("LOOP_MINUTES", "27"))
DRY_RUN = os.environ.get("DRY_RUN") == "1"
NTFY_TOPIC = os.environ.get("NTFY_TOPIC") or CFG.get("ntfy_topic")

SEATSEL_URL = ("https://tickets.fandango.com/mobileexpress/seatselection"
               "?mid={mid}&chainCode=AMC&sdate={sdate}%20{t}&tid={tid}")
THEATER_URL = "https://www.fandango.com/amc-metreon-16-aanem/theater-page?date={sdate}"
BUY_URL_FANDANGO = ("https://tickets.fandango.com/transaction/ticketing/mobile/jump.aspx"
                    "?sdate={sdate}%2B{t}&mid={mid}&tid={tid}&dfam=webbrowser")
BUY_URL_AMC = "https://www.amctheatres.com/showtimes/{sid}/seats"
SEAT_CODE_RE = re.compile(r"^[A-Z]{1,2}\d{1,2}$")
NOT_REGULAR_RE = re.compile(r"wheelchair|companion", re.I)


def log(msg):
    print(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {msg}", flush=True)


def notify(title, message, click_url=None, priority="urgent"):
    if DRY_RUN or not NTFY_TOPIC:
        log(f"(notify suppressed) {title}: {message}")
        return
    headers = {"Title": title.encode("ascii", "replace").decode(),
               "Priority": priority, "Tags": "movie_camera,cloud"}
    if click_url:
        headers["Click"] = click_url
    req = urllib.request.Request(f"https://ntfy.sh/{NTFY_TOPIC}", data=message.encode(),
                                 headers=headers)
    urllib.request.urlopen(req, timeout=15)


def buy_url(t):
    if t.get("sid"):
        return BUY_URL_AMC.format(sid=t["sid"])
    return BUY_URL_FANDANGO.format(**t["fandango"])


# ---------------------------------------------------------------- parsing

def iter_seat_dicts(obj, depth=0):
    """Yield every dict that looks like a seat: has a 'status' string and some id/name."""
    if depth > 12:
        return
    if isinstance(obj, dict):
        st = obj.get("status")
        if isinstance(st, str) and len(st) <= 2 and any(k in obj for k in ("id", "name", "seat_number", "number", "label")):
            yield obj
            return
        for v in obj.values():
            yield from iter_seat_dicts(v, depth + 1)
    elif isinstance(obj, list):
        for v in obj:
            yield from iter_seat_dicts(v, depth + 1)


def seat_code(seat):
    for k in ("name", "label", "id", "seat_number", "number"):
        v = seat.get(k)
        if isinstance(v, str) and SEAT_CODE_RE.match(v.strip().upper()):
            return v.strip().upper()
    row = seat.get("row") or seat.get("row_name") or seat.get("rowName")
    num = seat.get("number") or seat.get("seat_number") or seat.get("seatNumber")
    if row and num:
        return f"{row}{num}"
    return str(seat.get("id") or seat.get("name") or "?")


def seat_is_regular(seat):
    blob = json.dumps(seat).lower()
    return not NOT_REGULAR_RE.search(blob)


def summarize(avail_json):
    seats = list(iter_seat_dicts(avail_json))
    available = [s for s in seats if s.get("status") == "A"]
    regular = [s for s in available if seat_is_regular(s)]
    codes = sorted(seat_code(s) for s in regular)
    return len(regular), len(available), len(seats), codes, seats


def structure(obj, depth=0, path="", out=None, maxlines=60):
    """Compact key/type outline of a JSON blob for the dry-run log."""
    out = out if out is not None else []
    if len(out) >= maxlines:
        return out
    if isinstance(obj, dict):
        for k, v in list(obj.items())[:30]:
            if isinstance(v, (dict, list)):
                out.append(f"{path}/{k}: {type(v).__name__}{'[%d]' % len(v) if isinstance(v, list) else ''}")
                structure(v, depth + 1, path + "/" + k, out, maxlines)
            else:
                out.append(f"{path}/{k} = {str(v)[:50]}")
    elif isinstance(obj, list) and obj:
        structure(obj[0], depth + 1, path + "[0]", out, maxlines)
    return out


# ---------------------------------------------------------------- browser

def resolve_jump_links(ctx, targets):
    """Load the theatre listing once per date and pick up each target's real
    "Check Seats" link (jump.aspx with Fandango's showtime hash). Entering the
    checkout through that link is what sets up the order context the seat
    picker needs; hitting seatselection directly shows "not found"."""
    by_date = {}
    for t in targets:
        by_date.setdefault(t["fandango"]["sdate"], []).append(t)
    page = ctx.new_page()
    for sdate, ts in by_date.items():
        url = THEATER_URL.format(sdate=sdate)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_selector('a[href*="jump.aspx"]', timeout=45000)
            time.sleep(3)
            hrefs = page.evaluate(
                "() => Array.from(document.querySelectorAll('a[href*=\"jump.aspx\"]')).map(a => a.href)")
        except Exception as e:
            log(f"theater page {sdate}: {type(e).__name__}: {str(e)[:150]}; using plain jump links")
            hrefs = []
        for t in ts:
            f = t["fandango"]
            want_sdate = f"{f['sdate']}+{f['t']}"
            match = None
            for h in hrefs:
                q = urllib.parse.parse_qs(urllib.parse.urlparse(h).query)
                if q.get("mid", [""])[0] == f["mid"] and q.get("tid", [""])[0] == f["tid"] \
                        and q.get("sdate", [""])[0].replace(" ", "+") == want_sdate:
                    match = h
                    break
            t["_jump"] = match or BUY_URL_FANDANGO.format(**f)
            log(f"{t['label']}: entry link {'from listing' if match else 'FALLBACK (no listing match)'}: {t['_jump'][:140]}")
        time.sleep(4)
    page.close()


def fetch_availability(ctx, t, timeout_ms=60000):
    """Enter the checkout via the listing's link, return the availability JSON the page's own JS fetches."""
    f = t["fandango"]
    url = t.get("_jump") or BUY_URL_FANDANGO.format(**f)
    page = ctx.new_page()
    if DRY_RUN:
        page.on("response", lambda r: _log_xhr(r))
    try:
        with page.expect_response(
            lambda r: "seatpicker/Default.aspx" in r.url and "action=availability" in r.url,
            timeout=timeout_ms,
        ) as info:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
        resp = info.value
        body = resp.text()
        data = json.loads(body)
        status = (data.get("response") or {}).get("status") or {}
        if status.get("code") not in (0, 1, None) and not list(iter_seat_dicts(data)):
            raise RuntimeError(f"availability status {status}")
        return data, resp.url, page
    except Exception:
        # leave the page open for the caller's diagnostics, re-raise
        t["_page"] = page
        raise


def _log_xhr(r):
    u = r.url
    if "fandango.com" not in u or re.search(r"\.(css|js|png|jpg|gif|svg|woff2?)(\?|$)", u):
        return
    body = ""
    try:
        if "json" in (r.headers.get("content-type") or "") or "Default.aspx" in u:
            body = re.sub(r"\s+", " ", r.text())[:300]
    except Exception:
        pass
    log(f"  xhr {r.status} {r.request.method} {u[:150]} {body}")


def diagnostics(page):
    try:
        title = page.title()
        text = page.evaluate("() => document.body.innerText.replace(/\\s+/g,' ').slice(0, 1200)")
        n = page.evaluate("() => document.querySelectorAll('[class*=Seat], [class*=seat]').length")
        log(f"  diag: title={title!r} seat-ish elements={n} text={text!r}")
    except Exception as e:
        log(f"  diag failed: {e}")


def new_context(p):
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context(viewport={"width": 1400, "height": 1000})
    ctx.route("**/*", lambda route: route.abort()
              if route.request.resource_type in ("image", "media", "font")
              else route.continue_())
    return browser, ctx


# ---------------------------------------------------------------- main loop

def check_one(ctx, state, t, verbose):
    key = t["label"]
    prev = state.get(key, {})
    entry = dict(prev)
    now = time.time()
    page = None
    try:
        data, url, page = fetch_availability(ctx, t)
        regular, avail, total, codes, seats = summarize(data)
    except Exception as e:
        log(f"{key}: check failed: {type(e).__name__}: {str(e)[:200]}")
        page = t.pop("_page", None)
        if page:
            diagnostics(page)
            page.close()
        state[key] = entry
        return True
    if DRY_RUN:
        log(f"{key}: availability from {url}")
        log("  structure:\n    " + "\n    ".join(structure(data)))
        for s in seats[:3]:
            log(f"  sample seat: {json.dumps(s)[:400]}")
        kinds = {}
        for s in seats:
            kinds[json.dumps({k: v for k, v in s.items() if k != 'id' and not isinstance(v, (dict, list))}, sort_keys=True)[:120]] = kinds.get(json.dumps({k: v for k, v in s.items() if k != 'id' and not isinstance(v, (dict, list))}, sort_keys=True)[:120], 0) + 1
        for k, n in sorted(kinds.items(), key=lambda kv: -kv[1])[:12]:
            log(f"  {n:4d} x {k}")
    page.close()
    if total == 0:
        log(f"{key}: availability JSON had no seats; treating as failed")
        state[key] = entry
        return True
    entry["status"] = "open" if regular > 0 else "full"
    changed = [regular, avail] != prev.get("last_counts")
    entry["last_counts"] = [regular, avail]
    if verbose or changed or DRY_RUN:
        log(f"{key}: {regular} regular seats open ({avail} incl. wheelchair, {total} total)"
            + (f" -> {', '.join(codes[:12])}" if codes else ""))
    just_opened = regular > 0 and prev.get("status") != "open"
    renotify = regular > 0 and now - prev.get("last_notified", 0) > RENOTIFY_SECONDS
    if just_opened or renotify:
        notify(f"{MOVIE_NAME} IMAX 70mm - Metreon",
               f"{key}: {regular} seat(s) OPEN: {', '.join(codes[:8])} - tap to buy",
               click_url=buy_url(t))
        entry["last_notified"] = now
    state[key] = entry
    return False


def main():
    if datetime.now(timezone.utc) > RETIRE_AFTER:
        log("past targets; disable the workflow")
        return 0
    if not TARGETS:
        log("targets.json has no fandango targets")
        return 0

    from playwright.sync_api import sync_playwright

    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() and not DRY_RUN else {}
    with sync_playwright() as p:
        browser, ctx = new_context(p)
        resolve_jump_links(ctx, TARGETS)
        if DRY_RUN:
            for t in TARGETS:
                check_one(ctx, state, t, verbose=True)
                time.sleep(5)
            browser.close()
            return 0

        stagger = int(os.environ.get("GITHUB_RUN_ID", "0")) % 45
        log(f"stagger offset: {stagger}s; {len(TARGETS)} targets")
        time.sleep(stagger)
        fail_streak = 0
        for i in range(LOOP_MINUTES):
            t = TARGETS[i % len(TARGETS)]
            failed = check_one(ctx, state, t, verbose=(i < len(TARGETS)))
            if failed:
                fail_streak += 1
                fails = state.get("_consecutive_failures", 0) + 1
                state["_consecutive_failures"] = fails
                if fails == CONSEC_FAIL_ALERT:
                    notify("Cloud checker: checks failing",
                           "Fandango seat checks have failed for ~2h. Check the seat-watch "
                           "Actions logs.", priority="high")
                if fail_streak >= 4:   # browser session may be wedged; start fresh
                    log("4 failures in a row; restarting browser")
                    browser.close()
                    browser, ctx = new_context(p)
                    resolve_jump_links(ctx, TARGETS)
                    fail_streak = 0
            else:
                fail_streak = 0
                state["_consecutive_failures"] = 0
            STATE_FILE.write_text(json.dumps(state, indent=1))
            if i < LOOP_MINUTES - 1:
                time.sleep(60)
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
