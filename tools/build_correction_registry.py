from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections.abc import Iterable
from copy import deepcopy
from pathlib import Path

REGISTRY_FIELDS = [
    "rule_id", "stage", "operation", "nr_ref", "field", "before", "after",
    "reason", "source", "confidence",
]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [{key: value or "" for key, value in row.items()} for row in csv.DictReader(handle)]


def _rule_id(stage: str, operation: str, nr_ref: str, field: str) -> str:
    digest = hashlib.sha256(f"{stage}|{operation}|{nr_ref}|{field}".encode()).hexdigest()[:12].upper()
    return f"CORR-{digest}"


def _reason(stage: str, field: str, before: str, after: str) -> tuple[str, str]:
    if field == "wysokosc_nad_terenem_m":
        reason = "Korekta wysokości montażu zgodnie z kontrolami domenowymi V5–V8."
    elif field == "moc_w" and "V9" in stage:
        reason = "Ujednolicenie mocy modelowej, w tym analogowej SAD do odpowiednika DSE V9."
    elif field == "moc_w":
        reason = "Korekta jednostki lub wartości mocy po kontroli jakości."
    elif field == "npm_m":
        reason = "Uzupełnienie wysokości n.p.m. z danych SRTM."
    else:
        reason = "Zmiana zarejestrowana między zatwierdzonymi snapshotami pipeline."
    confidence = "1.0" if before != "" and after != "" else "0.9"
    return reason, confidence


def build_registry(versions: list[tuple[str, Path]]) -> list[dict[str, str]]:
    if len(versions) < 2:
        raise ValueError("Rejestr wymaga co najmniej dwóch wersji")
    registry: list[dict[str, str]] = []
    for (before_name, before_path), (after_name, after_path) in zip(versions, versions[1:]):
        before_rows = read_csv(before_path)
        after_rows = read_csv(after_path)
        before = {row["nr_ref"]: row for row in before_rows}
        after = {row["nr_ref"]: row for row in after_rows}
        stage = f"{before_name}→{after_name}"
        before_fields = set(before_rows[0]) if before_rows else set()
        after_fields = set(after_rows[0]) if after_rows else set()
        removed_fields = sorted(before_fields - after_fields)
        for field in removed_fields:
            registry.append({
                "rule_id": _rule_id(stage, "remove_field", "*", field), "stage": stage,
                "operation": "remove_field", "nr_ref": "*", "field": field,
                "before": "column present", "after": "column removed",
                "reason": "Usunięcie technicznej kolumny roboczej ze schematu kolejnego snapshotu.",
                "source": f"{before_path.name} -> {after_path.name}", "confidence": "1.0",
            })
        for nr_ref in sorted(before.keys() | after.keys()):
            if nr_ref not in before:
                fields = {key: value for key, value in after[nr_ref].items() if value}
                field, old, new, operation = "record_json", "", json.dumps(fields, ensure_ascii=False, sort_keys=True), "add"
                reason, confidence = "Rekord dodany między snapshotami.", "1.0"
                registry.append({
                    "rule_id": _rule_id(stage, operation, nr_ref, field), "stage": stage,
                    "operation": operation, "nr_ref": nr_ref, "field": field, "before": old,
                    "after": new, "reason": reason, "source": after_path.name, "confidence": confidence,
                })
                continue
            if nr_ref not in after:
                field, old, new, operation = "record", "active", "disabled", "disable"
                registry.append({
                    "rule_id": _rule_id(stage, operation, nr_ref, field), "stage": stage,
                    "operation": operation, "nr_ref": nr_ref, "field": field, "before": old,
                    "after": new, "reason": "Rekord usunięty z aktywnego snapshotu.",
                    "source": after_path.name, "confidence": "1.0",
                })
                continue
            for field in sorted((before[nr_ref].keys() | after[nr_ref].keys()) - set(removed_fields)):
                if field == "nr_ref":
                    continue
                old, new = before[nr_ref].get(field, ""), after[nr_ref].get(field, "")
                if old == new:
                    continue
                reason, confidence = _reason(stage, field, old, new)
                registry.append({
                    "rule_id": _rule_id(stage, "update", nr_ref, field), "stage": stage,
                    "operation": "update", "nr_ref": nr_ref, "field": field,
                    "before": old, "after": new, "reason": reason,
                    "source": f"{before_path.name} -> {after_path.name}", "confidence": confidence,
                })
    return registry


def apply_registry(base: Path | Iterable[dict[str, str]], registry: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    rows = read_csv(base) if isinstance(base, Path) else [deepcopy(row) for row in base]
    by_ref = {row["nr_ref"]: row for row in rows}
    for change in registry:
        nr_ref = change["nr_ref"]
        operation = change["operation"]
        if operation == "remove_field":
            for row in by_ref.values():
                row.pop(change["field"], None)
        elif operation == "add":
            row = json.loads(change["after"])
            by_ref[nr_ref] = row
        elif operation == "disable":
            by_ref.pop(nr_ref, None)
        else:
            if nr_ref not in by_ref:
                raise ValueError(f"Brak rekordu {nr_ref} podczas odtwarzania {change['rule_id']}")
            if by_ref[nr_ref].get(change["field"], "") != change["before"]:
                raise ValueError(f"Niezgodna wartość przed korektą {change['rule_id']}")
            by_ref[nr_ref][change["field"]] = change["after"]
    return list(by_ref.values())


def _write(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=REGISTRY_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Buduje odtwarzalny rejestr korekt V5–V9.")
    parser.add_argument("--analysis-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("data/corrections/corrections-v5-v9.csv"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    prefix = "inwentaryzacja-syren-2026-05-05.normalized.analysis.final"
    versions = [(version, args.analysis_dir / f"{prefix}.{version}.csv") for version in ("V5", "V6", "V7", "V8", "V9")]
    registry = build_registry(versions)
    replayed = apply_registry(versions[0][1], registry)
    expected = read_csv(versions[-1][1])
    if replayed != expected:
        raise SystemExit("Rejestr nie odtwarza finalnego V9")
    _write(args.output, registry)
    print(json.dumps({"changes": len(registry), "recreates_v9": True}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
