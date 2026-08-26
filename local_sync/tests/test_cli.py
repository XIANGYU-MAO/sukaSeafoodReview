from __future__ import annotations

import os
from io import StringIO
import json
from pathlib import Path
import runpy
import subprocess
import sys
from threading import Event
from types import ModuleType
from types import SimpleNamespace

import pytest

from conftest import BATCH_ID, RECEIPT_TOKEN, valid_row, write_manifest


def _module_environment() -> dict[str, str]:
    environment = os.environ.copy()
    source = str(Path(__file__).parents[1] / "src")
    environment["PYTHONPATH"] = os.pathsep.join(
        part for part in (source, environment.get("PYTHONPATH", "")) if part
    )
    environment["HTTP_PROXY"] = "http://127.0.0.1:9"
    environment["HTTPS_PROXY"] = "http://127.0.0.1:9"
    environment["NO_PROXY"] = ""
    return environment


def test_python_module_dry_run_is_headless_and_has_no_side_effects(tmp_path: Path) -> None:
    manifest_path = write_manifest(tmp_path)
    root = tmp_path / "must-not-be-created"

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "sukaseafood_sync",
            "sync",
            str(manifest_path),
            str(root),
            "--dry-run",
        ],
        cwd=tmp_path,
        env=_module_environment(),
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0
    assert completed.stdout.strip() == "ADD 1, MOVE 0, REMOVE 0"
    assert completed.stderr == ""
    assert not root.exists()


def test_inspect_prints_only_safe_batch_action_species_and_count_summaries(
    tmp_path: Path,
) -> None:
    from sukaseafood_sync.cli import main

    secret_url = "https://secret.example.test/private/fish.jpg"
    secret_creator = "PRIVATE CREATOR"
    secret_attribution = "PRIVATE ATTRIBUTION"
    manifest_path = write_manifest(
        tmp_path,
        rows=[
            valid_row(
                original_url=secret_url,
                creator=secret_creator,
                attribution=secret_attribution,
                license="PRIVATE LICENSE",
            )
        ],
    )
    stdout = StringIO()
    stderr = StringIO()

    exit_code = main(["inspect", str(manifest_path)], stdout=stdout, stderr=stderr)

    rendered = stdout.getvalue()
    assert exit_code == 0
    assert str(BATCH_ID) in rendered
    assert "ADD 1, MOVE 0, REMOVE 0" in rendered
    assert "SF006 1" in rendered
    assert "TOTAL 1" in rendered
    assert stderr.getvalue() == ""
    for forbidden in (
        RECEIPT_TOKEN,
        secret_url,
        secret_creator,
        secret_attribution,
        "PRIVATE LICENSE",
    ):
        assert forbidden not in rendered


def test_shared_sync_workflow_owns_one_session_and_submits_with_csv_token_and_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sukaseafood_sync.engine import BatchResult, ReceiptItem
    from sukaseafood_sync.receipt import SubmitResult
    from sukaseafood_sync import service

    manifest_path = write_manifest(tmp_path)
    root = tmp_path / "training"
    root.mkdir()
    session_instances: list[object] = []
    engine_sessions: list[object] = []
    submitted: list[tuple[object, str, object]] = []
    indexes: list[Path] = []

    class Session:
        def __init__(self) -> None:
            self.proxies: dict[str, str] = {}
            self.close_count = 0
            session_instances.append(self)

        def close(self) -> None:
            self.close_count += 1

    class Engine:
        def __init__(self, *, session: object) -> None:
            engine_sessions.append(session)

        def run(self, manifest, selected_root, callbacks, cancel_event) -> BatchResult:
            row = manifest.rows[0]
            callbacks.progress(
                service.ProgressEvent(1, 1, str(row.candidate_id), row.species_code, "SUCCEEDED", "safe")
            )
            return BatchResult(
                batch_id=str(manifest.batch_id),
                counts={"succeeded": 1, "failed": 0, "skipped": 0},
                receipt_items=(
                    ReceiptItem(
                        str(row.candidate_id),
                        str(row.review_id),
                        row.review_version,
                        "SUCCEEDED",
                        "a" * 64,
                        row.target_relative_path.as_posix(),
                        None,
                    ),
                ),
                cancelled=False,
                processed=1,
                total=1,
                operation_log_path=selected_root / "operations.jsonl",
            )

    class Index:
        def __init__(self, selected_root: Path) -> None:
            indexes.append(Path(selected_root))

    def submit(receipt, api_base, token, timeout, *, session, index):
        submitted.append((session, token, index))
        return SubmitResult(True, "SUBMITTED", status="completed", attempts=1)

    monkeypatch.setattr(service.requests, "Session", Session)
    monkeypatch.setattr(service, "SyncEngine", Engine)
    monkeypatch.setattr(service, "SyncIndex", Index)
    monkeypatch.setattr(service, "submit_receipt", submit)

    outcome = service.run_sync(
        service.SyncRequest(manifest_path=manifest_path, dataset_root=root),
        Event(),
    )

    assert outcome.exit_code == 0
    assert outcome.counts == {"succeeded": 1, "failed": 0, "skipped": 0}
    assert engine_sessions == session_instances
    assert submitted == [(session_instances[0], RECEIPT_TOKEN, submitted[0][2])]
    assert indexes == [root]
    assert session_instances[0].close_count == 1


