from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

from app.config import Settings
from app.database import create_database_engine, create_session_factory
from app.services.imports import (
    MAX_UPLOAD_BYTES,
    commit_candidate_csv_from_cli,
    dry_run_candidate_csv,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate a candidate CSV")
    parser.add_argument("path", type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--commit", action="store_true")
    parser.add_argument("--json-report", type=Path)
    return parser


async def _execute(settings: Settings, content: bytes, *, commit: bool) -> dict:
    engine = create_database_engine(settings)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            if commit:
                result = await commit_candidate_csv_from_cli(
                    session,
                    content,
                    image_origin_allowlist=settings.IMAGE_ORIGIN_ALLOWLIST,
                )
                return result.model_dump(mode="json")
            report = await dry_run_candidate_csv(
                session,
                content,
                image_origin_allowlist=settings.IMAGE_ORIGIN_ALLOWLIST,
            )
            return report.model_dump(mode="json", exclude={"preview_token"})
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        with args.path.open("rb") as source:
            content = source.read(MAX_UPLOAD_BYTES + 1)
        settings = Settings.from_env()
        report = asyncio.run(_execute(settings, content, commit=args.commit))
        encoded = json.dumps(report, ensure_ascii=False, sort_keys=True)
        if args.json_report is not None:
            args.json_report.write_text(encoded + "\n", encoding="utf-8")
        print(encoded)
        if args.dry_run and not report["can_commit"]:
            codes = sorted({issue["code"] for issue in report["issues"] if issue["blocking"]})
            print(
                "candidate CSV dry-run blocked: " + ", ".join(codes or ["validation error"]),
                file=sys.stderr,
            )
            return 2
        return 0
    except (OSError, ValueError) as exc:
        print(f"candidate CSV dry-run failed: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"candidate CSV dry-run failed: {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
