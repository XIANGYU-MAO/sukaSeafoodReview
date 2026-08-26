from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Mapping

import requests

from .engine import ProgressEvent, SyncCallbacks, SyncEngine
from .index import SyncIndex
from .manifest import load_manifest
from .receipt import (
    ReceiptError,
    build_receipt,
    load_receipt_file,
    prepare_receipt_directory,
    save_receipt_file,
    submit_receipt,
    validate_api_base,
)


DEFAULT_API_BASE = "https://findai.top/sukaseafood/api/v1"


@dataclass(frozen=True, slots=True)
class SyncRequest:
    manifest_path: Path
    dataset_root: Path
    api_base: str = DEFAULT_API_BASE
    receipt_dir: Path | None = None
    submit: bool = True
    dry_run: bool = False
    http_proxy: str | None = field(default=None, repr=False)
    https_proxy: str | None = field(default=None, repr=False)
    no_proxy: str | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class SyncOutcome:
    exit_code: int
    message: str
    counts: Mapping[str, int]
    offline_receipt_path: Path | None = None
    cancelled: bool = False


@dataclass(frozen=True, slots=True)
class ReceiptSubmitRequest:
    receipt_path: Path
    manifest_path: Path
    dataset_root: Path | None = None
    api_base: str = DEFAULT_API_BASE


def _ignore_progress(_event: ProgressEvent) -> None:
    return None


def _is_cancelled(cancel_event: object) -> bool:
    checker = getattr(cancel_event, "is_set", None)
    if not callable(checker):
        return False
    try:
        return bool(checker())
    except Exception:
        return True


def _cancel_aware_wait(cancel_event: object, delay: float) -> None:
    waiter = getattr(cancel_event, "wait", None)
    if callable(waiter):
        try:
            waiter(delay)
            return
        except Exception:
            return
    import time

    time.sleep(delay)


def _configure_session(session: requests.Session, request: SyncRequest) -> None:
    session_overrides = {
        key: value
        for key, value in (
            ("http", request.http_proxy),
            ("https", request.https_proxy),
            ("no_proxy", request.no_proxy),
        )
        if value
    }
    if not session_overrides:
        return
    session.proxies.update(session_overrides)
    transport_overrides = {
        key: value
        for key, value in session_overrides.items()
        if key in {"http", "https"}
    }
    original_merge = getattr(session, "merge_environment_settings", None)
    if not callable(original_merge):
        return

    def merge_with_overrides(
        url: str,
        proxies: Mapping[str, str] | None,
        stream: bool,
        verify: object,
        cert: object,
    ) -> dict[str, object]:
        requested = dict(proxies or {})
        if request.no_proxy:
            requested["no_proxy"] = request.no_proxy
        settings = original_merge(url, requested, stream, verify, cert)
        try:
            bypass = requests.utils.should_bypass_proxies(
                url,
                no_proxy=request.no_proxy or None,
            )
        except Exception:
            bypass = False
        if bypass:
            settings["proxies"] = {}
            return settings
        effective = dict(settings.get("proxies") or {})
        effective.update(transport_overrides)
        settings["proxies"] = effective
        return settings

    session.merge_environment_settings = merge_with_overrides  # type: ignore[method-assign]


