#!/usr/bin/env bash
# One-time setup of a Linux VM (Ubuntu 24.04 x64) for the collector and the sync server.
# Run ON the VM, with this repository at /opt/automatic-trader:
#
#     bash /opt/automatic-trader/deploy/azure/setup_vm.sh
#
# Safe to re-run: it reinstalls deps and the services, and leaves the DB alone.
# The live deployment also installed what the site transport needs; that part is not
# in this repository.
set -euo pipefail
ROOT="/opt/automatic-trader"
cd "$ROOT"

echo "== packages"
sudo apt-get update -q
sudo apt-get install -y -q python3-venv python3-pip sqlite3 ca-certificates

echo "== Python venv"
python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r collector/requirements.txt

echo "== needrestart: never auto-restart our services"
# Ubuntu's daily unattended upgrade runs needrestart, which restarted the collector
# four times in two minutes on 2026-10-17 (three passes cut short). Upgrades still
# install; the services pick up new libraries at their next restart or reboot.
echo '$nrconf{override_rc}{qr(^(collector|sync))} = 0;' | sudo tee /etc/needrestart/conf.d/collector.conf >/dev/null

echo "== services"
sudo cp deploy/azure/collector.service deploy/azure/sync.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable sync collector

echo
echo "Done. Not started yet. Next:"
echo "  sudo systemctl start sync collector"
echo "  journalctl -u collector -f"
