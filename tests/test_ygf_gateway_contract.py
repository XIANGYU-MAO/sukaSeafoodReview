from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml


@pytest.fixture(scope="module")
def ygf_root() -> Path:
    supplied = os.getenv("YGF_WORKTREE")
    if not supplied:
        pytest.skip("set YGF_WORKTREE to an isolated YGF checkout")
    root = Path(supplied).resolve(strict=True)
    assert root.is_dir()
    return root


def test_caddy_routes_review_before_main_fallback(ygf_root: Path):
    caddy = (ygf_root / "server/deploy/Caddyfile").read_text("utf-8")
    root_handler = caddy.split("{$DIANSHU_ROOT_DOMAIN} {", 1)[1]
    api_pos = root_handler.index("/sukaseafood/api/*")
    web_pos = root_handler.index("/sukaseafood/review*")
    fallback_pos = root_handler.index("redir https://{$DIANSHU_WEB_DOMAIN}{uri}")
    assert api_pos < fallback_pos
    assert web_pos < fallback_pos
    assert "handle_path /sukaseafood/api/*" in caddy
    assert "reverse_proxy review-api:8000" in caddy
    assert "handle_path /sukaseafood/review*" in caddy
    assert "reverse_proxy review-web:8080" in caddy


def test_legacy_project_is_404_before_fallback_on_root_and_www(ygf_root: Path):
    caddy = (ygf_root / "server/deploy/Caddyfile").read_text("utf-8")
    assert caddy.count("respond @legacyOcean 404") >= 2
    for marker in ("/project", "/project/*", "/project-assets/*"):
        assert marker in caddy
    assert caddy.index("respond @legacyOcean 404") < caddy.index(
        "reverse_proxy admin-web:8080"
    )
    assert "https://findai.top{uri}" in caddy


def test_gateway_joins_edge_and_has_no_ocean_mount(ygf_root: Path):
    config = yaml.safe_load((ygf_root / "docker-compose.caddy.yml").read_text("utf-8"))
    gateway = config["services"]["gateway"]
    assert "sukaseafood-edge" in gateway["networks"]
    assert config["networks"]["sukaseafood-edge"]["external"] is True
    assert "/srv/ocean-project" not in " ".join(gateway.get("volumes", []))


def test_ygf_preflight_preserves_existing_checks_and_adds_review_contract(
    ygf_root: Path,
):
    script = (ygf_root / "server/scripts/production_preflight.sh").read_text("utf-8")
    for marker in (
        "/sukaseafood/review",
        "/sukaseafood/api/v1/health",
        "https://findai.top/project",
        "https://www.findai.top/project/seafood",
        "404",
    ):
        assert marker in script
    assert "YGF" in script or "/api/" in script


def test_tracked_ocean_directory_is_removed(ygf_root: Path):
    assert not (ygf_root / "server/deploy/ocean-project").exists()
