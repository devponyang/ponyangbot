#!/usr/bin/env bash
set -euo pipefail

# Run as the VM login user, not with sudo. Only service installation uses sudo.
if [[ "$(id -u)" == "0" ]]; then
  echo 'Run this script as your normal SSH user, without sudo.' >&2
  exit 1
fi
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
service_user="$(id -un)"
if [[ ! "$project_dir" =~ ^/[a-zA-Z0-9_./-]+$ ]]; then
  echo 'Use a project directory without spaces or special characters.' >&2
  exit 1
fi
cd "$project_dir"
for required_file in bot.py .env credentials.json .venv/bin/python; do
  if [[ ! -f "$required_file" ]]; then
    echo "Missing: $required_file. Follow deploy/ubuntu.md first." >&2
    exit 1
  fi
done
if [[ ! -f data/bot.json ]]; then
  echo 'Copy the existing data folder, including data/bot.json, before starting this migration.' >&2
  exit 1
fi
chmod 600 .env credentials.json data/bot.json
chmod 700 data

unit_file="$(mktemp)"
trap 'rm -f -- "$unit_file"' EXIT
cat > "$unit_file" <<EOF
[Unit]
Description=Ponyang Discord recruitment bot
Wants=network-online.target
After=network-online.target
StartLimitIntervalSec=0

[Service]
Type=simple
User=$service_user
WorkingDirectory=$project_dir
ExecStart=$project_dir/.venv/bin/python -u $project_dir/bot.py
Restart=on-failure
RestartSec=15
TimeoutStopSec=30
Environment=PYTHONUNBUFFERED=1
Environment=TZ=Asia/Seoul
UMask=0077
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
EOF

sudo install -m 644 "$unit_file" /etc/systemd/system/ponyangbot.service
sudo systemctl daemon-reload
sudo systemctl enable ponyangbot
sudo systemctl restart ponyangbot
sudo systemctl status ponyangbot --no-pager
