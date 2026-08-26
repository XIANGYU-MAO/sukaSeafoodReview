#!/usr/bin/env bash
set -Eeuo pipefail

REMOTE_ROOT="/opt/sukaseafood-review"
ENV_FILE="$REMOTE_ROOT/deploy/.env"
COMPOSE_FILE="$REMOTE_ROOT/docker-compose.production.yml"
COMPOSE=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")
revision="${1:-unknown}"

if [[ ! "$revision" =~ ^[A-Za-z0-9._-]{1,64}$ ]]; then
    echo "deployment revision is invalid" >&2
    exit 2
fi
if [[ ! -f "$ENV_FILE" || "$(stat -c '%a' "$ENV_FILE")" != "600" ]]; then
    echo "deploy/.env must exist with mode 0600" >&2
    exit 2
fi

cd "$REMOTE_ROOT"
"${COMPOSE[@]}" config --quiet
"${COMPOSE[@]}" up -d review-postgres

postgres_id="$("${COMPOSE[@]}" ps -q review-postgres)"
for _ in $(seq 1 60); do
    if [[ "$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$postgres_id")" == "healthy" ]]; then
        break
    fi
    sleep 2
done
if [[ "$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$postgres_id")" != "healthy" ]]; then
    echo "review-postgres did not become healthy" >&2
    exit 3
fi

"$REMOTE_ROOT/deploy/scripts/backup_postgres.sh" >/dev/null
"${COMPOSE[@]}" build review-api review-web
"${COMPOSE[@]}" run --rm review-api alembic upgrade head
"${COMPOSE[@]}" up -d

for service in review-api review-web; do
    container_id="$("${COMPOSE[@]}" ps -q "$service")"
    for _ in $(seq 1 60); do
        if [[ "$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$container_id")" == "healthy" ]]; then
            break
        fi
        sleep 2
    done
    if [[ "$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$container_id")" != "healthy" ]]; then
        echo "$service did not become healthy" >&2
        exit 3
    fi
done

"${COMPOSE[@]}" exec -T review-api python -c \
    "import json,urllib.request; assert json.load(urllib.request.urlopen('http://127.0.0.1:8000/v1/health', timeout=3)) == {'status':'ok'}"
"${COMPOSE[@]}" exec -T review-web wget --quiet --output-document=- \
    http://127.0.0.1:8080/healthz | grep -F "SukaSeafood review-web ok" >/dev/null
printf 'deployed review revision %s\n' "$revision"
