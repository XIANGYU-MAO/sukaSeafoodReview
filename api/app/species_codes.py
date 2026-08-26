from __future__ import annotations

import re


SAFE_SPECIES_CODE_PATTERN = re.compile(r"[A-Z][A-Z0-9_-]{0,31}\Z", re.ASCII)
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


def is_safe_species_code(value: str) -> bool:
    return (
        SAFE_SPECIES_CODE_PATTERN.fullmatch(value) is not None
        and value not in WINDOWS_RESERVED_NAMES
    )


def require_safe_species_code(value: str) -> str:
    if not is_safe_species_code(value):
        raise ValueError(
            "species code must be a Windows-safe ASCII identifier using "
            "A-Z, 0-9, underscore, or hyphen"
        )
    return value


def species_code_check_sql(dialect: str, column: str = "code") -> str:
    reserved = ", ".join(f"'{name}'" for name in sorted(WINDOWS_RESERVED_NAMES))
    if dialect == "sqlite":
        allowed = (
            f"substr({column}, 1, 1) GLOB '[A-Z]' "
            f"AND {column} NOT GLOB '*[^A-Z0-9_-]*'"
        )
    elif dialect == "postgresql":
        allowed = f"{column} ~ '^[A-Z][A-Z0-9_-]{{0,31}}$'"
    else:
        raise ValueError(f"unsupported species code constraint dialect: {dialect}")
    return (
        f"length({column}) BETWEEN 1 AND 32 "
        f"AND {allowed} "
        f"AND {column} NOT IN ({reserved})"
    )


SQLITE_SPECIES_CODE_CHECK_SQL = species_code_check_sql("sqlite")
POSTGRESQL_SPECIES_CODE_CHECK_SQL = species_code_check_sql("postgresql")