def run_sync(
    request: SyncRequest,
    cancel_event: object,
    progress: Callable[[ProgressEvent], None] = _ignore_progress,
) -> SyncOutcome:
    """Run one sync and receipt workflow with a single caller-owned Session."""

    empty_counts = MappingProxyType(
        {"succeeded": 0, "failed": 0, "skipped": 0}
    )
    try:
        api_base = validate_api_base(request.api_base)
        manifest = load_manifest(request.manifest_path)
        receipt_directory = (
            prepare_receipt_directory(
                request.receipt_dir,
                create=not request.dry_run,
            )
            if request.receipt_dir is not None
            else request.dataset_root
        )
    except Exception:
        return SyncOutcome(2, "同步参数或增量 CSV 无效", empty_counts)
    if request.dry_run:
        actions = Counter(row.action for row in manifest.rows)
        summary = (
            f"ADD {actions['ADD']}, MOVE {actions['MOVE']}, "
            f"REMOVE {actions['REMOVE']}"
        )
        return SyncOutcome(
            0,
            summary,
            empty_counts,
        )
    session = requests.Session()
    counts: Mapping[str, int] = MappingProxyType(
        {"succeeded": 0, "failed": 0, "skipped": 0}
    )
    try:
        _configure_session(session, request)
        engine = SyncEngine(session=session)
        batch_result = engine.run(
            manifest,
            request.dataset_root,
            SyncCallbacks(progress=progress),
            cancel_event,
        )
        counts = MappingProxyType(dict(batch_result.counts))
        cancelled = bool(batch_result.cancelled) or _is_cancelled(cancel_event)
        if cancelled and not batch_result.receipt_items:
            return SyncOutcome(130, "同步已取消", counts, cancelled=True)
        receipt = build_receipt(manifest, batch_result)
        if cancelled or _is_cancelled(cancel_event):
            saved = save_receipt_file(
                receipt,
                receipt_directory,
            )
            return SyncOutcome(
                130,
                f"同步已取消，部分回执已保存：{saved}",
                counts,
                saved,
                True,
            )
        if not request.submit:
            saved = save_receipt_file(
                receipt,
                receipt_directory,
            )
            return SyncOutcome(4, f"回执已保存，尚未上传：{saved}", counts, saved)
        index_failed = False
        try:
            receipt_index = SyncIndex(request.dataset_root)
        except Exception:
            index_failed = True
            receipt_index = None
        if _is_cancelled(cancel_event):
            saved = save_receipt_file(
                receipt,
                receipt_directory,
            )
            return SyncOutcome(
                130,
                f"同步已取消，部分回执已保存：{saved}",
                counts,
                saved,
                True,
            )
        if index_failed:
            saved = save_receipt_file(
                receipt,
                receipt_directory,
            )
            return SyncOutcome(
                4,
                f"本地回执标记不可用，回执已保存：{saved}",
                counts,
                saved,
            )
        submission = submit_receipt(
            receipt,
            api_base,
            manifest.receipt_token,
            30.0,
            session=session,
            index=receipt_index,
            sleep=lambda delay: _cancel_aware_wait(cancel_event, delay),
            cancelled=lambda: _is_cancelled(cancel_event),
        )
        if submission.code == "SUBMITTED":
            exit_code = 3 if counts["failed"] else 0
            return SyncOutcome(exit_code, "同步完成", counts)
        if not submission.submitted and _is_cancelled(cancel_event):
            saved = save_receipt_file(
                receipt,
                receipt_directory,
            )
            return SyncOutcome(
                130,
                f"同步已取消，部分回执已保存：{saved}",
                counts,
                saved,
                True,
            )
        saved = save_receipt_file(
            receipt,
            receipt_directory,
        )
        return SyncOutcome(4, f"回执未完整上传，已保存：{saved}", counts, saved)
    except ReceiptError:
        return SyncOutcome(2, "回执文件无法保存或验证", counts)
    finally:
        session.close()


def submit_saved_receipt(request: ReceiptSubmitRequest) -> SyncOutcome:
    """Submit a bounded offline receipt using only its original CSV token."""

    empty_counts = MappingProxyType(
        {"succeeded": 0, "failed": 0, "skipped": 0}
    )
    try:
        api_base = validate_api_base(request.api_base)
        manifest = load_manifest(request.manifest_path)
        receipt = load_receipt_file(request.receipt_path, manifest)
        index = SyncIndex(request.dataset_root) if request.dataset_root is not None else None
        session = requests.Session()
    except Exception:
        return SyncOutcome(2, "回执文件、原始 CSV 或训练集目录无效", empty_counts)
    try:
        result = submit_receipt(
            receipt,
            api_base,
            manifest.receipt_token,
            30.0,
            session=session,
            index=index,
        )
        if result.code == "SUBMITTED":
            return SyncOutcome(0, "回执上传完成", empty_counts)
        return SyncOutcome(4, "回执尚未完整上传，原文件已保留", empty_counts)
    except Exception:
        return SyncOutcome(4, "回执尚未完整上传，原文件已保留", empty_counts)
    finally:
        session.close()
