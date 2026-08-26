from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
import sys
from threading import Event
from typing import Sequence, TextIO

from . import __version__
from .manifest import ManifestError, load_manifest
from .service import (
    DEFAULT_API_BASE,
    ReceiptSubmitRequest,
    SyncRequest,
    run_sync,
    submit_saved_receipt,
)


class _ArgumentError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        raise _ArgumentError


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="suka-seafood-sync", add_help=True)
    subcommands = parser.add_subparsers(dest="command", required=True)
    inspect = subcommands.add_parser("inspect")
    inspect.add_argument("batch_csv", type=Path)
    sync = subcommands.add_parser("sync")
    sync.add_argument("batch_csv", type=Path)
    sync.add_argument("dataset_root", type=Path)
    sync.add_argument("--dry-run", action="store_true")
    sync.add_argument("--api-base", default=DEFAULT_API_BASE)
    sync.add_argument("--receipt-dir", type=Path)
    sync.add_argument("--no-submit", action="store_true")
    sync.add_argument("--http-proxy")
    sync.add_argument("--https-proxy")
    sync.add_argument("--no-proxy")
    receipt = subcommands.add_parser("submit-receipt")
    receipt.add_argument("receipt_path", type=Path)
    receipt.add_argument("--batch-csv", required=True, type=Path)
    receipt.add_argument("--dataset-root", type=Path)
    receipt.add_argument("--api-base", default=DEFAULT_API_BASE)
    return parser


def _action_summary(actions: Sequence[str]) -> str:
    counts = Counter(actions)
    return f"ADD {counts['ADD']}, MOVE {counts['MOVE']}, REMOVE {counts['REMOVE']}"


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    output = stdout or sys.stdout
    errors = stderr or sys.stderr
    selected_argv = list(argv) if argv is not None else sys.argv[1:]
    if selected_argv == ["--version"]:
        print(__version__, file=output)
        return 0
    try:
        arguments = _parser().parse_args(selected_argv)
    except _ArgumentError:
        print("参数错误", file=errors)
        return 2
    if arguments.command == "inspect":
        try:
            manifest = load_manifest(arguments.batch_csv)
        except (ManifestError, OSError, ValueError):
            print("增量 CSV 无效", file=errors)
            return 2
        print(f"BATCH {manifest.batch_id}", file=output)
        print(_action_summary([row.action for row in manifest.rows]), file=output)
        species = Counter(row.species_code for row in manifest.rows)
        for code in sorted(species):
            print(f"{code} {species[code]}", file=output)
        print(f"TOTAL {len(manifest.rows)}", file=output)
        return 0
    if arguments.command == "submit-receipt":
        try:
            outcome = submit_saved_receipt(
                ReceiptSubmitRequest(
                    receipt_path=arguments.receipt_path,
                    manifest_path=arguments.batch_csv,
                    dataset_root=arguments.dataset_root,
                    api_base=arguments.api_base,
                )
            )
        except Exception:
            print("回执文件或原始 CSV 无效", file=errors)
            return 2
        print(
            outcome.message,
            file=errors if outcome.exit_code == 2 else output,
        )
        return outcome.exit_code
    if arguments.command == "sync":
        try:
            outcome = run_sync(
                SyncRequest(
                    manifest_path=arguments.batch_csv,
                    dataset_root=arguments.dataset_root,
                    api_base=arguments.api_base,
                    receipt_dir=arguments.receipt_dir,
                    submit=not arguments.no_submit,
                    dry_run=arguments.dry_run,
                    http_proxy=arguments.http_proxy,
                    https_proxy=arguments.https_proxy,
                    no_proxy=arguments.no_proxy,
                ),
                Event(),
            )
        except KeyboardInterrupt:
            print("同步已取消", file=errors)
            return 130
        except Exception:
            print("同步参数或文件无效", file=errors)
            return 2
        print(
            outcome.message,
            file=errors if outcome.exit_code == 2 else output,
        )
        return outcome.exit_code
    print("参数错误", file=errors)
    return 2
