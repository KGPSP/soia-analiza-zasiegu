from __future__ import annotations

import json
import platform
import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .data import as_float, first_existing, read_csv, table_path, write_csv
from .geopackage import create_geopackage, create_spatial_geopackage, read_admin_geometry
from .inventory import apply_updates, read_updates
from .release import sha256_file
from .rendering import (
    render_coverage_chart,
    render_geopackage_map,
    render_locations_map,
    render_markdown_pdf,
)
from .teryt import TerytCode, parse_teryt

DATA_RELEASE = "2026.05"
MODEL_VERSION = "V9–V13"


class OutputExistsError(FileExistsError):
    """Katalog wynikowy istnieje i nie udzielono zgody na nadpisanie."""


@dataclass(frozen=True)
class GenerationResult:
    output: Path
    teryt: TerytCode
    unit_name: str
    metrics: dict[str, float | int]


def _normal(value: str) -> str:
    return " ".join(value.casefold().replace("m. ", "").split())


def _unit_row(data_root: Path, teryt: TerytCode) -> tuple[dict[str, str], str]:
    general_names = {
        "gmina": "gmi_general_coverage.csv",
        "powiat": "pow_general_coverage.csv",
        "wojewodztwo": "woj_general_coverage.csv",
    }
    rows = read_csv(table_path(data_root, general_names[teryt.level], general=True))
    if teryt.level == "gmina":
        field, name_field = "teryt_gmi", "nazwa_gmi"
        row = next((item for item in rows if item.get(field) == teryt.code), None)
    elif teryt.level == "powiat":
        field, name_field = "teryt_pow", "nazwa_pow"
        row = next((item for item in rows if item.get(field) == teryt.code), None)
    else:
        name_field = "wojewodztwo"
        reference = read_csv(table_path(data_root, "A02_wojewodztwa.csv"))
        reference_row = next((item for item in reference if item.get("teryt_woj") == teryt.code), None)
        if reference_row is None:
            raise ValueError(f"Nie znaleziono województwa TERYT {teryt.code}")
        expected_name = _normal(reference_row.get("wojewodztwo", ""))
        row = next((item for item in rows if _normal(item.get(name_field, "")) == expected_name), None)
    if row is None:
        raise ValueError(f"Nie znaleziono jednostki TERYT {teryt.code} w wydaniu {DATA_RELEASE}")
    return row, row.get(name_field, teryt.code)


def _hierarchy(data_root: Path, teryt: TerytCode, unit_name: str) -> dict[str, str]:
    reference_names = {
        "gmina": "A04_gminy.csv",
        "powiat": "A03_powiaty.csv",
        "wojewodztwo": "A02_wojewodztwa.csv",
    }
    rows = read_csv(table_path(data_root, reference_names[teryt.level]))
    field = {"gmina": "teryt_gmi", "powiat": "teryt_pow", "wojewodztwo": "teryt_woj"}[teryt.level]
    row = next((item for item in rows if item.get(field) == teryt.code), {})
    return {
        "wojewodztwo": row.get("wojewodztwo", unit_name if teryt.level == "wojewodztwo" else ""),
        "powiat": row.get("powiat", unit_name if teryt.level == "powiat" else ""),
        "gmina": row.get("gmina", unit_name if teryt.level == "gmina" else ""),
    }


def _belongs(row: dict[str, str], teryt: TerytCode, hierarchy: dict[str, str]) -> bool:
    code_field = {"gmina": "teryt_gmi", "powiat": "teryt_pow", "wojewodztwo": "teryt_woj"}[teryt.level]
    if row.get(code_field):
        return row[code_field] == teryt.code
    name_field = teryt.level if teryt.level != "wojewodztwo" else "wojewodztwo"
    aliases = [name_field]
    if teryt.level == "wojewodztwo":
        aliases.append("woj")
    expected = _normal(hierarchy.get(name_field, ""))
    return bool(expected) and any(_normal(row.get(alias, "")) == expected for alias in aliases)


def _load_optional_table(data_root: Path, name: str) -> list[dict[str, str]]:
    try:
        return read_csv(table_path(data_root, name))
    except FileNotFoundError:
        return []


