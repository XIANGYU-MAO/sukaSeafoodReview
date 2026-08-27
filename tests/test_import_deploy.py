from __future__ import annotations

from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy/scripts/import_candidates_from_windows.ps1"


def test_import_upload_uses_only_sha_path_and_requires_clean_dry_run_for_commit():
    script = SCRIPT.read_text("utf-8")
    assert "C:\\Users\\86166\\Desktop\\sukaSeafoodReview\\collector\\output\\candidates.csv" in script
    assert ".Extension" in script and '".csv"' in script
    assert "ReparsePoint" in script
    assert "Get-FileHash" in script and "SHA256" in script
    assert "/opt/sukaseafood-review/imports/" in script
    assert "{0}.csv" in script or "$Sha256.csv" in script
    assert "--dry-run" in script and "--json-report" in script
    assert "$Report.blocking_errors -ne 0" in script
    assert "$Report.can_commit" in script
    assert "if ($Commit)" in script
    assert "--commit" in script
    assert "$RemoteCommitReport" in script and "$LocalCommitReport" in script
    assert "$CommitReport.file_sha256 -ne $Sha256" in script
    assert "total, inserted, skipped_exact, possible_url_duplicates" in script
    assert "1221" not in script
    assert "current_reviewer_id IS NULL" not in script
    assert "Invoke-WebRequest" not in script


def test_import_whatif_performs_no_network_or_file_read():
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-File", str(SCRIPT), "-WhatIf"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    assert completed.returncode == 0, completed.stderr
    assert "WHATIF-NO-NETWORK" in completed.stdout
