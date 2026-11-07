# Handoff: set this laptop up as the seat-watch runner

You are Claude Code running on Kushal's laptop. A cloud session built everything
in this repo but cannot reach AMC or Fandango (both refuse datacenter IPs since
Nov 2026). This laptop is on a home connection, which is why it's needed. Read
`OPERATIONS.md` and `AGENTS.md` first; this file is the to-do list.

Goal: Kushal's phone buzzes when a non-wheelchair seat opens for
**Dune: Part Three, IMAX 70mm, AMC Metreon 16, Sat Dec 26 and Sun Dec 27 2026
at 11:00am** (AMC showtime ids in `targets.json`). Read-only monitoring. Never
buy, never log in, never store credentials in this public repo.

## Hard rules (these are what got the home IP banned in August)
- One AMC page load per minute **total** from this connection. The runner
  already does exactly that. Do not run `friend_watch.py` or anything else that
  polls AMC on this laptop at the same time, and do not shorten any sleep.
- If checks return 429, stop and wait it out. Do not add retries.
- No proxies, VPNs, or "unblocked" cloud providers. Kushal has been told why.

## Steps
1. `git clone https://github.com/kushal238/odyssey-seat-watch && cd odyssey-seat-watch`
2. Confirm `gh auth status` works as kushal238 (it needs repo admin). If not,
   `gh auth login`.
3. Safety setting for a self-hosted runner on a public repo (fork PRs must be
   approved before their workflows run). Try the API; if it 404s, do it in the
   browser: repo Settings → Actions → General → Fork pull request workflows →
   "Require approval for all outside collaborators".
   ```bash
   gh api -X PUT repos/kushal238/odyssey-seat-watch/actions/permissions/fork-pr-contributor-approval -f approval_policy=all_external_contributors
   ```
4. Repo variables:
   ```bash
   gh variable set SEAT_WATCH_RUNNER --body self-hosted --repo kushal238/odyssey-seat-watch
   gh variable set FLEET_SIZE --body 1 --repo kushal238/odyssey-seat-watch
   ```
5. Registration token (valid 1 hour), then the setup script. It installs
   Playwright Chromium and the GitHub runner under `$HOME` only, as a per-user
   LaunchAgent; no sudo on macOS. Removal steps are in the script header.
   ```bash
   TOKEN=$(gh api -X POST repos/kushal238/odyssey-seat-watch/actions/runners/registration-token --jq .token)
   bash local/setup_runner.sh "$TOKEN"
   ```
6. Check the runner shows "Idle" in repo Settings → Actions → Runners
   (`gh api repos/kushal238/odyssey-seat-watch/actions/runners --jq '.runners[]|{name,status}'`).
7. Start it with a test push to the phone, then watch the first run's log:
   ```bash
   gh workflow run seat-watch --repo kushal238/odyssey-seat-watch -f test_ping=1
   ```
   Within ~2 min a run should appear (`gh run list --workflow seat-watch`), its
   "Fleet gate" step should print `go=1`, and the check step should log lines
   like `Sat 2026-12-26 11:00am: 0 regular seats open (N incl. wheelchair, M total)`.
   Kushal's phone should get "Cloud test ping". If the log shows
   `Attention Required! | Cloudflare` or `queue page`, the home IP is blocked
   too; stop and tell him rather than retrying.
8. Keep the laptop awake: System Settings → Battery/Energy → prevent sleeping
   on power adapter, or run `caffeinate -s` in a spare terminal. Plugged in,
   lid open or clamshell with external power.
9. Report back to Kushal: runner name, first successful check line, and that
   the 15-minute cron now keeps the fleet alive (cap 1 run). It self-retires
   after Dec 28.

## Optional one-off audit (6 requests, once)
If Kushal wants the current state of all six 70mm morning shows, the four
without AMC ids need their ids first: open each on amctheatres.com and copy
the number from the `/showtimes/<id>/seats` URL into `targets.json` (`sid`),
commit, push. Only the shows with ids are polled, and more shows means each is
checked less often, so ask before adding.

## If something's off
`state.json` (committed by runs) shows last counts per show. Logs are in the
Actions tab. The cloud session's notes on what was tried and why are in
`OPERATIONS.md` under "Nov 2026 findings".
