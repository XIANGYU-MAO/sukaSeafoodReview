#!/usr/bin/env bash
set -Eeuo pipefail

REMOTE_ROOT="/opt/sukaseafood-review"
ENV_FILE="$REMOTE_ROOT/deploy/.env"
COMPOSE_FILE="$REMOTE_ROOT/docker-compose.production.yml"
COMPOSE=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")

if [[ $# -ne 2 || "$2" != "--confirm-restore" ]]; then
    echo "usage: restore_postgres.sh /opt/sukaseafood-review/backups/FILE.sql.gz --confirm-restore" >&2
    exit 2
fi

requested="$1"
resolved="$(realpath -e -- "$requested")"
case "$resolved" in
    "$REMOTE_ROOT/backups/"*) ;;
    *) echo "backup must be inside $REMOTE_ROOT/backups/" >&2; exit 2 ;;
esac
if [[ ! -f "$resolved" ]]; then
    echo "backup must be an explicit regular file" >&2
    exit 2
fi
basename="$(basename -- "$resolved")"
if [[ ! "$basename" =~ ^review-[0-9]{8}T[0-9]{6}Z-[A-Za-z0-9_.-]+-(daily|weekly)\.sql\.gz$ ]]; then
    echo "backup filename is not canonical" >&2
    exit 2
fi

"$REMOTE_ROOT/deploy/scripts/backup_postgres.sh" >/dev/null
"${COMPOSE[@]}" exec -T review-postgres pg_restore --list "/backups/$basename" >/dev/null
"${COMPOSE[@]}" stop review-api
restart_api=true
trap 'if [[ "$restart_api" == true ]]; then "${COMPOSE[@]}" up -d review-api >/dev/null; fi' EXIT
"${COMPOSE[@]}" exec -T review-postgres psql \
    --username=review --dbname=review --set=ON_ERROR_STOP=1 \
    --command="SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'review' AND pid <> pg_backend_pid();" >/dev/null
"${COMPOSE[@]}" exec -T review-postgres pg_restore \
    --username=review --dbname=review --clean --if-exists --no-owner --no-privileges \
    --exit-on-error "/backups/$basename"
"${COMPOSE[@]}" up -d review-api >/dev/null
restart_api=false
trap - EXIT
"$REMOTE_ROOT/deploy/scripts/production_preflight.sh"
echo "database restore completed from an explicitly verified backup"
