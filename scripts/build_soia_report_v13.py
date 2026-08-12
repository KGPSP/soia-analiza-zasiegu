#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from pyproj import Transformer


ROOT = Path(__file__).resolve().parents[1]
ANALYSIS_DIR = ROOT / "analysis-output" / "inwentaryzacja-syren-2026-05-05"
V10_DIR = ANALYSIS_DIR / "atdi_sound_model_V10_osm"
V11_DIR = ANALYSIS_DIR / "population_model_V11"
V12_DIR = ANALYSIS_DIR / "decision_model_V12"
DEFAULT_OUTPUT_DIR = ANALYSIS_DIR / "report_SOIA_V13"

PUBLIC_BUILDING_TYPES = {
    "school": "edukacja",
    "kindergarten": "edukacja",
    "college": "edukacja",
    "university": "edukacja",
    "hospital": "zdrowie_i_opieka",
    "clinic": "zdrowie_i_opieka",
    "doctors": "zdrowie_i_opieka",
    "nursing_home": "zdrowie_i_opieka",
    "social_facility": "zdrowie_i_opieka",
    "fire_station": "bezpieczenstwo",
    "public": "administracja_i_publiczne",
    "civic": "administracja_i_publiczne",
    "city_hall": "administracja_i_publiczne",
    "office": "administracja_i_publiczne",
    "government": "administracja_i_publiczne",
    "sports_centre": "inne_publiczne",
    "stadium": "inne_publiczne",
    "transportation": "transport",
    "train_station": "transport",
}

MOUNT_BUILDING_TYPES = {
    "fire_station",
    "public",
    "civic",
    "city_hall",
    "office",
    "government",
    "school",
    "kindergarten",
    "sports_centre",
    "transportation",
}

DEFAULT_NEW_SIREN_COST_PLN = 40_000
DEFAULT_INTEGRATION_COST_PLN = 5_000


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build SOIA V13 report, decision tables and public candidate layers.")
    parser.add_argument("--analysis-dir", default=str(ANALYSIS_DIR))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--max-new-candidates", type=int, default=2500)
    parser.add_argument("--nearest-public-site-m", type=float, default=2000.0)
    parser.add_argument("--new-siren-cost-pln", type=float, default=DEFAULT_NEW_SIREN_COST_PLN)
    parser.add_argument("--integration-cost-pln", type=float, default=DEFAULT_INTEGRATION_COST_PLN)
    parser.add_argument("--force", action="store_true")
    return parser.parse_args(argv)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def write_csv(path: Path, df: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")


def safe_div(num: float, den: float) -> float:
    return 0.0 if den == 0 or pd.isna(den) else float(num) / float(den)


def pct(num: float, den: float) -> float:
    return round(safe_div(num, den) * 100.0, 2)


def cost_per_person(cost: float, population: float) -> float:
    return 0.0 if population <= 0 else round(float(cost) / float(population), 2)


def fmt_int(value: float | int) -> str:
    return f"{int(round(float(value))):,}".replace(",", " ")


def fmt_float(value: float, digits: int = 2) -> str:
    return f"{float(value):,.{digits}f}".replace(",", " ")


def class_from_score(score: float) -> str:
    if score >= 80:
        return "A"
    if score >= 60:
        return "B"
    if score >= 40:
        return "C"
    return "niski"


def recommended_power_class(pop_outside: float, area_ha: float) -> int:
    if pop_outside >= 1000 or area_ha >= 100:
        return 1200
    if pop_outside >= 300 or area_ha >= 25:
        return 900
    if pop_outside >= 100 or area_ha >= 5:
        return 600
    return 300


def normalize_sheet_name(name: str) -> str:
    cleaned = re.sub(r"[:\\/?*\[\]]", "_", name)
    return cleaned[:31]


def add_pct_columns(df: pd.DataFrame, total_col: str, pairs: list[tuple[str, str]]) -> pd.DataFrame:
    out = df.copy()
    for value_col, pct_col in pairs:
        out[pct_col] = [pct(value, total) for value, total in zip(out[value_col], out[total_col], strict=False)]
    return out.reset_index(drop=True)


def load_inputs(analysis_dir: Path) -> dict[str, Path]:
    v10_dir = analysis_dir / "atdi_sound_model_V10_osm"
    v11_dir = analysis_dir / "population_model_V11"
    v12_dir = analysis_dir / "decision_model_V12"
    return {
        "inventory": analysis_dir / "inwentaryzacja-syren-2026-05-05.normalized.analysis.final.V9.csv",
        "v9_validation": analysis_dir / "inwentaryzacja-syren-2026-05-05.normalized.analysis.final.V9.validation.json",
        "quality_metrics": analysis_dir / "post-correction-audit.conservative" / "quality_metrics.json",
        "quality_summary": analysis_dir / "post-correction-audit.conservative" / "summary.md",
        "v10_summary": v10_dir / "v10_osm_model_summary.json",
        "v10_validation": v10_dir / "v10_osm_model_validation.json",
        "winner_lookup": v10_dir / "winner_siren_lookup_V10.csv",
        "v11_summary": v11_dir / "population_model_V11_summary.json",
        "v11_validation": v11_dir / "population_model_V11_validation.json",
        "building_population": v11_dir / "building_population_V11.gpkg",
        "siren_population": v11_dir / "siren_population_coverage_V11.csv",
        "v12_summary": v12_dir / "decision_model_V12_summary.json",
        "v12_validation": v12_dir / "decision_model_V12_validation.json",
        "v12_country": v12_dir / "v12_risk_coverage_country.csv",
        "v12_woj": v12_dir / "v12_risk_coverage_woj.csv",
        "v12_powiat": v12_dir / "v12_risk_coverage_powiat.csv",
        "v12_gmina": v12_dir / "v12_risk_coverage_gmina.csv",
        "priority_gaps": v12_dir / "priority_gaps_V12.gpkg",
        "admin_ranking": v12_dir / "admin_gap_ranking_V12.csv",
        "priority_gaps_costed": v12_dir / "priority_gaps_procurement_V12.csv",
        "admin_ranking_costed": v12_dir / "admin_gap_ranking_procurement_V12.csv",
        "procurement_summary": v12_dir / "procurement_cost_summary_V12.csv",
        "procurement_priority": v12_dir / "procurement_costs_by_priority_V12.csv",
        "procurement_json": v12_dir / "procurement_cost_summary_V12.json",
        "sirens_without_gsm": v12_dir / "sirens_without_gsm_procurement_V12.csv",
        "sites_without_gsm": v12_dir / "sites_without_gsm_procurement_V12.csv",
        "gsm_integration_by_gmina": v12_dir / "gsm_integration_by_gmina_V12.csv",
    }


def required_paths(paths: dict[str, Path]) -> None:
    optional = {"quality_metrics", "quality_summary"}
    missing = [str(path) for name, path in paths.items() if name not in optional and not path.exists()]
    if missing:
        raise FileNotFoundError("Missing required V13 input files:\n" + "\n".join(missing[:20]))


def extract_sensitive_objects(building_population: Path, output_dir: Path) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame, Path, Path]:
    where_values = ",".join(f"'{value}'" for value in sorted(PUBLIC_BUILDING_TYPES))
    where = f"bldg IN ({where_values})"
    objects = gpd.read_file(building_population, layer="building_population", where=where)
    if objects.empty:
        objects = gpd.GeoDataFrame(
            columns=["osm_id", "bldg", "object_group", "candidate_role", "geometry"],
            geometry="geometry",
            crs="EPSG:2180",
        )
    else:
        objects = objects.to_crs("EPSG:2180")
        objects["object_group"] = objects["bldg"].map(PUBLIC_BUILDING_TYPES).fillna("inne_publiczne")
        objects["candidate_role"] = np.where(objects["bldg"].isin(MOUNT_BUILDING_TYPES), "candidate_mount_site", "sensitive_only")
        objects["source"] = "OSM_building_from_V11"
        objects["x_2180"] = objects.geometry.x.round(2)
        objects["y_2180"] = objects.geometry.y.round(2)
        transformer = Transformer.from_crs("EPSG:2180", "EPSG:4326", always_xy=True)
        lon, lat = transformer.transform(objects["x_2180"].to_numpy(), objects["y_2180"].to_numpy())
        objects["lon"] = np.round(lon, 7)
        objects["lat"] = np.round(lat, 7)

    candidates = objects.loc[objects["candidate_role"].eq("candidate_mount_site")].copy()
    sensitive_path = output_dir / "sensitive_objects_PL_2180.gpkg"
    candidates_path = output_dir / "candidate_mount_sites_PL_2180.gpkg"
    for path in [sensitive_path, candidates_path]:
        if path.exists():
            path.unlink()
    objects.to_file(sensitive_path, layer="sensitive_objects", driver="GPKG")
    candidates.to_file(candidates_path, layer="candidate_mount_sites", driver="GPKG")
    return objects, candidates, sensitive_path, candidates_path


