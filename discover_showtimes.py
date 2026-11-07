#!/usr/bin/env python3
"""Find AMC showtime IDs for the target movie/theatre/dates and write targets.json.

Runs on a GitHub runner (Anthropic sandboxes cannot reach AMC). Loads the
theatre's showtimes page for each target date in headless Chromium, collects
every showtime link (/showtimes/<id>) together with the surrounding movie and
format text, then keeps the ones that match MOVIE_MATCH, FORMAT_MATCH and the
morning window. Everything it sees is logged so a human (or agent) can sanity
check the pick from the Actions log.

Config comes from targets.json ("config" key) and can be overridden by env.
"""

import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGETS_FILE = HERE / "targets.json"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
TIME_RE = re.compile(r"\b(\d{1,2}):(\d{2})\s*([ap])\.?\s*m\b", re.I)

# JS: every /showtimes/<id> link plus the text of its ancestors (innermost first)
COLLECT_JS = """
() => {
  const out = [];
  const seen = new Set();
  for (const a of document.querySelectorAll('a[href*="/showtimes/"], [data-showtime-id]')) {
    const href = a.getAttribute('href') || '';
    const m = href.match(/\\/showtimes\\/(\\d+)/);
    const id = m ? m[1] : a.getAttribute('data-showtime-id');
    if (!id || seen.has(id)) continue;
    seen.add(id);
    const ancestors = [];
    let el = a.parentElement;
    for (let depth = 0; el && depth < 10; depth++) {
      const t = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
      ancestors.push(t.slice(0, 400));
      if (t.length > 4000) break;
      el = el.parentElement;
    }
    out.push({
      id, href,
      text: (a.innerText || a.textContent || '').replace(/\\s+/g, ' ').trim(),
      aria: a.getAttribute('aria-label') || '',
      ancestors,
    });
  }
  return out;
}
"""


