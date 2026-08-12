#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ANALYSIS_DIR = ROOT / "analysis-output" / "inwentaryzacja-syren-2026-05-05"
DEFAULT_INVENTORY_CSV = DEFAULT_ANALYSIS_DIR / "inwentaryzacja-syren-2026-05-05.normalized.analysis.final.V9.csv"
DEFAULT_V10_SUMMARY = DEFAULT_ANALYSIS_DIR / "atdi_sound_model_V10_osm" / "v10_osm_model_summary.json"
DEFAULT_V12_DIR = DEFAULT_ANALYSIS_DIR / "decision_model_V12"

NEW_LARGE_SIREN_UNIT_COST_PLN = 40_000
EXISTING_SIREN_INTEGRATION_UNIT_COST_PLN = 5_000


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Recalculate V12 procurement costs using current public-procurement unit-cost assumptions."
    )
    parser.add_argument("--inventory-csv", default=str(DEFAULT_INVENTORY_CSV))
    parser.add_argument("--v10-summary", default=str(DEFAULT_V10_SUMMARY))
    parser.add_argument("--v12-dir", default=str(DEFAULT_V12_DIR))
    parser.add_argument("--new-large-siren-cost", type=float, default=NEW_LARGE_SIREN_UNIT_COST_PLN)
    parser.add_argument("--existing-integration-cost", type=float, default=EXISTING_SIREN_INTEGRATION_UNIT_COST_PLN)
    return parser.parse_args(argv)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def to_money(value: float) -> int:
    return int(round(float(value)))


def cost_per_person(cost: float, population: float) -> float:
    if population <= 0:
        return 0.0
    return round(float(cost) / float(population), 2)


def load_inventory(inventory_csv: Path) -> pd.DataFrame:
    return pd.read_csv(inventory_csv, dtype={"gsm_flag": "Int64", "sk_psp_flag": "Int64"})


def inventory_counts(inventory: pd.DataFrame, v10_summary: dict[str, Any]) -> dict[str, int]:
    site_status = inventory.groupby(["lat", "lon"], dropna=False)["gsm_flag"].max()
    active_sites = int(v10_summary.get("active_sites_after_deduplication") or len(site_status))
    return {
        "inventory_records": int(len(inventory)),
        "records_with_gsm": int((inventory["gsm_flag"] == 1).sum()),
        "records_without_gsm": int((inventory["gsm_flag"] == 0).sum()),
        "unique_sites": int(len(site_status)),
        "active_unique_sites": active_sites,
        "sites_with_any_gsm": int((site_status == 1).sum()),
        "sites_without_gsm": int((site_status == 0).sum()),
        "sirens_with_coverage": int(v10_summary.get("sirens_with_coverage") or 0),
    }


