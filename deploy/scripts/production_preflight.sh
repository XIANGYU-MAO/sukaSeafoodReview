#!/usr/bin/env bash
set -Eeuo pipefail

REMOTE_ROOT="/opt/sukaseafood-review"
ENV_FILE="$REMOTE_ROOT/deploy/.env"
COMPOSE_FILE="$REMOTE_ROOT/docker-compose.production.yml"
COMPOSE=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")
public=false
first="${1:-}"
if [[ $# -gt 1 || ( -n "$first" && "$first" != "--public" ) ]]; then
    echo "usage: production_preflight.sh [--public]" >&2
    exit 2
fi
if [[ "$first" == "--public" ]]; then
    public=true
fi

cd "$REMOTE_ROOT"
"${COMPOSE[@]}" config --quiet
for service in review-postgres review-api review-web; do
    state="$("${COMPOSE[@]}" ps --format json "$service")"
    grep -F '"State":"running"' <<<"$state" >/dev/null
done

api_body="$("${COMPOSE[@]}" exec -T review-api python -c \
    "import json,urllib.request; print(json.dumps(json.load(urllib.request.urlopen('http://127.0.0.1:8000/v1/health', timeout=3)), separators=(',',':')))" )"
[[ "$api_body" == '{"status":"ok"}' ]]
web_body="$("${COMPOSE[@]}" exec -T review-web wget --quiet --output-document=- http://127.0.0.1:8080/healthz)"
grep -F "SukaSeafood" <<<"$web_body" >/dev/null

if [[ "$public" == true ]]; then
    review_body="$(curl --fail --silent --show-error --max-time 15 https://findai.top/sukaseafood/review)"
    grep -F "SukaSeafood" <<<"$review_body" >/dev/null
    public_health="$(curl --fail --silent --show-error --max-time 15 https://findai.top/sukaseafood/api/v1/health)"
    [[ "${public_health// /}" == '{"status":"ok"}' ]]
fi

echo "review preflight passed"
