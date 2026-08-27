#!/usr/bin/env bash
set -Eeuo pipefail

REMOTE_ROOT="/opt/sukaseafood-review"
ENV_FILE="$REMOTE_ROOT/deploy/.env"
EDGE_NETWORK="sukaseafood-edge"
revision="${1:-initial}"

available_kib="$(df -Pk /opt | awk 'NR == 2 {print $4}')"
memory_kib="$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)"
if [[ ! "$available_kib" =~ ^[0-9]+$ || "$available_kib" -lt 5242880 ]]; then
    echo "at least 5 GiB free space under /opt is required" >&2
    exit 2
fi
if [[ ! "$memory_kib" =~ ^[0-9]+$ || "$memory_kib" -lt 1048576 ]]; then
    echo "at least 1 GiB memory is required" >&2
    exit 2
fi
df -h /opt
free -m
docker version >/dev/null
docker compose version >/dev/null

if ! docker network inspect sukaseafood-edge >/dev/null 2>&1; then
    docker network create --driver bridge --subnet 172.30.0.0/24 "$EDGE_NETWORK" >/dev/null
fi
edge_subnet="$(docker network inspect --format '{{(index .IPAM.Config 0).Subnet}}' "$EDGE_NETWORK")"
if [[ ! "$edge_subnet" =~ ^[0-9A-Fa-f:.]+/[0-9]{1,3}$ ]]; then
    echo "sukaseafood-edge has no usable subnet" >&2
    exit 2
fi

umask 077
install -d -m 0700 "$REMOTE_ROOT" "$REMOTE_ROOT/deploy" \
    "$REMOTE_ROOT/backups" "$REMOTE_ROOT/imports"
if [ ! -f "$ENV_FILE" ]; then
    postgres_password="$(openssl rand -hex 32)"
    session_secret="$(openssl rand -hex 32)"
    csrf_secret="$(openssl rand -hex 32)"
    receipt_secret="$(openssl rand -hex 32)"
    {
        printf 'POSTGRES_PASSWORD=%s\n' "$postgres_password"
        printf 'SESSION_SECRET=%s\n' "$session_secret"
        printf 'CSRF_SECRET=%s\n' "$csrf_secret"
        printf 'RECEIPT_SECRET=%s\n' "$receipt_secret"
        printf 'TRUSTED_PROXY_CIDRS=%s\n' "$edge_subnet"
        printf '%s\n' 'IMAGE_ORIGIN_ALLOWLIST=.inaturalist.org,inaturalist-open-data.s3.amazonaws.com,caos.boldsystems.org,cdn.floridamuseum.ufl.edu,collections.nmnh.si.edu,data.nhm.ac.uk,huggingface.co,pictures.snsb.info,specify.saiab.ac.za,www.morphosource.org,.wikimedia.org,.wikimediausercontent.com,.gbif.org,.fishair.org,.fish-vista.org,.fishvista.org'
    } > "$ENV_FILE"
    unset postgres_password session_secret csrf_secret receipt_secret
fi
chmod 600 "$ENV_FILE"

"$REMOTE_ROOT/deploy/scripts/deploy_cloud.sh" "$revision"
COMPOSE=(docker compose --env-file "$ENV_FILE" -f "$REMOTE_ROOT/docker-compose.production.yml")
accounts="$("${COMPOSE[@]}" run --rm review-api python -m app.commands.seed_users --print-once)"
if [[ -z "$accounts" ]]; then
    echo "accounts already initialized"
else
    echo "Temporary passwords follow once; store them in the password manager now:"
    printf '%s\n' "$accounts"
fi
echo "Production secrets remain only at $ENV_FILE (mode 0600); no value was logged."
