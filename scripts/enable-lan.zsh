#!/bin/zsh
# Reviewed transition plan: run only after approval to restart all Colima workloads.
set -euo pipefail
cd "${0:A:h:h}"
if [[ "${1:-}" != "--restart-colima" ]]; then
  print -u2 'Usage: zsh scripts/enable-lan.zsh --restart-colima'
  print -u2 'This briefly stops all containers on the current Colima VM, then restores them.'
  exit 2
fi
: "${ICSEE_PASSWORD:?Set ICSEE_PASSWORD in the launching shell}"
export ICSEE_WEB_LAN_IP="${ICSEE_WEB_LAN_IP:-192.168.1.159}"
.venv/bin/python -c 'import os; from icsee_ptz.config import Config; Config(host=os.environ["ICSEE_WEB_LAN_IP"])'
if [[ "$(/usr/sbin/ipconfig getifaddr en0)" != "$ICSEE_WEB_LAN_IP" ]]; then
  print -u2 'The configured LAN address no longer matches en0. Update ICSEE_WEB_LAN_IP first.'
  exit 2
fi
docker compose config --quiet
running_names=("${(@f)$(docker ps --format '{{.Names}}')}")
# Finish the collector cleanly and preserve its frames/MP4 before VM shutdown.
docker compose stop web timelapse
colima stop
colima start --network-host-addresses
.venv/bin/python scripts/fix-colima-lan-route.py
for name in "${running_names[@]}"; do
  if [[ "$name" != icsee-local-web-1 && "$name" != icsee-local-timelapse-1 ]]; then
    docker start "$name" >/dev/null
  fi
done
docker compose up -d web timelapse
docker compose ps
print "LAN URL: http://${ICSEE_WEB_LAN_IP}:8000/"
