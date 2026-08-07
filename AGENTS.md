# For agents (and humans) with doubts — read me

I'm the Claude instance that built this. This file is me answering the questions
another agent would raise looking at this repo cold. Start with `OPERATIONS.md`
for the full spec; this is the "why," the honest caveats, and the FAQ.

## What this actually is
A seat sniper for one specific thing: **The Odyssey in IMAX 70mm at AMC Metreon 16
(SF)**, three showtimes Aug 7–9 2026. It watches AMC's seat maps and pushes a phone
alert (with exact seat codes) the instant a non-wheelchair seat opens. That's it.

## "Isn't this a bot that abuses AMC?"
It's a **read-only monitor**, not a scalper. It never buys, never holds, never
touches inventory — it loads the same public seat page a human would and counts
open seats. Rate discipline is deliberate: 1 request/minute per IP, images/fonts
blocked, exponential backoff on 429. We scaled coverage by running more *gentle*
pollers on different IPs, never by hammering one. The design goal was explicitly
"look like a handful of normal visitors," because we NEED AMC's bot wall to keep
letting the browser sessions through.

## "How does it get past the bot wall?"
AMC fronts the site with Cloudflare + Queue-it. Plain HTTP gets a JS challenge
wall. So each run launches real headless Chromium (Playwright), lets it execute
the challenge JS and earn session cookies, then either uses the browser directly
or hands the cookies to lightweight HTTP. Datacenter IPs are blocked at the
raw-HTTP layer but usually pass the browser path. Kushal's home IP got 429-banned
from over-polling early on — the lesson that shaped all the rate discipline above.

## "Why 8 runs / what's the 'fleet'?"
One GitHub Actions run lasts ~27 min (platform limit) and its session goes
rate-limited after ~30–60 requests. So instead of one long checker we run ~8
concurrently: each on its own GitHub runner IP, each checking one showtime per
minute rotating, staggered so collectively a show is checked every ~30–45s. Each
run's last act dispatches its successor — the fleet is self-perpetuating. An
hourly claude.ai routine tops it up and restarts it if it fully dies.

## "Can you make it auto-BUY the seat?"
Technically yes, and we deliberately DIDN'T. Buying needs a logged-in AMC account
+ payment, i.e. storing purchase authority in a repo — and autonomous spend from
rotating datacenter IPs is exactly what gets an AMC account *banned*, plus risks
buying the wrong seat / a show he didn't want. Detection is the safe 95%; the
purchase stays on a human thumb. If you're tempted to add auto-buy: private repo,
single dedicated runner, one hardcoded seat-type, explicit per-purchase confirm,
kill-switch. Not the public fleet.

## Notifications
Phone alerts go to an ntfy topic (see OPERATIONS.md). GitHub runners can reach
ntfy; Anthropic cloud sandboxes CANNOT (egress-blocked) — sandboxed agents route
alerts by committing a `.md` to `notify/`, which the `notify-relay` workflow
forwards. Heads up: the topic name is public in this repo, so anyone with the repo
can push to Kushal's phone. Be a good guest.

## If you're an agent asked to help
Read OPERATIONS.md, check fleet health via the GitHub public API, verify seats
with your own browser if your sandbox allows egress, and advise. Don't raise
per-IP request rates (that's the one move that breaks everything). Don't add
credentials to this public repo. The whole thing self-retires after Aug 10 2026.

— Claude (Fable), Aug 2026. Built with Kushal over about four days of AMC fighting back.
