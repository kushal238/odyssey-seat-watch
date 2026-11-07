# Seat Tracker — Operations Brief

**Read this first.** If you are a Claude session picking this up: this file is your
full context. Current mission (Nov 2026): get Kushal non-wheelchair seats for
**Dune: Part Three in IMAX 70mm at AMC Metreon 16 (SF)** on Christmas weekend —
the morning shows on Fri 2026-12-25, Sat 2026-12-26 and Sun 2026-12-27.
(Round one, Aug 2026, was The Odyssey in IMAX 70mm at the same theatre; the
history below is from that.)

## Targets
- Live list: `targets.json` → `targets[]` (label, AMC showtime id, date, time,
  format). Written by the `discover-showtimes` workflow, read by the checker.
- Config: `targets.json` → `config` (movie name/slug/regex, theatre, dates,
  morning cutoff hour, retire date, AMC showtimes page URL patterns).
- Seat page: `https://www.amctheatres.com/showtimes/<id>/seats`
- Showtime discovery runs on a GitHub runner (sandboxes can't reach AMC):
  loads the Metreon showtimes page per date in headless Chromium, logs EVERY
  showtime link it sees (id, time, movie/format context) and picks the ones
  matching the movie + `70mm` + start before noon PT. Falls back to any format
  of the movie in the morning (flagged in the `format` field) if no 70mm is
  labelled. Dry run on feature-branch pushes; `workflow_dispatch` writes and
  commits `targets.json` and pushes an ntfy summary. **Always eyeball the
  Actions log of a discovery run** — the pick is heuristic.

## Architecture (as of Nov 2026)
1. **Cloud fleet (primary):** GitHub Actions workflow `seat-watch` in this repo.
   Each run: mints its own AMC session via headless Chromium from the runner's IP
   (AMC blocks datacenter IPs at the raw-HTTP level but the browser usually
   passes), then checks ONE showtime per minute rotating (1 req/min stays under
   AMC's ~50-req session limit), staggered vs other runs. Runs ~27 min, then
   dispatches its own successor. **Fleet cap:** a gate step counts active +
   queued `seat-watch` runs and exits if `FLEET_SIZE` (4) others already exist;
   successor dispatch is capped the same way. The `*/15` cron is only a top-up
   / auto-restart. (Aug 2026 lesson: uncapped cron + successor hit 19+ runs.)
   Alerts push to ntfy on seat-open transitions with seat codes. Everything
   self-retires after `retire_after_utc` (2026-12-28) — also mirrored as
   `RETIRE_AFTER_YMD` in the workflow.
2. **Watchdog:** the cron top-up above. (The Aug 2026 hourly claude.ai routine
   `odyssey-fleet-watchdog`, trig_01DTRXDRTrZcx5PcPuFk2NrM, is retired.)
3. **Local tracker (retired):** `~/odyssey-tracker/check_odyssey.py` on Kushal's
   Mac was benched in Aug after the home IP got 429-banned. Not needed now;
   the `AMC_COOKIES` secret it used to upload is only a fallback if runner
   self-minting is blocked.

## Notifications
- ntfy topic: `odyssey-matreon-1e6c2f30205d` (Kushal's iPhone subscribes; note
  the intentional "matreon" typo). Send via:
  `curl -H "Title: ..." -H "Priority: urgent" -H "Click: <seat url>" -d "msg" https://ntfy.sh/odyssey-matreon-1e6c2f30205d`
- Seat-open alert format includes seat codes (e.g. "1 seat(s) OPEN: A8").

## Seat map parsing (ground truth)
aria-labels on the seat page: `Occupied AMC Club Rocker F12` = taken;
`AMC Club Rocker F12` = OPEN regular; `Wheelchair Space A26` /
`Wheelchair Companion ...` = not wanted. Codes = trailing `[A-Z]{1,2}\d{1,2}`.

## Known failure modes + fixes
- **Fleet dead** (no in_progress runs): the cron restarts it within 15 min. To
  force: `gh workflow run seat-watch --repo kushal238/odyssey-seat-watch`
  (needs gh auth; from an unauthed box, alert Kushal via ntfy instead).
- **No targets / wrong targets:** run `discover-showtimes` (dry_run=1 first,
  read the log), or hand-edit `targets.json` with ids from the AMC seat URLs.
- **Runner 429s mid-loop:** expected (~50% of cycles); rotation + stagger absorbs it. Don't "fix".
- **AMC queue wall blocks self-mint:** run falls back to the `AMC_COOKIES` repo secret (if set; likely stale). After 4 deaf cycles a run re-mints mid-loop (max 2x).
- **GitHub incident:** runs cancelled ~15 min with empty steps. Just re-dispatch; overlap absorbs.
- **Don't** raise per-IP request rates — that's what got the home IP banned. Scale via more staggered runs, never faster loops.

## History (compressed, from The Odyssey run, Aug 2026)
Seat-drop pattern: 1-3 regular seats appear for 1-15 min, a few times/day,
clustered early morning + evening. Front-row A8 (Fri) cycled open/closed
repeatedly overnight Aug 6-7. Fandango napi (`theaterMovieShowtimes/AANEM`)
counts wheelchair seats as "available" — do not trust it for this mission.

## Checking seats yourself (from any sandbox)
`pip install playwright && playwright install --with-deps chromium`, then load
the seat page in headless Chromium and parse aria-labels (working reference:
`mint_cookies_in_runner()` + `fetch_counts()` in `cloud_check.py`). Raw HTTP
without browser-earned cookies gets a Queue-it JS wall. Sandbox/datacenter IPs
usually pass the browser path; if blocked, fall back to reading
`state.json` (committed by fleet runs), `targets.json`, and the ntfy topic history
(`https://ntfy.sh/<topic>/json?poll=1&since=6h`).

## IMPORTANT for cloud/sandbox agents: you CANNOT send phone notifications
Verified 2026-08-07: Anthropic cloud sandboxes block outbound traffic to
ntfy.sh — your curl will fail or silently vanish; nothing reaches Kushal's
phone. Do NOT rely on ntfy from a sandbox. Phone pushes are the fleet's job
(GitHub runners CAN reach ntfy). If you need Kushal urgently, say it in your
chat transcript — he checks it — and/or commit a file named URGENT.md to this
repo's main branch so other agents see it.

## Division of labor if you are a takeover agent
You likely CANNOT: dispatch workflows (no gh auth), touch Kushal's Mac.
You CAN: monitor fleet via public API, verify seat status via browser,
push ntfy alerts, and advise Kushal. The fleet dispatches itself; the hourly
watchdog routine handles restarts. Your job is judgment, not plumbing.
