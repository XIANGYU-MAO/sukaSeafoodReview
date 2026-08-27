from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_first_deploy_is_capacity_checked_secret_safe_and_idempotent():
    script = (ROOT / "deploy/scripts/first_deploy.sh").read_text("utf-8")
    assert 'REMOTE_ROOT="/opt/sukaseafood-review"' in script
    assert "df -Pk /opt" in script
    assert "/proc/meminfo" in script
    assert "5242880" in script  # 5 GiB in KiB
    assert "1048576" in script  # 1 GiB in KiB
    assert "docker version" in script and "docker compose version" in script
    assert "docker network inspect sukaseafood-edge" in script
    assert "docker network create" in script
    assert "umask 077" in script
    assert 'if [ ! -f "$ENV_FILE" ]' in script
    assert script.count("openssl rand") >= 4
    assert "chmod 600" in script
    assert "seed_users --print-once" in script
    assert "seed_species" not in script
    assert "accounts already initialized" in script
    assert "docker volume rm" not in script
    assert "down -v" not in script


def test_operations_guide_covers_accounts_backups_imports_and_external_images():
    guide = (ROOT / "deploy/OPERATIONS_ZH.md").read_text("utf-8")
    for name in ("Hassan", "Mao", "Xinhui", "Wahid", "Sharmaa", "Yiming"):
        assert name in guide
    for topic in (
        "0600",
        "备份",
        "恢复",
        "dry-run",
        "采集与导入",
        "collector/output/candidates.csv",
        "file_sha256",
        "外部图片",
        "不代理",
        "/opt/sukaseafood-review",
    ):
        assert topic in guide


def test_release_checklist_keeps_gateway_order_and_legacy_404_during_rollback():
    checklist = (ROOT / "deploy/RELEASE_CHECKLIST_ZH.md").read_text("utf-8")
    assert checklist.index("先发布审核服务") < checklist.index("再发布 YGF 网关")
    assert "浏览器验收" in checklist
    assert "pg_restore --list" in checklist
    assert "持续保留 Ocean 路径 404" in checklist
    assert "需要操作者另行明确授权" in checklist
