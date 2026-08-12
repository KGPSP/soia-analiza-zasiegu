import csv
import json
import sqlite3
from pathlib import Path

import pytest

from soia.generator import OutputExistsError, generate_jst_report


def write_csv(path: Path, fieldnames: list[str], rows: list[list[object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(fieldnames)
        writer.writerows(rows)


def build_fixture(root: Path, *, with_sirens: bool = True, with_risk: bool = True) -> Path:
    tables = root / "ANALIZA_DOC" / "zalaczniki" / "tabele"
    csv_dir = root / "ANALIZA_DOC" / "zalaczniki" / "csv"
    analysis = root / "analysis-output" / "inwentaryzacja-syren-2026-05-05"
    write_csv(
        tables / "gmi_general_coverage.csv",
        ["teryt_gmi", "nazwa_gmi", "pop_total", "pop_ge65", "pop_ge70", "pop_ge75", "pop_outside_ge65", "pct_ge65", "pct_outside_ge65"],
        [["2815092", "Ostróda", 16375, 9986, 5571, 1998, 6389, 60.98, 39.02]],
    )
    write_csv(
        csv_dir / "A04_gminy.csv",
        ["teryt_woj", "wojewodztwo", "teryt_pow", "powiat", "teryt_gmi", "gmina", "risk_class", "pop_night_total", "pop_day_total", "pop_night_ge65", "pop_day_ge65", "pop_night_outside", "pop_day_outside", "pct_ge65_of_total", "pct_outside_ge65_of_total"],
        [["28", "WARMIŃSKO-MAZURSKIE", "2815", "ostródzki", "2815092", "Ostróda", "niskie ryzyko", 4.42, 4.42, 0, 0, 4.42, 4.42, 0, 100]] if with_risk else [],
    )
    write_csv(
        csv_dir / "A06_riskzone_poza_zasiegiem.csv",
        ["gmina", "powiat", "wojewodztwo", "teryt_gmi", "pop_risk_total", "pop_ge65", "pop_outside_ge65", "outside_share", "risk_class_max", "priority_score_v12", "rank", "new_large_siren_gap_count", "gap_count_not_priced_as_integration", "estimated_new_siren_gap_cost_pln", "new_siren_cost_per_pop_outside_ge65_pln"],
        [["Ostróda", "ostródzki", "WARMIŃSKO-MAZURSKIE", "2815092", 4.42, 0, 4.42, 1, "niskie ryzyko", 42.14, 1069, 0, 1, 0, 0]] if with_risk else [],
    )
    write_csv(
        csv_dir / "A07_luki_priorytetowe.csv",
        ["gap_id", "risk_class", "pop_outside_night", "area_ha", "teryt_gmi", "wojewodztwo", "powiat", "gmina", "priority_score_v12", "priority_class", "procurement_action", "estimated_new_siren_cost_pln", "lat", "lon"],
        [[5261, "niskie ryzyko", 4.42, 5, "2815092", "WARMIŃSKO-MAZURSKIE", "ostródzki", "Ostróda", 42.14, "C", "field_validation", 0, 53.707052, 19.936531]] if with_risk else [],
    )
    write_csv(
        csv_dir / "A08_kandydaci_syreny.csv",
        ["candidate_id", "nr_ref", "wojewodztwo", "powiat", "gmina", "teryt_gmi", "recommended_action", "priority_class", "estimated_cost_pln", "candidate_lat", "candidate_lon"],
        [],
    )
    write_csv(
        csv_dir / "A09_redundancja.csv",
        ["rank", "nr_ref", "woj", "powiat", "gmina", "lat", "lon", "gsm_status", "failure_loss_pop_night_proxy", "recommended_action", "estimated_cost_pln"],
        [[1, "S-1", "WARMIŃSKO-MAZURSKIE", "ostródzki", "Ostróda", 53.61, 20.02, "NIE", 352, "integracja", 5000]] if with_sirens else [],
    )
    write_csv(
        csv_dir / "A11_integracja_GSM.csv",
        ["wojewodztwo", "powiat", "gmina", "unique_sites_without_gsm", "integration_cost_unique_sites_pln"],
        [["WARMIŃSKO-MAZURSKIE", "ostródzki", "Ostróda", 1, 5000]] if with_sirens else [],
    )
    write_csv(
        analysis / "inwentaryzacja-syren-2026-05-05.normalized.analysis.final.V9.csv",
        ["nr_ref", "wojewodztwo", "powiat", "gmina", "lat", "lon", "rodzaj_syreny", "moc_w", "wysokosc_nad_terenem_m", "gsm_status", "sk_psp_status"],
        [["S-1", "WARMIŃSKO-MAZURSKIE", "ostródzki", "Ostróda", 53.61, 20.02, "cyfrowa", 600, 10, "NIE", "TAK"]] if with_sirens else [],
    )
    return root


def test_generator_creates_complete_ostroda_package(tmp_path: Path) -> None:
    data_root = build_fixture(tmp_path / "data")
    output = tmp_path / "output"

    result = generate_jst_report(data_root, "2815092", output, skip_pdf=True)

    for relative in [
        "README.md", "raport.md", "maps/pokrycie.png", "maps/lokalizacje.png",
        "tables/coverage.csv", "tables/risk.csv", "layers/soia_2815092.gpkg",
        "inventory-changes.csv", "manifest.json", "validation.json"
    ]:
        assert (output / relative).exists(), relative
    validation = json.loads((output / "validation.json").read_text())
    assert validation["validation_passed"] is True
    assert result.metrics["pop_total"] == 16375
    assert result.metrics["pop_ge65"] == 9986
    assert result.metrics["pop_outside_ge65"] == 6389
    assert result.metrics["pct_ge65"] == 60.98
    assert result.metrics["risk_pop_outside"] == pytest.approx(4.42)
    assert result.metrics["unique_sites_without_gsm"] == 1
    with sqlite3.connect(output / "layers" / "soia_2815092.gpkg") as connection:
        layers = {row[0] for row in connection.execute("select table_name from gpkg_contents")}
    assert {"admin_boundary", "sirens", "coverage_ranges", "priority_gaps", "candidates", "sensitive_objects"} <= layers


def test_generator_refuses_to_overwrite_without_force(tmp_path: Path) -> None:
    data_root = build_fixture(tmp_path / "data")
    output = tmp_path / "output"
    generate_jst_report(data_root, "2815092", output, skip_pdf=True)
    with pytest.raises(OutputExistsError):
        generate_jst_report(data_root, "2815092", output, skip_pdf=True)


def test_generator_handles_unit_without_sirens_or_risk(tmp_path: Path) -> None:
    data_root = build_fixture(tmp_path / "data", with_sirens=False, with_risk=False)
    result = generate_jst_report(data_root, "2815092", tmp_path / "output", skip_pdf=True)
    assert result.metrics["risk_pop_total"] == 0
    assert result.metrics["siren_count"] == 0
    assert "Brak populacji modelowej w RiskZone" in (tmp_path / "output" / "raport.md").read_text()
