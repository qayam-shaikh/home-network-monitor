#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env. Edit SNMP_COMMUNITY, then run ./run.sh again."
  exit 0
fi
sudo apt install -y python3-venv snmp
if [ ! -d .venv ]; then python3 -m venv .venv; fi
source .venv/bin/activate
if ! .venv/bin/python -c 'import flask' >/dev/null 2>&1; then
  .venv/bin/pip install -r requirements.txt
fi
set -a
source .env
set +a
exec .venv/bin/python app.py