def test_proxy_overrides_configure_only_the_run_session_and_stay_out_of_repr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sukaseafood_sync import service
    from sukaseafood_sync.receipt import SubmitResult

    manifest_path = write_manifest(tmp_path)
    root = tmp_path / "training"
    root.mkdir()
    http_secret = "http://proxy-user:proxy-pass@proxy.example.test:8080"
    https_secret = "http://secure-user:secure-pass@proxy.example.test:8443"
    no_proxy_secret = "private.internal.example"
    sessions: list[object] = []

    class Session:
        def __init__(self) -> None:
            self.proxies: dict[str, str] = {}
            sessions.append(self)

        def close(self) -> None:
            return None

    class Engine:
        def __init__(self, *, session: object) -> None:
            assert session is sessions[0]

        def run(self, manifest, selected_root, callbacks, cancel_event):
            return SimpleNamespace(
                counts={"succeeded": 1, "failed": 0, "skipped": 0},
                receipt_items=(object(),),
                cancelled=False,
            )

    monkeypatch.setattr(service.requests, "Session", Session)
    monkeypatch.setattr(service, "SyncEngine", Engine)
    monkeypatch.setattr(service, "build_receipt", lambda *_: object())
    monkeypatch.setattr(service, "SyncIndex", lambda _root: object())
    monkeypatch.setattr(
        service,
        "submit_receipt",
        lambda *_args, **_kwargs: SubmitResult(True, "SUBMITTED"),
    )

    request = service.SyncRequest(
        manifest_path=manifest_path,
        dataset_root=root,
        http_proxy=http_secret,
        https_proxy=https_secret,
        no_proxy=no_proxy_secret,
    )
    outcome = service.run_sync(request, Event())

    assert sessions[0].proxies == {
        "http": http_secret,
        "https": https_secret,
        "no_proxy": no_proxy_secret,
    }
    exposed = repr(request) + repr(outcome)
    for secret in (http_secret, https_secret, no_proxy_secret, RECEIPT_TOKEN):
        assert secret not in exposed


def test_proxy_overrides_win_over_environment_in_effective_session_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sukaseafood_sync import service
    from sukaseafood_sync.receipt import SubmitResult

    manifest_path = write_manifest(tmp_path)
    root = tmp_path / "training"
    root.mkdir()
    environment_http = "http://environment-http.example:8080"
    environment_https = "http://environment-https.example:8443"
    override_http = "http://override-http.example:9080"
    override_https = "http://override-https.example:9443"
    override_no_proxy = "bypass.example.test"
    for name in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy", "NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HTTP_PROXY", environment_http)
    monkeypatch.setenv("HTTPS_PROXY", environment_https)
    effective: list[dict[str, str]] = []

    class Engine:
        def __init__(self, *, session: object) -> None:
            self.session = session

        def run(self, manifest, selected_root, callbacks, cancel_event):
            settings = self.session.merge_environment_settings(
                "https://bypass.example.test/resource",
                {},
                False,
                self.session.verify,
                self.session.cert,
            )
            effective.append(dict(settings["proxies"]))
            return SimpleNamespace(
                counts={"succeeded": 1, "failed": 0, "skipped": 0},
                receipt_items=(object(),),
                cancelled=False,
            )

    monkeypatch.setattr(service, "SyncEngine", Engine)
    monkeypatch.setattr(service, "build_receipt", lambda *_args: object())
    monkeypatch.setattr(service, "SyncIndex", lambda _root: object())
    monkeypatch.setattr(
        service,
        "submit_receipt",
        lambda *_args, **_kwargs: SubmitResult(True, "SUBMITTED"),
    )

    outcome = service.run_sync(
        service.SyncRequest(
            manifest_path,
            root,
            http_proxy=override_http,
            https_proxy=override_https,
            no_proxy=override_no_proxy,
        ),
        Event(),
    )

    assert outcome.exit_code == 0
    assert effective[0]["http"] == override_http
    assert effective[0]["https"] == override_https
    assert effective[0]["no_proxy"] == override_no_proxy


