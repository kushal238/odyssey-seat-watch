# Help Kushal get Dune: Part Three IMAX 70mm seats (Christmas weekend, SF)

Thanks for lending your laptop. This script loads the AMC seat map for a few
showtimes **once a minute** in a hidden browser and pings Kushal's phone when a
seat opens. It never buys anything, never logs in, and uses less bandwidth than
a web page. It stops by itself after the shows.

## Run it (Mac or Linux, 3 commands)

```bash
pip3 install playwright
```

```bash
python3 -m playwright install chromium
```

```bash
curl -sO https://raw.githubusercontent.com/kushal238/odyssey-seat-watch/main/friend_watch.py && python3 friend_watch.py --name "YourName"
```

Leave the terminal open. Keep the laptop plugged in and awake (on a Mac, run
`caffeinate -s` in another tab, or just don't close the lid). Ctrl-C to stop.

Windows: install Python from python.org (tick "Add to PATH"), then the same
commands in PowerShell with `pip` and `python`.

## Rules (they matter)

- **One copy per connection.** Two laptops on the same wifi count as one; run
  it on one of them. A phone hotspot is a separate connection.
- **Don't speed it up.** `--poll` below 60 is ignored on purpose. AMC rate-bans
  IPs that poll too fast; the whole point is to look like a normal visitor.
- If it prints "AMC is limiting this connection", that's normal. It backs off
  on its own. Don't restart it in a loop.

## What's in the alerts

"Fri 2026-12-25 10:00am: 2 seat(s) OPEN: F12, F13 - tap to buy (seen by You)".
Several friends may spot the same seat; Kushal gets a few pings, that's fine.

For the full design, operations and the GitHub-runner version see
`OPERATIONS.md` and `AGENTS.md`.
