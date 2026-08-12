import csv
import json
import sys
from pathlib import Path

from tools.build_correction_registry import apply_registry, build_registry
from tools.replay_corrections import main as replay_main


def write(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def test_registry_replays_all_changes_to_final_v9(tmp_path: Path) -> None:
    v5 = tmp_path / "final.V5.csv"
    v6 = tmp_path / "final.V6.csv"
    v9 = tmp_path / "final.V9.csv"
    write(v5, [{"nr_ref": "A", "moc_w": "100", "wysokosc_nad_terenem_m": "0", "gmina": "X"}])
    write(v6, [{"nr_ref": "A", "moc_w": "100", "wysokosc_nad_terenem_m": "10", "gmina": "X"}])
    write(v9, [{"nr_ref": "A", "moc_w": "900", "wysokosc_nad_terenem_m": "10", "gmina": "X"}])

    registry = build_registry([("V5", v5), ("V6", v6), ("V9", v9)])
    replayed = apply_registry(v5, registry)

    assert replayed == [{"nr_ref": "A", "moc_w": "900", "wysokosc_nad_terenem_m": "10", "gmina": "X"}]
    assert [row["stage"] for row in registry] == ["V5→V6", "V6→V9"]
    assert all(row["rule_id"].startswith("CORR-") for row in registry)
    assert all(row["confidence"] for row in registry)


def test_registry_tracks_added_and_disabled_records(tmp_path: Path) -> None:
    before = tmp_path / "before.csv"
    after = tmp_path / "after.csv"
    write(before, [{"nr_ref": "A", "active": "1"}])
    write(after, [{"nr_ref": "B", "active": "1"}])

    registry = build_registry([("V5", before), ("V9", after)])

    assert {(row["operation"], row["nr_ref"]) for row in registry} == {("disable", "A"), ("add", "B")}


def test_replay_writes_v13_compatible_validation(monkeypatch, tmp_path: Path) -> None:
    v5 = tmp_path / "final.V5.csv"
    v9 = tmp_path / "final.V9.csv"
    registry_path = tmp_path / "corrections.csv"
    output = tmp_path / "inventory.final.V9.csv"
    rows = [{
        "nr_ref": "A", "rodzaj_syreny": "analogowa", "moc_w": "900",
        "wysokosc_nad_terenem_m": "10", "npm_m": "123", "gmina": "X",
    }]
    write(v5, rows)
    write(v9, rows)
    with registry_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "rule_id", "stage", "operation", "nr_ref", "field", "before",
                "after", "reason", "source", "confidence", "record_json",
            ],
        )
        writer.writeheader()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "replay_corrections.py", "--input", str(v5), "--registry",
            str(registry_path), "--output", str(output), "--expected", str(v9),
        ],
    )

    assert replay_main() == 0
    validation = json.loads(output.with_suffix(".validation.json").read_text())
    assert validation["validation_passed"] is True
    assert validation["rows"] == 1
    assert validation["columns"] == 6
    assert validation["analog_records"] == 1
    assert validation["npm_missing"] == 0
