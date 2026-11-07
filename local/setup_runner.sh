#!/usr/bin/env bash
# One-shot setup for an always-on seat-watch box on a HOME connection
# (Raspberry Pi 5 / Raspberry Pi OS 64-bit, Ubuntu, Debian, or a Mac).
#
# It installs Python + Playwright Chromium, downloads the GitHub Actions
# runner, registers it to this repo, and installs it as a service that starts
# on boot. After this, the seat-watch workflow runs here instead of on GitHub's
# (blocked) servers. Pairs with repo variables SEAT_WATCH_RUNNER=self-hosted
# and FLEET_SIZE=1.
#
# Usage:
#   1. Repo -> Settings -> Actions -> Runners -> "New self-hosted runner".
#      Copy the registration TOKEN shown in the "Configure" step (valid 1 hour).
#   2. On the box:
#        curl -sO https://raw.githubusercontent.com/kushal238/odyssey-seat-watch/main/local/setup_runner.sh
#        bash setup_runner.sh <TOKEN>
#   3. Repo -> Settings -> Secrets and variables -> Actions -> Variables:
#        SEAT_WATCH_RUNNER = self-hosted      FLEET_SIZE = 1
#   4. Actions -> seat-watch -> Run workflow. The 15-min cron keeps it alive.
#
# Idempotent: re-running updates deps and re-registers the runner.
set -euo pipefail

REPO_URL="https://github.com/kushal238/odyssey-seat-watch"
TOKEN="${1:-}"
RUNNER_VERSION="${RUNNER_VERSION:-2.338.2}"
RUNNER_DIR="${RUNNER_DIR:-$HOME/actions-runner}"

if [ -z "$TOKEN" ]; then
  echo "usage: bash setup_runner.sh <registration-token>   (from Settings > Actions > Runners > New self-hosted runner)"; exit 1
fi

os="$(uname -s)"; arch="$(uname -m)"
case "$os-$arch" in
  Linux-x86_64)  pkg="linux-x64" ;;
  Linux-aarch64) pkg="linux-arm64" ;;
  Darwin-arm64)  pkg="osx-arm64" ;;
  Darwin-x86_64) pkg="osx-x64" ;;
  *) echo "unsupported platform $os $arch"; exit 1 ;;
esac

echo "== 1/4 Python + Playwright Chromium"
if [ "$os" = "Linux" ]; then
  sudo apt-get update -qq
  sudo apt-get install -y -qq python3 python3-pip python3-venv curl tar jq >/dev/null
fi
python3 -m pip install -q --user --break-system-packages playwright 2>/dev/null || python3 -m pip install -q --user playwright
export PATH="$HOME/.local/bin:$PATH"
if [ "$os" = "Linux" ]; then
  python3 -m playwright install --with-deps chromium 2>&1 | tail -1
else
  python3 -m playwright install chromium 2>&1 | tail -1
fi

echo "== 2/4 GitHub Actions runner $RUNNER_VERSION ($pkg)"
mkdir -p "$RUNNER_DIR" && cd "$RUNNER_DIR"
if [ ! -x ./config.sh ]; then
  curl -sL -o runner.tar.gz \
    "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-${pkg}-${RUNNER_VERSION}.tar.gz"
  tar xzf runner.tar.gz && rm runner.tar.gz
fi
if [ -f .runner ]; then
  echo "   already configured; removing old registration first"
  sudo ./svc.sh stop 2>/dev/null || true
  sudo ./svc.sh uninstall 2>/dev/null || true
  ./config.sh remove --token "$TOKEN" || true
fi

echo "== 3/4 Register runner (labels: self-hosted, seat-watch)"
./config.sh --unattended --url "$REPO_URL" --token "$TOKEN" \
  --name "seat-watch-$(hostname -s)" --labels seat-watch --work _work --replace

echo "== 4/4 Install as a service that starts on boot"
sudo ./svc.sh install "$USER"
sudo ./svc.sh start
sudo ./svc.sh status | head -5 || true

cat <<EOF

Done. Now in the repo:
  Settings > Secrets and variables > Actions > Variables:
    SEAT_WATCH_RUNNER = self-hosted
    FLEET_SIZE        = 1
  Actions > seat-watch > Run workflow.

The box must stay on and on the home network. Pi: disable wifi power saving
if it drops offline (sudo iw wlan0 set power_save off). Mac: System Settings >
Energy > prevent sleep, or run 'caffeinate -s' in a terminal.
EOF
