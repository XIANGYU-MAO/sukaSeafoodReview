from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
README_ZH = ROOT / "README_ZH.md"
LOCAL_README_ZH = ROOT / "local_sync" / "README_ZH.md"


def test_local_readme_defines_generation_and_schema_v3_replacement_safety():
    root_readme_zh = README_ZH.read_text(encoding="utf-8")
    local_readme_zh = LOCAL_README_ZH.read_text(encoding="utf-8")

    assert "候选图片同步代次" in root_readme_zh
    assert "候选图片同步代次" in local_readme_zh
    assert "审核代次" not in local_readme_zh
    assert "SQLite schema v3" in local_readme_zh
    assert "同路径替换" in local_readme_zh
    assert "不会覆盖用户修改过的文件" in local_readme_zh
    assert "未执行线上部署" in local_readme_zh
