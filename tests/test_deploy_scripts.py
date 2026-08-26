from __future__ import annotations

from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "deploy/scripts"


def text(name: str) -> str:
    return (SCRIPTS / name).read_text("utf-8")


def test_backup_is_custom_verified_atomic_and_retention_is_bounded():
    script = text("backup_postgres.sh")
    assert 'REMOTE_ROOT="/opt/sukaseafood-review"' in script
    assert "pg_dump" in script and "--format=custom" in script
    assert "pg_restore --list" in script
    assert re.search(r"mv --? ", script)
    assert "daily" in script and "weekly" in script
    assert "14" in script and "8" in script
    assert "rm -rf" not in script
    assert "set -Eeuo pipefail" in script


def test_restore_accepts_only_an_explicit_verified_backup_and_backs_up_first():
    script = text("restore_postgres.sh")
    assert 'REMOTE_ROOT="/opt/sukaseafood-review"' in script
    assert "realpath" in script
    assert '"$REMOTE_ROOT/backups/"' in script
    assert "backup_postgres.sh" in script
    assert script.index("backup_postgres.sh") < script.index("pg_restore")
    assert "--clean" in script and "--if-exists" in script
    assert "rm -rf" not in script


def test_cloud_deploy_backs_up_then_migrates_before_service_switch():
    script = text("deploy_cloud.sh")
    assert 'REMOTE_ROOT="/opt/sukaseafood-review"' in script
    assert "config --quiet" in script
    assert "backup_postgres.sh" in script
    assert "alembic upgrade head" in script
    assert "up -d" in script
    assert script.index("backup_postgres.sh") < script.index("alembic upgrade head")
    assert script.index("alembic upgrade head") < script.rindex("up -d")
    assert "/v1/health" in script and "/healthz" in script
    assert "rm -rf /opt" not in script


def test_windows_deploy_has_safe_ssh_hash_stage_and_preserves_state():
    script = text("deploy_from_windows.ps1")
    assert '"dianshu-prod"' in script
    assert "/opt/sukaseafood-review" in script
    for option in ("BatchMode=yes", "ServerAliveInterval=15", "ConnectTimeout=10"):
        assert option in script
    assert "Get-FileHash" in script and "SHA256" in script
    assert "sha256sum" in script
    assert "rsync" in script and "--delete" in script
    for protected in ("deploy/.env", "backups", "imports"):
        assert f"--exclude={protected}" in script
    assert "deploy_cloud.sh" in script
    assert "test ! -e '$RemoteStage'" in script
    assert "find '$RemoteStage' -xdev -depth -delete" in script
    assert "Remove-Item -Recurse" not in script


def test_windows_deploy_whatif_performs_no_network():
    completed = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-File",
            str(SCRIPTS / "deploy_from_windows.ps1"),
            "-WhatIf",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    assert completed.returncode == 0, completed.stderr
    assert "WHATIF-NO-NETWORK" in completed.stdout


def test_review_preflight_is_content_aware_and_has_no_live_default():
    script = text("production_preflight.sh")
    assert 'REMOTE_ROOT="/opt/sukaseafood-review"' in script
    assert '"status":"ok"' in script.replace(" ", "")
    assert "SukaSeafood" in script
    assert "--public" in script
    assert "/sukaseafood/review" in script
    assert "/sukaseafood/api/v1/health" in script
    assert 'first="${1:-}"' in script
    assert '[[ "$first" == "--public" ]]' in script


def test_compose_array_expansion_stays_one_argument_per_element():
    for name in (
        "backup_postgres.sh",
        "deploy_cloud.sh",
        "first_deploy.sh",
        "production_preflight.sh",
    ):
        script = text(name)
        assert "$(${COMPOSE[@]}" not in script


def test_scripts_use_no_broad_destructive_command_or_secret_echo():
    for path in sorted(SCRIPTS.glob("*")):
        content = path.read_text("utf-8")
        assert not re.search(r"rm\s+-rf\s+(?:/|/opt(?:\s|$)|\$REMOTE_ROOT(?:\s|$))", content)
        assert "set -x" not in content
        assert not re.search(r"echo\s+.*(?:SESSION_SECRET|CSRF_SECRET|RECEIPT_SECRET|POSTGRES_PASSWORD)", content)