def priority_gaps_with_centroids(gaps: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    out = gaps.copy()
    pts = out.geometry.representative_point()
    out["geometry"] = pts
    out = out.set_geometry("geometry")
    out["x_2180"] = out.geometry.x.round(2)
    out["y_2180"] = out.geometry.y.round(2)
    transformer = Transformer.from_crs("EPSG:2180", "EPSG:4326", always_xy=True)
    lon, lat = transformer.transform(out["x_2180"].to_numpy(), out["y_2180"].to_numpy())
    out["lon"] = np.round(lon, 7)
    out["lat"] = np.round(lat, 7)
    return out


def build_new_siren_candidates(
    gaps: gpd.GeoDataFrame,
    mount_sites: gpd.GeoDataFrame,
    max_candidates: int,
    max_distance_m: float,
    new_siren_cost_pln: float,
) -> gpd.GeoDataFrame:
    gap_points = priority_gaps_with_centroids(gaps).sort_values(
        ["priority_score_v12", "pop_outside_night"], ascending=[False, False]
    )
    if max_candidates:
        priority_sample = gap_points.head(max_candidates).copy()
        mandatory_purchase = gap_points.loc[gap_points["recommended_action_seed"].eq("new_siren_likely")].copy()
        gap_points = (
            pd.concat([priority_sample, mandatory_purchase], ignore_index=True)
            .drop_duplicates(subset=["gap_id"], keep="first")
            .sort_values(["priority_score_v12", "pop_outside_night"], ascending=[False, False])
            .reset_index(drop=True)
        )

    if not mount_sites.empty:
        nearest = gpd.sjoin_nearest(
            gap_points,
            mount_sites[["osm_id", "bldg", "object_group", "geometry"]],
            how="left",
            max_distance=max_distance_m,
            distance_col="candidate_distance_m",
        )
    else:
        nearest = gap_points.copy()
        nearest["index_right"] = np.nan
        nearest["candidate_distance_m"] = np.nan
        nearest["osm_id"] = ""
        nearest["bldg"] = ""
        nearest["object_group"] = ""

    rows: list[dict[str, Any]] = []
    mount_lookup = mount_sites if not mount_sites.empty else gpd.GeoDataFrame(geometry=[])
    transformer = Transformer.from_crs("EPSG:2180", "EPSG:4326", always_xy=True)
    for row in nearest.itertuples(index=False):
        has_public_site = not pd.isna(getattr(row, "index_right", np.nan))
        if has_public_site:
            site = mount_lookup.loc[int(row.index_right)]
            geom = site.geometry
            candidate_source = "nearest_public_osm_object"
            candidate_type = str(site.get("bldg", "public"))
            object_group = str(site.get("object_group", "inne_publiczne"))
            osm_id = str(site.get("osm_id", ""))
            distance_m = float(getattr(row, "candidate_distance_m", 0.0))
        else:
            geom = row.geometry
            candidate_source = "gap_representative_point"
            candidate_type = "gap_centroid"
            object_group = "brak_obiektu_publicznego_w_promieniu"
            osm_id = ""
            distance_m = np.nan
        x = float(geom.x)
        y = float(geom.y)
        lon, lat = transformer.transform(x, y)
        pop = float(row.pop_outside_night)
        area = float(row.area_ha)
        power = recommended_power_class(pop, area)
        base_score = float(row.priority_score_v12)
        public_boost = 5.0 if has_public_site else 0.0
        score = round(min(100.0, base_score + public_boost), 2)
        recommended_action = "nowa_syrena" if row.recommended_action_seed == "new_siren_likely" else "walidacja_terenowa"
        estimated_cost = new_siren_cost_pln if recommended_action == "nowa_syrena" else 0.0
        rows.append(
            {
                "candidate_id": f"NEW-{int(row.gap_id)}",
                "gap_id": int(row.gap_id),
                "candidate_source": candidate_source,
                "candidate_type": candidate_type,
                "object_group": object_group,
                "osm_id": osm_id,
                "candidate_distance_m": round(distance_m, 2) if not pd.isna(distance_m) else "",
                "wojewodztwo": row.wojewodztwo,
                "powiat": row.powiat,
                "gmina": row.gmina,
                "teryt_gmi": str(row.teryt_gmi),
                "risk_class": row.risk_class,
                "priority_class": row.priority_class,
                "priority_score_v12": base_score,
                "priority_score_v13": score,
                "recommended_action": recommended_action,
                "recommended_power_class_w": power,
                "estimated_cost_pln": int(round(estimated_cost)),
                "new_pop_ge65_proxy": round(pop, 2),
                "flood_risk_pop_ge65_gain_proxy": round(pop, 2),
                "redundancy_gain_pop_proxy": 0.0,
                "cost_per_new_person_ge65_pln": cost_per_person(estimated_cost, pop),
                "effect_method": "gap_population_proxy",
                "x_2180": round(x, 2),
                "y_2180": round(y, 2),
                "lon": round(float(lon), 7),
                "lat": round(float(lat), 7),
                "geometry": geom,
            }
        )
    return gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:2180")


def collapse_siren_population(path: Path) -> pd.DataFrame:
    pop = pd.read_csv(path)
    grouped = (
        pop.groupby(["site_uid", "nr_ref"], dropna=False)
        .agg(
            primary_pop_night_ge65=("pop_night_ge65", "max"),
            primary_pop_day_ge65=("pop_day_ge65", "max"),
            primary_pop_night_ge70=("pop_night_ge70", "max"),
            primary_pop_night_ge75=("pop_night_ge75", "max"),
        )
        .reset_index()
    )
    return grouped


def action_for_siren(row: pd.Series) -> str:
    sk = int(row.get("sk_psp_flag", 0) or 0)
    gsm = int(row.get("gsm_flag", 0) or 0)
    typ = str(row.get("rodzaj_syreny", row.get("typ", ""))).lower()
    power = float(row.get("moc_w", row.get("pow_w", 0.0)) or 0.0)
    if gsm == 0:
        return "integracja"
    if typ == "analogowa" or power <= 300:
        return "modernizacja"
    if sk == 0:
        return "walidacja_terenowa"
    return "brak_dzialania"


def build_critical_sirens(paths: dict[str, Path], integration_cost_pln: float) -> pd.DataFrame:
    coverage = collapse_siren_population(paths["siren_population"])
    lookup = pd.read_csv(paths["winner_lookup"], dtype={"site_uid": int})
    inventory = pd.read_csv(paths["inventory"], dtype={"nr_ref": str})
    merged = coverage.merge(lookup, on=["site_uid", "nr_ref"], how="left")
    merged = merged.merge(
        inventory[
            [
                "nr_ref",
                "wlasciciel",
                "rodzaj_syreny",
                "moc_w",
                "gsm_flag",
                "sk_psp_flag",
                "gsm_status",
                "sk_psp_status",
            ]
        ],
        on="nr_ref",
        how="left",
    )
    merged["failure_loss_pop_night_proxy"] = merged["primary_pop_night_ge65"].round(2)
    merged["failure_loss_pop_day_proxy"] = merged["primary_pop_day_ge65"].round(2)
    merged["coverage_count_ge65_method"] = "dominant_siren_proxy"
    merged["recommended_action"] = merged.apply(action_for_siren, axis=1)
    merged["estimated_cost_pln"] = np.where(merged["recommended_action"].eq("integracja"), integration_cost_pln, 0).round().astype(int)
    merged["rank"] = (
        merged["failure_loss_pop_night_proxy"].rank(method="first", ascending=False).astype(int)
    )
    cols = [
        "rank",
        "site_uid",
        "nr_ref",
        "woj",
        "powiat",
        "gmina",
        "lat",
        "lon",
        "wlasciciel",
        "rodzaj_syreny",
        "moc_w",
        "gsm_status",
        "sk_psp_status",
        "primary_pop_night_ge65",
        "primary_pop_day_ge65",
        "failure_loss_pop_night_proxy",
        "failure_loss_pop_day_proxy",
        "coverage_count_ge65_method",
        "recommended_action",
        "estimated_cost_pln",
    ]
    return merged.sort_values("failure_loss_pop_night_proxy", ascending=False).loc[:, cols].reset_index(drop=True)


def build_existing_siren_recommendations(critical: pd.DataFrame) -> pd.DataFrame:
    out = critical.loc[~critical["recommended_action"].eq("brak_dzialania")].copy()
    out["candidate_id"] = "EXIST-" + out["nr_ref"].astype(str)
    out["priority_score_v13"] = np.minimum(
        100.0,
        45.0 + 35.0 * np.minimum(1.0, np.log1p(out["failure_loss_pop_night_proxy"]) / np.log1p(max(1.0, out["failure_loss_pop_night_proxy"].quantile(0.95))))
        + np.where(out["recommended_action"].eq("integracja"), 10.0, 0.0),
    ).round(2)
    out["priority_class"] = out["priority_score_v13"].map(class_from_score)
    out["new_pop_ge65_proxy"] = 0.0
    out["flood_risk_pop_ge65_gain_proxy"] = 0.0
    out["redundancy_gain_pop_proxy"] = 0.0
    out["cost_per_new_person_ge65_pln"] = 0.0
    return out


def build_missing_gsm_recommendations(
    paths: dict[str, Path],
    existing: pd.DataFrame,
    integration_cost_pln: float,
) -> pd.DataFrame:
    gsm = pd.read_csv(paths["sirens_without_gsm"], dtype={"nr_ref": str})
    if gsm.empty:
        return pd.DataFrame(columns=existing.columns)

    existing_refs = set(existing["nr_ref"].astype(str))
    missing = gsm.loc[~gsm["nr_ref"].astype(str).isin(existing_refs)].copy()
    if missing.empty:
        return pd.DataFrame(columns=existing.columns)

    out = pd.DataFrame(
        {
            "candidate_id": "GSM-" + missing["nr_ref"].astype(str),
            "nr_ref": missing["nr_ref"].astype(str),
            "rank": "",
            "site_uid": "",
            "woj": missing["wojewodztwo"],
            "powiat": missing["powiat"],
            "gmina": missing["gmina"],
            "lat": missing["lat"],
            "lon": missing["lon"],
            "wlasciciel": missing["wlasciciel"],
            "rodzaj_syreny": missing["rodzaj_syreny"],
            "moc_w": missing["moc_w"],
            "gsm_status": missing["gsm_status"],
            "sk_psp_status": missing["sk_psp_status"],
            "primary_pop_night_ge65": 0.0,
            "primary_pop_day_ge65": 0.0,
            "failure_loss_pop_night_proxy": 0.0,
            "failure_loss_pop_day_proxy": 0.0,
            "coverage_count_ge65_method": "outside_V10_population_proxy",
            "recommended_action": "integracja",
            "estimated_cost_pln": int(round(integration_cost_pln)),
            "priority_score_v13": 55.0,
            "priority_class": class_from_score(55.0),
            "new_pop_ge65_proxy": 0.0,
            "flood_risk_pop_ge65_gain_proxy": 0.0,
            "redundancy_gain_pop_proxy": 0.0,
            "cost_per_new_person_ge65_pln": 0.0,
        }
    )
    return out.reset_index(drop=True)


def build_purchase_recommendations(new_candidates: gpd.GeoDataFrame, existing: pd.DataFrame) -> pd.DataFrame:
    new_part = pd.DataFrame(new_candidates.drop(columns="geometry"))
    new_part = new_part.rename(columns={"lat": "candidate_lat", "lon": "candidate_lon"})
    new_part["nr_ref"] = ""
    new_part["failure_loss_pop_night_proxy"] = 0.0
    new_part["wlasciciel"] = ""
    new_cols = [
        "candidate_id",
        "nr_ref",
        "wojewodztwo",
        "powiat",
        "gmina",
        "teryt_gmi",
        "recommended_action",
        "priority_class",
        "priority_score_v13",
        "risk_class",
        "recommended_power_class_w",
        "new_pop_ge65_proxy",
        "flood_risk_pop_ge65_gain_proxy",
        "redundancy_gain_pop_proxy",
        "failure_loss_pop_night_proxy",
        "estimated_cost_pln",
        "cost_per_new_person_ge65_pln",
        "candidate_lat",
        "candidate_lon",
        "candidate_source",
        "candidate_type",
        "effect_method",
    ]
    for column in new_cols:
        if column not in new_part.columns:
            new_part[column] = ""
    new_part = new_part.loc[:, new_cols]

    existing_part = existing.copy()
    existing_part["wojewodztwo"] = existing_part["woj"]
    existing_part["teryt_gmi"] = ""
    existing_part["risk_class"] = ""
    existing_part["recommended_power_class_w"] = existing_part["moc_w"]
    existing_part["candidate_lat"] = existing_part["lat"]
    existing_part["candidate_lon"] = existing_part["lon"]
    existing_part["candidate_source"] = "existing_siren_V9_V10"
    existing_part["candidate_type"] = existing_part["rodzaj_syreny"]
    existing_part["effect_method"] = "existing_siren_primary_coverage_proxy"
    existing_part = existing_part.loc[:, new_cols]

    out = pd.concat([new_part, existing_part], ignore_index=True)
    return out.sort_values(["priority_score_v13", "new_pop_ge65_proxy", "failure_loss_pop_night_proxy"], ascending=[False, False, False]).reset_index(drop=True)


def enrich_priority_gaps(gaps: gpd.GeoDataFrame, costed: pd.DataFrame) -> pd.DataFrame:
    centroids = priority_gaps_with_centroids(gaps)
    cols = ["gap_id", "x_2180", "y_2180", "lat", "lon"]
    out = costed.merge(pd.DataFrame(centroids[cols]), on="gap_id", how="left")
    if "estimated_unit_cost_pln" not in out.columns and "estimated_new_siren_cost_pln" in out.columns:
        out["estimated_unit_cost_pln"] = out["estimated_new_siren_cost_pln"]
    if "cost_per_pop_outside_night_pln" not in out.columns and "new_siren_cost_per_pop_outside_night_pln" in out.columns:
        out["cost_per_pop_outside_night_pln"] = out["new_siren_cost_per_pop_outside_night_pln"]
    return out.sort_values(["priority_score_v12", "pop_outside_night"], ascending=[False, False]).reset_index(drop=True)


def risk_frames(paths: dict[str, Path]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    country = pd.read_csv(paths["v12_country"])
    woj = pd.read_csv(paths["v12_woj"], dtype={"teryt_woj": str})
    powiat = pd.read_csv(paths["v12_powiat"], dtype={"teryt_woj": str, "teryt_pow": str})
    gmina = pd.read_csv(paths["v12_gmina"], dtype={"teryt_woj": str, "teryt_pow": str, "teryt_gmi": str})
    frames = []
    for level, frame in [("kraj", country), ("wojewodztwo", woj), ("powiat", powiat), ("gmina", gmina)]:
        temp = frame.copy()
        temp.insert(0, "poziom", level)
        frames.append(temp)
    return country, woj, powiat, gmina


def build_quality_table(paths: dict[str, Path]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    v9 = read_json(paths["v9_validation"])
    rows.extend(
        [
            {"category": "V9", "metric": "records", "value": v9.get("rows"), "note": "Finalna inwentaryzacja po normalizacji"},
            {"category": "V9", "metric": "columns", "value": v9.get("columns"), "note": "Kolumny finalnego CSV"},
            {"category": "V9", "metric": "analog_records", "value": v9.get("analog_records"), "note": "Syreny analogowe po przeliczeniu profili"},
            {"category": "V9", "metric": "npm_missing", "value": v9.get("npm_missing"), "note": "Braki wysokości n.p.m."},
        ]
    )
    if paths["quality_metrics"].exists():
        qm = read_json(paths["quality_metrics"])
        rows.append({"category": "quality", "metric": "raw_missing_total", "value": sum(qm.get("raw_missing_by_column", {}).values()), "note": "Surowe braki i N/D"})
        rows.append({"category": "quality", "metric": "actionable_missing_total", "value": sum(qm.get("actionable_missing_by_column", {}).values()), "note": "Braki wymagające interwencji"})
        rows.append({"category": "quality", "metric": "duplicate_nr_ref", "value": qm.get("duplicates", {}).get("Nr ref."), "note": "Duplikaty identyfikatorów"})
        for owner, count in list(qm.get("wlasciciel", {}).items())[:12]:
            rows.append({"category": "owners", "metric": owner, "value": count, "note": "Liczba syren według właściciela"})
    return pd.DataFrame(rows)


def build_gsm_integration_sheet(paths: dict[str, Path]) -> pd.DataFrame:
    gsm = pd.read_csv(paths["gsm_integration_by_gmina"])
    if gsm.empty:
        return gsm
    return gsm.sort_values(["integration_cost_records_pln", "siren_records_without_gsm"], ascending=[False, False]).reset_index(drop=True)


def build_table_package(
    output_dir: Path,
    paths: dict[str, Path],
    gaps_enriched: pd.DataFrame,
    new_candidates: gpd.GeoDataFrame,
    purchase: pd.DataFrame,
    critical: pd.DataFrame,
    sensitive: gpd.GeoDataFrame,
) -> tuple[dict[str, pd.DataFrame], Path, Path]:
    v10_summary = read_json(paths["v10_summary"])
    v11_summary = read_json(paths["v11_summary"])
    v12_summary = read_json(paths["v12_summary"])
    country, woj, powiat, gmina = risk_frames(paths)
    risk_all = pd.concat(
        [
            country.assign(poziom="kraj"),
            woj.assign(poziom="wojewodztwo"),
            powiat.assign(poziom="powiat"),
            gmina.assign(poziom="gmina"),
        ],
        ignore_index=True,
        sort=False,
    )

    country_totals = country[["pop_night_total", "pop_day_total", "pop_night_ge65", "pop_day_ge65", "pop_night_outside", "pop_day_outside"]].sum()
    a01 = pd.DataFrame(
        [
            {"metric": "populacja_GUS_NSP2021", "value": v11_summary["gus"]["sum_tot"], "unit": "osoby", "source": "V11"},
            {"metric": "pop_night_ge65", "value": round(v11_summary["coverage"]["pop_night_ge65"], 2), "unit": "osoby", "source": "V11"},
            {"metric": "pct_pop_night_ge65", "value": pct(v11_summary["coverage"]["pop_night_ge65"], v11_summary["gus"]["sum_tot"]), "unit": "pct", "source": "V11"},
            {"metric": "pop_night_outside_ge65", "value": round(v11_summary["coverage"]["pop_night_outside_ge65"], 2), "unit": "osoby", "source": "V11"},
            {"metric": "riskzone_pop_night_total", "value": round(country_totals["pop_night_total"], 2), "unit": "osoby", "source": "V12"},
            {"metric": "riskzone_pop_night_ge65", "value": round(country_totals["pop_night_ge65"], 2), "unit": "osoby", "source": "V12"},
            {"metric": "riskzone_pop_night_outside_ge65", "value": round(country_totals["pop_night_outside"], 2), "unit": "osoby", "source": "V12"},
            {"metric": "riskzone_pct_outside_ge65", "value": pct(country_totals["pop_night_outside"], country_totals["pop_night_total"]), "unit": "pct", "source": "V12"},
            {"metric": "priority_gaps_count", "value": v12_summary["gaps"]["written_gap_count"], "unit": "luki", "source": "V12"},
            {"metric": "active_sites_after_deduplication", "value": v10_summary["active_sites_after_deduplication"], "unit": "lokalizacje", "source": "V10"},
            {"metric": "sirens_with_coverage", "value": v10_summary["sirens_with_coverage"], "unit": "lokalizacje", "source": "V10"},
            {"metric": "sensitive_objects_count", "value": len(sensitive), "unit": "obiekty", "source": "V13"},
            {"metric": "new_candidate_count", "value": len(new_candidates), "unit": "lokalizacje", "source": "V13"},
        ]
    )

    admin_ranking = pd.read_csv(paths["admin_ranking_costed"], dtype={"teryt_gmi": str}) if paths["admin_ranking_costed"].exists() else pd.read_csv(paths["admin_ranking"], dtype={"teryt_gmi": str})
    outside = admin_ranking.sort_values("pop_outside_ge65", ascending=False).reset_index(drop=True)
    gsm_integration = build_gsm_integration_sheet(paths)

    tables = {
        "A01_kraj_podsumowanie": a01,
        "A02_wojewodztwa": add_pct_columns(woj, "pop_night_total", [("pop_night_ge65", "pct_ge65_of_total"), ("pop_night_outside", "pct_outside_ge65_of_total")]),
        "A03_powiaty": add_pct_columns(powiat, "pop_night_total", [("pop_night_ge65", "pct_ge65_of_total"), ("pop_night_outside", "pct_outside_ge65_of_total")]),
        "A04_gminy": add_pct_columns(gmina, "pop_night_total", [("pop_night_ge65", "pct_ge65_of_total"), ("pop_night_outside", "pct_outside_ge65_of_total")]),
        "A05_riskzone_kraj_woj_pow_gmi": risk_all,
        "A06_riskzone_poza_zasiegiem": outside,
        "A07_luki_priorytetowe": gaps_enriched,
        "A08_kandydaci_syreny": purchase,
        "A09_redundancja": critical,
        "A10_jakosc_danych": build_quality_table(paths),
        "A11_integracja_GSM": gsm_integration,
    }

    csv_dir = output_dir / "csv"
    csv_dir.mkdir(parents=True, exist_ok=True)
    for sheet, frame in tables.items():
        write_csv(csv_dir / f"{sheet}.csv", frame)

    xlsx_path = output_dir / "SOIA_wyniki_tabele.xlsx"
    with pd.ExcelWriter(xlsx_path, engine="openpyxl") as writer:
        for sheet, frame in tables.items():
            frame.to_excel(writer, sheet_name=normalize_sheet_name(sheet), index=False)
    format_workbook(xlsx_path)
    return tables, xlsx_path, csv_dir


def format_workbook(path: Path) -> None:
    wb = load_workbook(path)
    header_fill = PatternFill("solid", fgColor="1F4E78")
    header_font = Font(color="FFFFFF", bold=True)
    for ws in wb.worksheets:
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
        for cell in ws[1]:
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        for col_idx, column_cells in enumerate(ws.columns, start=1):
            max_len = 0
            for cell in column_cells[:200]:
                value = "" if cell.value is None else str(cell.value)
                max_len = max(max_len, len(value))
            width = min(max(max_len + 2, 10), 42)
            ws.column_dimensions[get_column_letter(col_idx)].width = width
        ws.sheet_view.showGridLines = False
    wb.save(path)


def markdown_table(df: pd.DataFrame, cols: list[str], max_rows: int = 10) -> str:
    if df.empty:
        return "_Brak danych._"
    view = df.loc[:, [col for col in cols if col in df.columns]].head(max_rows).copy()
    for col in view.columns:
        if col.startswith("teryt"):
            view[col] = view[col].map(lambda value: "" if pd.isna(value) else str(value))
            continue
        if pd.api.types.is_numeric_dtype(view[col]):
            formatted: list[str] = []
            for value in view[col]:
                if pd.isna(value):
                    formatted.append("")
                elif any(token in col for token in ["lat", "lon", "share"]):
                    formatted.append(f"{float(value):.6f}".rstrip("0").rstrip("."))
                elif any(token in col for token in ["score", "pct", "cost_per"]):
                    formatted.append(f"{float(value):.2f}".rstrip("0").rstrip("."))
                elif float(value).is_integer():
                    formatted.append(f"{int(value)}")
                else:
                    formatted.append(f"{float(value):.2f}")
            view[col] = formatted
    return view.to_markdown(index=False, disable_numparse=True)


def fmt_pln(value: float | int) -> str:
    return f"{fmt_int(value)} zł"


def metric_value(metrics: dict[str, Any], name: str, default: float = 0.0) -> float:
    value = metrics.get(name, default)
    return float(default if pd.isna(value) else value)


def budget_lookup(budget: pd.DataFrame, scenario: str, column: str, default: float = 0.0) -> float:
    if budget.empty or scenario not in set(budget.get("scenario", [])):
        return default
    values = budget.loc[budget["scenario"].eq(scenario), column]
    if values.empty or pd.isna(values.iloc[0]):
        return default
    return float(values.iloc[0])


def purchase_action_summary(purchase: pd.DataFrame) -> pd.DataFrame:
    summary = (
        purchase.groupby("recommended_action", dropna=False)
        .agg(
            liczba=("candidate_id", "count"),
            koszt_pln=("estimated_cost_pln", "sum"),
            populacja_nowa_ge65_proxy=("new_pop_ge65_proxy", "sum"),
            utrata_po_awarii_proxy=("failure_loss_pop_night_proxy", "sum"),
        )
        .reset_index()
        .sort_values(["koszt_pln", "liczba"], ascending=[False, False])
    )
    labels = {
        "integracja": "Integracja syren bez GSM z warstwą sterowania i potwierdzeń.",
        "nowa_syrena": "Nowe duże syreny dla luk V12 oznaczonych jako new_siren_likely.",
        "modernizacja": "Weryfikacja lub wymiana urządzeń analogowych i niskiej mocy.",
        "walidacja_terenowa": "Sprawdzenie lokalizacji, łączności, zasilania i warunków montażu.",
    }
    summary["opis"] = summary["recommended_action"].map(labels).fillna("Działanie operacyjne z modelu V13.")
    return summary


def required_management_sections() -> list[str]:
    return [
        "## 1. Streszczenie zarządcze",
        "## 2. Najważniejsze decyzje do podjęcia",
        "## 3. Cel i zakres raportu",
        "## 4. Stan faktyczny",
        "## 5. Diagnoza problemu",
        "## 6. Tezy analityczne",
        "## 7. Analiza wariantów działania",
        "## 8. Tabela porównawcza wariantów",
        "## 9. Analiza ryzyk",
        "## 10. Rekomendowane rozwiązanie",
        "## 11. Plan realizacji",
        "## 12. Konsekwencje braku działania",
        "## 13. Szybkie zwycięstwa",
        "## 14. Warunki powodzenia",
        "## 15. Mierniki sukcesu",
        "## 16. Luki informacyjne",
        "## 17. Dane szczegółowe w raporcie głównym",
        "## 18. Załączniki analityczne",
        "## 19. Wnioski końcowe",
    ]


def build_decision_process_mermaid() -> str:
    return """```mermaid
flowchart TD
    A["Ustalenie stanu faktycznego V9-V13"] --> B["Identyfikacja luk i ryzyk operacyjnych"]
    B --> C["Ocena wariantów działania"]
    C --> D["Wybór wariantu rekomendowanego"]
    D --> E["Decyzja kierownictwa i finansowanie"]
    E --> F["Walidacja terenowa lokalizacji A/B"]
    F --> G["Integracja, modernizacja i zakup"]
    G --> H["Testy, odbiory i monitoring KPI"]
```"""


def build_architecture_mermaid() -> str:
    return """```mermaid
flowchart LR
    V9["Inwentaryzacja syren V9"] --> V10["Model zasięgu V10"]
    V10 --> V11["Model populacji V11"]
    V11 --> V12["RiskZone i luki V12"]
    OSM["Obiekty publiczne OSM"] --> V13["Kandydaci i rekomendacje V13"]
    V12 --> V13
    V13 --> DEC["Decyzje: integracja, modernizacja, nowe syreny"]
    DEC --> CTRL["Docelowa warstwa sterowania"]
    CTRL --> LOG["Potwierdzenia, status, rejestr zdarzeń"]
    CTRL --> BACKUP["Kanał zapasowy: TETRA, radio, IP VPN lub satelita"]
```"""


def build_gantt_mermaid() -> str:
    return """```mermaid
gantt
    title Harmonogram realizacji rekomendowanego wariantu SOIA V13
    dateFormat  YYYY-MM-DD
    section Przygotowanie
    Zatwierdzenie wariantu i właścicieli ryzyk       :a1, 2026-06-01, 14d
    Zamrożenie list A/B i pakietu wymagań            :a2, after a1, 21d
    section Formalności
    Zabezpieczenie finansowania                      :b1, after a1, 30d
    Przygotowanie postępowania i uzgodnień           :b2, after a2, 45d
    section Projektowanie
    Projekt integracji i kanałów zapasowych          :c1, after b2, 45d
    Plan testów, odbiorów i monitoringu              :c2, after c1, 21d
    section Wdrożenie
    Integracja syren bez GSM                         :d1, after c2, 120d
    Montaż nowych syren new_siren_likely             :d2, after c2, 120d
    section Odbiory
    Testy obszarowe, audyt zdarzeń, korekty          :e1, after d1, 45d
    Przekazanie do utrzymania i raport KPI           :e2, after e1, 30d
```"""


def build_report(
    output_dir: Path,
    paths: dict[str, Path],
    gaps_enriched: pd.DataFrame,
    purchase: pd.DataFrame,
    critical: pd.DataFrame,
    tables: dict[str, pd.DataFrame],
    xlsx_path: Path,
    csv_dir: Path,
    sensitive_count: int,
    new_candidate_count: int,
) -> Path:
    v9_validation = read_json(paths["v9_validation"])
    v10_summary = read_json(paths["v10_summary"])
    v10_validation = read_json(paths["v10_validation"])
    v11_summary = read_json(paths["v11_summary"])
    v11_validation = read_json(paths["v11_validation"])
    v12_validation = read_json(paths["v12_validation"])

    a01 = tables["A01_kraj_podsumowanie"].set_index("metric")["value"].to_dict()
    top_admin = pd.read_csv(paths["admin_ranking"], dtype={"teryt_gmi": str}).head(15)
    risk_country = pd.read_csv(paths["v12_country"])
    procurement_scenarios = pd.read_csv(paths["procurement_summary"]) if paths["procurement_summary"].exists() else pd.DataFrame()
    purchase_summary = purchase_action_summary(purchase)
    top_purchase = purchase.head(25)
    top_critical = critical.head(20)
    top_gsm_gminas = tables["A11_integracja_GSM"].head(15)

    population_total = metric_value(a01, "populacja_GUS_NSP2021")
    pop_night_ge65 = metric_value(a01, "pop_night_ge65")
    pop_night_outside = metric_value(a01, "pop_night_outside_ge65")
    pct_night_ge65 = metric_value(a01, "pct_pop_night_ge65")
    riskzone_total = metric_value(a01, "riskzone_pop_night_total")
    riskzone_ge65 = metric_value(a01, "riskzone_pop_night_ge65")
    riskzone_outside = metric_value(a01, "riskzone_pop_night_outside_ge65")
    riskzone_pct_outside = metric_value(a01, "riskzone_pct_outside_ge65")
    priority_gaps_count = int(metric_value(a01, "priority_gaps_count"))
    sirens_with_coverage = int(metric_value(a01, "sirens_with_coverage"))
    active_sites = int(metric_value(a01, "active_sites_after_deduplication"))

    integration_qty = int(budget_lookup(procurement_scenarios, "integracja_syren_bez_gsm", "quantity", 0))
    integration_cost = int(budget_lookup(procurement_scenarios, "integracja_syren_bez_gsm", "estimated_cost_pln", 0))
    integration_unique_qty = int(budget_lookup(procurement_scenarios, "integracja_lokalizacji_bez_gsm_po_deduplikacji", "quantity", 0))
    integration_unique_cost = int(budget_lookup(procurement_scenarios, "integracja_lokalizacji_bez_gsm_po_deduplikacji", "estimated_cost_pln", 0))
    new_siren_qty = int(budget_lookup(procurement_scenarios, "tylko_luki_new_siren_likely", "quantity", 0))
    new_siren_cost = int(budget_lookup(procurement_scenarios, "tylko_luki_new_siren_likely", "estimated_cost_pln", 0))
    recommended_qty = int(budget_lookup(procurement_scenarios, "integracja_syren_bez_gsm_plus_new_siren_likely", "quantity", integration_qty + new_siren_qty))
    recommended_cost = int(budget_lookup(procurement_scenarios, "integracja_syren_bez_gsm_plus_new_siren_likely", "estimated_cost_pln", integration_cost + new_siren_cost))
    maximal_qty = int(budget_lookup(procurement_scenarios, "integracja_aktywnych_lokalizacji_plus_wszystkie_luki_jako_nowe", "quantity", 0))
    maximal_cost = int(budget_lookup(procurement_scenarios, "integracja_aktywnych_lokalizacji_plus_wszystkie_luki_jako_nowe", "estimated_cost_pln", 0))

    decisions = pd.DataFrame(
        [
            {"decyzja": "Zatwierdzić wariant rekomendowany", "zakres": f"{fmt_int(integration_qty)} integracji GSM i {fmt_int(new_siren_qty)} nowych syren", "termin": "0-14 dni", "skutek": f"Uruchomienie programu o wartości {fmt_pln(recommended_cost)}"},
            {"decyzja": "Powołać właściciela programu i komitet sterujący", "zakres": "MSWiA/PSP/JST, finanse, zamówienia, cyberbezpieczeństwo", "termin": "0-14 dni", "skutek": "Jedno miejsce odpowiedzialności za decyzje, ryzyka i odbiory"},
            {"decyzja": "Zabezpieczyć finansowanie i tryb postępowania", "zakres": "integracja, nowe syreny, walidacje terenowe, testy i utrzymanie", "termin": "0-30 dni", "skutek": "Możliwość rozpoczęcia uzgodnień i zamówienia"},
            {"decyzja": "Przyjąć minimalny standard sterowania", "zakres": "GSM/APN jako kanał podstawowy, kanał zapasowy, potwierdzenia, rejestr zdarzeń", "termin": "0-45 dni", "skutek": "Wspólne wymagania dla wszystkich jednostek i dostawców"},
            {"decyzja": "Zaakceptować ograniczenia modelu", "zakres": "model inżynierski, brak pełnego rastra redundancji, potrzeba walidacji terenowej", "termin": "przed zakupem", "skutek": "Decyzja planistyczna bez mylenia modelu z pomiarem certyfikowanym"},
        ]
    )

    variants = pd.DataFrame(
        [
            {"wariant": "0. Brak działania", "opis": "Utrzymanie rozproszonego stanu i brak programu integracji.", "naklad": "0 zł CAPEX", "czas": "brak wdrożenia", "zalety": "Brak natychmiastowego wydatku", "wady": "Utrzymanie luk, braku potwierdzeń i ryzyk awarii", "ocena": "Nie rekomenduje się"},
            {"wariant": "1. Minimalny", "opis": "Integracja lokalizacji bez GSM po deduplikacji oraz walidacja danych i lokalizacji.", "naklad": fmt_pln(integration_unique_cost), "czas": "6-9 miesięcy", "zalety": "Szybkie ograniczenie problemu łączności", "wady": "Nie zamyka luk new_siren_likely", "ocena": "Dopuszczalny jako etap startowy"},
            {"wariant": "2. Rekomendowany", "opis": "Integracja syren bez GSM oraz zakup nowych dużych syren dla luk new_siren_likely.", "naklad": fmt_pln(recommended_cost), "czas": "9-15 miesięcy", "zalety": "Najlepszy bilans kosztu, ryzyka i efektu decyzyjnego", "wady": "Wymaga koordynacji wielu właścicieli", "ocena": "Rekomendowany"},
            {"wariant": "3. Maksymalny", "opis": "Pełna integracja aktywnych lokalizacji i zakup syren dla wszystkich luk V12.", "naklad": fmt_pln(maximal_cost), "czas": "18-36 miesięcy", "zalety": "Najszerszy zakres pokrycia i standaryzacji", "wady": "Bardzo wysoki koszt i ryzyko przewymiarowania", "ocena": "Nie jako pierwszy etap"},
        ]
    )

    comparison = pd.DataFrame(
        [
            {"kryterium": "Koszt", "wariant_0": "najniższy", "wariant_1": fmt_pln(integration_unique_cost), "wariant_2": fmt_pln(recommended_cost), "wariant_3": fmt_pln(maximal_cost), "rekomendacja": "wariant 2"},
            {"kryterium": "Czas wdrożenia", "wariant_0": "brak", "wariant_1": "6-9 mies.", "wariant_2": "9-15 mies.", "wariant_3": "18-36 mies.", "rekomendacja": "wariant 1 lub 2"},
            {"kryterium": "Ryzyko pozostawienia luk", "wariant_0": "bardzo wysokie", "wariant_1": "średnie", "wariant_2": "niskie dla priorytetów", "wariant_3": "najniższe", "rekomendacja": "wariant 2"},
            {"kryterium": "Zgodność z celem publicznym", "wariant_0": "niska", "wariant_1": "częściowa", "wariant_2": "wysoka", "wariant_3": "wysoka", "rekomendacja": "wariant 2"},
            {"kryterium": "Skalowalność", "wariant_0": "niska", "wariant_1": "średnia", "wariant_2": "wysoka", "wariant_3": "wysoka", "rekomendacja": "wariant 2"},
            {"kryterium": "Bezpieczeństwo i ciągłość", "wariant_0": "niskie", "wariant_1": "średnie", "wariant_2": "wysokie", "wariant_3": "wysokie", "rekomendacja": "wariant 2"},
            {"kryterium": "Łatwość utrzymania", "wariant_0": "niska", "wariant_1": "średnia", "wariant_2": "wysoka przy standardzie", "wariant_3": "zależna od finansowania", "rekomendacja": "wariant 2"},
            {"kryterium": "Wpływ na użytkowników", "wariant_0": "brak poprawy", "wariant_1": "umiarkowany", "wariant_2": "wysoki w obszarach ryzyka", "wariant_3": "bardzo wysoki", "rekomendacja": "wariant 2"},
        ]
    )

    risk_register = pd.DataFrame(
        [
            {"id": "R1", "kategoria": "prawne", "opis": "Brak jednolitego modelu odpowiedzialności właścicieli syren i operatorów sterowania.", "prawdopodobieństwo": "średnie", "wpływ": "wysoki", "poziom": "wysoki", "działania": "porozumienia, standard sterowania, RACI, decyzja kierownictwa", "właściciel": "kierownik programu", "termin": "0-60 dni"},
            {"id": "R2", "kategoria": "techniczne", "opis": "Część syren nie ma GSM albo nie ma potwierdzonego kanału sterowania.", "prawdopodobieństwo": "wysokie", "wpływ": "wysoki", "poziom": "krytyczny", "działania": "integracja po gsm_flag = 0 i testy odbiorowe", "właściciel": "PSP/administratorzy", "termin": "0-12 mies."},
            {"id": "R3", "kategoria": "organizacyjne", "opis": "Rozproszona własność utrudnia jednoczesne uruchomienie syren w obszarze zagrożenia.", "prawdopodobieństwo": "wysokie", "wpływ": "wysoki", "poziom": "krytyczny", "działania": "komitet sterujący, harmonogram JST, rejestr statusów", "właściciel": "kierownictwo instytucji", "termin": "0-90 dni"},
            {"id": "R4", "kategoria": "finansowe", "opis": "Brak decyzji budżetowej przesuwa wdrożenie i zwiększa koszt późniejszych zakupów.", "prawdopodobieństwo": "średnie", "wpływ": "wysoki", "poziom": "wysoki", "działania": "zatwierdzić wariant 2 i rezerwę na walidacje", "właściciel": "finanse/program", "termin": "0-30 dni"},
            {"id": "R5", "kategoria": "cyberbezpieczeństwo", "opis": "Nowa warstwa sterowania bez standardu autoryzacji i audytu może zwiększyć ryzyko nadużyć.", "prawdopodobieństwo": "średnie", "wpływ": "wysoki", "poziom": "wysoki", "działania": "MFA, role, szyfrowanie, rejestr zdarzeń, testy bezpieczeństwa", "właściciel": "CISO/IT", "termin": "projekt techniczny"},
            {"id": "R6", "kategoria": "ciągłość działania", "opis": "Pojedynczy kanał komunikacji może być niedostępny w kryzysie.", "prawdopodobieństwo": "średnie", "wpływ": "wysoki", "poziom": "wysoki", "działania": "kanał zapasowy TETRA/radio/IP VPN/satelita i testy cykliczne", "właściciel": "operator systemu", "termin": "przed odbiorem"},
            {"id": "R7", "kategoria": "reputacyjne", "opis": "Utrzymanie luk ostrzegania w RiskZone może być negatywnie ocenione po zdarzeniu kryzysowym.", "prawdopodobieństwo": "średnie", "wpływ": "wysoki", "poziom": "wysoki", "działania": "publiczny plan działań i raportowanie KPI", "właściciel": "kierownictwo", "termin": "0-90 dni"},
            {"id": "R8", "kategoria": "wykonawcze", "opis": "Niezweryfikowane lokalizacje kandydackie mogą nie nadawać się do montażu.", "prawdopodobieństwo": "średnie", "wpływ": "średni", "poziom": "średni", "działania": "walidacja terenowa A/B przed zakupem", "właściciel": "zespół wdrożeniowy", "termin": "przed zamówieniem"},
        ]
    )

    implementation_plan = pd.DataFrame(
        [
            {"etap": "1. Przygotowanie", "działania": "zatwierdzenie wariantu, właścicieli ryzyk, list A/B", "odpowiedzialni": "kierownictwo, kierownik programu", "produkt": "karta programu i rejestr decyzji", "czas": "2-4 tyg.", "ryzyka": "brak właściciela programu"},
            {"etap": "2. Decyzje i formalności", "działania": "finansowanie, tryb postępowania, porozumienia JST", "odpowiedzialni": "finanse, zamówienia, prawnicy", "produkt": "zatwierdzony budżet i model zamówienia", "czas": "1-2 mies.", "ryzyka": "opóźnienie budżetu"},
            {"etap": "3. Projektowanie", "działania": "standard sterowania, kanały, bezpieczeństwo, odbiory", "odpowiedzialni": "IT, PSP, cyberbezpieczeństwo", "produkt": "opis wymagań i plan testów", "czas": "2 mies.", "ryzyka": "niespójne wymagania"},
            {"etap": "4. Wdrożenie", "działania": "integracja GSM, montaż nowych syren, aktualizacja rejestru", "odpowiedzialni": "wykonawcy, administratorzy", "produkt": "urządzenia gotowe do testów", "czas": "4-6 mies.", "ryzyka": "braki lokalizacyjne i dostawy"},
            {"etap": "5. Testy i odbiory", "działania": "testy sterowania, potwierdzeń, awarii kanału, audytu", "odpowiedzialni": "operator, odbierający, CISO", "produkt": "protokoły odbioru i lista korekt", "czas": "1-2 mies.", "ryzyka": "negatywne testy obszarowe"},
            {"etap": "6. Utrzymanie i rozwój", "działania": "monitoring KPI, przeglądy danych, aktualizacja luk", "odpowiedzialni": "operator systemu, właściciele JST", "produkt": "cykliczny raport gotowości", "czas": "ciągłe", "ryzyka": "spadek jakości danych"},
        ]
    )

    quick_wins = pd.DataFrame(
        [
            {"działanie": "Zamrożenie listy syren bez GSM", "priorytet": "pilne", "koszt": "niski", "efekt": "jednoznaczny zakres integracji"},
            {"działanie": "Publikacja listy top gmin i top luk do walidacji", "priorytet": "pilne", "koszt": "niski", "efekt": "szybkie uzgodnienia z JST"},
            {"działanie": "Wprowadzenie jednolitego statusu syreny", "priorytet": "ważne", "koszt": "niski", "efekt": "porównywalność danych i odbiorów"},
            {"działanie": "Pilotaż testu potwierdzeń dla gmin z dużym kosztem GSM", "priorytet": "ważne", "koszt": "średni", "efekt": "weryfikacja wymagań przed skalowaniem"},
            {"działanie": "Pełny raster redundancji coverage_count_ge65", "priorytet": "można odłożyć", "koszt": "średni", "efekt": "lepsza analiza odporności po pierwszej decyzji"},
        ]
    )

    kpi = pd.DataFrame(
        [
            {"kpi": "Udział syren ze sterowaniem i potwierdzeniem", "wartość bazowa": f"{fmt_int(integration_qty)} syren bez GSM", "cel": "100% syren z programu ma status potwierdzony", "termin": "odbiór etapu 4"},
            {"kpi": "Nowe syreny dla luk new_siren_likely", "wartość bazowa": f"{fmt_int(new_siren_qty)} luk", "cel": "100% lokalizacji zwalidowanych, zakupionych albo odrzuconych z uzasadnieniem", "termin": "12-15 mies."},
            {"kpi": "Populacja RiskZone poza >=65 dB(A)", "wartość bazowa": fmt_int(riskzone_outside), "cel": "spadek w gminach A po wdrożeniu i ponownym przeliczeniu", "termin": "po odbiorach"},
            {"kpi": "Jakość danych V9/V13", "wartość bazowa": "walidacja V13 true", "cel": "brak krytycznych braków dla syren objętych programem", "termin": "przed zamówieniem"},
            {"kpi": "Gotowość operacyjna", "wartość bazowa": "brak jednolitego potwierdzenia krajowego", "cel": "raport statusu i rejestr zdarzeń dla testów obszarowych", "termin": "odbiór końcowy"},
        ]
    )

    information_gaps = pd.DataFrame(
        [
            {"luka": "Pełny raster redundancji coverage_count_ge65", "wpływ": "ogranicza precyzyjną ocenę utraty pokrycia po awarii wielu syren", "działanie": "osobny przebieg propagacyjny na rastrze 100 m"},
            {"luka": "Koszty modernizacji analogowych i niskiej mocy", "wpływ": "brak pełnego budżetu dla modernizacji", "działanie": "pozyskać katalog cen i warunki techniczne"},
            {"luka": "Status kanału zapasowego", "wpływ": "nie można potwierdzić odporności w kryzysie", "działanie": "inwentaryzacja TETRA/radio/IP VPN/satelita"},
            {"luka": "Zgody właścicielskie i warunki montażu", "wpływ": "część kandydatów może odpaść po wizji lokalnej", "działanie": "walidacja terenowa list A/B"},
            {"luka": "Pomiary akustyczne w terenie", "wpływ": "model nie zastępuje certyfikowanego pomiaru", "działanie": "pomiary kontrolne dla lokalizacji wysokiego ryzyka"},
        ]
    )

    raci = pd.DataFrame(
        [
            {"obszar": "Decyzja wariantu i finansowania", "R": "kierownictwo", "A": "kierownik instytucji", "C": "PSP, finanse, prawnicy", "I": "JST"},
            {"obszar": "Standard sterowania i cyberbezpieczeństwo", "R": "IT/CISO", "A": "kierownik programu", "C": "operatorzy, dostawcy", "I": "właściciele syren"},
            {"obszar": "Walidacja terenowa", "R": "zespół wdrożeniowy", "A": "kierownik programu", "C": "JST, PSP", "I": "kierownictwo"},
            {"obszar": "Testy i odbiory", "R": "operator systemu", "A": "zamawiający", "C": "CISO, użytkownicy operacyjni", "I": "komitet sterujący"},
        ]
    )

    report_path = output_dir / "SOIA_analiza_V13.md"
    lines = [
        "# SOIA V13: raport kierowniczy o zasięgu syren, ryzyku powodziowym i priorytetach decyzji",
        "",
        f"Data wygenerowania: `{datetime.now(timezone.utc).isoformat()}`",
        "",
        "## 1. Streszczenie zarządcze",
        "",
        f"System ostrzegania akustycznego jest liczbowo rozbudowany, ale organizacyjnie i technicznie rozproszony. Inwentaryzacja V9 obejmuje `{fmt_int(v9_validation['rows'])}` rekordów syren, po deduplikacji do modelu V10 weszło `{fmt_int(active_sites)}` aktywnych lokalizacji, a zasięg uzyskało `{fmt_int(sirens_with_coverage)}` lokalizacji.",
        "",
        f"Model V11 wskazuje, że w zasięgu co najmniej `65 dB(A)` znajduje się `{fmt_int(pop_night_ge65)}` osób, czyli `{fmt_float(pct_night_ge65)}%` populacji GUS NSP 2021. Poza tym progiem pozostaje `{fmt_int(pop_night_outside)}` osób. W obszarach `RiskZone` V12 znajduje się `{fmt_int(riskzone_total)}` osób, z czego `{fmt_int(riskzone_outside)}` osób pozostaje poza zasięgiem `>=65 dB(A)`, czyli `{fmt_float(riskzone_pct_outside)}%` populacji ryzyka.",
        "",
        f"Problem decyzyjny nie polega wyłącznie na dołożeniu syren. Kluczowe są: integracja sterowania, potwierdzenie wykonania alarmowania, zamknięcie priorytetowych luk oraz walidacja lokalizacji. V12 wskazuje `{fmt_int(priority_gaps_count)}` luk inwestycyjnych, a V13 porządkuje `{fmt_int(len(purchase))}` rekomendacji zakupowo-operacyjnych.",
        "",
        f"Rekomenduje się wariant 2: integrację `{fmt_int(integration_qty)}` syren bez GSM za `{fmt_pln(integration_cost)}` oraz zakup `{fmt_int(new_siren_qty)}` nowych dużych syren dla luk `new_siren_likely` za `{fmt_pln(new_siren_cost)}`. Łączny koszt wariantu rekomendowanego wynosi `{fmt_pln(recommended_cost)}`. Wariant ten równoważy koszt, skalę ryzyka i wykonalność organizacyjną.",
        "",
        "Działania pierwszej kolejności: zatwierdzić wariant rekomendowany, powołać właściciela programu, zamrozić listę syren bez GSM i lokalizacji `new_siren_likely`, uruchomić walidację terenową oraz przyjąć minimalny standard sterowania i audytu.",
        "",
        "## 2. Najważniejsze decyzje do podjęcia",
        "",
        markdown_table(decisions, ["decyzja", "zakres", "termin", "skutek"], 10),
        "",
        "## 3. Cel i zakres raportu",
        "",
        "Celem raportu jest przedstawienie kierownictwu materiału decyzyjnego: stanu faktycznego, problemu, wariantów działania, ryzyk, kosztów i rekomendowanego planu. Raport wspiera decyzję o uruchomieniu programu integracji i rozbudowy SOIA, a nie zastępuje dokumentacji przetargowej ani projektu wykonawczego.",
        "",
        "Zakres obejmuje inwentaryzację V9, model zasięgu V10, populację V11, ryzyko powodziowe V12, rekomendacje V13, koszty integracji GSM i nowych syren oraz szczegółowe listy lokalizacji. Raport nie obejmuje certyfikowanego pomiaru propagacji dźwięku, pełnego projektu radiowego ani końcowego kosztorysu modernizacji analogowych urządzeń.",
        "",
        "## 4. Stan faktyczny",
        "",
        "### 4.1. Źródła i status walidacji",
        "",
        "| Etap | Zakres | Status |",
        "|---|---|---|",
        f"| V9 | inwentaryzacja syren, właściciele, moc, łączność, współrzędne | `{v9_validation.get('rows')}` rekordów, brak pustych wartości po normalizacji |",
        f"| V10 | raster i poligony zasięgu `65/70/75 dB(A)` | validation `{v10_validation.get('validation_passed')}`, overlap `{v10_validation['ranges_validation'].get('overlap_ratio')}` |",
        f"| V11 | populacja GUS NSP 2021 na budynki i zasięgi | validation `{v11_validation.get('validation_passed')}`, suma `{fmt_int(v11_summary['gus']['sum_tot'])}` |",
        f"| V12 | `RiskZone`, populacja w ryzyku, luki | validation `{v12_validation.get('validation_passed')}`, PRG/TERC `{v12_validation['admin'].get('exact_match')}` |",
        "| V13 | obiekty wrażliwe, kandydaci, rekomendacje | wygenerowane z jawnych danych V9-V12 i OSM/V11 |",
        "",
        "### 4.2. Dane krajowe i populacyjne",
        "",
        f"- Populacja GUS NSP 2021 w modelu: `{fmt_int(population_total)}` osób.",
        f"- Populacja nocna w zasięgu `>=65 dB(A)`: `{fmt_int(pop_night_ge65)}` osób.",
        f"- Populacja nocna poza zasięgiem `>=65 dB(A)`: `{fmt_int(pop_night_outside)}` osób.",
        f"- Populacja nocna w `RiskZone`: `{fmt_int(riskzone_total)}` osób.",
        f"- Populacja nocna w `RiskZone` poza `>=65 dB(A)`: `{fmt_int(riskzone_outside)}` osób.",
        f"- Obiekty wrażliwe V13: `{fmt_int(sensitive_count)}`.",
        f"- Kandydaci nowych syren lub walidacji terenowej: `{fmt_int(new_candidate_count)}`.",
        "",
        "### 4.3. Wynik krajowy V12 dla RiskZone",
        "",
        markdown_table(risk_country, ["risk_class", "pop_night_total", "pop_night_ge65", "pop_night_outside", "pop_day_total", "pop_day_ge65", "pop_day_outside"], 10),
        "",
        "### 4.4. Proces decyzyjny",
        "",
        build_decision_process_mermaid(),
        "",
        "### 4.5. Architektura zależności analitycznych i operacyjnych",
        "",
        build_architecture_mermaid(),
        "",
        "## 5. Diagnoza problemu",
        "",
        "Diagnoza łączy fakty liczbowe z oceną operacyjną. Zasięg akustyczny jest konieczny, ale niewystarczający: syrena musi być możliwa do uruchomienia we właściwym obszarze, we właściwym czasie, z potwierdzeniem wykonania i możliwością audytu.",
        "",
        "| Kategoria | Problem | Przyczyna | Skutek decyzyjny |",
        "|---|---|---|---|",
        f"| organizacyjna | rozproszona własność i odpowiedzialność | różni właściciele: JST, OSP, PSP, wojewodowie i inne podmioty | potrzebny właściciel programu i RACI |",
        f"| techniczna | `{fmt_int(integration_qty)}` syren bez GSM | brak jednolitego kanału sterowania i statusu | priorytet integracji po `gsm_flag = 0` |",
        f"| operacyjna | `{fmt_int(riskzone_outside)}` osób w RiskZone poza `>=65 dB(A)` | luki pokrycia na obszarach ryzyka | potrzebne nowe syreny i walidacja terenowa |",
        f"| finansowa | warianty od `{fmt_pln(integration_unique_cost)}` do `{fmt_pln(maximal_cost)}` | różny zakres integracji i zakupów | potrzebna decyzja zakresowa, nie tylko kosztorys |",
        "| bezpieczeństwa | brak jednolitego audytu i kanału zapasowego | lokalne systemy i niepełne statusy | standard sterowania musi obejmować potwierdzenia i logi |",
        "| kompetencyjna | model wymaga interpretacji | V10-V13 są modelem inżynierskim | przed zakupem konieczna walidacja terenowa |",
        "",
        "## 6. Tezy analityczne",
        "",
        "| Teza | Argumenty za | Argumenty przeciw lub ograniczenia | Ocena | Wniosek |",
        "|---|---|---|---|---|",
        f"| 1. Głównym problemem jest integracja, nie sama liczba syren | istnieje `{fmt_int(v9_validation['rows'])}` rekordów i `{fmt_int(sirens_with_coverage)}` lokalizacji z zasięgiem | zasięg nadal nie obejmuje `{fmt_int(pop_night_outside)}` osób | potwierdzona | integracja i rozbudowa muszą iść równolegle |",
        f"| 2. RiskZone wskazuje realny priorytet interwencji | `{fmt_int(riskzone_outside)}` osób w ryzyku poza `>=65 dB(A)` | `RiskZone` nie jest mapą samej wody | potwierdzona z ograniczeniem | priorytety powinny wynikać z V12, nie tylko z map zasięgu |",
        f"| 3. Wariant 2 jest najlepszym pierwszym programem | obejmuje `{fmt_int(integration_qty)}` integracji i `{fmt_int(new_siren_qty)}` nowych syren | wymaga koordynacji wielu właścicieli | potwierdzona | rekomendować wariant za `{fmt_pln(recommended_cost)}` |",
        "| 4. Brak SK PSP nie jest kosztem GSM | poprawiony model liczy integrację po `gsm_flag = 0` | brak SK PSP nadal wymaga sprawdzenia operacyjnego | potwierdzona | SK PSP traktować jako walidację, nie jako budżet GSM |",
        "| 5. Pełna odporność wymaga osobnego modelu redundancji | V13 ma ranking syren dominujących i utraty proxy | brak pełnego `coverage_count_ge65` dla wszystkich komórek | częściowo potwierdzona | dodać raster redundancji jako kolejny etap |",
        "",
        "## 7. Analiza wariantów działania",
        "",
        markdown_table(variants, ["wariant", "opis", "naklad", "czas", "zalety", "wady", "ocena"], 10),
        "",
        "## 8. Tabela porównawcza wariantów",
        "",
        markdown_table(comparison, ["kryterium", "wariant_0", "wariant_1", "wariant_2", "wariant_3", "rekomendacja"], 20),
        "",
        "## 9. Analiza ryzyk",
        "",
        markdown_table(risk_register, ["id", "kategoria", "opis", "prawdopodobieństwo", "wpływ", "poziom", "działania", "właściciel", "termin"], 20),
        "",
        "## 10. Rekomendowane rozwiązanie",
        "",
        f"Rekomendowany jest wariant 2: program integracji syren bez GSM i zakup nowych dużych syren wyłącznie dla luk `new_siren_likely`. Decyzja obejmuje `{fmt_int(recommended_qty)}` pozycji kosztowych i budżet `{fmt_pln(recommended_cost)}`. Program rozwiązuje dwa problemy jednocześnie: brak potwierdzonego sterowania dla syren bez GSM oraz największe luki ostrzegania w obszarach ryzyka powodziowego.",
        "",
        "Warunki brzegowe rekomendacji:",
        "",
        "- integracja jest liczona po `gsm_flag = 0`, a nie po braku `SK PSP`;",
        "- nowe syreny kosztowane są tylko dla luk `new_siren_likely`;",
        "- pozostałe luki V12 wymagają walidacji terenowej albo dalszej analizy, ale nie są automatycznie kosztem integracji;",
        "- każda lokalizacja A/B musi mieć potwierdzone warunki montażu, zasilanie, łączność, właściciela i akceptowalny profil akustyczny;",
        "- odbiór musi obejmować potwierdzenie przyjęcia komendy, status urządzenia, rejestr zdarzeń i test kanału zapasowego.",
        "",
        "### 10.1. Podsumowanie rekomendacji zakupowo-operacyjnych",
        "",
        markdown_table(purchase_summary, ["recommended_action", "liczba", "koszt_pln", "populacja_nowa_ge65_proxy", "utrata_po_awarii_proxy", "opis"], 10),
        "",
        "## 11. Plan realizacji",
        "",
        markdown_table(implementation_plan, ["etap", "działania", "odpowiedzialni", "produkt", "czas", "ryzyka"], 10),
        "",
        build_gantt_mermaid(),
        "",
        "### 11.1. Macierz odpowiedzialności RACI",
        "",
        markdown_table(raci, ["obszar", "R", "A", "C", "I"], 10),
        "",
        "## 12. Konsekwencje braku działania",
        "",
        f"Brak działania utrzymuje `{fmt_int(pop_night_outside)}` osób poza modelem słyszalności `>=65 dB(A)` oraz `{fmt_int(riskzone_outside)}` osób w `RiskZone` poza tym progiem. Instytucja pozostaje z rozproszonym sterowaniem, niejednolitym potwierdzaniem wykonania alarmu i ograniczoną zdolnością do wykazania, które syreny zadziałały w danym zdarzeniu.",
        "",
        "Konsekwencje zarządcze: trudniejsza obrona decyzji po zdarzeniu kryzysowym, wyższe ryzyko reputacyjne, brak jednego rejestru gotowości oraz odkładanie kosztów, które prawdopodobnie wrócą w trybie pilnym i droższym.",
        "",
        "## 13. Szybkie zwycięstwa",
        "",
        markdown_table(quick_wins, ["działanie", "priorytet", "koszt", "efekt"], 10),
        "",
        "## 14. Warunki powodzenia",
        "",
        "- Jednoznaczny właściciel programu i komitet sterujący z mandatem do uzgodnień z JST i PSP.",
        "- Przyjęty minimalny standard techniczny: kanał podstawowy, kanał zapasowy, autoryzacja, status, potwierdzenia, rejestr zdarzeń.",
        "- Zamrożona lista priorytetów A/B z kontrolą zmian i śladem decyzji.",
        "- Finansowanie obejmujące nie tylko urządzenia, ale też walidację, testy, odbiory, cyberbezpieczeństwo i utrzymanie.",
        "- Cykliczny raport KPI i aktualizacja danych po każdej zmianie w inwentaryzacji.",
        "",
        "## 15. Mierniki sukcesu",
        "",
        markdown_table(kpi, ["kpi", "wartość bazowa", "cel", "termin"], 10),
        "",
        "## 16. Luki informacyjne",
        "",
        markdown_table(information_gaps, ["luka", "wpływ", "działanie"], 10),
        "",
        "## 17. Dane szczegółowe w raporcie głównym",
        "",
        "### 17.1. Największe deficyty w gminach",
        "",
        markdown_table(top_admin, ["rank", "wojewodztwo", "powiat", "gmina", "teryt_gmi", "pop_risk_total", "pop_ge65", "pop_outside_ge65", "outside_share", "risk_class_max", "priority_score_v12"], 10),
        "",
        "### 17.2. Priorytetowe luki inwestycyjne",
        "",
        markdown_table(gaps_enriched, ["gap_id", "wojewodztwo", "powiat", "gmina", "teryt_gmi", "risk_class", "pop_outside_night", "area_ha", "priority_score_v12", "priority_class", "procurement_action", "estimated_unit_cost_pln", "cost_per_pop_outside_night_pln", "lat", "lon"], 20),
        "",
        "### 17.3. Kandydaci i rekomendowane działania V13",
        "",
        "Rekomendacja V13 rozdziela działania na pięć klas: `integracja`, `modernizacja`, `nowa_syrena`, `walidacja_terenowa`, `brak_dzialania`. Dane lokalizacyjne są jawne, dlatego w tabeli pozostają współrzędne, TERYT, `nr_ref` oraz klasy działania.",
        "",
        markdown_table(top_purchase, ["candidate_id", "nr_ref", "recommended_action", "priority_class", "priority_score_v13", "wojewodztwo", "powiat", "gmina", "teryt_gmi", "risk_class", "recommended_power_class_w", "new_pop_ge65_proxy", "failure_loss_pop_night_proxy", "estimated_cost_pln", "cost_per_new_person_ge65_pln", "candidate_lat", "candidate_lon", "candidate_source", "candidate_type"], 25),
        "",
        "### 17.4. Syreny krytyczne i odporność",
        "",
        "W tym wydaniu V13 lista syren krytycznych używa wskaźnika `dominant_siren_proxy`: populacja przypisana jest do syreny dominującej w modelu V10/V11. To poprawny ranking podatności operacyjnej dla syreny dominującej, ale nie zastępuje pełnego modelu redundancji `coverage_count_ge65`, który wymaga drugiego przebiegu propagacyjnego dla wszystkich syren i komórek rastra.",
        "",
        markdown_table(top_critical, ["rank", "nr_ref", "woj", "powiat", "gmina", "wlasciciel", "rodzaj_syreny", "moc_w", "gsm_status", "sk_psp_status", "failure_loss_pop_night_proxy", "recommended_action", "estimated_cost_pln", "lat", "lon"], 20),
        "",
        "### 17.5. Największe potrzeby integracji GSM według gmin",
        "",
        markdown_table(top_gsm_gminas, ["wojewodztwo", "powiat", "gmina", "siren_records_without_gsm", "unique_sites_without_gsm", "integration_cost_records_pln", "integration_cost_unique_sites_pln"], 15),
        "",
        "### 17.6. Warianty budżetowe",
        "",
        "Koszty wykorzystują dostępne w projekcie założenia z przeliczenia V12: nowa duża syrena `40 000 zł`, integracja istniejącej syreny bez GSM `5 000 zł/szt.`. Wariant integracji nie jest już liczony po `SK PSP` ani po pozostałych lukach V12; luki inne niż `new_siren_likely` pozostają bez kosztu integracji proxy.",
        "",
        markdown_table(procurement_scenarios, ["scenario", "basis", "quantity", "unit_cost_pln", "estimated_cost_pln"], 12),
        "",
        "## 18. Załączniki analityczne",
        "",
        f"- `SOIA_wyniki_tabele.xlsx`: `{xlsx_path}`",
        f"- CSV A01-A11: `{csv_dir}`",
        f"- `purchase_recommendations_V13.csv`: `{output_dir / 'purchase_recommendations_V13.csv'}`",
        f"- `critical_sirens_V13.csv`: `{output_dir / 'critical_sirens_V13.csv'}`",
        f"- `budget_variants_V13.csv`: `{output_dir / 'budget_variants_V13.csv'}`",
        f"- Warstwa obiektów wrażliwych: `{output_dir / 'sensitive_objects_PL_2180.gpkg'}`",
        f"- Warstwa kandydatów: `{output_dir / 'candidate_mount_sites_ranked_V13.gpkg'}`",
        f"- Walidacja: `{output_dir / 'decision_model_V13_validation.json'}`",
        "",
        "### 18.1. Ograniczenia interpretacyjne",
        "",
        "- `RiskZone` opisuje mapy ryzyka powodziowego, a nie samą geometrię wody ani prawdopodobieństwo zalania.",
        "- V10/V11/V12/V13 są modelem inżynierskim i decyzyjnym; wyniki nie są certyfikowanym pomiarem propagacji.",
        "- Kandydaci V13 używają efektu `gap_population_proxy`: wskazują priorytet i lokalizację do sprawdzenia, nie są projektem wykonawczym montażu.",
        "- Lista syren krytycznych w tej wersji używa syreny dominującej V10/V11 jako proxy utraty pokrycia. Pełna redundancja wymaga osobnego rastra liczby syren słyszalnych w każdej komórce.",
        "- Koszty integracji dotyczą `gsm_flag = 0`; brak `SK PSP` jest sygnałem do walidacji operacyjnej, ale nie jest automatycznie kosztem integracji GSM.",
        "- Wariant dzienny jest modelem ekspozycji, nie oficjalną populacją dzienną.",
        "",
        "## 19. Wnioski końcowe",
        "",
        f"Z analizy wynika, że utrzymywanie obecnego stanu oznacza świadome pozostawienie luk ostrzegania i braku jednolitego potwierdzenia działania syren. Nie należy odkładać decyzji o integracji, ponieważ problem dotyczy zarówno bezpieczeństwa ludności, jak i odpowiedzialności instytucji za wykazanie gotowości systemu.",
        "",
        f"Rekomenduje się zatwierdzenie wariantu 2: integrację `{fmt_int(integration_qty)}` syren bez GSM, zakup `{fmt_int(new_siren_qty)}` nowych dużych syren dla luk `new_siren_likely`, uruchomienie walidacji terenowej oraz przyjęcie standardu sterowania z potwierdzeniem, statusem urządzeń, rejestrem zdarzeń i kanałem zapasowym. Rekomendowany budżet programu wynosi `{fmt_pln(recommended_cost)}`.",
        "",
    ]
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


def validate_outputs(
    report_path: Path,
    xlsx_path: Path,
    csv_dir: Path,
    tables: dict[str, pd.DataFrame],
    paths: dict[str, Path],
    purchase: pd.DataFrame,
) -> dict[str, Any]:
    report_text = report_path.read_text(encoding="utf-8")
    placeholder_terms = ["TODO", "do uzupełnienia", "TBD", "[WSTAW"]
    no_placeholders = not any(term.lower() in report_text.lower() for term in placeholder_terms)
    section_checks = {section: section in report_text for section in required_management_sections()}
    mermaid_checks = {
        "has_mermaid_block": "```mermaid" in report_text,
        "has_flowchart": "flowchart" in report_text,
        "has_gantt": "gantt" in report_text,
    }
    csv_matches: dict[str, bool] = {}
    for sheet, frame in tables.items():
        csv_path = csv_dir / f"{sheet}.csv"
        loaded = pd.read_csv(csv_path)
        csv_matches[sheet] = loaded.shape == frame.shape
    pct_ok = True
    for frame in tables.values():
        for col in frame.columns:
            if col.startswith("pct_") and not frame.empty:
                values = pd.to_numeric(frame[col], errors="coerce").dropna()
                if not values.between(0, 100).all():
                    pct_ok = False

    def contains_numeric(value: float | int) -> bool:
        if pd.isna(value):
            return True
        as_int = int(round(float(value)))
        return str(as_int) in report_text or fmt_int(as_int) in report_text

    budget_values_match = True
    budget_checks: dict[str, bool] = {}
    budget_path = paths.get("procurement_summary")
    if budget_path and budget_path.exists():
        budget = pd.read_csv(budget_path)
        for scenario in [
            "integracja_syren_bez_gsm",
            "integracja_lokalizacji_bez_gsm_po_deduplikacji",
            "tylko_luki_new_siren_likely",
            "integracja_syren_bez_gsm_plus_new_siren_likely",
        ]:
            row = budget.loc[budget["scenario"].eq(scenario)]
            if row.empty:
                budget_checks[scenario] = False
                budget_values_match = False
                continue
            quantity_ok = contains_numeric(row.iloc[0]["quantity"])
            cost_ok = contains_numeric(row.iloc[0]["estimated_cost_pln"])
            budget_checks[scenario] = quantity_ok and cost_ok
            budget_values_match = budget_values_match and budget_checks[scenario]

    purchase_action_counts_match = True
    purchase_checks: dict[str, bool] = {}
    if {"recommended_action", "candidate_id", "estimated_cost_pln"}.issubset(purchase.columns):
        grouped = (
            purchase.groupby("recommended_action")
            .agg(count=("candidate_id", "count"), cost=("estimated_cost_pln", "sum"))
            .reset_index()
        )
        for action in ["integracja", "nowa_syrena"]:
            row = grouped.loc[grouped["recommended_action"].eq(action)]
            if row.empty:
                purchase_checks[action] = False
                purchase_action_counts_match = False
                continue
            action_ok = action in report_text
            count_ok = contains_numeric(row.iloc[0]["count"])
            cost_ok = contains_numeric(row.iloc[0]["cost"])
            purchase_checks[action] = action_ok and count_ok and cost_ok
            purchase_action_counts_match = purchase_action_counts_match and purchase_checks[action]

    riskzone_balance_ok = True
    risk_path = paths.get("v12_country")
    if risk_path and risk_path.exists():
        risk_country = pd.read_csv(risk_path)
        riskzone_balance_ok = bool(
            np.isclose(
                risk_country["pop_night_total"].to_numpy(dtype=float),
                risk_country["pop_night_ge65"].to_numpy(dtype=float) + risk_country["pop_night_outside"].to_numpy(dtype=float),
                rtol=0,
                atol=0.05,
            ).all()
        )

    v12_validation = read_json(paths["v12_validation"])
    v11_validation = read_json(paths["v11_validation"])
    validation = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "validation_passed": bool(
            no_placeholders
            and all(section_checks.values())
            and all(mermaid_checks.values())
            and xlsx_path.exists()
            and all(csv_matches.values())
            and pct_ok
            and budget_values_match
            and purchase_action_counts_match
            and riskzone_balance_ok
            and v12_validation.get("validation_passed") is True
            and v11_validation.get("validation_passed") is True
            and not purchase.empty
        ),
        "report_no_placeholders": no_placeholders,
        "report_required_sections": section_checks,
        "report_mermaid": mermaid_checks,
        "xlsx_exists": xlsx_path.exists(),
        "csv_shapes_match_tables": csv_matches,
        "percent_values_in_0_100": pct_ok,
        "budget_values_match_report": budget_values_match,
        "budget_checks": budget_checks,
        "purchase_action_counts_match_report": purchase_action_counts_match,
        "purchase_checks": purchase_checks,
        "riskzone_night_total_equals_ge65_plus_outside": riskzone_balance_ok,
        "v12_validation_passed": v12_validation.get("validation_passed"),
        "v11_validation_passed": v11_validation.get("validation_passed"),
        "purchase_rows": int(len(purchase)),
    }
    return validation


def run(args: argparse.Namespace) -> dict[str, Any]:
    analysis_dir = Path(args.analysis_dir).resolve()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = load_inputs(analysis_dir)
    required_paths(paths)

    sensitive, mount_sites, sensitive_path, mount_sites_path = extract_sensitive_objects(paths["building_population"], output_dir)
    gaps = gpd.read_file(paths["priority_gaps"], layer="priority_gaps")
    gap_costed = pd.read_csv(paths["priority_gaps_costed"], dtype={"teryt_gmi": str})
    gaps_enriched = enrich_priority_gaps(gaps, gap_costed)
    new_candidates = build_new_siren_candidates(
        gaps,
        mount_sites,
        max_candidates=args.max_new_candidates,
        max_distance_m=args.nearest_public_site_m,
        new_siren_cost_pln=args.new_siren_cost_pln,
    )
    critical = build_critical_sirens(paths, args.integration_cost_pln)
    existing_recommendations = build_existing_siren_recommendations(critical)
    missing_gsm_recommendations = build_missing_gsm_recommendations(paths, existing_recommendations, args.integration_cost_pln)
    if not missing_gsm_recommendations.empty:
        existing_recommendations = pd.concat([existing_recommendations, missing_gsm_recommendations], ignore_index=True)
    purchase = build_purchase_recommendations(new_candidates, existing_recommendations)

    ranked_candidates_path = output_dir / "candidate_mount_sites_ranked_V13.gpkg"
    if ranked_candidates_path.exists():
        ranked_candidates_path.unlink()
    new_candidates.to_file(ranked_candidates_path, layer="candidate_mount_sites_ranked", driver="GPKG")

    outputs = {
        "critical_sirens": output_dir / "critical_sirens_V13.csv",
        "redundancy_risk": output_dir / "redundancy_risk_V13.csv",
        "purchase_recommendations": output_dir / "purchase_recommendations_V13.csv",
        "candidate_validation": output_dir / "candidate_site_validation_V13.csv",
        "budget_variants": output_dir / "budget_variants_V13.csv",
    }
    write_csv(outputs["critical_sirens"], critical)
    write_csv(outputs["redundancy_risk"], critical)
    write_csv(outputs["purchase_recommendations"], purchase)
    write_csv(outputs["candidate_validation"], pd.DataFrame(new_candidates.drop(columns="geometry")))
    if paths["procurement_summary"].exists():
        budget = pd.read_csv(paths["procurement_summary"])
    else:
        budget = pd.DataFrame()
    write_csv(outputs["budget_variants"], budget)

    tables, xlsx_path, csv_dir = build_table_package(output_dir, paths, gaps_enriched, new_candidates, purchase, critical, sensitive)
    report_path = build_report(
        output_dir,
        paths,
        gaps_enriched,
        purchase,
        critical,
        tables,
        xlsx_path,
        csv_dir,
        len(sensitive),
        len(new_candidates),
    )
    validation = validate_outputs(report_path, xlsx_path, csv_dir, tables, paths, purchase)
    write_json(output_dir / "decision_model_V13_validation.json", validation)

    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "method": {
            "v12_is_source_of_truth": True,
            "candidate_method": "nearest_public_osm_object_or_gap_representative_point",
            "new_siren_effect_method": "gap_population_proxy",
            "critical_siren_method": "dominant_siren_proxy_from_V11",
        },
        "counts": {
            "sensitive_objects": int(len(sensitive)),
            "candidate_mount_sites_raw": int(len(mount_sites)),
            "new_siren_candidates": int(len(new_candidates)),
            "critical_sirens": int(len(critical)),
            "purchase_recommendations": int(len(purchase)),
        },
        "costs": {
            "new_siren_cost_pln": int(round(args.new_siren_cost_pln)),
            "integration_cost_pln": int(round(args.integration_cost_pln)),
        },
        "outputs": {
            "report": str(report_path.resolve()),
            "xlsx": str(xlsx_path.resolve()),
            "csv_dir": str(csv_dir.resolve()),
            "sensitive_objects": str(sensitive_path.resolve()),
            "candidate_mount_sites": str(mount_sites_path.resolve()),
            "ranked_candidates": str(ranked_candidates_path.resolve()),
            **{key: str(value.resolve()) for key, value in outputs.items()},
            "validation": str((output_dir / "decision_model_V13_validation.json").resolve()),
        },
    }
    write_json(output_dir / "decision_model_V13_summary.json", summary)
    print(json.dumps({"summary": summary, "validation": validation}, indent=2, ensure_ascii=False))
    return summary


def main(argv: list[str] | None = None) -> int:
    run(parse_args(argv))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
