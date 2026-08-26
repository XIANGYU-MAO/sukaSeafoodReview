from __future__ import annotations

import ast
from io import StringIO
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

from sukaseafood_sync.cli import main as cli_main
import sukaseafood_sync.selftest as selftest


LOCAL_SYNC_ROOT = Path(__file__).parents[1]
REPOSITORY_ROOT = LOCAL_SYNC_ROOT.parent


def _powershell_literal(path: Path) -> str:
    return str(path).replace("'", "''")


def test_cleanup_refuses_nested_junction_before_deleting_any_content(
    tmp_path: Path,
) -> None:
    """A nested junction must not escape cleanup or cause partial in-tree deletion."""

    if os.name != "nt":
        pytest.skip("Windows junction behavior is Windows-specific")
    local_root = tmp_path / "local_sync"
    candidate = local_root / "build"
    outside = tmp_path / "outside"
    candidate.mkdir(parents=True)
    outside.mkdir()
    in_tree = candidate / "must-remain.txt"
    sentinel = outside / "outside-sentinel.txt"
    in_tree.write_text("inside", encoding="utf-8")
    sentinel.write_text("outside", encoding="utf-8")
    junction = candidate / "nested-junction"
    created = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(outside)],
        text=True,
        capture_output=True,
        check=False,
    )
    if created.returncode != 0:
        pytest.skip(f"junction creation unavailable: {created.stderr or created.stdout}")

    helper = LOCAL_SYNC_ROOT / "scripts" / "build_helpers.ps1"
    command = (
        f". '{_powershell_literal(helper)}'; "
        f"Remove-VerifiedTree -Candidate '{_powershell_literal(candidate)}' "
        f"-RequiredParent '{_powershell_literal(local_root)}'"
    )
    try:
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                command,
            ],
            text=True,
            capture_output=True,
            timeout=15,
            check=False,
        )
        assert result.returncode != 0
        assert "reparse" in (result.stdout + result.stderr).casefold()
        assert sentinel.read_text("utf-8") == "outside"
        assert in_tree.read_text("utf-8") == "inside"
    finally:
        if junction.exists():
            junction.rmdir()