def _filter_rows(
    rows: Iterable[dict[str, str]], teryt: TerytCode, hierarchy: dict[str, str]
) -> list[dict[str, str]]:
    return [row for row in rows if _belongs(row, teryt, hierarchy)]


def _inventory_path(data_root: Path) -> Path:
    return first_existing(
        data_root,
        [
            "analysis-output/inwentaryzacja-syren-2026-05-05/inwentaryzacja-syren-2026-05-05.normalized.analysis.final.V9.csv",
            "data/inventory/inventory-v9.csv",
            "inventory-v9.csv",
            "tables/inventory-v9.csv",
        ],
    )


def _admin_source(data_root: Path) -> Path | None:
    candidates = [
        data_root / "analysis-output/inwentaryzacja-syren-2026-05-05/decision_model_V12/admin_boundaries_PL_2180.gpkg",
        data_root / "data/quickstart/admin_boundaries_PL_2180.gpkg",
        data_root / "layers/soia-quickstart.gpkg",
    ]
    return next((path for path in candidates if path.exists()), None)


def _number(value: float) -> str:
    return f"{value:,.0f}".replace(",", " ")


def _money(value: float) -> str:
    return f"{value:,.0f} zł".replace(",", " ")


def _decimal(value: float, places: int = 2) -> str:
    return f"{value:.{places}f}".replace(".", ",")


def _report_markdown(
    *,
    teryt: TerytCode,
    unit_name: str,
    metrics: dict[str, float | int],
    counts: dict[str, int],
) -> str:
    risk_note = (
        f"Populacja modelowa RiskZone poza zasięgiem ≥65 dB(A): **{_decimal(float(metrics['risk_pop_outside']))}**."
        if metrics["risk_pop_total"]
        else "Brak populacji modelowej w RiskZone dla wybranej jednostki. Wartości RiskZone wynoszą zero."
    )
    return f"""# Raport SOIA — {unit_name}

**TERYT:** `{teryt.code}`  
**Poziom administracyjny:** {teryt.level}  
**Wydanie danych:** {DATA_RELEASE} (inwentaryzacja: maj 2026; CAP/IoT: eksport lipiec 2026)  
**Model:** {MODEL_VERSION}; bufor kontekstu sąsiednich JST: 10 km.

## Podsumowanie

| Wskaźnik | Wartość |
|---|---:|
| Populacja ogółem | {_number(float(metrics['pop_total']))} |
| W zasięgu ≥65 dB(A) | {_number(float(metrics['pop_ge65']))} ({_decimal(float(metrics['pct_ge65']))}%) |
| W zasięgu ≥70 dB(A) | {_number(float(metrics['pop_ge70']))} |
| W zasięgu ≥75 dB(A) | {_number(float(metrics['pop_ge75']))} |
| Poza zasięgiem ≥65 dB(A) | {_number(float(metrics['pop_outside_ge65']))} |

![Pokrycie populacji](maps/pokrycie.png)

## RiskZone i luki

{risk_note}

- luki priorytetowe: **{counts['gaps']}**;
- szacowany koszt nowych syren przypisany do luk: **{_money(float(metrics['gap_cost']))}**.

## Syreny, GSM i redundancja

- aktywne rekordy inwentaryzacji w jednostce: **{counts['sirens']}**;
- lokalizacje bez GSM wskazane do integracji: **{int(metrics['unique_sites_without_gsm'])}**;
- pozycje analizy redundancji: **{counts['redundancy']}**.

![Syreny, luki i kandydaci](maps/lokalizacje.png)

## Rekomendacje i obiekty wrażliwe

- kandydaci/rekomendacje lokalizacyjne V13: **{counts['candidates']}**;
- obiekty wrażliwe ujęte w lokalnym pakiecie: **{counts['sensitive']}**;
- łączny szacowany koszt rekomendacji i integracji GSM: **{_money(float(metrics['recommendation_cost']))}**.

Szczegóły dostępne są w katalogu [`tables/`](tables/) i warstwie
[`layers/soia_{teryt.code}.gpkg`](layers/soia_{teryt.code}.gpkg).

## Ograniczenia interpretacyjne

Wyniki są modelem planistycznym, a nie pomiarem terenowym. Zależą od jakości inwentaryzacji,
założeń propagacji dźwięku, rozdzielczości danych o ludności, granic administracyjnych i warstw
RiskZone. Wartości populacji mogą być ułamkowe przed zaokrągleniem. Decyzję inwestycyjną należy
poprzedzić wizją lokalną, pomiarem akustycznym oraz oceną techniczną i formalnoprawną.
"""


