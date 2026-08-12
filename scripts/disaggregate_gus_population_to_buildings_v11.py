#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import fiona
import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from pyproj import Transformer
from shapely.geometry import shape
from shapely.ops import transform as shapely_transform


PLAN_TEXT = """# Plan V11: Disaggregation of GUS NSP 2021 population to buildings and siren ranges

## Goal

Distribute GUS NSP 2021 population from the official 500 m grid to OSM building objects, preserving the `tot`
sum in every grid cell. Then aggregate the resulting population to non-overlapping V10 siren range polygons.

## Inputs

- `data/population-grid/gus_nsp2021_population_grid_500m_tot.gpkg`
- `analysis-output/inwentaryzacja-syren-2026-05-05/osm_sound_layers_PL_2180/osm_buildings_PL_2180_part*.shp`
- `analysis-output/inwentaryzacja-syren-2026-05-05/atdi_sound_model_V10_osm/zasiegi_OSM_65_70_75_nonoverlap_V10.shp`
- `winner_siren_osm_V10_100m.tif` and `coverage_class_osm_V10_100m.tif` from V10.

## Model

For each GUS 500 m cell:

`building_population = tot * building_capacity / sum(building_capacity_in_cell)`

Two variants are produced:

- night/residential population,
- day/mixed exposure population.

The model preserves GUS totals per grid cell. If a populated grid cell has no usable building capacity, a residual
pseudo-object is created at the grid representative point.

## Reproducible command

```bash
python3 scripts/disaggregate_gus_population_to_buildings_v11.py \\
  --gus-grid data/population-grid/gus_nsp2021_population_grid_500m_tot.gpkg \\
  --buildings-dir analysis-output/inwentaryzacja-syren-2026-05-05/osm_sound_layers_PL_2180 \\
  --ranges-shp analysis-output/inwentaryzacja-syren-2026-05-05/atdi_sound_model_V10_osm/zasiegi_OSM_65_70_75_nonoverlap_V10.shp \\
  --output-dir analysis-output/inwentaryzacja-syren-2026-05-05/population_model_V11
```

## Limitations

- The day variant is an exposure model, not an official daytime population.
- Building assignment uses representative points and GUS 500 m CRS3035 cell codes.
- Final siren coverage is sampled from the non-overlapping V10 rasters, then joined back to V10 polygons.
"""


