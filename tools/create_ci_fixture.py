from __future__ import annotations

import argparse
import csv
from pathlib import Path


def _write(path: Path, fieldnames: list[str], rows: list[list[object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(fieldnames)
        writer.writerows(rows)


def create_fixture(root: Path) -> None:
    tables = root / "ANALIZA_DOC/zalaczniki/tabele"
    attachments = root / "ANALIZA_DOC/zalaczniki/csv"
    analysis = root / "analysis-output/inwentaryzacja-syren-2026-05-05"
    _write(
        tables / "gmi_general_coverage.csv",
        [
            "teryt_gmi", "nazwa_gmi", "pop_total", "pop_ge65", "pop_ge70",
            "pop_ge75", "pop_outside_ge65", "pct_ge65", "pct_outside_ge65",
        ],
        [["2815092", "Ostróda", 16375, 9986, 5571, 1998, 6389, 60.98, 39.02]],
    )
    _write(
        attachments / "A04_gminy.csv",
        [
            "teryt_woj", "wojewodztwo", "teryt_pow", "powiat", "teryt_gmi",
            "gmina", "risk_class", "pop_night_total", "pop_day_total",
            "pop_night_ge65", "pop_day_ge65", "pop_night_outside",
            "pop_day_outside", "pct_ge65_of_total", "pct_outside_ge65_of_total",
        ],
        [[
            "28", "WARMIŃSKO-MAZURSKIE", "2815", "ostródzki", "2815092",
            "Ostróda", "niskie ryzyko", 4.42, 4.42, 0, 0, 4.42, 4.42, 0, 100,
        ]],
    )
    _write(
        attachments / "A07_luki_priorytetowe.csv",
        [
            "gap_id", "risk_class", "pop_outside_night", "area_ha", "teryt_gmi",
            "wojewodztwo", "powiat", "gmina", "priority_score_v12",
            "priority_class", "procurement_action", "estimated_new_siren_cost_pln",
            "lat", "lon",
        ],
        [[
            5261, "niskie ryzyko", 4.42, 5, "2815092", "WARMIŃSKO-MAZURSKIE",
            "ostródzki", "Ostróda", 42.14, "C", "field_validation", 0,
            53.707052, 19.936531,
        ]],
    )
    _write(
        attachments / "A08_kandydaci_syreny.csv",
        [
            "candidate_id", "nr_ref", "wojewodztwo", "powiat", "gmina",
            "teryt_gmi", "recommended_action", "priority_class",
            "estimated_cost_pln", "candidate_lat", "candidate_lon",
        ],
        [],
    )
    _write(
        attachments / "A09_redundancja.csv",
        [
            "rank", "nr_ref", "woj", "powiat", "gmina", "lat", "lon",
            "gsm_status", "failure_loss_pop_night_proxy", "recommended_action",
            "estimated_cost_pln",
        ],
        [[
            1, "S-1", "WARMIŃSKO-MAZURSKIE", "ostródzki", "Ostróda", 53.61,
            20.02, "NIE", 352, "integracja", 5000,
        ]],
    )
    _write(
        attachments / "A11_integracja_GSM.csv",
        [
            "wojewodztwo", "powiat", "gmina", "unique_sites_without_gsm",
            "integration_cost_unique_sites_pln",
        ],
        [["WARMIŃSKO-MAZURSKIE", "ostródzki", "Ostróda", 1, 5000]],
    )
    _write(
        analysis / "inwentaryzacja-syren-2026-05-05.normalized.analysis.final.V9.csv",
        [
            "nr_ref", "wojewodztwo", "powiat", "gmina", "lat", "lon",
            "rodzaj_syreny", "moc_w", "wysokosc_nad_terenem_m", "gsm_status",
            "sk_psp_status",
        ],
        [[
            "S-1", "WARMIŃSKO-MAZURSKIE", "ostródzki", "Ostróda", 53.61,
            20.02, "cyfrowa", 600, 10, "NIE", "TAK",
        ]],
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Tworzy mały fixture raportu używany wyłącznie w CI.")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    create_fixture(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