def _file_manifest(output: Path, teryt: TerytCode, metrics: dict[str, float | int]) -> dict[str, object]:
    files = []
    for path in sorted(output.rglob("*")):
        if not path.is_file() or path.name == "manifest.json":
            continue
        files.append(
            {
                "path": path.relative_to(output).as_posix(),
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    return {
        "schema_version": "1.0.0",
        "data_release": DATA_RELEASE,
        "model_version": MODEL_VERSION,
        "teryt": teryt.code,
        "administrative_level": teryt.level,
        "parameters": {"context_buffer_km": 10, "thresholds_dba": [65, 70, 75]},
        "runtime": {"python": platform.python_version(), "platform": platform.platform()},
        "metrics": metrics,
        "files": files,
    }


def generate_jst_report(
    data_root: Path | str,
    teryt_value: str,
    output: Path | str,
    *,
    inventory_updates: Path | str | None = None,
    force: bool = False,
    skip_pdf: bool = False,
) -> GenerationResult:
    data_root = Path(data_root).resolve()
    output = Path(output).resolve()
    teryt = parse_teryt(teryt_value)
    if output.exists():
        if not force:
            raise OutputExistsError(f"Katalog wynikowy już istnieje: {output}; użyj --force")
        if output == data_root or output.parent == output:
            raise OutputExistsError("Odmowa usunięcia katalogu danych albo katalogu głównego")
        shutil.rmtree(output)
    output.mkdir(parents=True)

    try:
        general, unit_name = _unit_row(data_root, teryt)
        hierarchy = _hierarchy(data_root, teryt, unit_name)
        risk_rows = _filter_rows(_load_optional_table(data_root, {
            "gmina": "A04_gminy.csv", "powiat": "A03_powiaty.csv", "wojewodztwo": "A02_wojewodztwa.csv"
        }[teryt.level]), teryt, hierarchy)
        gaps = _filter_rows(_load_optional_table(data_root, "A07_luki_priorytetowe.csv"), teryt, hierarchy)
        candidates = _filter_rows(_load_optional_table(data_root, "A08_kandydaci_syreny.csv"), teryt, hierarchy)
        redundancy = _filter_rows(_load_optional_table(data_root, "A09_redundancja.csv"), teryt, hierarchy)
        gsm = _filter_rows(_load_optional_table(data_root, "A11_integracja_GSM.csv"), teryt, hierarchy)

        inventory_all = read_csv(_inventory_path(data_root))
        audit: list[dict[str, str]] = []
        if inventory_updates:
            valid_codes = {
                row.get("teryt_gmi", "")
                for row in read_csv(table_path(data_root, "gmi_general_coverage.csv", general=True))
                if row.get("teryt_gmi")
            }
            inventory_all, audit = apply_updates(
                inventory_all,
                read_updates(Path(inventory_updates)),
                valid_gmina_codes=valid_codes,
            )
        active_inventory = [row for row in inventory_all if row.get("active", "1") not in {"0", "false", "NIE"}]
        sirens = _filter_rows(active_inventory, teryt, hierarchy)

        metrics: dict[str, float | int] = {
            "pop_total": round(as_float(general, "pop_total")),
            "pop_ge65": round(as_float(general, "pop_ge65")),
            "pop_ge70": round(as_float(general, "pop_ge70")),
            "pop_ge75": round(as_float(general, "pop_ge75")),
            "pop_outside_ge65": round(as_float(general, "pop_outside_ge65")),
            "pct_ge65": round(as_float(general, "pct_ge65"), 2),
            "risk_pop_total": round(sum(as_float(row, "pop_night_total") for row in risk_rows), 6),
            "risk_pop_outside": round(sum(as_float(row, "pop_night_outside") for row in risk_rows), 6),
            "unique_sites_without_gsm": round(sum(as_float(row, "unique_sites_without_gsm") for row in gsm)),
            "gap_cost": round(sum(as_float(row, "estimated_new_siren_cost_pln") for row in gaps), 2),
            "recommendation_cost": round(
                sum(as_float(row, "estimated_cost_pln") for row in candidates)
                + sum(as_float(row, "integration_cost_unique_sites_pln") for row in gsm), 2
            ),
            "siren_count": len(sirens),
        }
        counts = {
            "sirens": len(sirens), "gaps": len(gaps), "candidates": len(candidates),
            "redundancy": len(redundancy), "sensitive": 0,
        }

        coverage_row = dict(general)
        coverage_row.update({"teryt": teryt.code, "poziom": teryt.level, "nazwa": unit_name})
        write_csv(output / "tables/coverage.csv", [coverage_row])
        write_csv(output / "tables/risk.csv", risk_rows)
        write_csv(output / "tables/priority-gaps.csv", gaps)
        write_csv(output / "tables/candidates.csv", candidates)
        write_csv(output / "tables/redundancy.csv", redundancy)
        write_csv(output / "tables/gsm-integration.csv", gsm)
        write_csv(
            output / "inventory-changes.csv",
            audit,
            fieldnames=["operation", "nr_ref", "field", "before", "after", "reason", "status"],
        )

        point_layers = {
            "sirens": [(as_float(row, "lon"), as_float(row, "lat"), json.dumps(row, ensure_ascii=False)) for row in sirens],
            "priority_gaps": [(as_float(row, "lon"), as_float(row, "lat"), json.dumps(row, ensure_ascii=False)) for row in gaps],
            "candidates": [(as_float(row, "candidate_lon"), as_float(row, "candidate_lat"), json.dumps(row, ensure_ascii=False)) for row in candidates],
            "sensitive_objects": [],
        }
        admin_source = _admin_source(data_root)
        gpkg_output = output / f"layers/soia_{teryt.code}.gpkg"
        spatial_counts = create_spatial_geopackage(
            gpkg_output,
            source=admin_source,
            level=teryt.level,
            code=teryt.code,
            inventory=active_inventory,
            buffer_km=10,
        ) if admin_source else None
        if spatial_counts:
            counts["sensitive"] = spatial_counts.get("sensitive_objects", 0)
        else:
            admin_geometry = read_admin_geometry(admin_source, teryt.level, teryt.code) if admin_source else None
            create_geopackage(gpkg_output, points=point_layers, admin_geometry=admin_geometry)

        title = f"{unit_name} (TERYT {teryt.code})"
        render_coverage_chart(output / "maps/pokrycie.png", metrics, title)
        if not render_geopackage_map(output / "maps/lokalizacje.png", title, gpkg_output):
            render_locations_map(
                output / "maps/lokalizacje.png", title,
                {"Syreny": sirens, "Luki": gaps, "Kandydaci": candidates},
            )
        report = _report_markdown(teryt=teryt, unit_name=unit_name, metrics=metrics, counts=counts)
        (output / "raport.md").write_text(report, encoding="utf-8")
        (output / "README.md").write_text(
            f"# Pakiet wynikowy SOIA — {unit_name}\n\n"
            f"Raport dla TERYT `{teryt.code}` wygenerowany z wydania danych `{DATA_RELEASE}`.\n\n"
            "Rozpocznij od [raport.md](raport.md). Plik `manifest.json` zawiera sumy SHA-256.\n",
            encoding="utf-8",
        )
        if not skip_pdf:
            render_markdown_pdf(output / "raport.md", output / "raport.pdf")

        checks = {
            "validation_passed": True,
            "teryt_valid": True,
            "population_balance": metrics["pop_ge65"] + metrics["pop_outside_ge65"] == metrics["pop_total"],
            "non_negative_metrics": all(float(value) >= 0 for value in metrics.values()),
            "warnings": [],
        }
        if not checks["population_balance"]:
            checks["validation_passed"] = False
            checks["warnings"].append("Populacja w i poza zasięgiem nie sumuje się do populacji ogółem po zaokrągleniu")
        (output / "validation.json").write_text(json.dumps(checks, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        manifest = _file_manifest(output, teryt, metrics)
        (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return GenerationResult(output, teryt, unit_name, metrics)
    except Exception:
        # Nie zostawiamy kompletnego z wyglądu pakietu po błędzie; pliki diagnostyczne
        # pozostają wyłącznie w logu wywołania.
        shutil.rmtree(output, ignore_errors=True)
        raise