def log(msg):
    print(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {msg}", flush=True)


def notify(title, message, priority="high"):
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        return
    try:
        req = urllib.request.Request(
            f"https://ntfy.sh/{topic}", data=message.encode(),
            headers={"Title": title, "Priority": priority, "Tags": "mag,movie_camera"},
        )
        urllib.request.urlopen(req, timeout=15)
    except Exception as e:  # discovery must not die because ntfy hiccupped
        log(f"ntfy failed: {e}")


def load():
    return json.loads(TARGETS_FILE.read_text())


def parse_time(*texts):
    """Return (hour24, minute, 'h:mmam') from the first text containing a clock time."""
    for t in texts:
        m = TIME_RE.search(t or "")
        if m:
            h, mi, ap = int(m.group(1)), int(m.group(2)), m.group(3).lower()
            h24 = (h % 12) + (12 if ap == "p" else 0)
            return h24, mi, f"{h}:{mi:02d}{ap}m"
    return None


def classify(link, movie_re, format_re):
    """Decide movie match + format label from the ancestor text around a link."""
    anc = link["ancestors"]
    movie_ok = any(movie_re.search(t) for t in anc)
    fmt = "unknown"
    for t in anc:  # innermost first: the first ancestor mentioning a format wins
        if re.search(r"70\s*mm", t, re.I) and re.search(r"imax", t, re.I):
            fmt = "IMAX 70mm"
            break
        if re.search(r"imax", t, re.I):
            fmt = "IMAX"
            break
        if re.search(r"dolby", t, re.I):
            fmt = "Dolby"
            break
    format_ok = bool(format_re.search(fmt))
    return movie_ok, fmt, format_ok


def discover_date(page, cfg, date):
    urls = [u.format(date=date, **cfg) for u in cfg["page_urls"]]
    links = []
    for url in urls:
        log(f"{date}: loading {url}")
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_function(
                "() => document.querySelectorAll('a[href*=\"/showtimes/\"]').length > 0",
                timeout=45000,
            )
            time.sleep(3)  # let lazy sections settle
            links = page.evaluate(COLLECT_JS)
        except Exception as e:
            log(f"{date}: {url} -> {type(e).__name__}: {str(e)[:200]}")
            title = page.title() if page else ""
            log(f"{date}: page title was {title!r}")
            continue
        if links:
            break
    return links


def main():
    data = load()
    cfg = data["config"]
    cfg["movie_slug"] = os.environ.get("MOVIE_SLUG") or cfg["movie_slug"]
    dates = [d.strip() for d in (os.environ.get("DATES") or ",".join(cfg["dates"])).split(",") if d.strip()]
    movie_re = re.compile(cfg["movie_match"], re.I)
    format_re = re.compile(cfg["format_match"], re.I)
    morning_end = int(os.environ.get("MORNING_END_HOUR") or cfg.get("morning_end_hour", 12))

    from playwright.sync_api import sync_playwright

    found = {}
    everything = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(user_agent=UA, viewport={"width": 1400, "height": 1000})
        ctx.route("**/*", lambda route: route.abort()
                  if route.request.resource_type in ("image", "media", "font")
                  else route.continue_())
        page = ctx.new_page()
        for date in dates:
            links = discover_date(page, cfg, date)
            log(f"{date}: {len(links)} showtime links on page")
            rows = []
            for l in links:
                movie_ok, fmt, format_ok = classify(l, movie_re, format_re)
                t = parse_time(l["text"], l["aria"], *l["ancestors"][:2])
                rows.append({"id": l["id"], "time": t[2] if t else "?", "h24": t[0] if t else None,
                             "movie_ok": movie_ok, "format": fmt, "format_ok": format_ok,
                             "text": l["text"][:60], "aria": l["aria"][:80]})
            everything[date] = rows
            for r in rows:
                log(f"  {date} id={r['id']} {r['time']:>8} movie={'Y' if r['movie_ok'] else 'n'} "
                    f"fmt={r['format']:<10} text={r['text']!r} aria={r['aria']!r}")
            picks = [r for r in rows if r["movie_ok"] and r["format_ok"]
                     and r["h24"] is not None and r["h24"] < morning_end]
            if not picks:  # fall back: any morning show of the movie, flagged
                picks = [r for r in rows if r["movie_ok"] and r["h24"] is not None
                         and r["h24"] < morning_end]
                if picks:
                    log(f"{date}: no {cfg['format_match']} match; falling back to any format")
            found[date] = picks
            time.sleep(5)  # be a polite visitor between dates
        browser.close()

    targets = []
    for date, picks in found.items():
        for r in sorted(picks, key=lambda r: (r["h24"], r["id"])):
            dow = datetime.strptime(date, "%Y-%m-%d").strftime("%a")
            targets.append({
                "label": f"{dow} {date} {r['time']}",
                "sid": r["id"],
                "date": date,
                "time": r["time"],
                "format": r["format"],
            })

    summary = []
    for date in dates:
        picks = found.get(date, [])
        if picks:
            summary.append(f"{date}: " + ", ".join(f"{r['time']} [{r['format']}] #{r['id']}" for r in picks))
        else:
            n = len(everything.get(date, []))
            nm = sum(1 for r in everything.get(date, []) if r["movie_ok"])
            summary.append(f"{date}: NO morning pick ({n} links, {nm} for movie)")
    log("SUMMARY\n  " + "\n  ".join(summary))

    if os.environ.get("DRY_RUN") == "1":
        log("dry run; not writing targets.json")
        return 0

    if targets:
        old = {t["sid"] for t in data.get("targets", [])}
        data["targets"] = targets
        data["discovered_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        TARGETS_FILE.write_text(json.dumps(data, indent=1) + "\n")
        log(f"wrote {len(targets)} targets to targets.json")
        if {t["sid"] for t in targets} != old:
            notify(f"{cfg['movie_name']} targets set",
                   "Watching:\n" + "\n".join(summary) + "\nReply in chat if a pick is wrong.")
    else:
        log("no targets found; leaving targets.json untouched")
        notify(f"{cfg['movie_name']}: showtime discovery found nothing",
               "\n".join(summary) + "\nCheck the discover-showtimes Actions log.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
