from __future__ import annotations

import csv
from collections.abc import Iterable
from pathlib import Path

from .teryt import TerytCode


def repair_text(value: str) -> str:
    if not any(marker in value for marker in ("Ã", "Å", "Ä")):
        return value
    try:
        return value.encode("latin1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [
            {key: repair_text((value or "").strip()) for key, value in row.items()}
            for row in csv.DictReader(handle)
        ]


def write_csv(path: Path, rows: Iterable[dict[str, object]], fieldnames: list[str] | None = None) -> None:
    records = list(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        fieldnames = []
        for row in records:
            for field in row:
                if field not in fieldnames:
                    fieldnames.append(field)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)


def first_existing(root: Path, relatives: Iterable[str]) -> Path:
    attempted: list[Path] = []
    for relative in relatives:
        path = root / relative
        attempted.append(path)
        if path.exists():
            return path
    raise FileNotFoundError("Brak wymaganego pliku; sprawdzono: " + ", ".join(map(str, attempted)))


def table_path(root: Path, name: str, *, general: bool = False) -> Path:
    if general:
        candidates = [
            f"ANALIZA_DOC/zalaczniki/tabele/{name}",
            f"publication/tables/{name}",
            f"tables/{name}",
        ]
    else:
        candidates = [
            f"ANALIZA_DOC/zalaczniki/csv/{name}",
            f"analysis-output/inwentaryzacja-syren-2026-05-05/report_SOIA_V13/csv/{name}",
            f"publication/tables/{name}",
            f"tables/{name}",
        ]
    return first_existing(root, candidates)


def matches_teryt(row: dict[str, str], teryt: TerytCode) -> bool:
    value = row.get("teryt_gmi") or row.get("teryt_pow") or row.get("teryt_woj") or ""
    return value.startswith(teryt.code)


def as_float(row: dict[str, str], field: str, default: float = 0.0) -> float:
    value = row.get(field, "")
    try:
        return float(value.replace(",", ".")) if value else default
    except (TypeError, ValueError):
        return default
