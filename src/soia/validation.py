from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _rows(path: Path) -> int:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return sum(1 for _ in csv.DictReader(handle))


def validate_country_release(root: Path) -> dict[str, Any]:
    analysis = root / "analysis-output/inwentaryzacja-syren-2026-05-05"
    v9 = _json(analysis / "inwentaryzacja-syren-2026-05-05.normalized.analysis.final.V9.validation.json")
    v10 = _json(analysis / "atdi_sound_model_V10_osm/v10_osm_model_validation.json")
    v11 = _json(analysis / "population_model_V11/population_model_V11_validation.json")
    v12 = _json(analysis / "decision_model_V12/decision_model_V12_validation.json")
    v13 = _json(analysis / "report_SOIA_V13/decision_model_V13_validation.json")
    checks = {
        "v9_input_records_22614": v9.get("rows") == 22_614,
        "v9_active_sites_22032": v10.get("expected_active_sites_full") == 22_032,
        "v10_ranges_61110": v10.get("ranges_validation", {}).get("features") == 61_110,
        "v10_no_class_overlap": v10.get("ranges_validation", {}).get("overlap_ratio") == 0,
        "v11_population_38035768": v11.get("population_totals", {}).get("sum_gus_tot") == 38_035_768,
        "v12_admin_2479": v12.get("admin", {}).get("prg_gmina_count") == 2_479,
        "v12_country_equals_gmina": v12.get("aggregates", {}).get("country_equals_gmina") is True,
        "v13_recommendations_21909": v13.get("purchase_rows") == 21_909,
        "validations_v10_v13": all(item.get("validation_passed") is True for item in (v10, v11, v12, v13)),
    }
    return {
        "schema_version": "1.0.0",
        "release": "2026.05",
        "checks": checks,
        "validation_passed": all(checks.values()),
    }