def build_gsm_integration_tables(
    inventory: pd.DataFrame,
    integration_cost: float,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    admin_cols = ["wojewodztwo", "powiat", "gmina", "wojewodztwo_key", "powiat_key", "gmina_key"]
    detail_cols = [
        "lp",
        "nr_ref",
        *admin_cols,
        "wlasciciel",
        "rodzaj_syreny",
        "moc_w",
        "lat",
        "lon",
        "jednostka_psp",
        "kod_swd",
        "gsm_status",
        "gsm_flag",
        "sk_psp_status",
        "sk_psp_flag",
    ]
    sirens_without_gsm = inventory.loc[inventory["gsm_flag"] == 0, detail_cols].copy()
    sirens_without_gsm["estimated_integration_cost_pln"] = to_money(integration_cost)

    sites = (
        inventory.sort_values(["lat", "lon", "lp"])
        .groupby(["lat", "lon"], dropna=False, as_index=False)
        .agg(
            gsm_flag=("gsm_flag", "max"),
            nr_ref=("nr_ref", "first"),
            wojewodztwo=("wojewodztwo", "first"),
            powiat=("powiat", "first"),
            gmina=("gmina", "first"),
            wojewodztwo_key=("wojewodztwo_key", "first"),
            powiat_key=("powiat_key", "first"),
            gmina_key=("gmina_key", "first"),
            siren_records_at_site=("nr_ref", "count"),
        )
    )
    sites_without_gsm = sites.loc[sites["gsm_flag"] == 0].copy()
    sites_without_gsm["estimated_integration_cost_pln"] = to_money(integration_cost)

    records_by_gmina = (
        sirens_without_gsm.groupby(admin_cols, dropna=False)
        .agg(siren_records_without_gsm=("nr_ref", "count"))
        .reset_index()
    )
    sites_by_gmina = (
        sites_without_gsm.groupby(admin_cols, dropna=False)
        .agg(unique_sites_without_gsm=("nr_ref", "count"))
        .reset_index()
    )
    by_gmina = records_by_gmina.merge(sites_by_gmina, on=admin_cols, how="outer").fillna(0)
    by_gmina["siren_records_without_gsm"] = by_gmina["siren_records_without_gsm"].astype(int)
    by_gmina["unique_sites_without_gsm"] = by_gmina["unique_sites_without_gsm"].astype(int)
    by_gmina["integration_cost_records_pln"] = (
        by_gmina["siren_records_without_gsm"] * to_money(integration_cost)
    ).astype(int)
    by_gmina["integration_cost_unique_sites_pln"] = (
        by_gmina["unique_sites_without_gsm"] * to_money(integration_cost)
    ).astype(int)
    by_gmina = by_gmina.sort_values(
        ["integration_cost_records_pln", "siren_records_without_gsm"], ascending=[False, False]
    ).reset_index(drop=True)
    return sirens_without_gsm, sites_without_gsm, by_gmina


def add_procurement_columns(gaps: gpd.GeoDataFrame, new_cost: float) -> pd.DataFrame:
    out = pd.DataFrame(gaps.drop(columns="geometry"))
    out["procurement_action"] = np.where(
        out["recommended_action_seed"].eq("new_siren_likely"),
        "new_large_siren",
        "not_priced_as_integration",
    )
    out["estimated_new_siren_cost_pln"] = np.where(
        out["procurement_action"].eq("new_large_siren"), new_cost, 0
    ).round().astype(int)
    out["new_siren_cost_per_pop_outside_night_pln"] = [
        cost_per_person(cost, pop)
        for cost, pop in zip(out["estimated_new_siren_cost_pln"], out["pop_outside_night"], strict=False)
    ]
    return out.sort_values(["priority_score_v12", "pop_outside_night"], ascending=[False, False]).reset_index(drop=True)


def build_admin_costs(admin_ranking: pd.DataFrame, gaps_costed: pd.DataFrame) -> pd.DataFrame:
    counts = (
        gaps_costed.pivot_table(
            index="teryt_gmi",
            columns="procurement_action",
            values="gap_id",
            aggfunc="count",
            fill_value=0,
        )
        .reset_index()
        .rename_axis(None, axis=1)
    )
    costs = gaps_costed.groupby("teryt_gmi", dropna=False)["estimated_new_siren_cost_pln"].sum().reset_index()
    costs = costs.rename(columns={"estimated_new_siren_cost_pln": "estimated_new_siren_gap_cost_pln"})
    out = admin_ranking.merge(counts, on="teryt_gmi", how="left").merge(costs, on="teryt_gmi", how="left")
    for column in ["new_large_siren", "not_priced_as_integration"]:
        if column not in out.columns:
            out[column] = 0
        out[column] = out[column].fillna(0).astype(int)
    out = out.rename(
        columns={
            "new_large_siren": "new_large_siren_gap_count",
            "not_priced_as_integration": "gap_count_not_priced_as_integration",
        }
    )
    out["estimated_new_siren_gap_cost_pln"] = out["estimated_new_siren_gap_cost_pln"].fillna(0).round().astype(int)
    out["new_siren_cost_per_pop_outside_ge65_pln"] = [
        cost_per_person(cost, pop)
        for cost, pop in zip(out["estimated_new_siren_gap_cost_pln"], out["pop_outside_ge65"], strict=False)
    ]
    return out


def scenario_rows(
    counts: dict[str, int],
    gaps_costed: pd.DataFrame,
    new_cost: float,
    integration_cost: float,
) -> list[dict[str, Any]]:
    gap_count = int(len(gaps_costed))
    new_likely = int(gaps_costed["procurement_action"].eq("new_large_siren").sum())
    new_likely_cost = to_money(gaps_costed["estimated_new_siren_cost_pln"].sum())
    all_inventory_integration_cost = to_money(counts["inventory_records"] * integration_cost)
    active_integration_cost = to_money(counts["active_unique_sites"] * integration_cost)
    no_gsm_record_integration_cost = to_money(counts["records_without_gsm"] * integration_cost)
    no_gsm_site_integration_cost = to_money(counts["sites_without_gsm"] * integration_cost)
    all_gaps_as_new_cost = to_money(gap_count * new_cost)
    return [
        {
            "scenario": "integracja_wszystkich_syren_z_inwentaryzacji",
            "basis": "wszystkie rekordy syren w V9",
            "quantity": counts["inventory_records"],
            "unit_cost_pln": to_money(integration_cost),
            "estimated_cost_pln": all_inventory_integration_cost,
        },
        {
            "scenario": "integracja_wszystkich_aktywnych_lokalizacji",
            "basis": "unikalne aktywne lokalizacje po deduplikacji V10",
            "quantity": counts["active_unique_sites"],
            "unit_cost_pln": to_money(integration_cost),
            "estimated_cost_pln": active_integration_cost,
        },
        {
            "scenario": "integracja_syren_bez_gsm",
            "basis": "rekordy syren w V9, gdzie gsm_flag = 0",
            "quantity": counts["records_without_gsm"],
            "unit_cost_pln": to_money(integration_cost),
            "estimated_cost_pln": no_gsm_record_integration_cost,
        },
        {
            "scenario": "integracja_lokalizacji_bez_gsm_po_deduplikacji",
            "basis": "unikalne lokalizacje V9 bez żadnej syreny z GSM",
            "quantity": counts["sites_without_gsm"],
            "unit_cost_pln": to_money(integration_cost),
            "estimated_cost_pln": no_gsm_site_integration_cost,
        },
        {
            "scenario": "integracja_lokalizacji_z_zasiegiem",
            "basis": "syreny z pokryciem w V10",
            "quantity": counts["sirens_with_coverage"],
            "unit_cost_pln": to_money(integration_cost),
            "estimated_cost_pln": to_money(counts["sirens_with_coverage"] * integration_cost),
        },
        {
            "scenario": "wszystkie_luki_v12_jako_nowe_duze_syreny",
            "basis": "każda zapisana luka V12 traktowana jako jeden zakup",
            "quantity": gap_count,
            "unit_cost_pln": to_money(new_cost),
            "estimated_cost_pln": all_gaps_as_new_cost,
        },
        {
            "scenario": "tylko_luki_new_siren_likely",
            "basis": "luki V12 z rekomendacją new_siren_likely",
            "quantity": new_likely,
            "unit_cost_pln": to_money(new_cost),
            "estimated_cost_pln": new_likely_cost,
        },
        {
            "scenario": "integracja_syren_bez_gsm_plus_new_siren_likely",
            "basis": "syreny bez GSM z V9 + nowe syreny tylko dla luk new_siren_likely",
            "quantity": counts["records_without_gsm"] + new_likely,
            "unit_cost_pln": "",
            "estimated_cost_pln": no_gsm_record_integration_cost + new_likely_cost,
        },
        {
            "scenario": "integracja_lokalizacji_bez_gsm_plus_new_siren_likely",
            "basis": "lokalizacje bez GSM po deduplikacji + nowe syreny tylko dla luk new_siren_likely",
            "quantity": counts["sites_without_gsm"] + new_likely,
            "unit_cost_pln": "",
            "estimated_cost_pln": no_gsm_site_integration_cost + new_likely_cost,
        },
        {
            "scenario": "integracja_aktywnych_lokalizacji_plus_new_siren_likely",
            "basis": "pełna integracja aktywnych lokalizacji + nowe syreny tylko dla new_siren_likely",
            "quantity": counts["active_unique_sites"] + new_likely,
            "unit_cost_pln": "",
            "estimated_cost_pln": active_integration_cost + new_likely_cost,
        },
        {
            "scenario": "integracja_aktywnych_lokalizacji_plus_wszystkie_luki_jako_nowe",
            "basis": "pełna integracja aktywnych lokalizacji + każda luka jako nowa duża syrena",
            "quantity": counts["active_unique_sites"] + gap_count,
            "unit_cost_pln": "",
            "estimated_cost_pln": active_integration_cost + all_gaps_as_new_cost,
        },
    ]


def write_markdown_report(path: Path, summary: dict[str, Any], scenario_df: pd.DataFrame) -> None:
    top_rows = scenario_df.loc[
        scenario_df["scenario"].isin(
            [
                "integracja_syren_bez_gsm",
                "integracja_lokalizacji_bez_gsm_po_deduplikacji",
                "tylko_luki_new_siren_likely",
                "integracja_syren_bez_gsm_plus_new_siren_likely",
                "integracja_lokalizacji_bez_gsm_plus_new_siren_likely",
            ]
        )
    ]
    lines = [
        "# Przeliczenie kosztów V12 według stawek z postępowań publicznych",
        "",
        "## Założenia",
        "",
        f"- Nowa duża syrena: {summary['unit_costs_pln']['new_large_siren']:,} zł".replace(",", " "),
        f"- Integracja istniejącej syreny z systemem centralnym: {summary['unit_costs_pln']['existing_siren_integration']:,} zł/szt.".replace(",", " "),
        "",
        "## Najważniejsze warianty",
        "",
        "| Wariant | Ilość | Koszt szacunkowy |",
        "|---|---:|---:|",
    ]
    for row in top_rows.itertuples(index=False):
        lines.append(
            f"| {row.scenario} | {int(row.quantity):,} | {int(row.estimated_cost_pln):,} zł |".replace(",", " ")
        )
    lines.extend(
        [
            "",
            "## Uwagi metodologiczne",
            "",
            "- `new_siren_likely` pochodzi z V12 i oznacza luki, gdzie model sugeruje zakup nowej syreny.",
            "- Integrację istniejących syren liczono po fladze `gsm_flag = 0` w V9.",
            "- Wariant główny integracji jest liczony po rekordach syren, bo stawka jest za sztukę.",
            "- Wariant po deduplikacji lokalizacji jest pokazany pomocniczo, żeby oddzielić koszt urządzeń od liczby punktów terenowych.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def run(args: argparse.Namespace) -> dict[str, Any]:
    inventory_csv = Path(args.inventory_csv)
    v10_summary = read_json(Path(args.v10_summary))
    v12_dir = Path(args.v12_dir)
    priority_gaps_path = v12_dir / "priority_gaps_V12.gpkg"
    admin_ranking_path = v12_dir / "admin_gap_ranking_V12.csv"
    v12_summary_path = v12_dir / "decision_model_V12_summary.json"

    gaps = gpd.read_file(priority_gaps_path, layer="priority_gaps")
    admin_ranking = pd.read_csv(admin_ranking_path, dtype={"teryt_gmi": str})
    v12_summary = read_json(v12_summary_path)

    inventory = load_inventory(inventory_csv)
    counts = inventory_counts(inventory, v10_summary)
    sirens_without_gsm, sites_without_gsm, gsm_integration_by_gmina = build_gsm_integration_tables(
        inventory,
        args.existing_integration_cost,
    )
    gaps_costed = add_procurement_columns(gaps, args.new_large_siren_cost)
    admin_costs = build_admin_costs(admin_ranking, gaps_costed)
    scenarios = scenario_rows(counts, gaps_costed, args.new_large_siren_cost, args.existing_integration_cost)
    scenario_df = pd.DataFrame(scenarios)

    costs_by_priority = (
        gaps_costed.groupby(["priority_class", "procurement_action"], dropna=False)
        .agg(
            gap_count=("gap_id", "count"),
            pop_outside_night=("pop_outside_night", "sum"),
            estimated_new_siren_cost_pln=("estimated_new_siren_cost_pln", "sum"),
        )
        .reset_index()
    )
    costs_by_priority["new_siren_cost_per_pop_outside_night_pln"] = [
        cost_per_person(cost, pop)
        for cost, pop in zip(
            costs_by_priority["estimated_new_siren_cost_pln"],
            costs_by_priority["pop_outside_night"],
            strict=False,
        )
    ]

    output_paths = {
        "scenario_csv": v12_dir / "procurement_cost_summary_V12.csv",
        "summary_json": v12_dir / "procurement_cost_summary_V12.json",
        "report_md": v12_dir / "procurement_cost_summary_V12.md",
        "priority_gaps_csv": v12_dir / "priority_gaps_procurement_V12.csv",
        "admin_ranking_csv": v12_dir / "admin_gap_ranking_procurement_V12.csv",
        "costs_by_priority_csv": v12_dir / "procurement_costs_by_priority_V12.csv",
        "sirens_without_gsm_csv": v12_dir / "sirens_without_gsm_procurement_V12.csv",
        "sites_without_gsm_csv": v12_dir / "sites_without_gsm_procurement_V12.csv",
        "gsm_integration_by_gmina_csv": v12_dir / "gsm_integration_by_gmina_V12.csv",
    }
    scenario_df.to_csv(output_paths["scenario_csv"], index=False, encoding="utf-8-sig")
    gaps_costed.to_csv(output_paths["priority_gaps_csv"], index=False, encoding="utf-8-sig")
    admin_costs.to_csv(output_paths["admin_ranking_csv"], index=False, encoding="utf-8-sig")
    costs_by_priority.to_csv(output_paths["costs_by_priority_csv"], index=False, encoding="utf-8-sig")
    sirens_without_gsm.to_csv(output_paths["sirens_without_gsm_csv"], index=False, encoding="utf-8-sig")
    sites_without_gsm.to_csv(output_paths["sites_without_gsm_csv"], index=False, encoding="utf-8-sig")
    gsm_integration_by_gmina.to_csv(output_paths["gsm_integration_by_gmina_csv"], index=False, encoding="utf-8-sig")

    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "unit_costs_pln": {
            "new_large_siren": to_money(args.new_large_siren_cost),
            "existing_siren_integration": to_money(args.existing_integration_cost),
        },
        "inputs": {
            "inventory_csv": str(inventory_csv.resolve()),
            "v10_summary": str(Path(args.v10_summary).resolve()),
            "v12_summary": str(v12_summary_path.resolve()),
            "priority_gaps": str(priority_gaps_path.resolve()),
            "admin_gap_ranking": str(admin_ranking_path.resolve()),
        },
        "inventory_counts": counts,
        "v12_counts": {
            "written_gap_count": int(v12_summary.get("gaps", {}).get("written_gap_count", len(gaps_costed))),
            "new_siren_likely_gap_count": int(gaps_costed["procurement_action"].eq("new_large_siren").sum()),
            "gap_count_not_priced_as_integration": int(
                gaps_costed["procurement_action"].eq("not_priced_as_integration").sum()
            ),
        },
        "scenarios": scenarios,
        "costs_by_priority": costs_by_priority.to_dict(orient="records"),
        "outputs": {key: str(value.resolve()) for key, value in output_paths.items()},
    }
    write_json(output_paths["summary_json"], summary)
    write_markdown_report(output_paths["report_md"], summary, scenario_df)
    return summary


def main(argv: list[str] | None = None) -> int:
    run(parse_args(argv))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
