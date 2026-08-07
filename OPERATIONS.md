# Odyssey Seat Tracker — Operations Brief

**Read this first.** If you are a Claude session picking this up: this file is your
full context. The mission: get Kushal non-wheelchair seats for The Odyssey in
IMAX 70mm at AMC Metreon 16 (SF). Deadline-driven — showtimes below.

## Targets (AMC showtime IDs)
- Fri 2026-08-07 10:00 PM PT — id 144696961
- Sat 2026-08-08 10:00 AM PT — id 144696962
- Sun 2026-08-09 10:00 AM PT — id 144696966
- Seat page: `https://www.amctheatres.com/showtimes/<id>/seats`

## Architecture (as of Aug 7, 2026)
1. **Cloud fleet (primary):** GitHub Actions workflow `seat-watch` in this repo.
   Each run: mints its own AMC session via headless Chromium from the runner's IP
   (AMC blocks datacenter IPs at the raw-HTTP level but the browser usually
   passes), then checks ONE showtime per minute rotating (1 req/min stays under
   AMC's ~50-req session limit), staggered vs other runs. Runs ~27 min, then
   dispatches its own successor (self-perpetuating until Aug 10). Target ~4
   concurrent runs. Alerts push to ntfy on seat-open transitions with seat codes.
2. **Cloud watchdog:** hourly claude.ai routine `odyssey-fleet-watchdog`
   (trig_01DTRXDRTrZcx5PcPuFk2NrM) — restarts/alerts if the fleet dies.
3. **Local tracker (benched):** `~/odyssey-tracker/check_odyssey.py` on Kushal's
   Mac, cron every 1 min. Home IP (174.127.174.162) is 429-banned by AMC since
   Aug 6; script auto-backs-off (escalating, streak 11+). Revives automatically
   if the ban clears (router power-cycle may assign a new IP).

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
- **Fleet dead** (no in_progress runs): `gh workflow run seat-watch --repo kushal238/odyssey-seat-watch` (needs gh auth; from an unauthed box, alert Kushal via ntfy instead).
- **Runner 429s mid-loop:** expected (~50% of cycles); rotation + stagger absorbs it. Don't "fix".
- **AMC queue wall blocks self-mint:** run falls back to `AMC_COOKIES` repo secret (uploaded by the Mac when its browser refreshes).
- **GitHub incident:** runs cancelled ~15 min with empty steps. Just re-dispatch; overlap absorbs.
- **Don't** raise per-IP request rates — that's what got the home IP banned. Scale via more staggered runs, never faster loops.

## History (compressed)
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
`state.json` (committed by fleet runs) and the ntfy topic history
(`https://ntfy.sh/<topic>/json?poll=1&since=6h`).

## Division of labor if you are a takeover agent
You likely CANNOT: dispatch workflows (no gh auth), touch Kushal's Mac.
You CAN: monitor fleet via public API, verify seat status via browser,
push ntfy alerts, and advise Kushal. The fleet dispatches itself; the hourly
watchdog routine handles restarts. Your job is judgment, not plumbing.
