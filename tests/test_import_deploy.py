from __future__ import annotations

from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy/scripts/import_candidates_from_windows.ps1"


def test_import_upload_uses_only_sha_path_and_requires_clean_dry_run_for_commit():
    script = SCRIPT.read_text("utf-8")
    assert ".Extension" in script and '".csv"' in script
    assert "ReparsePoint" in script
    assert "Get-FileHash" in script and "SHA256" in script
    assert "/opt/sukaseafood-review/imports/" in script
    assert "{0}.csv" in script or "$Sha256.csv" in script
    assert "--dry-run" in script and "--json-report" in script
    assert "$Report.total -ne 1221" in script
    assert "$Report.blocking_errors -ne 0" in script
    assert "if ($Commit)" in script
    assert "--commit" in script
    assert "current_reviewer_id IS NULL" in script
    assert "-At" in script and "-F '|'" in script
    assert '$CountOutput -ne "1221|1221"' in script
    assert "FISH_VISTA" in script and "INATURALIST" in script
    assert "GBIF" in script and "WIKIMEDIA_COMMONS" in script
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