def test_no_submit_saves_offline_receipt_and_exit_four_overrides_item_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sukaseafood_sync import service
    from sukaseafood_sync.engine import BatchResult, ReceiptItem

    manifest_path = write_manifest(tmp_path)
    root = tmp_path / "training"
    receipt_dir = tmp_path / "receipts"
    root.mkdir()
    receipt_dir.mkdir()
    saved_path = receipt_dir / f"download_receipt-{BATCH_ID}.json"

    class Session:
        proxies: dict[str, str] = {}

        def close(self) -> None:
            return None

    class Engine:
        def __init__(self, *, session: object) -> None:
            pass

        def run(self, manifest, selected_root, callbacks, cancel_event) -> BatchResult:
            row = manifest.rows[0]
            return BatchResult(
                str(manifest.batch_id),
                {"succeeded": 0, "failed": 1, "skipped": 0},
                (
                    ReceiptItem(
                        str(row.candidate_id),
                        str(row.review_id),
                        row.review_version,
                        "FAILED",
                        None,
                        None,
                        "NETWORK_ERROR",
                    ),
                ),
                False,
                1,
                1,
                selected_root / "operations.jsonl",
            )

    monkeypatch.setattr(service.requests, "Session", Session)
    monkeypatch.setattr(service, "SyncEngine", Engine)
    monkeypatch.setattr(
        service,
        "submit_receipt",
        lambda *_args, **_kwargs: pytest.fail("--no-submit must not upload"),
    )
    monkeypatch.setattr(
        service,
        "save_receipt_file",
        lambda receipt, selected: saved_path if selected == receipt_dir else pytest.fail("wrong receipt directory"),
        raising=False,
    )

    outcome = service.run_sync(
        service.SyncRequest(
            manifest_path=manifest_path,
            dataset_root=root,
            receipt_dir=receipt_dir,
            submit=False,
        ),
        Event(),
    )

    assert outcome.exit_code == 4
    assert outcome.offline_receipt_path == saved_path
    assert outcome.counts["failed"] == 1
    assert str(saved_path) in outcome.message


@pytest.mark.parametrize(
    "submit_code",
    ["CONFLICT", "AUTHENTICATION_FAILED", "RETRY_EXHAUSTED", "MALFORMED_RESPONSE", "INDEX_UPDATE_FAILED"],
)
def test_nonfull_online_submission_is_saved_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, submit_code: str
) -> None:
    from sukaseafood_sync import service
    from sukaseafood_sync.receipt import SubmitResult

    manifest_path = write_manifest(tmp_path)
    root = tmp_path / "training"
    root.mkdir()
    saved_path = root / f"download_receipt-{BATCH_ID}.json"

    class Session:
        proxies: dict[str, str] = {}

        def close(self) -> None:
            return None

    class Engine:
        def __init__(self, *, session: object) -> None:
            pass

        def run(self, manifest, selected_root, callbacks, cancel_event):
            return SimpleNamespace(
                counts={"succeeded": 1, "failed": 0, "skipped": 0},
                receipt_items=(object(),),
                cancelled=False,
            )

    monkeypatch.setattr(service.requests, "Session", Session)
    monkeypatch.setattr(service, "SyncEngine", Engine)
    monkeypatch.setattr(service, "build_receipt", lambda *_: object())
    monkeypatch.setattr(service, "SyncIndex", lambda _root: object())
    monkeypatch.setattr(
        service,
        "submit_receipt",
        lambda *_args, **_kwargs: SubmitResult(False, submit_code),
    )
    monkeypatch.setattr(service, "save_receipt_file", lambda _receipt, selected: saved_path)

    outcome = service.run_sync(service.SyncRequest(manifest_path, root), Event())

    assert outcome.exit_code == 4
    assert outcome.offline_receipt_path == saved_path
    assert str(saved_path) in outcome.message


