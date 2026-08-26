#!/usr/bin/env bash
set -Eeuo pipefail

REMOTE_ROOT="/opt/sukaseafood-review"
ENV_FILE="$REMOTE_ROOT/deploy/.env"
COMPOSE_FILE="$REMOTE_ROOT/docker-compose.production.yml"
COMPOSE=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")

if [[ ! -f "$ENV_FILE" || ! -f "$COMPOSE_FILE" ]]; then
    echo "review deployment configuration is missing" >&2
    exit 2
fi

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
revision="$("${COMPOSE[@]}" run --rm --no-deps review-api alembic current 2>/dev/null | tail -n 1 | tr -cd 'A-Za-z0-9_.-')"
revision="${revision:-unknown}"
revision="${revision:0:64}"
daily_name="review-${timestamp}-${revision}-daily.sql.gz"

"${COMPOSE[@]}" exec -T review-postgres sh -eu -c '
name="$1"
temporary="/backups/.${name}.tmp"
final="/backups/${name}"
trap '\''rm -f -- "$temporary"'\'' EXIT HUP INT TERM
pg_dump --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" --format=custom --file="$temporary"
pg_restore --list "$temporary" >/dev/null
chmod 600 "$temporary"
mv -- "$temporary" "$final"
trap - EXIT HUP INT TERM
' sh "$daily_name"

if [[ "$(date -u +%u)" == "7" ]]; then
    weekly_name="review-${timestamp}-${revision}-weekly.sql.gz"
    "${COMPOSE[@]}" exec -T review-postgres sh -eu -c '
source="/backups/$1"
temporary="/backups/.$2.tmp"
final="/backups/$2"
trap '\''rm -f -- "$temporary"'\'' EXIT HUP INT TERM
cp -- "$source" "$temporary"
pg_restore --list "$temporary" >/dev/null
chmod 600 "$temporary"
mv -- "$temporary" "$final"
trap - EXIT HUP INT TERM
' sh "$daily_name" "$weekly_name"
fi

"${COMPOSE[@]}" exec -T review-postgres sh -eu -c '
prune_kind() {
    kind="$1"
    keep="$2"
    find /backups -maxdepth 1 -type f -name "review-*-${kind}.sql.gz" -print \
        | sort -r \
        | awk -v keep="$keep" "NR > keep" \
        | while IFS= read -r stale; do
            case "$stale" in
                /backups/review-*-"$kind".sql.gz) rm -- "$stale" ;;
                *) echo "refusing unsafe backup retention path" >&2; exit 3 ;;
            esac
        done
}
prune_kind daily 14
prune_kind weekly 8
'

printf '%s\n' "$REMOTE_ROOT/backups/$daily_name"
