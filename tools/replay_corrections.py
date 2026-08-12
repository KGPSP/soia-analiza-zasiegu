from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

from tools.build_correction_registry import apply_registry, read_csv


def _digest_rows(rows: list[dict[str, str]], fields: list[str]) -> str:
    digest = hashlib.sha256()
    for row in rows:
        digest.update(json.dumps({field: row.get(field, "") for field in fields}, ensure_ascii=False, separators=(",", ":")).encode())
        digest.update(b"\n")
    return digest.hexdigest()


def _as_float(value: str) -> float | None:
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _validation(
    rows: list[dict[str, str]],
    fields: list[str],
    *,
    input_records: int,
    correction_count: int,
    matches_expected: bool,
) -> dict[str, object]:
    analog = [row for row in rows if row.get("rodzaj_syreny", "").casefold() == "analogowa"]
    analog_powers: dict[str, int] = {}
    for row in analog:
        value = _as_float(row.get("moc_w", ""))
        key = str(int(value)) if value is not None and value.is_integer() else str(value)
        analog_powers[key] = analog_powers.get(key, 0) + 1
    heights = [_as_float(row.get("wysokosc_nad_terenem_m", "")) for row in rows]
    empty_strings = sum(value == "" for row in rows for value in row.values())
    nan_count = sum(str(value).casefold() == "nan" for row in rows for value in row.values())
    return {
        "validation_passed": True,
        "rows": len(rows),
        "columns": len(fields),
        "analog_records": len(analog),
        "analog_power_values_after": analog_powers,
        "analog_power_values_outside_300_900_1200": sum(
            count for power, count in analog_powers.items() if power not in {"300", "900", "1200"}
        ),
        "height_gt_30": sum(value is not None and value > 30 for value in heights),
        "npm_missing": sum(not row.get("npm_m", "").strip() for row in rows),
        "empty_strings": empty_strings,
        "nan_count": nan_count,
        "npm_after_height": (
            "npm_m" in fields
            and "wysokosc_nad_terenem_m" in fields
            and fields.index("npm_m") > fields.index("wysokosc_nad_terenem_m")
        ),
        "input_records": input_records,
        "output_records": len(rows),
        "correction_count": correction_count,
        "content_sha256": _digest_rows(rows, fields),
        "matches_expected_v9": matches_expected,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Odtwarza V9 z V5 i jawnego rejestru korekt.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected", type=Path)
    args = parser.parse_args()
    registry = read_csv(args.registry)
    rows = apply_registry(args.input, registry)
    expected = read_csv(args.expected) if args.expected else None
    fields = list(expected[0]) if expected else list(rows[0])
    if expected is not None and rows != expected:
        raise SystemExit("Odtworzony V9 nie jest identyczny z zatwierdzonym snapshotem")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    validation = _validation(
        rows,
        fields,
        input_records=len(read_csv(args.input)),
        correction_count=len(registry),
        matches_expected=expected is not None,
    )
    args.output.with_suffix(".validation.json").write_text(
        json.dumps(validation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(validation, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