def test_build_lock_is_exact_and_covers_clean_windows_runtime_and_tests() -> None:
    """A floating or incomplete dependency can make a clean build non-reproducible."""

    lock_path = LOCAL_SYNC_ROOT / "requirements-build.lock"
    active_lines = [
        line.strip()
        for line in lock_path.read_text("utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    exact = re.compile(r"([A-Za-z0-9_.-]+)==([A-Za-z0-9_.+!-]+)(?:\s*;\s*.+)?\Z")
    parsed = [exact.fullmatch(line) for line in active_lines]
    assert active_lines
    assert all(match is not None for match in parsed)
    names = {match.group(1).lower().replace("_", "-") for match in parsed if match}
    assert {
        "certifi",
        "imagehash",
        "pillow",
        "pyinstaller",
        "pyinstaller-hooks-contrib",
        "pytest",
        "requests",
        "responses",
        "setuptools",
    } <= names


def test_spec_defines_one_console_capable_onedir_with_explicit_collections() -> None:
    """A onefile/GUI-only or under-collected spec breaks supported launch modes."""

    spec_path = LOCAL_SYNC_ROOT / "packaging" / "suka-seafood-sync.spec"
    source = spec_path.read_text("utf-8")
    tree = ast.parse(source)
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    names = {
        node.func.id
        for node in calls
        if isinstance(node.func, ast.Name)
    }
    constants = {node.value for node in ast.walk(tree) if isinstance(node, ast.Constant)}
    assert {"Analysis", "PYZ", "EXE", "COLLECT"} <= names
    assert "SukaSeafoodTrainingSync" in constants
    assert "sukaseafood_sync" in constants
    assert "PIL" in constants
    assert "certifi" in constants
    assert "sukaseafood-sync" in constants
    assert "tkinter" in constants
    assert "_tkinter" in constants
    assert "scipy.fftpack" in constants
    assert "scipy.special._cdflib" in constants
    assert "scipy._lib._testutils" in constants
    assert "numpy.testing" in constants
    assert "numpy._pytesttester" in constants
    assert "pywt._pytesttester" in constants
    assert "tests" in constants
    runtime_hook = (
        LOCAL_SYNC_ROOT / "packaging" / "pyi_rth_scipy_runtime.py"
    ).read_text("utf-8")
    assert 'sys.modules["scipy._lib._testutils"]' in runtime_hook
    assert 'sys.modules["numpy.testing"]' in runtime_hook
    assert 'sys.modules["numpy._pytesttester"]' in runtime_hook
    assert 'sys.modules["pywt._pytesttester"]' in runtime_hook
    assert "numpy.testing =" in runtime_hook
    assert "PytestTester" in runtime_hook
    assert re.search(r"exclude_binaries\s*=\s*True", source)
    assert re.search(r"console\s*=\s*True", source)


def test_scipy_runtime_hook_keeps_phash_working_without_test_modules() -> None:
    hook = LOCAL_SYNC_ROOT / "packaging" / "pyi_rth_scipy_runtime.py"
    code = (
        "import runpy; "
        f"runpy.run_path({str(hook)!r}); "
        "import imagehash; "
        "from PIL import Image; "
        "value = str(imagehash.phash(Image.new('RGB', (32, 24)))); "
        "print('PHASH OK' if len(value) == 16 else 'PHASH BAD')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "PHASH OK\n"


def test_build_script_is_fail_fast_task_local_and_checks_artifact_boundary() -> None:
    """A broad delete or unchecked bundle can erase or package unrelated local data."""

    script = (LOCAL_SYNC_ROOT / "scripts" / "build_windows.ps1").read_text("utf-8")
    required_fragments = (
        "$ErrorActionPreference = \"Stop\"",
        "Set-StrictMode -Version Latest",
        "Assert-TaskLocalPath",
        "Assert-NoReparseAncestry",
        ".build",
        "requirements-build.lock",
        "--no-deps",
        "--no-build-isolation",
        "-m", "pytest",
        "--clean",
        "--noconfirm",
        "SukaSeafoodTrainingSync.exe",
        "--version",
        "self-test",
        "SHA256SUMS.txt",
        "Get-FileHash",
        "Assert-PackageBoundary",
        "PyInstaller.utils.cliutils.archive_viewer",
        "forbidden frozen module",
        "direct_url",
        "ReparsePoint",
        "-LiteralPath",
    )
    assert all(fragment in script for fragment in required_fragments)
    assert "Invoke-Expression" not in script
    assert "Remove-Item -Recurse" not in script
    assert not re.search(r"Remove-Item[^\r\n]*(?:\$HOME|~|[\"']/[\"'])", script)


def test_cleanup_rechecks_each_entry_ancestry_before_bottom_up_removal() -> None:
    helper = (LOCAL_SYNC_ROOT / "scripts" / "build_helpers.ps1").read_text("utf-8")

    assert re.search(
        r"function Assert-SafeTreeEntry[\s\S]*?"
        r"Assert-NoReparseAncestry\s+-Candidate\s+\$safeEntry",
        helper,
    )


def test_task_local_build_outputs_are_ignored() -> None:
    """Generated venv/build/dist trees must never enter the source commit."""

    candidates = (
        "local_sync/.build/windows-venv/pyvenv.cfg",
        "local_sync/build/suka-seafood-sync/Analysis-00.toc",
        "local_sync/dist/SukaSeafoodTrainingSync/SukaSeafoodTrainingSync.exe",
        "local_sync/src/sukaseafood_sync.egg-info/PKG-INFO",
    )
    result = subprocess.run(
        ["git", "check-ignore", *candidates],
        cwd=REPOSITORY_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0
    assert set(result.stdout.splitlines()) == set(candidates)


def test_ci_builds_windows_artifact_without_deployment_or_production_secrets() -> None:
    """A publish/deploy step would turn packaging CI into a production mutation."""

    workflow = (REPOSITORY_ROOT / ".github" / "workflows" / "windows-sync.yml").read_text(
        "utf-8"
    )
    lowered = workflow.casefold()
    assert "windows-latest" in lowered
    assert "python-version: \"3.12\"" in lowered
    assert "local_sync/scripts/build_windows.ps1" in lowered
    assert "local_sync/dist/sukaseafoodtrainingsync" in lowered
    assert "local_sync/dist/sha256sums.txt" in lowered
    assert "actions/upload-artifact@" in lowered
    assert "permissions:\n  contents: read" in lowered
    for forbidden in (
        "deploy",
        "release",
        "docker push",
        "git push",
        "findai.top",
        "secrets.",
        "workflow_dispatch",
    ):
        assert forbidden not in lowered


def test_chinese_readme_covers_safe_local_incremental_workflow() -> None:
    """Omitting recovery or data-flow guidance can cause unsafe operator actions."""

    readme = (LOCAL_SYNC_ROOT / "README_ZH.md").read_text("utf-8")
    for concept in (
        "管理后台",
        "双击",
        "训练集目录",
        "动态鱼种",
        "代理",
        "NO_PROXY",
        "失败重试",
        "取消",
        "离线回执",
        "_removed",
        "恢复",
        "备份",
        "SHA256SUMS.txt",
        ".sukaseafood-sync.sqlite3",
        "PostgreSQL",
        "不保存图片字节",
        "不保存原图地址",
        "不保存回执令牌",
        "不会经过中国服务器",
    ):
        assert concept in readme
    assert "Mao 管理后台" not in readme


def test_module_version_is_headless_and_matches_package_version() -> None:
    """Importing Tk for --version can hang the frozen CLI on headless systems."""

    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(LOCAL_SYNC_ROOT / "src")
    result = subprocess.run(
        [sys.executable, "-m", "sukaseafood_sync", "--version"],
        cwd=LOCAL_SYNC_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "0.1.0"
    assert result.stderr == ""


def test_module_self_test_exercises_local_frozen_dependencies_without_network() -> None:
    """The functional diagnostic must cover ADD dependencies with stable safe output."""

    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(LOCAL_SYNC_ROOT / "src")
    environment["HTTP_PROXY"] = "http://127.0.0.1:1"
    environment["HTTPS_PROXY"] = "http://127.0.0.1:1"
    result = subprocess.run(
        [sys.executable, "-m", "sukaseafood_sync", "self-test"],
        cwd=LOCAL_SYNC_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout == "SELF-TEST OK\n"
    assert result.stderr == ""


def test_self_test_failure_reports_only_stable_phase_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Frozen diagnostic failures must identify a phase without leaking details."""

    def fail() -> None:
        raise selftest.SelfTestError("IMAGE_PIPELINE")

    monkeypatch.setattr(selftest, "run_self_test", fail)
    stdout = StringIO()
    stderr = StringIO()

    exit_code = cli_main(["self-test"], stdout=stdout, stderr=stderr)

    assert exit_code == 2
    assert stdout.getvalue() == ""
    assert stderr.getvalue() == "SELF-TEST FAILED IMAGE_PIPELINE\n"


def test_self_test_identifies_packaged_phash_dependency_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing frozen ImageHash/SciPy path must have a safe, useful phase code."""

    def fail_phash(_image):
        raise ImportError("private installation path must not be printed")

    monkeypatch.setattr(selftest.imagehash, "phash", fail_phash)

    with pytest.raises(selftest.SelfTestError) as caught:
        selftest.run_self_test()

    assert caught.value.code == "IMAGE_PHASH_BINARY"
    assert str(caught.value) == "IMAGE_PHASH_BINARY"


def test_self_test_classifies_known_missing_cdflib_without_leaking_module_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_phash(_image):
        raise ModuleNotFoundError(
            "untrusted exception details", name="scipy.special._cdflib"
        )

    monkeypatch.setattr(selftest.imagehash, "phash", fail_phash)

    with pytest.raises(selftest.SelfTestError) as caught:
        selftest.run_self_test()

    assert caught.value.code == "IMAGE_PHASH_MODULE_CDFLIB"
    assert "untrusted" not in str(caught.value)


def test_self_test_classifies_missing_stdlib_test_support(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_phash(_image):
        raise ModuleNotFoundError("details must not be printed", name="unittest")

    monkeypatch.setattr(selftest.imagehash, "phash", fail_phash)

    with pytest.raises(selftest.SelfTestError) as caught:
        selftest.run_self_test()

    assert caught.value.code == "IMAGE_PHASH_MODULE_TEST_SUPPORT"


def test_self_test_classifies_phash_attribute_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_phash(_image):
        raise AttributeError("dependency internals must not be printed")

    monkeypatch.setattr(selftest.imagehash, "phash", fail_phash)

    with pytest.raises(selftest.SelfTestError) as caught:
        selftest.run_self_test()

    assert caught.value.code == "IMAGE_PHASH_ATTRIBUTE"


def test_self_test_classifies_phash_runtime_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_phash(_image):
        raise RuntimeError("dependency internals must not be printed")

    monkeypatch.setattr(selftest.imagehash, "phash", fail_phash)

    with pytest.raises(selftest.SelfTestError) as caught:
        selftest.run_self_test()

    assert caught.value.code == "IMAGE_PHASH_RUNTIME_ERROR"


def test_frozen_entry_script_version_uses_absolute_package_imports() -> None:
    """Executing PyInstaller's entry script directly must retain package imports."""

    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(LOCAL_SYNC_ROOT / "src")
    result = subprocess.run(
        [
            sys.executable,
            str(LOCAL_SYNC_ROOT / "src" / "sukaseafood_sync" / "__main__.py"),
            "--version",
        ],
        cwd=LOCAL_SYNC_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "0.1.0"
    assert result.stderr == ""
