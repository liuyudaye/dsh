#!/bin/sh
set -eu

export DSH_PUBLIC_URL="${DSH_PUBLIC_URL:-http://127.0.0.1:3080}"
export PRD_REPORT_URL="${PRD_REPORT_URL:-http://127.0.0.1:8000}"

# Docker maps the container hostname to the IP that published ports use.
bind_address=$(python -c 'import socket; print(socket.gethostbyname(socket.gethostname()))')
exec python -m prd_review.agent.launcher --source-dir /app --host "$bind_address" --port 3080
