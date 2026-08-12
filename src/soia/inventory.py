from __future__ import annotations

import csv
from collections.abc import Iterable
from copy import deepcopy
from pathlib import Path


class InventoryUpdateError(ValueError):
    """Nakładka inwentaryzacji jest niespójna albo niepoprawna."""


OPERATIONS = {"add", "update", "disable"}
ADD_REQUIRED = {
    "teryt_gmi",
    "lat",
    "lon",
    "rodzaj_syreny",
    "moc_w",
    "wysokosc_nad_terenem_m",
}
UPDATE_CONTROL_FIELDS = {"operation", "nr_ref", "reason"}


def read_updates(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or not {"operation", "nr_ref"}.issubset(reader.fieldnames):
            raise InventoryUpdateError("CSV musi zawierać kolumny operation i nr_ref")
        return [
            {key: (value or "").strip() for key, value in row.items()}
            for row in reader
            if any((value or "").strip() for value in row.values())
        ]


def _number(value: str, name: str, nr_ref: str) -> float:
    try:
        return float(value.replace(",", "."))
    except (TypeError, ValueError) as error:
        raise InventoryUpdateError(f"{nr_ref}: niepoprawna wartość {name}") from error


def _validate_add(row: dict[str, str], valid_gmina_codes: set[str]) -> None:
    nr_ref = row["nr_ref"]
    missing = sorted(field for field in ADD_REQUIRED if not row.get(field))
    if missing:
        raise InventoryUpdateError(f"{nr_ref}: brak pól add: {', '.join(missing)}")
    if row["teryt_gmi"] not in valid_gmina_codes:
        raise InventoryUpdateError(f"{nr_ref}: nieznany TERYT gminy {row['teryt_gmi']}")
    lat = _number(row["lat"], "lat", nr_ref)
    lon = _number(row["lon"], "lon", nr_ref)
    power = _number(row["moc_w"], "moc_w", nr_ref)
    height = _number(row["wysokosc_nad_terenem_m"], "wysokosc_nad_terenem_m", nr_ref)
    if not (48.5 <= lat <= 55.2 and 13.5 <= lon <= 24.5):
        raise InventoryUpdateError(f"{nr_ref}: współrzędne poza zakresem kontrolnym Polski")
    if not (300 <= power <= 5500) or not (3 <= height <= 30):
        raise InventoryUpdateError(f"{nr_ref}: moc lub wysokość poza zakresem modelu")


def _validate_updated_record(
    row: dict[str, str], nr_ref: str, valid_gmina_codes: set[str]
) -> None:
    if row.get("teryt_gmi") and row["teryt_gmi"] not in valid_gmina_codes:
        raise InventoryUpdateError(f"{nr_ref}: nieznany TERYT gminy {row['teryt_gmi']}")
    lat = _number(row.get("lat", ""), "lat", nr_ref)
    lon = _number(row.get("lon", ""), "lon", nr_ref)
    power = _number(row.get("moc_w", ""), "moc_w", nr_ref)
    height = _number(
        row.get("wysokosc_nad_terenem_m", ""), "wysokosc_nad_terenem_m", nr_ref
    )
    if not (48.5 <= lat <= 55.2 and 13.5 <= lon <= 24.5):
        raise InventoryUpdateError(f"{nr_ref}: współrzędne poza zakresem kontrolnym Polski")
    if not (300 <= power <= 5500) or not (3 <= height <= 30):
        raise InventoryUpdateError(f"{nr_ref}: moc lub wysokość poza zakresem modelu")


def _audit_row(
    operation: str,
    nr_ref: str,
    field: str,
    before: str,
    after: str,
    reason: str,
) -> dict[str, str]:
    return {
        "operation": operation,
        "nr_ref": nr_ref,
        "field": field,
        "before": before,
        "after": after,
        "reason": reason,
        "status": "applied",
    }


def apply_updates(
    inventory: Iterable[dict[str, str]],
    updates: Iterable[dict[str, str]],
    *,
    valid_gmina_codes: set[str],
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    result = [deepcopy(row) for row in inventory]
    by_ref = {row["nr_ref"]: row for row in result}
    update_rows = list(updates)
    references = [row.get("nr_ref", "").strip() for row in update_rows]
    if len(references) != len(set(references)):
        raise InventoryUpdateError("CSV zawiera więcej niż jedną operację dla tego samego nr_ref")

    for row in update_rows:
        operation = row.get("operation", "").lower()
        nr_ref = row.get("nr_ref", "")
        if operation not in OPERATIONS or not nr_ref:
            raise InventoryUpdateError(f"{nr_ref or '<brak nr_ref>'}: niepoprawna operacja")
        if operation == "add":
            if nr_ref in by_ref:
                raise InventoryUpdateError(f"{nr_ref}: rekord już istnieje")
            _validate_add(row, valid_gmina_codes)
            new_row = {key: value for key, value in row.items() if key not in UPDATE_CONTROL_FIELDS}
            new_row.update({"nr_ref": nr_ref, "active": "1"})
            result.append(new_row)
            by_ref[nr_ref] = new_row
        else:
            if nr_ref not in by_ref:
                raise InventoryUpdateError(f"{nr_ref}: rekord nie istnieje")
            if operation == "disable":
                unexpected = [
                    field for field, value in row.items()
                    if field not in UPDATE_CONTROL_FIELDS and value
                ]
                if unexpected:
                    raise InventoryUpdateError(
                        f"{nr_ref}: disable przyjmuje tylko nr_ref i opcjonalne reason"
                    )
                by_ref[nr_ref]["active"] = "0"
            else:
                changed = [
                    field for field, value in row.items()
                    if field not in UPDATE_CONTROL_FIELDS and value
                ]
                if not changed:
                    raise InventoryUpdateError(f"{nr_ref}: update nie zawiera zmienianych pól")
                for field, value in row.items():
                    if field in UPDATE_CONTROL_FIELDS or not value:
                        continue
                    if field in {"lat", "lon", "moc_w", "wysokosc_nad_terenem_m"}:
                        _number(value, field, nr_ref)
                    by_ref[nr_ref][field] = value
                _validate_updated_record(by_ref[nr_ref], nr_ref, valid_gmina_codes)

    audit: list[dict[str, str]] = []
    original = {row["nr_ref"]: row for row in inventory}
    for row in update_rows:
        operation = row["operation"].lower()
        nr_ref = row["nr_ref"]
        reason = row.get("reason", "")
        if operation == "add":
            audit.append(_audit_row(operation, nr_ref, "record", "", "added", reason))
        elif operation == "disable":
            audit.append(_audit_row(operation, nr_ref, "active", original[nr_ref].get("active", "1"), "0", reason))
        else:
            changed_fields = [
                field for field, value in row.items()
                if field not in UPDATE_CONTROL_FIELDS and value
            ]
            summary_before = "; ".join(f"{field}={original[nr_ref].get(field, '')}" for field in changed_fields)
            summary_after = "; ".join(f"{field}={by_ref[nr_ref].get(field, '')}" for field in changed_fields)
            audit.append(_audit_row(operation, nr_ref, ",".join(changed_fields), summary_before, summary_after, reason))
    return result, audit