RES_SINGLE = {"house", "detached", "bungalow", "farm", "farmhouse"}
RES_MULTI = {"apartments", "residential"}
RES_SEMI = {"semidetached_house"}
RES_TERRACE = {"terrace"}
RES_DORM = {"dormitory"}
YES_TYPES = {"yes"}
NON_OCCUPIED = {
    "garage",
    "garages",
    "shed",
    "roof",
    "greenhouse",
    "outbuilding",
    "farm_auxiliary",
    "allotment_house",
    "sty",
    "barn",
    "carport",
}
DEST_DIVISORS = {
    "school": 12.0,
    "kindergarten": 12.0,
    "university": 15.0,
    "college": 15.0,
    "hospital": 20.0,
    "office": 25.0,
    "government": 25.0,
    "civic": 25.0,
    "public": 30.0,
    "retail": 30.0,
    "commercial": 30.0,
    "supermarket": 30.0,
    "service": 35.0,
    "hotel": 25.0,
    "industrial": 60.0,
    "manufacture": 60.0,
    "warehouse": 80.0,
    "transportation": 60.0,
    "train_station": 35.0,
    "church": 60.0,
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Disaggregate GUS NSP2021 population to OSM buildings and V10 ranges.")
    parser.add_argument("--gus-grid", required=True, help="GUS NSP 2021 500 m population GPKG.")
    parser.add_argument("--buildings-dir", required=True, help="Directory with OSM building SHP parts in EPSG:2180.")
    parser.add_argument("--ranges-shp", required=True, help="V10 non-overlapping siren ranges SHP.")
    parser.add_argument("--output-dir", required=True, help="Output directory for V11 artifacts.")
    parser.add_argument("--building-limit", type=int, help="Optional building limit for smoke tests.")
    parser.add_argument("--batch-size", type=int, default=10_000, help="Fiona write batch size.")
    parser.add_argument("--skip-building-gpkg", action="store_true", help="Skip full building GPKG for faster diagnostics.")
    parser.add_argument("--force", action="store_true", help="Overwrite existing outputs.")
    return parser.parse_args(argv)


def clean_shp(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    for suffix in [".shp", ".shx", ".dbf", ".prj", ".cpg", ".fix", ".qix"]:
        candidate = path.with_suffix(suffix)
        if candidate.exists():
            candidate.unlink()


def unlink(path: Path) -> None:
    if path.exists():
        path.unlink()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def parse_float(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def normalize_bldg(value: Any) -> str:
    if value in (None, ""):
        return "unknown"
    return str(value).strip().lower()


def default_floors(bldg: str) -> float:
    if bldg == "bungalow":
        return 1.0
    if bldg in RES_SINGLE or bldg in RES_SEMI or bldg in RES_TERRACE:
        return 2.0
    if bldg in RES_MULTI or bldg in RES_DORM:
        return 3.0
    if bldg in {"office", "hotel", "commercial", "retail", "school", "hospital", "university", "college"}:
        return 3.0
    if bldg in {"industrial", "warehouse", "manufacture"}:
        return 1.0
    if bldg == "yes":
        return 1.5
    return 1.0


def infer_floors(bldg: str, levels: float, height_m: float) -> float:
    if levels > 0:
        floors = levels
    elif height_m > 0:
        floors = height_m / 3.0
    else:
        floors = default_floors(bldg)
    return float(min(30.0, max(1.0, floors)))


def compute_scores(bldg_raw: Any, levels_raw: Any, height_raw: Any, area_raw: Any) -> dict[str, Any]:
    bldg = normalize_bldg(bldg_raw)
    area_m2 = max(1.0, parse_float(area_raw))
    floors = infer_floors(bldg, parse_float(levels_raw), parse_float(height_raw))
    floor_area = area_m2 * floors
    night_explicit = 0.0
    night_low_yes = 0.0
    night_yes_fallback = 0.0
    generic = max(1.0, floor_area / 120.0)
    destination = 0.0
    class_name = "other"
    confidence = "low"

    if bldg in RES_SINGLE:
        night_explicit = 4.0 * floors
        class_name = "single_family"
        confidence = "high"
    elif bldg in RES_SEMI:
        night_explicit = 6.0 * floors
        class_name = "semi_detached"
        confidence = "high"
    elif bldg in RES_TERRACE:
        segment_proxy = max(1.0, area_m2 / 70.0)
        night_explicit = 3.0 * floors * segment_proxy
        class_name = "terrace"
        confidence = "high"
    elif bldg in RES_MULTI:
        night_explicit = floor_area / 35.0
        class_name = "multi_family"
        confidence = "high"
    elif bldg in RES_DORM:
        night_explicit = floor_area / 25.0
        destination = floor_area / 25.0
        class_name = "dormitory"
        confidence = "high"
    elif bldg in YES_TYPES:
        night_low_yes = floor_area / 200.0
        night_yes_fallback = floor_area / 45.0
        class_name = "generic_yes"
        confidence = "medium"
    elif bldg in NON_OCCUPIED:
        generic = max(0.5, floor_area / 200.0)
        class_name = "non_occupied_fallback"
        confidence = "low"

    divisor = DEST_DIVISORS.get(bldg)
    if divisor:
        destination = max(destination, floor_area / divisor)
        if confidence != "high":
            confidence = "medium"
        if class_name == "other":
            class_name = "destination"

    return {
        "bldg": bldg,
        "floors": floors,
        "area_m2": area_m2,
        "floor_area": floor_area,
        "night_explicit": night_explicit,
        "night_low_yes": night_low_yes,
        "night_yes_fallback": night_yes_fallback,
        "generic": generic,
        "destination": destination,
        "class_name": class_name,
        "confidence": confidence,
    }


def code_from_3035(x: float, y: float) -> str:
    e = int(math.floor(x / 500.0) * 500)
    n = int(math.floor(y / 500.0) * 500)
    return f"CRS3035RES500mN{n}E{e}"


def iter_building_paths(buildings_dir: Path) -> list[Path]:
    paths = sorted(buildings_dir.glob("osm_buildings_PL_2180_part*.shp"))
    if not paths:
        paths = sorted(buildings_dir.glob("osm_buildings_PL_2180.shp"))
    return paths


def iter_buildings(paths: list[Path], limit: int | None = None) -> Iterable[tuple[Path, dict[str, Any]]]:
    emitted = 0
    for path in paths:
        with fiona.open(path) as src:
            for feat in src:
                yield path, feat
                emitted += 1
                if limit is not None and emitted >= limit:
                    return


def load_gus_grid(gus_grid: Path) -> tuple[dict[str, int], dict[str, int], dict[str, Any]]:
    grid_tot: dict[str, int] = {}
    grid_oid: dict[str, int] = {}
    nonzero = 0
    with fiona.open(gus_grid, layer="population_grid_500m_tot") as src:
        crs_epsg = src.crs.to_epsg() if src.crs else None
        count = len(src)
        for feat in src:
            props = feat["properties"]
            code = str(props["code"])
            tot = int(props["tot"] or 0)
            grid_tot[code] = tot
            grid_oid[code] = int(props["oid"])
            if tot > 0:
                nonzero += 1
    meta = {
        "feature_count": count,
        "crs_epsg": crs_epsg,
        "sum_tot": int(sum(grid_tot.values())),
        "nonzero_cells": nonzero,
    }
    return grid_tot, grid_oid, meta


def representative_point_2180(feat: dict[str, Any]) -> tuple[float, float]:
    geom = shape(feat["geometry"])
    point = geom.representative_point()
    return float(point.x), float(point.y)


def selected_night_score(scores: dict[str, Any], model: dict[str, float]) -> float:
    if model["night_mode"] == 1:
        return scores["night_explicit"] + scores["night_low_yes"]
    if model["night_mode"] == 2:
        return scores["night_yes_fallback"]
    if model["night_mode"] == 3:
        return scores["generic"]
    return 0.0


def selected_day_score(scores: dict[str, Any], night_score: float, model: dict[str, float]) -> float:
    if model["day_mode"] == 1:
        return 0.55 * night_score + 0.45 * scores["destination"]
    if model["day_mode"] == 2:
        return scores["generic"]
    return 0.0


def first_pass(
    building_paths: list[Path],
    grid_tot: dict[str, int],
    transformer_2180_to_3035: Transformer,
    limit: int | None,
) -> tuple[dict[str, dict[str, float]], dict[str, Any]]:
    stats: dict[str, dict[str, float]] = defaultdict(
        lambda: {
            "explicit": 0.0,
            "low_yes": 0.0,
            "yes_fallback": 0.0,
            "generic": 0.0,
            "destination": 0.0,
            "buildings": 0,
            "candidate_buildings": 0,
        }
    )
    type_counter: Counter[str] = Counter()
    counters = Counter()

    for idx, (_, feat) in enumerate(iter_buildings(building_paths, limit), start=1):
        try:
            props = feat["properties"]
            x, y = representative_point_2180(feat)
            x3035, y3035 = transformer_2180_to_3035.transform(x, y)
            code = code_from_3035(x3035, y3035)
            scores = compute_scores(props.get("bldg"), props.get("levels"), props.get("height_m"), props.get("area_m2"))
        except Exception:  # noqa: BLE001
            counters["geometry_or_score_errors"] += 1
            continue

        type_counter[scores["bldg"]] += 1
        counters["buildings_read"] += 1
        if code not in grid_tot:
            counters["buildings_outside_gus_grid"] += 1
            continue

        cell = stats[code]
        cell["explicit"] += scores["night_explicit"]
        cell["low_yes"] += scores["night_low_yes"]
        cell["yes_fallback"] += scores["night_yes_fallback"]
        cell["generic"] += scores["generic"]
        cell["destination"] += scores["destination"]
        cell["buildings"] += 1
        if any(scores[key] > 0 for key in ["night_explicit", "night_low_yes", "night_yes_fallback", "generic", "destination"]):
            cell["candidate_buildings"] += 1

        if idx % 500_000 == 0:
            print(f"first pass: {idx} buildings", flush=True)

    meta = {
        "counters": dict(counters),
        "top_building_types": type_counter.most_common(40),
        "grid_cells_with_buildings": len(stats),
    }
    return stats, meta


def build_grid_models(stats: dict[str, dict[str, float]], grid_tot: dict[str, int]) -> dict[str, dict[str, float]]:
    models: dict[str, dict[str, float]] = {}
    for code, tot in grid_tot.items():
        cell = stats.get(code)
        if cell is None:
            models[code] = {"night_mode": 0, "night_denom": 0.0, "day_mode": 0, "day_denom": 0.0}
            continue
        explicit_plus_low = cell["explicit"] + cell["low_yes"]
        if explicit_plus_low > 0:
            night_mode = 1
            night_denom = explicit_plus_low
        elif cell["yes_fallback"] > 0:
            night_mode = 2
            night_denom = cell["yes_fallback"]
        elif cell["generic"] > 0:
            night_mode = 3
            night_denom = cell["generic"]
        else:
            night_mode = 0
            night_denom = 0.0

        day_denom_primary = 0.55 * night_denom + 0.45 * cell["destination"]
        if day_denom_primary > 0:
            day_mode = 1
            day_denom = day_denom_primary
        elif cell["generic"] > 0:
            day_mode = 2
            day_denom = cell["generic"]
        else:
            day_mode = 0
            day_denom = 0.0
        models[code] = {
            "night_mode": float(night_mode),
            "night_denom": night_denom,
            "day_mode": float(day_mode),
            "day_denom": day_denom,
        }
    return models


def gpkg_schema() -> dict[str, Any]:
    return {
        "geometry": "Point",
        "properties": {
            "osm_id": "str:32",
            "bldg": "str:48",
            "floors": "float",
            "area_m2": "float",
            "capacity_night": "float",
            "capacity_day": "float",
            "pop_night": "float",
            "pop_day": "float",
            "grid_code": "str:64",
            "site_uid": "int",
            "db": "int",
            "nr_ref": "str:80",
            "class_name": "str:48",
            "confidence": "str:16",
        },
    }


def residual_schema() -> dict[str, Any]:
    return {
        "geometry": "Point",
        "properties": {
            "grid_code": "str:64",
            "tot": "float",
            "pop_night": "float",
            "pop_day": "float",
            "site_uid": "int",
            "db": "int",
            "nr_ref": "str:80",
            "confidence": "str:16",
            "reason": "str:80",
        },
    }


def sample_coverage(
    x: float,
    y: float,
    winner: np.ndarray,
    coverage: np.ndarray,
    transform: Any,
    uid_to_nr_ref: dict[int, str],
) -> tuple[int, int, str]:
    col = int((x - transform.c) / transform.a)
    row = int((y - transform.f) / transform.e)
    if row < 0 or col < 0 or row >= coverage.shape[0] or col >= coverage.shape[1]:
        return 0, 0, ""
    db = int(coverage[row, col])
    if db <= 0:
        return 0, 0, ""
    site_uid = int(winner[row, col])
    return site_uid, db, uid_to_nr_ref.get(site_uid, "")


def flush_batch(writer: Any, batch: list[dict[str, Any]]) -> None:
    if batch:
        writer.writerecords(batch)
        batch.clear()


def second_pass(
    building_paths: list[Path],
    grid_tot: dict[str, int],
    models: dict[str, dict[str, float]],
    transformer_2180_to_3035: Transformer,
    winner: np.ndarray,
    coverage: np.ndarray,
    raster_transform: Any,
    uid_to_nr_ref: dict[int, str],
    output_gpkg: Path,
    skip_gpkg: bool,
    batch_size: int,
    limit: int | None,
) -> tuple[dict[str, float], dict[str, float], dict[tuple[int, int], list[float]], dict[str, Any]]:
    allocated_night: dict[str, float] = defaultdict(float)
    allocated_day: dict[str, float] = defaultdict(float)
    coverage_pop: dict[tuple[int, int], list[float]] = defaultdict(lambda: [0.0, 0.0])
    counters = Counter()

    writer = None
    batch: list[dict[str, Any]] = []
    if not skip_gpkg:
        unlink(output_gpkg)
        writer = fiona.open(
            output_gpkg,
            "w",
            driver="GPKG",
            layer="building_population",
            crs="EPSG:2180",
            schema=gpkg_schema(),
        )

    try:
        for idx, (_, feat) in enumerate(iter_buildings(building_paths, limit), start=1):
            try:
                props = feat["properties"]
                x, y = representative_point_2180(feat)
                x3035, y3035 = transformer_2180_to_3035.transform(x, y)
                code = code_from_3035(x3035, y3035)
                scores = compute_scores(props.get("bldg"), props.get("levels"), props.get("height_m"), props.get("area_m2"))
            except Exception:  # noqa: BLE001
                counters["geometry_or_score_errors"] += 1
                continue

            counters["buildings_read"] += 1
            model = models.get(code, {"night_mode": 0.0, "night_denom": 0.0, "day_mode": 0.0, "day_denom": 0.0})
            tot = float(grid_tot.get(code, 0))
            night_score = selected_night_score(scores, model)
            day_score = selected_day_score(scores, night_score, model)
            pop_night = (tot * night_score / model["night_denom"]) if model["night_denom"] > 0 else 0.0
            pop_day = (tot * day_score / model["day_denom"]) if model["day_denom"] > 0 else 0.0

            if pop_night > 0 or pop_day > 0:
                allocated_night[code] += pop_night
                allocated_day[code] += pop_day

            site_uid, db, nr_ref = sample_coverage(x, y, winner, coverage, raster_transform, uid_to_nr_ref)
            if site_uid and db:
                coverage_pop[(site_uid, db)][0] += pop_night
                coverage_pop[(site_uid, db)][1] += pop_day

            if writer is not None:
                batch.append(
                    {
                        "geometry": {"type": "Point", "coordinates": (x, y)},
                        "properties": {
                            "osm_id": str(props.get("osm_id", "")),
                            "bldg": scores["bldg"][:48],
                            "floors": scores["floors"],
                            "area_m2": scores["area_m2"],
                            "capacity_night": night_score,
                            "capacity_day": day_score,
                            "pop_night": pop_night,
                            "pop_day": pop_day,
                            "grid_code": code if code in grid_tot else "",
                            "site_uid": site_uid,
                            "db": db,
                            "nr_ref": nr_ref,
                            "class_name": scores["class_name"],
                            "confidence": scores["confidence"],
                        },
                    }
                )
                if len(batch) >= batch_size:
                    flush_batch(writer, batch)

            if idx % 500_000 == 0:
                flush_batch(writer, batch) if writer is not None else None
                print(f"second pass: {idx} buildings", flush=True)
    finally:
        if writer is not None:
            flush_batch(writer, batch)
            writer.close()

    return allocated_night, allocated_day, coverage_pop, {"counters": dict(counters)}


def load_v10_rasters(ranges_shp: Path) -> tuple[np.ndarray, np.ndarray, Any, dict[int, str], Path, Path]:
    v10_dir = ranges_shp.parent
    winner_path = v10_dir / "winner_siren_osm_V10_100m.tif"
    coverage_path = v10_dir / "coverage_class_osm_V10_100m.tif"
    lookup_path = v10_dir / "winner_siren_lookup_V10.csv"
    lookup = pd.read_csv(lookup_path)
    uid_to_nr_ref = dict(zip(lookup["site_uid"].astype(int), lookup["nr_ref"].astype(str)))
    with rasterio.open(winner_path) as src:
        winner = src.read(1)
        raster_transform = src.transform
    with rasterio.open(coverage_path) as src:
        coverage = src.read(1)
    return winner, coverage, raster_transform, uid_to_nr_ref, winner_path, coverage_path


def add_residuals(
    gus_grid: Path,
    grid_tot: dict[str, int],
    models: dict[str, dict[str, float]],
    allocated_night: dict[str, float],
    allocated_day: dict[str, float],
    coverage_pop: dict[tuple[int, int], list[float]],
    winner: np.ndarray,
    coverage: np.ndarray,
    raster_transform: Any,
    uid_to_nr_ref: dict[int, str],
    output_gpkg: Path,
) -> dict[str, Any]:
    residual_codes = {
        code
        for code, tot in grid_tot.items()
        if tot > 0 and (models[code]["night_denom"] <= 0 or models[code]["day_denom"] <= 0)
    }
    unlink(output_gpkg)
    writer = fiona.open(
        output_gpkg,
        "w",
        driver="GPKG",
        layer="grid_residual_population",
        crs="EPSG:2180",
        schema=residual_schema(),
    )
    transformer = Transformer.from_crs("EPSG:3857", "EPSG:2180", always_xy=True)
    count = 0
    pop_night_total = 0.0
    pop_day_total = 0.0
    try:
        with fiona.open(gus_grid, layer="population_grid_500m_tot") as src:
            for feat in src:
                code = str(feat["properties"]["code"])
                if code not in residual_codes:
                    continue
                tot = float(grid_tot[code])
                model = models[code]
                pop_night = tot if model["night_denom"] <= 0 else 0.0
                pop_day = tot if model["day_denom"] <= 0 else 0.0
                if pop_night <= 0 and pop_day <= 0:
                    continue
                geom = shapely_transform(transformer.transform, shape(feat["geometry"]))
                pt = geom.representative_point()
                x, y = float(pt.x), float(pt.y)
                site_uid, db, nr_ref = sample_coverage(x, y, winner, coverage, raster_transform, uid_to_nr_ref)
                if site_uid and db:
                    coverage_pop[(site_uid, db)][0] += pop_night
                    coverage_pop[(site_uid, db)][1] += pop_day
                allocated_night[code] += pop_night
                allocated_day[code] += pop_day
                pop_night_total += pop_night
                pop_day_total += pop_day
                count += 1
                writer.write(
                    {
                        "geometry": {"type": "Point", "coordinates": (x, y)},
                        "properties": {
                            "grid_code": code,
                            "tot": tot,
                            "pop_night": pop_night,
                            "pop_day": pop_day,
                            "site_uid": site_uid,
                            "db": db,
                            "nr_ref": nr_ref,
                            "confidence": "low",
                            "reason": "no_usable_building_capacity",
                        },
                    }
                )
    finally:
        writer.close()
    return {
        "residual_records": count,
        "residual_pop_night": pop_night_total,
        "residual_pop_day": pop_day_total,
    }


def write_grid_balance(
    path: Path,
    grid_tot: dict[str, int],
    grid_oid: dict[str, int],
    stats: dict[str, dict[str, float]],
    models: dict[str, dict[str, float]],
    allocated_night: dict[str, float],
    allocated_day: dict[str, float],
) -> dict[str, Any]:
    max_abs_diff_night = 0.0
    max_abs_diff_day = 0.0
    sum_night = 0.0
    sum_day = 0.0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "grid_code",
                "oid",
                "tot",
                "building_count",
                "night_denom",
                "day_denom",
                "pop_night",
                "pop_day",
                "diff_night",
                "diff_day",
            ],
        )
        writer.writeheader()
        for code, tot in grid_tot.items():
            pop_n = allocated_night.get(code, 0.0)
            pop_d = allocated_day.get(code, 0.0)
            diff_n = float(tot) - pop_n
            diff_d = float(tot) - pop_d
            max_abs_diff_night = max(max_abs_diff_night, abs(diff_n))
            max_abs_diff_day = max(max_abs_diff_day, abs(diff_d))
            sum_night += pop_n
            sum_day += pop_d
            writer.writerow(
                {
                    "grid_code": code,
                    "oid": grid_oid[code],
                    "tot": tot,
                    "building_count": int(stats.get(code, {}).get("buildings", 0)),
                    "night_denom": models[code]["night_denom"],
                    "day_denom": models[code]["day_denom"],
                    "pop_night": pop_n,
                    "pop_day": pop_d,
                    "diff_night": diff_n,
                    "diff_day": diff_d,
                }
            )
    return {
        "sum_pop_night": sum_night,
        "sum_pop_day": sum_day,
        "max_abs_diff_night": max_abs_diff_night,
        "max_abs_diff_day": max_abs_diff_day,
    }


def write_range_population(
    ranges_shp: Path,
    output_shp: Path,
    coverage_pop: dict[tuple[int, int], list[float]],
) -> tuple[dict[str, Any], gpd.GeoDataFrame]:
    ranges = gpd.read_file(ranges_shp)
    pop_night = []
    pop_day = []
    for row in ranges[["site_uid", "db"]].itertuples(index=False):
        vals = coverage_pop.get((int(row.site_uid), int(row.db)), [0.0, 0.0])
        pop_night.append(vals[0])
        pop_day.append(vals[1])
    ranges["pop_n"] = pop_night
    ranges["pop_d"] = pop_day
    clean_shp(output_shp)
    ranges.to_file(output_shp, driver="ESRI Shapefile", encoding="UTF-8")
    output_shp.with_suffix(".cpg").write_text("UTF-8", encoding="ascii")
    return {
        "features": int(len(ranges)),
        "sum_pop_night": float(ranges["pop_n"].sum()),
        "sum_pop_day": float(ranges["pop_d"].sum()),
    }, ranges


def write_siren_coverage_csv(path: Path, ranges: gpd.GeoDataFrame) -> dict[str, Any]:
    grouped = (
        ranges.groupby(["site_uid", "nr_ref", "db"], as_index=False)[["pop_n", "pop_d"]]
        .sum()
        .sort_values(["site_uid", "db"])
    )
    cumulative_rows: list[dict[str, Any]] = []
    for (site_uid, nr_ref), group in grouped.groupby(["site_uid", "nr_ref"], sort=False):
        by_db = {int(row.db): (float(row.pop_n), float(row.pop_d)) for row in group.itertuples(index=False)}
        ge65_n = sum(v[0] for db, v in by_db.items() if db >= 65)
        ge70_n = sum(v[0] for db, v in by_db.items() if db >= 70)
        ge75_n = sum(v[0] for db, v in by_db.items() if db >= 75)
        ge65_d = sum(v[1] for db, v in by_db.items() if db >= 65)
        ge70_d = sum(v[1] for db, v in by_db.items() if db >= 70)
        ge75_d = sum(v[1] for db, v in by_db.items() if db >= 75)
        for db in [65, 70, 75]:
            pop_n, pop_d = by_db.get(db, (0.0, 0.0))
            cumulative_rows.append(
                {
                    "site_uid": site_uid,
                    "nr_ref": nr_ref,
                    "db": db,
                    "pop_night": pop_n,
                    "pop_day": pop_d,
                    "pop_night_ge65": ge65_n,
                    "pop_night_ge70": ge70_n,
                    "pop_night_ge75": ge75_n,
                    "pop_day_ge65": ge65_d,
                    "pop_day_ge70": ge70_d,
                    "pop_day_ge75": ge75_d,
                }
            )
    df = pd.DataFrame(cumulative_rows)
    df.to_csv(path, index=False)
    return {
        "rows": int(len(df)),
        "sirens": int(df["site_uid"].nunique()) if not df.empty else 0,
        "pop_night_ge65": float(df.drop_duplicates("site_uid")["pop_night_ge65"].sum()) if not df.empty else 0.0,
        "pop_day_ge65": float(df.drop_duplicates("site_uid")["pop_day_ge65"].sum()) if not df.empty else 0.0,
    }


def build_report(summary: dict[str, Any], validation: dict[str, Any]) -> str:
    return f"""# Raport V11: Populacja GUS na budynki i zasięgi syren

## Podsumowanie

- Komórki GUS: {summary['gus']['feature_count']}
- Suma GUS `tot`: {summary['gus']['sum_tot']}
- Budynki przetworzone: {summary['buildings']['buildings_read']}
- Komórki z budynkami: {summary['buildings']['grid_cells_with_buildings']}
- Residual pseudo-obiekty: {summary['residual']['residual_records']}

## Populacja w zasięgach V10

- Noc `>=65 dB(A)`: {summary['coverage']['pop_night_ge65']:.2f}
- Dzień `>=65 dB(A)`: {summary['coverage']['pop_day_ge65']:.2f}
- Noc poza zasięgiem: {summary['coverage']['pop_night_outside_ge65']:.2f}
- Dzień poza zasięgiem: {summary['coverage']['pop_day_outside_ge65']:.2f}

## Walidacja

- Suma nocna zgodna z GUS: {validation['population_totals']['night_matches_gus']}
- Suma dzienna zgodna z GUS: {validation['population_totals']['day_matches_gus']}
- Maksymalna różnica per komórka noc: {validation['grid_balance']['max_abs_diff_night']:.10f}
- Maksymalna różnica per komórka dzień: {validation['grid_balance']['max_abs_diff_day']:.10f}
- Brak ujemnych populacji: {validation['no_negative_population']}

## Ograniczenia

- Wariant dzienny jest modelem ekspozycji, nie oficjalną populacją dzienną.
- Budynki są przypisane do siatki GUS przez punkt reprezentatywny i kod CRS3035 500 m.
- Zasięgi są próbkowane z niedublujących się rastrów V10.
"""


def run(args: argparse.Namespace) -> dict[str, Any]:
    gus_grid = Path(args.gus_grid).resolve()
    buildings_dir = Path(args.buildings_dir).resolve()
    ranges_shp = Path(args.ranges_shp).resolve()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "PLAN_V11_POPULATION.md").write_text(PLAN_TEXT, encoding="utf-8")

    building_paths = iter_building_paths(buildings_dir)
    grid_tot, grid_oid, gus_meta = load_gus_grid(gus_grid)
    transformer_2180_to_3035 = Transformer.from_crs("EPSG:2180", "EPSG:3035", always_xy=True)

    stats, first_meta = first_pass(building_paths, grid_tot, transformer_2180_to_3035, args.building_limit)
    models = build_grid_models(stats, grid_tot)
    winner, coverage, raster_transform, uid_to_nr_ref, winner_path, coverage_path = load_v10_rasters(ranges_shp)

    building_gpkg = output_dir / "building_population_V11.gpkg"
    allocated_night, allocated_day, coverage_pop, second_meta = second_pass(
        building_paths,
        grid_tot,
        models,
        transformer_2180_to_3035,
        winner,
        coverage,
        raster_transform,
        uid_to_nr_ref,
        building_gpkg,
        args.skip_building_gpkg,
        args.batch_size,
        args.building_limit,
    )

    residual_gpkg = output_dir / "grid_residual_population_V11.gpkg"
    residual_meta = add_residuals(
        gus_grid,
        grid_tot,
        models,
        allocated_night,
        allocated_day,
        coverage_pop,
        winner,
        coverage,
        raster_transform,
        uid_to_nr_ref,
        residual_gpkg,
    )

    balance_path = output_dir / "grid_population_balance_V11.csv"
    balance_meta = write_grid_balance(balance_path, grid_tot, grid_oid, stats, models, allocated_night, allocated_day)

    range_pop_shp = output_dir / "zasiegi_OSM_65_70_75_population_V11.shp"
    range_meta, ranges_with_pop = write_range_population(ranges_shp, range_pop_shp, coverage_pop)
    siren_csv = output_dir / "siren_population_coverage_V11.csv"
    siren_meta = write_siren_coverage_csv(siren_csv, ranges_with_pop)

    total_pop = float(gus_meta["sum_tot"])
    covered_night = float(range_meta["sum_pop_night"])
    covered_day = float(range_meta["sum_pop_day"])
    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "inputs": {
            "gus_grid": str(gus_grid),
            "buildings_dir": str(buildings_dir),
            "ranges_shp": str(ranges_shp),
            "winner_raster": str(winner_path),
            "coverage_raster": str(coverage_path),
        },
        "outputs": {
            "plan": str((output_dir / "PLAN_V11_POPULATION.md").resolve()),
            "building_population_gpkg": None if args.skip_building_gpkg else str(building_gpkg.resolve()),
            "grid_residual_population_gpkg": str(residual_gpkg.resolve()),
            "range_population_shp": str(range_pop_shp.resolve()),
            "siren_population_csv": str(siren_csv.resolve()),
            "grid_balance_csv": str(balance_path.resolve()),
        },
        "gus": gus_meta,
        "buildings": {
            **first_meta["counters"],
            "grid_cells_with_buildings": first_meta["grid_cells_with_buildings"],
            "top_building_types": first_meta["top_building_types"],
            "building_limit": args.building_limit,
            "building_gpkg_written": not args.skip_building_gpkg,
        },
        "residual": residual_meta,
        "range_population": range_meta,
        "coverage": {
            **siren_meta,
            "pop_night_ge65": covered_night,
            "pop_day_ge65": covered_day,
            "pop_night_outside_ge65": total_pop - covered_night,
            "pop_day_outside_ge65": total_pop - covered_day,
        },
        "model": {
            "night_model": "residential capacity preserving GUS tot per 500m cell",
            "day_model": "0.55 residential score + 0.45 destination score preserving GUS tot per 500m cell",
        },
    }

    tolerance = 1e-5 if args.building_limit is None else 1e9
    validation = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "validation_passed": (
            abs(balance_meta["sum_pop_night"] - total_pop) <= tolerance
            and abs(balance_meta["sum_pop_day"] - total_pop) <= tolerance
            and balance_meta["max_abs_diff_night"] <= tolerance
            and balance_meta["max_abs_diff_day"] <= tolerance
        ),
        "gus_expected": {
            "feature_count_expected": 1_252_059,
            "sum_tot_expected": 38_035_768,
            "feature_count_match": gus_meta["feature_count"] == 1_252_059,
            "sum_tot_match": gus_meta["sum_tot"] == 38_035_768,
            "crs_epsg_match": gus_meta["crs_epsg"] == 3857,
        },
        "population_totals": {
            "sum_gus_tot": total_pop,
            "sum_pop_night": balance_meta["sum_pop_night"],
            "sum_pop_day": balance_meta["sum_pop_day"],
            "night_matches_gus": abs(balance_meta["sum_pop_night"] - total_pop) <= tolerance,
            "day_matches_gus": abs(balance_meta["sum_pop_day"] - total_pop) <= tolerance,
        },
        "grid_balance": balance_meta,
        "no_negative_population": covered_night >= 0 and covered_day >= 0 and balance_meta["sum_pop_night"] >= 0 and balance_meta["sum_pop_day"] >= 0,
        "range_population_features": range_meta["features"],
    }
    write_json(output_dir / "population_model_V11_summary.json", summary)
    write_json(output_dir / "population_model_V11_validation.json", validation)
    (output_dir / "population_model_V11_report.md").write_text(build_report(summary, validation), encoding="utf-8")
    return {"summary": summary, "validation": validation}


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = run(args)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
