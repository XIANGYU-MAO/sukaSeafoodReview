from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
README_ZH = ROOT / "README_ZH.md"
README_EN = ROOT / "README.md"
ENV_EXAMPLE = ROOT / "api" / ".env.example"

SHARED_COMMANDS_AND_FACTS = (
    "`main`",
    "https://github.com/XIANGYU-MAO/sukaSeafoodReview.git",
    "http://localhost:5173/sukaseafood/review/",
    "https://findai.top/sukaseafood/review",
    "/sukaseafood/api/v1",
    "C:\\Users\\86166\\Desktop\\sukaSeafoodReview\\collector\\output\\candidates.csv",
    "collector/",
    "local_sync/",
    "python.exe -m alembic upgrade head",
    "python.exe -m app.commands.seed_users --print-once",
    "python.exe -m app.commands.import_candidates",
    "python.exe -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000",
    "npm install",
    "https://pypi.tuna.tsinghua.edu.cn/simple",
    "https://registry.npmmirror.com",
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
    "采集与导入",
    "审核成员工作流",
    "七标签中文管理后台",
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
    "Collection and import",
    "Reviewer workflow",
    "Seven-tab Chinese administration",
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
    for text in (chinese, english):
        assert text.index("python.exe -m app.commands.seed_users --print-once") < text.index(
            "python.exe -m app.commands.import_candidates"
        )


def test_readmes_describe_published_main_checkout_security_boundaries_and_extensible_catalog():
    chinese = README_ZH.read_text(encoding="utf-8")
    english = README_EN.read_text(encoding="utf-8")
    checkout = "C:\\Users\\86166\\Desktop\\sukaSeafoodReview"

    assert checkout in chinese and checkout in english
    assert "已合并、推送并生产发布" in chinese
    assert "merged, pushed, and deployed to production" in english
    assert "仅存在于本地，尚未发布" not in chinese
    assert "local and unpublished" not in english
    assert "登录是未认证入口" in chinese
    assert "login is the unauthenticated entry point" in english
    assert "批次 token" in chinese and "batch token" in english
    assert "包含 `reason`" in chinese and "include `reason`" in english
    assert "SF006" in chinese and "SF006" in english
    assert "鱼种管理" in chinese and "Species management" in english


def test_readmes_describe_implemented_sync_envelope_storage_and_release_truth():
    chinese = README_ZH.read_text(encoding="utf-8")
    english = README_EN.read_text(encoding="utf-8")

    for text in (chinese, english):
        assert "local_sync/README_ZH.md" in text
        assert "10,000" in text
        assert "20 MiB" in text
        assert "/sukaseafood/review" in text
        assert "/sukaseafood/api/v1" in text
        assert "IMAGE_ORIGIN_ALLOWLIST" in text
    assert "Windows 本地同步工具已经实现" in chinese
    assert "Windows local-sync tool is implemented" in english
    assert "离线回执" in chinese and "offline receipt" in english
    assert "不保存图片字节、原图 URL 或批次 token" in chinese
    assert "stores no image bytes, original URLs, or batch tokens" in english
    assert "YGF 网关已删除 `/project`" in chinese
    assert "YGF gateway now returns 404 for `/project`" in english
    assert "已完成生产 SSH 部署与公开验收" in chinese
    assert "production SSH deployment and public acceptance are complete" in english
    assert "精确 17 列 CSV" in chinese
    assert "exact 17-column CSV" in english
    assert "七字段 CSV" not in chinese
    assert "seven-field CSV" not in english


def test_readmes_define_the_candidate_synchronization_generation_epoch():
    chinese = README_ZH.read_text(encoding="utf-8")
    english = README_EN.read_text(encoding="utf-8")

    assert "candidate synchronization generation" in english
    assert "候选图片同步代次" in chinese
    assert "review generation" not in english.lower()
    assert "审核代次" not in chinese
    for text in (chinese, english):
        assert "`review_version`" in text
        assert "`20260827_07`" in text
    assert "pending pre-revision batches" in english
    assert "修订前的待处理批次" in chinese


def test_readmes_show_the_exact_locked_clean_windows_build_command():
    chinese = README_ZH.read_text(encoding="utf-8")
    english = README_EN.read_text(encoding="utf-8")
    command = (
        "powershell -NoProfile -ExecutionPolicy Bypass -File "
        "local_sync/scripts/build_windows.ps1"
    )

    for text in (chinese, english):
        assert command in text
        assert "build_windows.ps1 -Clean -Locked" not in text


def test_readmes_claim_current_deployment_without_embedding_secret_material():
    chinese = README_ZH.read_text(encoding="utf-8")
    english = README_EN.read_text(encoding="utf-8")
    combined = f"{chinese}\n{english}"

    assert "-----BEGIN" not in combined
    assert not re.search(r"(?i)ssh-rsa|ssh-ed25519|postgresql\+asyncpg://[^<\s]+:[^<\s]+@", combined)
    assert "已部署" in chinese
    assert "is deployed" in english


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