def test_fully_submitted_receipt_with_item_failure_returns_three(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sukaseafood_sync import service
    from sukaseafood_sync.receipt import SubmitResult

    manifest_path = write_manifest(tmp_path)
    root = tmp_path / "training"
    root.mkdir()

    class Session:
        proxies: dict[str, str] = {}

        def close(self) -> None:
            return None

    class Engine:
        def __init__(self, *, session: object) -> None:
            pass

        def run(self, manifest, selected_root, callbacks, cancel_event):
            return SimpleNamespace(
                counts={"succeeded": 0, "failed": 1, "skipped": 0},
                receipt_items=(object(),),
                cancelled=False,
            )

    monkeypatch.setattr(service.requests, "Session", Session)
    monkeypatch.setattr(service, "SyncEngine", Engine)
    monkeypatch.setattr(service, "build_receipt", lambda *_args: object())
    monkeypatch.setattr(service, "SyncIndex", lambda _root: object())
    monkeypatch.setattr(
        service,
        "submit_receipt",
        lambda *_args, **_kwargs: SubmitResult(True, "SUBMITTED"),
    )

    outcome = service.run_sync(service.SyncRequest(manifest_path, root), Event())

    assert outcome.exit_code == 3


def test_post_run_index_setup_failure_saves_receipt_offline_and_returns_four(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sukaseafood_sync import service

    manifest_path = write_manifest(tmp_path)
    root = tmp_path / "training"
    root.mkdir()
    saved_path = root / f"download_receipt-{BATCH_ID}.json"
    secret = "private-index-path-secret"

    class Session:
        proxies: dict[str, str] = {}

        def __init__(self) -> None:
            self.closed = 0

        def close(self) -> None:
            self.closed += 1

    class Engine:
        def __init__(self, *, session: object) -> None:
            pass

        def run(self, manifest, selected_root, callbacks, cancel_event):
            return SimpleNamespace(
                counts={"succeeded": 1, "failed": 0, "skipped": 0},
                receipt_items=(object(),),
                cancelled=False,
            )

    monkeypatch.setattr(service.requests, "Session", Session)
    monkeypatch.setattr(service, "SyncEngine", Engine)
    monkeypatch.setattr(service, "build_receipt", lambda *_args: object())
    monkeypatch.setattr(
        service,
        "SyncIndex",
        lambda _root: (_ for _ in ()).throw(RuntimeError(secret)),
    )
    monkeypatch.setattr(
        service,
        "submit_receipt",
        lambda *_args, **_kwargs: pytest.fail("unmarkable receipt must not upload"),
    )
    monkeypatch.setattr(service, "save_receipt_file", lambda _receipt, _root: saved_path)

    outcome = service.run_sync(service.SyncRequest(manifest_path, root), Event())

    assert outcome.exit_code == 4
    assert outcome.offline_receipt_path == saved_path
    assert secret not in outcome.message + repr(outcome)


def test_cancellation_saves_nonempty_partial_receipt_without_submit_and_returns_130(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sukaseafood_sync import service
    from sukaseafood_sync.engine import BatchResult, ReceiptItem

    manifest_path = write_manifest(tmp_path)
    root = tmp_path / "training"
    root.mkdir()
    saved_path = root / f"download_receipt-{BATCH_ID}.json"

    class Session:
        proxies: dict[str, str] = {}

        def close(self) -> None:
            return None

    class Engine:
        def __init__(self, *, session: object) -> None:
            pass

        def run(self, manifest, selected_root, callbacks, cancel_event) -> BatchResult:
            row = manifest.rows[0]
            return BatchResult(
                str(manifest.batch_id),
                {"succeeded": 0, "failed": 1, "skipped": 0},
                (
                    ReceiptItem(
                        str(row.candidate_id),
                        str(row.review_id),
                        row.review_version,
                        "FAILED",
                        None,
                        None,
                        "NETWORK_ERROR",
                    ),
                ),
                True,
                1,
                1,
                selected_root / "operations.jsonl",
            )

    monkeypatch.setattr(service.requests, "Session", Session)
    monkeypatch.setattr(service, "SyncEngine", Engine)
    monkeypatch.setattr(
        service,
        "submit_receipt",
        lambda *_args, **_kwargs: pytest.fail("cancelled results must not upload"),
    )
    monkeypatch.setattr(service, "save_receipt_file", lambda _receipt, _selected: saved_path)

    outcome = service.run_sync(service.SyncRequest(manifest_path, root), Event())

    assert outcome.exit_code == 130
    assert outcome.cancelled is True
    assert outcome.offline_receipt_path == saved_path


def test_cancellation_before_any_item_returns_130_without_empty_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sukaseafood_sync import service

    manifest_path = write_manifest(tmp_path)
    root = tmp_path / "training"
    root.mkdir()

    class Session:
        proxies: dict[str, str] = {}

        def close(self) -> None:
            return None

    class Engine:
        def __init__(self, *, session: object) -> None:
            pass

        def run(self, manifest, selected_root, callbacks, cancel_event):
            return SimpleNamespace(
                counts={"succeeded": 0, "failed": 0, "skipped": 0},
                receipt_items=(),
                cancelled=True,
            )

    monkeypatch.setattr(service.requests, "Session", Session)
    monkeypatch.setattr(service, "SyncEngine", Engine)
    monkeypatch.setattr(
        service,
        "build_receipt",
        lambda *_args: pytest.fail("empty cancellation must not build a receipt"),
    )
    monkeypatch.setattr(
        service,
        "save_receipt_file",
        lambda *_args: pytest.fail("empty cancellation must not save a receipt"),
    )
    monkeypatch.setattr(
        service,
        "submit_receipt",
        lambda *_args, **_kwargs: pytest.fail("empty cancellation must not upload"),
    )

    outcome = service.run_sync(service.SyncRequest(manifest_path, root), Event())

    assert outcome.exit_code == 130
    assert outcome.cancelled is True
    assert outcome.offline_receipt_path is None


def test_offline_save_failure_returns_stable_two_and_closes_session_secret_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sukaseafood_sync import service
    from sukaseafood_sync.receipt import ReceiptError, SubmitResult

    manifest_path = write_manifest(tmp_path)
    root = tmp_path / "training"
    root.mkdir()
    sessions: list[object] = []

    class Session:
        proxies: dict[str, str] = {}

        def __init__(self) -> None:
            self.closed = 0
            sessions.append(self)

        def close(self) -> None:
            self.closed += 1

    class Engine:
        def __init__(self, *, session: object) -> None:
            pass

        def run(self, manifest, selected_root, callbacks, cancel_event):
            return SimpleNamespace(
                counts={"succeeded": 1, "failed": 0, "skipped": 0},
                receipt_items=(object(),),
                cancelled=False,
            )

    monkeypatch.setattr(service.requests, "Session", Session)
    monkeypatch.setattr(service, "SyncEngine", Engine)
    monkeypatch.setattr(service, "build_receipt", lambda *_args: object())
    monkeypatch.setattr(service, "SyncIndex", lambda _root: object())
    monkeypatch.setattr(
        service,
        "submit_receipt",
        lambda *_args, **_kwargs: SubmitResult(False, "CONFLICT"),
    )
    monkeypatch.setattr(
        service,
        "save_receipt_file",
        lambda *_args: (_ for _ in ()).throw(ReceiptError("FILE_WRITE_FAILED")),
    )

    outcome = service.run_sync(service.SyncRequest(manifest_path, root), Event())

    assert outcome.exit_code == 2
    assert outcome.offline_receipt_path is None
    assert sessions[0].closed == 1
    exposed = repr(outcome) + outcome.message
    assert RECEIPT_TOKEN not in exposed
    assert "FILE_WRITE_FAILED" not in exposed


def test_shared_dry_run_validates_and_summarizes_without_session_or_root_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sukaseafood_sync import service

    manifest_path = write_manifest(tmp_path)
    root = tmp_path / "absent-training-root"
    monkeypatch.setattr(
        service.requests,
        "Session",
        lambda: pytest.fail("dry-run must not construct a network Session"),
    )
    monkeypatch.setattr(
        service,
        "SyncEngine",
        lambda **_kwargs: pytest.fail("dry-run must not construct an engine"),
    )

    outcome = service.run_sync(
        service.SyncRequest(manifest_path, root, dry_run=True),
        Event(),
    )

    assert outcome.exit_code == 0
    assert outcome.message == "ADD 1, MOVE 0, REMOVE 0"
    assert not root.exists()


def test_invalid_api_base_is_exit_two_before_dry_run_session_or_root_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sukaseafood_sync import service

    manifest_path = write_manifest(tmp_path)
    root = tmp_path / "absent-training-root"
    monkeypatch.setattr(
        service.requests,
        "Session",
        lambda: pytest.fail("invalid parameters must not construct a Session"),
    )

    outcome = service.run_sync(
        service.SyncRequest(
            manifest_path,
            root,
            api_base="https://example.test/wrong/path",
            dry_run=True,
        ),
        Event(),
    )

    assert outcome.exit_code == 2
    assert not root.exists()


def test_cli_sync_routes_all_options_to_shared_service_without_echoing_proxies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sukaseafood_sync import cli
    from sukaseafood_sync.service import SyncOutcome

    manifest_path = tmp_path / "batch.csv"
    root = tmp_path / "training"
    receipts = tmp_path / "receipts"
    http_secret = "http://user:password@proxy.example.test:8080"
    https_secret = "http://secure:password@proxy.example.test:8443"
    no_proxy_secret = "private.example.test"
    captured: list[object] = []

    def run(request, cancel_event):
        captured.append(request)
        assert isinstance(cancel_event, Event)
        return SyncOutcome(
            4,
            f"回执未完整上传，已保存：{receipts / 'download_receipt.json'}",
            {"succeeded": 1, "failed": 0, "skipped": 0},
            receipts / "download_receipt.json",
        )

    monkeypatch.setattr(cli, "run_sync", run, raising=False)
    stdout = StringIO()
    stderr = StringIO()

    exit_code = cli.main(
        [
            "sync",
            str(manifest_path),
            str(root),
            "--api-base",
            "http://127.0.0.1:8000/sukaseafood/api/v1",
            "--receipt-dir",
            str(receipts),
            "--no-submit",
            "--http-proxy",
            http_secret,
            "--https-proxy",
            https_secret,
            "--no-proxy",
            no_proxy_secret,
        ],
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 4
    request = captured[0]
    assert request.manifest_path == manifest_path
    assert request.dataset_root == root
    assert request.api_base == "http://127.0.0.1:8000/sukaseafood/api/v1"
    assert request.receipt_dir == receipts
    assert request.submit is False
    assert request.http_proxy == http_secret
    assert request.https_proxy == https_secret
    assert request.no_proxy == no_proxy_secret
    assert stderr.getvalue() == ""
    rendered = stdout.getvalue()
    for secret in (http_secret, https_secret, no_proxy_secret):
        assert secret not in rendered


def test_load_offline_receipt_reconstructs_exact_manifest_mapping_without_token(
    tmp_path: Path,
) -> None:
    from sukaseafood_sync.manifest import load_manifest
    from sukaseafood_sync.receipt import load_receipt_file

    manifest_path = write_manifest(tmp_path)
    manifest = load_manifest(manifest_path)
    row = manifest.rows[0]
    receipt_path = tmp_path / "download_receipt.json"
    receipt_path.write_text(
        json.dumps(
            {
                "batch_id": str(manifest.batch_id),
                "items": [
                    {
                        "candidate_id": str(row.candidate_id),
                        "review_id": str(row.review_id),
                        "review_version": row.review_version,
                        "status": "SUCCEEDED",
                        "sha256": "a" * 64,
                        "relative_path": row.target_relative_path.as_posix(),
                        "error": None,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    receipt = load_receipt_file(receipt_path, manifest)

    assert receipt.to_file_dict() == json.loads(receipt_path.read_text(encoding="utf-8"))
    assert RECEIPT_TOKEN not in repr(receipt)
    assert RECEIPT_TOKEN not in json.dumps(receipt.to_file_dict())


def test_offline_receipt_loader_is_public_package_api() -> None:
    from sukaseafood_sync import load_receipt_file
    from sukaseafood_sync.receipt import load_receipt_file as implementation

    assert load_receipt_file is implementation


def test_offline_receipt_path_must_match_manifest_with_add_suffix_exception(
    tmp_path: Path,
) -> None:
    from sukaseafood_sync.manifest import load_manifest
    from sukaseafood_sync.receipt import ReceiptError, load_receipt_file

    manifest = load_manifest(write_manifest(tmp_path))
    row = manifest.rows[0]
    receipt_path = tmp_path / "download_receipt.json"
    payload = {
        "batch_id": str(manifest.batch_id),
        "items": [
            {
                "candidate_id": str(row.candidate_id),
                "review_id": str(row.review_id),
                "review_version": row.review_version,
                "status": "SUCCEEDED",
                "sha256": "b" * 64,
                "relative_path": "images/SF006/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa.jpg",
                "error": None,
            }
        ],
    }
    receipt_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ReceiptError, match="ITEM_MISMATCH"):
        load_receipt_file(receipt_path, manifest)

    payload["items"][0]["relative_path"] = row.target_relative_path.with_suffix(".png").as_posix()
    receipt_path.write_text(json.dumps(payload), encoding="utf-8")
    loaded = load_receipt_file(receipt_path, manifest)
    assert loaded.items[0].relative_path.endswith(".png")


def test_offline_receipt_loader_rejects_oversized_file_before_json_use(
    tmp_path: Path,
) -> None:
    from sukaseafood_sync.manifest import load_manifest
    from sukaseafood_sync.receipt import (
        MAX_RECEIPT_FILE_BYTES,
        ReceiptError,
        load_receipt_file,
    )

    manifest = load_manifest(write_manifest(tmp_path))
    row = manifest.rows[0]
    payload = json.dumps(
        {
            "batch_id": str(manifest.batch_id),
            "items": [
                {
                    "candidate_id": str(row.candidate_id),
                    "review_id": str(row.review_id),
                    "review_version": row.review_version,
                    "status": "FAILED",
                    "sha256": None,
                    "relative_path": None,
                    "error": "NETWORK_ERROR",
                }
            ],
        }
    ).encode("utf-8")
    receipt_path = tmp_path / "oversized.json"
    receipt_path.write_bytes(b" " * (MAX_RECEIPT_FILE_BYTES + 1) + payload)

    with pytest.raises(ReceiptError, match="RECEIPT_FILE_TOO_LARGE"):
        load_receipt_file(receipt_path, manifest)


def test_offline_receipt_loader_accepts_large_valid_server_batch(
    tmp_path: Path,
) -> None:
    from uuid import UUID

    from sukaseafood_sync.manifest import load_manifest
    from sukaseafood_sync.receipt import load_receipt_file

    rows: list[dict[str, str]] = []
    items: list[dict[str, object]] = []
    for number in range(1, 5_001):
        candidate = UUID(int=number)
        review = UUID(int=20_000 + number)
        rows.append(
            valid_row(
                candidate_id=candidate,
                review_id=review,
                target_relative_path=f"images/SF006/{candidate}.jpg",
            )
        )
        items.append(
            {
                "candidate_id": str(candidate),
                "review_id": str(review),
                "review_version": 1,
                "status": "FAILED",
                "sha256": None,
                "relative_path": None,
                "error": "NETWORK_ERROR",
            }
        )
    manifest = load_manifest(write_manifest(tmp_path, rows=rows))
    receipt_path = tmp_path / "large-valid.json"
    receipt_path.write_text(
        json.dumps({"batch_id": str(manifest.batch_id), "items": items}),
        encoding="utf-8",
    )
    assert receipt_path.stat().st_size > 1024 * 1024

    receipt = load_receipt_file(receipt_path, manifest)

    assert len(receipt.items) == 5_000


def test_offline_receipt_loader_rejects_deep_json_with_stable_error(
    tmp_path: Path,
) -> None:
    from sukaseafood_sync.manifest import load_manifest
    from sukaseafood_sync.receipt import (
        MAX_RECEIPT_JSON_DEPTH,
        ReceiptError,
        load_receipt_file,
    )

    manifest = load_manifest(write_manifest(tmp_path))
    nested: object = "NETWORK_ERROR"
    for _ in range(MAX_RECEIPT_JSON_DEPTH + 1):
        nested = [nested]
    row = manifest.rows[0]
    payload = {
        "batch_id": str(manifest.batch_id),
        "items": [
            {
                "candidate_id": str(row.candidate_id),
                "review_id": str(row.review_id),
                "review_version": row.review_version,
                "status": "FAILED",
                "sha256": None,
                "relative_path": None,
                "error": nested,
            }
        ],
    }
    receipt_path = tmp_path / "deep.json"
    receipt_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ReceiptError, match="RECEIPT_FILE_TOO_DEEP"):
        load_receipt_file(receipt_path, manifest)


def test_submit_saved_receipt_uses_original_csv_token_one_session_and_optional_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sukaseafood_sync import service
    from sukaseafood_sync.receipt import SubmitResult

    manifest_path = write_manifest(tmp_path)
    receipt_path = tmp_path / "download_receipt.json"
    receipt_path.write_text("{}", encoding="utf-8")
    root = tmp_path / "training"
    root.mkdir()
    sessions: list[object] = []
    indexes: list[Path] = []
    submissions: list[tuple[object, str, object]] = []
    loaded_receipt = object()

    class Session:
        def __init__(self) -> None:
            self.closed = 0
            sessions.append(self)

        def close(self) -> None:
            self.closed += 1

    class Index:
        def __init__(self, selected: Path) -> None:
            indexes.append(Path(selected))

    def submit(receipt, api_base, token, timeout, *, session, index):
        assert receipt is loaded_receipt
        submissions.append((session, token, index))
        return SubmitResult(True, "SUBMITTED", status="completed", attempts=1)

    monkeypatch.setattr(service.requests, "Session", Session)
    monkeypatch.setattr(service, "load_receipt_file", lambda path, manifest: loaded_receipt, raising=False)
    monkeypatch.setattr(service, "SyncIndex", Index)
    monkeypatch.setattr(service, "submit_receipt", submit)

    outcome = service.submit_saved_receipt(
        service.ReceiptSubmitRequest(
            receipt_path=receipt_path,
            manifest_path=manifest_path,
            dataset_root=root,
        )
    )

    assert outcome.exit_code == 0
    assert indexes == [root]
    assert submissions[0][0] is sessions[0]
    assert submissions[0][1] == RECEIPT_TOKEN
    assert submissions[0][2].__class__ is Index
    assert sessions[0].closed == 1
    assert receipt_path.exists()


def test_submit_receipt_cli_requires_original_csv_and_never_accepts_or_echoes_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sukaseafood_sync import cli
    from sukaseafood_sync.service import SyncOutcome

    receipt_path = tmp_path / "download_receipt.json"
    manifest_path = tmp_path / "batch.csv"
    root = tmp_path / "training"
    captured: list[object] = []

    def submit(request):
        captured.append(request)
        return SyncOutcome(
            4,
            "回执尚未完整上传，原文件已保留",
            {"succeeded": 0, "failed": 0, "skipped": 0},
        )

    monkeypatch.setattr(cli, "submit_saved_receipt", submit, raising=False)
    stdout = StringIO()
    stderr = StringIO()
    exit_code = cli.main(
        [
            "submit-receipt",
            str(receipt_path),
            "--batch-csv",
            str(manifest_path),
            "--dataset-root",
            str(root),
            "--api-base",
            "http://localhost:8000/sukaseafood/api/v1",
        ],
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 4
    assert captured[0].receipt_path == receipt_path
    assert captured[0].manifest_path == manifest_path
    assert captured[0].dataset_root == root
    assert captured[0].api_base == "http://localhost:8000/sukaseafood/api/v1"

    forbidden = "command-line-token-secret"
    stdout = StringIO()
    stderr = StringIO()
    invalid_exit = cli.main(
        [
            "submit-receipt",
            str(receipt_path),
            "--batch-csv",
            str(manifest_path),
            "--token",
            forbidden,
        ],
        stdout=stdout,
        stderr=stderr,
    )
    assert invalid_exit == 2
    assert forbidden not in stdout.getvalue() + stderr.getvalue()


def test_offline_receipt_loader_rejects_symlink_file(
    tmp_path: Path,
) -> None:
    from sukaseafood_sync.manifest import load_manifest
    from sukaseafood_sync.receipt import ReceiptError, load_receipt_file

    manifest = load_manifest(write_manifest(tmp_path))
    row = manifest.rows[0]
    target = tmp_path / "real-receipt.json"
    target.write_text(
        json.dumps(
            {
                "batch_id": str(manifest.batch_id),
                "items": [
                    {
                        "candidate_id": str(row.candidate_id),
                        "review_id": str(row.review_id),
                        "review_version": row.review_version,
                        "status": "FAILED",
                        "sha256": None,
                        "relative_path": None,
                        "error": "NETWORK_ERROR",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    linked = tmp_path / "linked-receipt.json"
    try:
        linked.symlink_to(target)
    except OSError:
        pytest.skip("symlinks unavailable on this platform")

    with pytest.raises(ReceiptError, match="INVALID_RECEIPT_FILE"):
        load_receipt_file(linked, manifest)


def test_cli_version_is_headless_and_returns_zero() -> None:
    from sukaseafood_sync import __version__
    from sukaseafood_sync.cli import main

    stdout = StringIO()
    stderr = StringIO()

    exit_code = main(["--version"], stdout=stdout, stderr=stderr)

    assert exit_code == 0
    assert stdout.getvalue().strip() == __version__
    assert stderr.getvalue() == ""


def test_python_module_version_routes_to_cli_without_tk(tmp_path: Path) -> None:
    from sukaseafood_sync import __version__

    completed = subprocess.run(
        [sys.executable, "-m", "sukaseafood_sync", "--version"],
        cwd=tmp_path,
        env=_module_environment(),
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0
    assert completed.stdout.strip() == __version__
    assert completed.stderr == ""


def test_python_module_without_arguments_routes_to_gui_headlessly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    fake_gui = ModuleType("sukaseafood_sync.gui")
    fake_gui.main = lambda: calls.append("gui") or 0  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sukaseafood_sync.gui", fake_gui)
    monkeypatch.setattr(sys, "argv", ["sukaseafood_sync"])

    with pytest.raises(SystemExit) as caught:
        runpy.run_module("sukaseafood_sync.__main__", run_name="__main__")

    assert caught.value.code == 0
    assert calls == ["gui"]


@pytest.mark.parametrize(
    ("scope", "extra_key"),
    [
        ("top", "receipt_token"),
        ("item", "original_url"),
        ("item", "action"),
        ("item", "perceptual_hash"),
    ],
)
def test_offline_receipt_loader_rejects_every_forbidden_extra_field(
    tmp_path: Path, scope: str, extra_key: str
) -> None:
    from sukaseafood_sync.manifest import load_manifest
    from sukaseafood_sync.receipt import ReceiptError, load_receipt_file

    manifest = load_manifest(write_manifest(tmp_path))
    row = manifest.rows[0]
    payload = {
        "batch_id": str(manifest.batch_id),
        "items": [
            {
                "candidate_id": str(row.candidate_id),
                "review_id": str(row.review_id),
                "review_version": row.review_version,
                "status": "FAILED",
                "sha256": None,
                "relative_path": None,
                "error": "NETWORK_ERROR",
            }
        ],
    }
    destination = payload if scope == "top" else payload["items"][0]
    destination[extra_key] = RECEIPT_TOKEN
    receipt_path = tmp_path / f"extra-{extra_key}.json"
    receipt_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ReceiptError, match="INVALID_RECEIPT_FILE") as caught:
        load_receipt_file(receipt_path, manifest)
    assert RECEIPT_TOKEN not in str(caught.value)
