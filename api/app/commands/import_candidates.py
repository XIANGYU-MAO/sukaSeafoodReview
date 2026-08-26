from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

from app.config import Settings
from app.database import create_database_engine, create_session_factory
from app.services.imports import MAX_UPLOAD_BYTES, dry_run_candidate_csv


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate a candidate CSV")
    parser.add_argument("path", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json-report", type=Path)
    return parser


async def _dry_run(settings: Settings, content: bytes) -> dict:
    engine = create_database_engine(settings)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            report = await dry_run_candidate_csv(session, content)
            return report.model_dump(mode="json", exclude={"preview_token"})
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.dry_run:
        parser.error("only --dry-run is supported")
    try:
        with args.path.open("rb") as source:
            content = source.read(MAX_UPLOAD_BYTES + 1)
        settings = Settings.from_env()
        report = asyncio.run(_dry_run(settings, content))
        encoded = json.dumps(report, ensure_ascii=False, sort_keys=True)
        if args.json_report is not None:
            args.json_report.write_text(encoded + "\n", encoding="utf-8")
        print(encoded)
        if not report["can_commit"]:
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
