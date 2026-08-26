from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
README_ZH = ROOT / "README_ZH.md"
README_EN = ROOT / "README.md"
ENV_EXAMPLE = ROOT / "api" / ".env.example"

SHARED_COMMANDS_AND_FACTS = (
    "codex/collaborative-review",
    "https://github.com/XIANGYU-MAO/sukaSeafoodReview.git",
    "http://localhost:5173/sukaseafood/review/",
    "https://findai.top/sukaseafood/review",
    "/sukaseafood/api/v1",
    "C:\\Users\\86166\\Desktop\\SukaSeafood_CV_Dataset_Collector\\output\\candidates.csv",
    "1,221",
    "247",
    "262",
    "python.exe -m alembic upgrade head",
    "python.exe -m app.commands.seed_users --print-once",
    "python.exe -m app.commands.import_candidates",
    "python.exe -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000",
    "npm install",
    "npm run dev",
    "pytest -q",
    "npm run typecheck",
    "npm run build",
)

ZH_SECTIONS = (
    "系统成果",
    "仓库与当前开发上下文",
    "架构与数据流",
    "环境要求",
    "Windows 本地快速启动",
    "账号、密码与会话",
    "导入初始 1,221 行清单",
    "审核成员工作流",
    "Mao 的七标签中文后台",
    "增量 CSV 与本地下载边界",
    "验证命令",
    "故障排查",
    "仓库结构与后续阶段",
)

EN_SECTIONS = (
    "Outcome",
    "Repository and current development context",
    "Architecture and data flow",
    "Prerequisites",
    "Windows local quick start",
    "Accounts, passwords, and sessions",
    "Import the initial 1,221-row manifest",
    "Reviewer workflow",
    "Mao's seven-tab Chinese administration",
    "Incremental CSV and local downloader boundary",
    "Verification commands",
    "Troubleshooting",
    "Repository layout and later stages",
)


def test_bilingual_readmes_exist_and_keep_critical_commands_and_facts_in_parity():
    chinese = README_ZH.read_text(encoding="utf-8")
    english = README_EN.read_text(encoding="utf-8")

    for value in SHARED_COMMANDS_AND_FACTS:
        assert value in chinese
        assert value in english
    assert "[English](README.md)" in chinese
    assert "[中文](README_ZH.md)" in english
    assert "`/project`" in chinese and "`/project`" in english
    assert len(re.findall(r"^## ", chinese, flags=re.MULTILINE)) == len(ZH_SECTIONS)
    assert len(re.findall(r"^## ", english, flags=re.MULTILINE)) == len(EN_SECTIONS)
    for section in ZH_SECTIONS:
        assert f"## {section}" in chinese
    for section in EN_SECTIONS:
        assert f"## {section}" in english


def test_readmes_do_not_claim_deployment_or_embed_secret_material():
    chinese = README_ZH.read_text(encoding="utf-8")
    english = README_EN.read_text(encoding="utf-8")
    combined = f"{chinese}\n{english}"

    assert "-----BEGIN" not in combined
    assert not re.search(r"(?i)ssh-rsa|ssh-ed25519|postgresql\+asyncpg://[^<\s]+:[^<\s]+@", combined)
    assert "已部署" not in chinese
    assert "已经上线" not in chinese
    assert not re.search(r"(?i)\b(is|has been) deployed\b|currently live", english)


def test_environment_example_is_safe_complete_and_development_runnable():
    values = {}
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            name, value = stripped.split("=", 1)
            values[name] = value

    assert set(values) == {
        "DATABASE_URL",
        "SESSION_COOKIE_NAME",
        "SESSION_HOURS",
        "SESSION_SECRET",
        "CSRF_SECRET",
        "RECEIPT_SECRET",
        "APP_ENV",
        "SECURE_COOKIE",
        "TRUSTED_PROXY_CIDRS",
    }
    assert values["DATABASE_URL"].startswith("sqlite+aiosqlite:///")
    assert values["APP_ENV"] == "development"
    assert values["SECURE_COOKIE"] == "false"
    placeholder_secrets = {
        values["SESSION_SECRET"],
        values["CSRF_SECRET"],
        values["RECEIPT_SECRET"],
    }
    assert len(placeholder_secrets) == 3
    assert all(value.startswith("change-me-") for value in placeholder_secrets)
    ignored_paths = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "api/.env" in ignored_paths
