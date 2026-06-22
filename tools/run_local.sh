#!/usr/bin/env bash
# Run the voice_bridge on the HOST (not docker) so Telethon's first-run phone
# login is interactive. Brain + proxy still run in docker.
#
#   docker-compose up -d anthropic_proxy chromadb odysseus odysseus_shim
#   ./tools/run_local.sh
#
# On first run Telethon asks for your phone number and the login code.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
. .venv/bin/activate
pip -q install -r voice_bridge/requirements.txt

set -a
. ./.env
set +a

# talk to the dockerized brain via the published localhost port
export BRAIN_URL="http://127.0.0.1:9200/v1"
export SESSIONS_DIR="$(pwd)/voice_bridge/sessions"
export PERSONA_FILE="$(pwd)/prompts/persona_hermes.txt"

cd voice_bridge
exec python main.py
