#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

install -m 0644 deploy/omnichannel-watchdog.service /etc/systemd/system/omnichannel-watchdog.service
install -m 0644 deploy/omnichannel-watchdog.timer /etc/systemd/system/omnichannel-watchdog.timer

systemctl daemon-reload
systemctl enable --now docker
systemctl enable --now omnichannel-watchdog.timer

echo "Omnichannel watchdog enabled"
systemctl status omnichannel-watchdog.timer --no-pager
