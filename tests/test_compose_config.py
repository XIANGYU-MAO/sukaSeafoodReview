from __future__ import annotations

from pathlib import Path
import re

import yaml


ROOT = Path(__file__).resolve().parents[1]


def compose(name: str) -> dict:
    return yaml.safe_load((ROOT / name).read_text("utf-8"))


def test_production_compose_has_only_review_services_and_internal_postgres():
    config = compose("docker-compose.production.yml")
    services = config["services"]
    assert set(services) == {"review-postgres", "review-api", "review-web"}
    postgres = services["review-postgres"]
    assert "ports" not in postgres
    assert set(postgres["networks"]) == {"review-internal"}
    assert config["networks"]["review-internal"]["internal"] is True
    assert config["networks"]["sukaseafood-edge"] == {
        "external": True,
        "name": "sukaseafood-edge",
    }


def test_only_api_and_web_join_external_edge_and_have_health_checks():
    services = compose("docker-compose.production.yml")["services"]
    assert "sukaseafood-edge" in services["review-api"]["networks"]
    assert "sukaseafood-edge" in services["review-web"]["networks"]
    assert "sukaseafood-edge" not in services["review-postgres"]["networks"]
    assert all("healthcheck" in service for service in services.values())
    assert all("ports" not in service for service in services.values())
    assert "/tmp:size=32m,mode=1777" in services["review-web"]["tmpfs"]


def test_web_build_context_contains_published_collector_zip():
    web_context = compose("docker-compose.production.yml")["services"]["review-web"][
        "build"
    ]["context"]
    assert web_context == "./web"
    package = ROOT / web_context.removeprefix("./") / "public" / "downloads"
    assert (package / "sukaseafood-collector.zip").is_file()


def test_production_compose_uses_named_data_backup_and_import_storage():
    config = compose("docker-compose.production.yml")
    assert {"review-postgres-data", "review-backups", "review-imports"} <= set(
        config["volumes"]
    )
    postgres_mounts = " ".join(config["services"]["review-postgres"]["volumes"])
    api_mounts = " ".join(config["services"]["review-api"]["volumes"])
    assert "review-postgres-data:/var/lib/postgresql/data" in postgres_mounts
    assert "review-backups:/backups" in postgres_mounts
    assert "review-imports:/imports" in api_mounts


def test_production_has_no_secret_defaults_and_example_is_placeholder_only():
    raw = (ROOT / "docker-compose.production.yml").read_text("utf-8")
    for name in (
        "POSTGRES_PASSWORD",
        "SESSION_SECRET",
        "CSRF_SECRET",
        "RECEIPT_SECRET",
        "TRUSTED_PROXY_CIDRS",
    ):
        assert re.search(rf"\$\{{{name}:\?[^}}]+\}}", raw)
    example = (ROOT / "deploy/.env.example").read_text("utf-8")
    assert "change-me" in example
    assert "BEGIN PRIVATE KEY" not in example
    assert not re.search(r"(?:secret|password)=[0-9a-f]{32,}", example, re.I)
    ignored = (ROOT / ".gitignore").read_text("utf-8").splitlines()
    assert "deploy/.env" in ignored
    assert "deploy/*.report.json" in ignored


def test_api_and_web_images_are_minimal_non_root_and_image_byte_free():
    api = (ROOT / "api/Dockerfile").read_text("utf-8")
    web = (ROOT / "web/Dockerfile").read_text("utf-8")
    assert "USER review" in api
    assert "uvicorn" in api and "0.0.0.0" in api and "8000" in api
    assert "/v1/health" in api
    assert re.search(r"FROM node:22[^\n]+ AS build", web, re.I)
    assert "npm ci" in web
    assert "npm run typecheck" in web and "npm test" in web and "npm run build" in web
    assert "FROM nginx:" in web and "COPY --from=build" in web
    assert "USER nginx" in web
    for relative in ("api/.dockerignore", "web/.dockerignore"):
        ignored = (ROOT / relative).read_text("utf-8").lower()
        for pattern in ("*.jpg", "*.png", "*.csv", "*.sqlite*", "*.log"):
            assert pattern in ignored


def test_container_dependency_installs_use_the_ygf_china_mirrors():
    api = (ROOT / "api/Dockerfile").read_text("utf-8")
    web = (ROOT / "web/Dockerfile").read_text("utf-8")
    assert "https://pypi.tuna.tsinghua.edu.cn/simple" in api
    assert "--index-url" in api
    assert "https://registry.npmmirror.com" in web
    assert 'npm ci --registry="${SUKASEAFOOD_NPM_REGISTRY}"' in web


def test_nginx_spa_health_body_limit_and_image_csp_match_origin_policy():
    nginx = (ROOT / "web/nginx.conf").read_text("utf-8")
    assert "listen 8080" in nginx
    assert "location = /healthz" in nginx
    assert "try_files $uri $uri/ /index.html" in nginx
    assert "client_max_body_size 20m" in nginx
    assert "proxy_pass" not in nginx
    csp = next(line for line in nginx.splitlines() if "Content-Security-Policy" in line)
    # Exact image hosts can be approved at runtime, while API validation remains
    # the authority deciding which HTTPS URLs may enter the candidate catalog.
    assert "img-src 'self' data: https:" in csp


def test_batch_and_receipt_envelope_constants_agree_across_layers():
    api = (ROOT / "api/app/services/exports.py").read_text("utf-8")
    local_manifest = (ROOT / "local_sync/src/sukaseafood_sync/manifest.py").read_text("utf-8")
    local_receipt = (ROOT / "local_sync/src/sukaseafood_sync/receipt.py").read_text("utf-8")
    web = (ROOT / "web/src/admin/ExportsTab.tsx").read_text("utf-8")
    assert "EXPORT_MAX_ROWS = 10_000" in api
    assert "MAX_MANIFEST_ROWS = 10_000" in local_manifest
    assert "EXPORT_MAX_BYTES = 20 * 1024 * 1024" in api
    assert "MAX_MANIFEST_BYTES = 20 * 1024 * 1024" in local_manifest
    assert "MAX_RECEIPT_FILE_BYTES = MAX_MANIFEST_BYTES" in local_receipt
    assert "MAX_RECEIPT_FILE_BYTES = 20 * 1024 * 1024" in web
