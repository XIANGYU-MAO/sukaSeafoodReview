from __future__ import annotations

import argparse
from pathlib import Path
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile


COLLECTOR_DIR = Path(__file__).resolve().parent
REPOSITORY_ROOT = COLLECTOR_DIR.parent
ZIP_PATH = (
    REPOSITORY_ROOT / "web" / "public" / "downloads" / "sukaseafood-collector.zip"
)
PACKAGE_ROOT = "sukaseafood-collector"
ALLOWLIST = (
    "collect_fish_images.py",
    "species_config.example.json",
    "requirements.txt",
    "README_ZH.md",
    "README.md",
)


def package_members() -> tuple[str, ...]:
    return tuple(f"{PACKAGE_ROOT}/{name}" for name in ALLOWLIST)


def build_package() -> None:
    ZIP_PATH.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(ZIP_PATH, "w", compression=ZIP_DEFLATED) as archive:
        for name, member in zip(ALLOWLIST, package_members(), strict=True):
            archive.write(COLLECTOR_DIR / name, member)


def check_package() -> None:
    if not ZIP_PATH.is_file():
        raise SystemExit(f"Collector package is missing: {ZIP_PATH}")

    try:
        with ZipFile(ZIP_PATH) as archive:
            if tuple(archive.namelist()) != package_members():
                raise SystemExit("Collector package members do not match the allowlist")
            for name, member in zip(ALLOWLIST, package_members(), strict=True):
                if archive.read(member) != (COLLECTOR_DIR / name).read_bytes():
                    raise SystemExit(f"Collector package is stale: {member}")
    except BadZipFile as error:
        raise SystemExit(f"Collector package is invalid: {ZIP_PATH}") from error


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the collector download ZIP")
    parser.add_argument("--check", action="store_true", help="verify the existing ZIP")
    args = parser.parse_args()

    if args.check:
        check_package()
    else:
        build_package()


if __name__ == "__main__":
    main()
