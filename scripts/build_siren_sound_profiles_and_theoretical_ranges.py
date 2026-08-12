#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
from shapely.geometry import box
from shapely.ops import voronoi_diagram


PROFILE_TABLE = {
    300: {"spl": 103.0, "calc_rad": 3000.0},
    600: {"spl": 109.0, "calc_rad": 5000.0},
    900: {"spl": 112.0, "calc_rad": 7000.0},
    1200: {"spl": 115.0, "calc_rad": 10000.0},
}
THRESHOLDS = [65, 70, 75]
R_REF_M = 30.0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Buduje SHP punktow ATDI i teoretycznych izofon 65/70/75 dB(A) dla syren V9."
    )
    parser.add_argument("--points-csv", required=True, help="Finalny CSV V9.")
    parser.add_argument("--output-dir", required=True, help="Katalog wyjsciowy.")
    parser.add_argument("--osm-pbf", help="Pobrany plik Geofabrik OSM PBF jako metadane zrodla warstw.")
    return parser.parse_args(argv)


def profile_for_power(power: float) -> tuple[int, dict[str, float], str]:
    if power <= 450:
        return 300, PROFILE_TABLE[300], "nearest_profile_300"
    if power <= 750:
        return 600, PROFILE_TABLE[600], "nearest_profile_600"
    if power <= 1050:
        return 900, PROFILE_TABLE[900], "nearest_profile_900"
    return 1200, PROFILE_TABLE[1200], "nearest_profile_1200_or_cap"


def radius_for_threshold(spl: float, threshold: int) -> float:
    return R_REF_M * (10 ** ((spl - threshold) / 20.0))


def load_points(csv_path: Path) -> tuple[gpd.GeoDataFrame, dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    duplicate_groups: dict[tuple[float, float], int] = defaultdict(int)
    source_records = 0
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            source_records += 1
            lat = float(row["lat"])
            lon = float(row["lon"])
            power = float(row["moc_w"])
            profile_w, profile, rule = profile_for_power(power)
            radii = {f"r{threshold}_m": radius_for_threshold(profile["spl"], threshold) for threshold in THRESHOLDS}
            duplicate_groups[(lat, lon)] += 1
            rows.append(
                {
                    "nr_ref": row["nr_ref"],
                    "typ": row["rodzaj_syreny"],
                    "pow_w": power,
                    "prof_w": profile_w,
                    "spl30": profile["spl"],
                    "r65_m": radii["r65_m"],
                    "r70_m": radii["r70_m"],
                    "r75_m": radii["r75_m"],
                    "calc_rad": profile["calc_rad"],
                    "prof_rule": rule,
                    "h_m": float(row["wysokosc_nad_terenem_m"]),
                    "npm_m": float(row["npm_m"]),
                    "woj": row["wojewodztwo"],
                    "powiat": row["powiat"],
                    "gmina": row["gmina"],
                    "lat": lat,
                    "lon": lon,
                }
            )

    coord_counts = {coord: count for coord, count in duplicate_groups.items() if count > 1}
    for row in rows:
        row["site_cnt"] = duplicate_groups[(row["lat"], row["lon"])]

    gdf = gpd.GeoDataFrame(
        rows,
        geometry=gpd.points_from_xy([row["lon"] for row in rows], [row["lat"] for row in rows]),
        crs="EPSG:4326",
    ).to_crs("EPSG:2180")
    metadata = {
        "source_records": source_records,
        "duplicate_coordinate_groups": len(coord_counts),
        "duplicate_coordinate_records": sum(coord_counts.values()),
    }
    return gdf, metadata


def deduplicate_sites(points: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    records: list[gpd.GeoDataFrame] = []
    for _, group in points.groupby(["lat", "lon"], sort=False):
        selected = group.sort_values(["pow_w", "nr_ref"], ascending=[False, True]).head(1)
        records.append(selected)
    return gpd.GeoDataFrame(
        pd.concat(records, ignore_index=True),
        geometry="geometry",
        crs=points.crs,
    ).reset_index(drop=True)


def build_voronoi_cells(sites: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    extent = sites.total_bounds
    max_radius = float(sites["r65_m"].max())
    envelope = box(extent[0] - max_radius, extent[1] - max_radius, extent[2] + max_radius, extent[3] + max_radius)
    vor = voronoi_diagram(sites.geometry.unary_union, envelope=envelope, edges=False)
    cells = gpd.GeoDataFrame(geometry=list(vor.geoms), crs=sites.crs)
    assigned = gpd.sjoin_nearest(cells, sites[["nr_ref", "geometry"]], how="left", distance_col="dist_m")
    assigned = assigned.drop(columns=["index_right", "dist_m"])
    return assigned


def build_non_overlapping_ranges(sites: gpd.GeoDataFrame, cells: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    site_attrs = sites.drop(columns="geometry").set_index("nr_ref").to_dict("index")
    records: list[dict[str, Any]] = []
    for _, cell_row in cells.iterrows():
        nr_ref = cell_row["nr_ref"]
        attrs = site_attrs[nr_ref]
        cell = cell_row.geometry
        r65 = float(attrs["r65_m"])
        r70 = float(attrs["r70_m"])
        r75 = float(attrs["r75_m"])
        point = sites.loc[sites["nr_ref"] == nr_ref].geometry.iloc[0]

        zones = [
            (75, point.buffer(r75)),
            (70, point.buffer(r70).difference(point.buffer(r75))),
            (65, point.buffer(r65).difference(point.buffer(r70))),
        ]
        for threshold, geometry in zones:
            clipped = geometry.intersection(cell)
            if clipped.is_empty:
                continue
            rec = {
                "nr_ref": nr_ref,
                "db": threshold,
                "typ": attrs["typ"],
                "pow_w": attrs["pow_w"],
                "prof_w": attrs["prof_w"],
                "spl30": attrs["spl30"],
                "h_m": attrs["h_m"],
                "npm_m": attrs["npm_m"],
                "site_cnt": attrs["site_cnt"],
                "woj": attrs["woj"],
                "powiat": attrs["powiat"],
                "gmina": attrs["gmina"],
                "model": "free_field",
                "osm_ready": 1,
                "geometry": clipped,
            }
            records.append(rec)

    ranges = gpd.GeoDataFrame(records, geometry="geometry", crs=sites.crs)
    ranges = ranges[ranges.geometry.notna() & ~ranges.geometry.is_empty].copy()
    ranges["geometry"] = ranges.geometry.buffer(0)
    ranges = ranges[ranges.geometry.notna() & ~ranges.geometry.is_empty].copy()
    ranges["area_m2"] = ranges.geometry.area.round(2)
    ranges["area_ha"] = (ranges["area_m2"] / 10_000.0).round(4)
    return ranges


def clean_shp(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    for suffix in [".shp", ".shx", ".dbf", ".prj", ".cpg", ".fix", ".qix"]:
        candidate = path.with_suffix(suffix)
        if candidate.exists():
            candidate.unlink()


def write_shp(gdf: gpd.GeoDataFrame, path: Path) -> None:
    clean_shp(path)
    gdf.to_file(path, driver="ESRI Shapefile", encoding="UTF-8")
    path.with_suffix(".cpg").write_text("UTF-8", encoding="ascii")


def validate_ranges(ranges: gpd.GeoDataFrame) -> dict[str, Any]:
    sum_area = float(ranges.geometry.area.sum())
    union_area = float(ranges.geometry.unary_union.area)
    overlap_ratio = 0.0 if sum_area == 0 else max(0.0, (sum_area - union_area) / sum_area)
    return {
        "features": int(len(ranges)),
        "geometry_empty": int(ranges.geometry.is_empty.sum()),
        "geometry_invalid": int((~ranges.geometry.is_valid).sum()),
        "db_values": sorted(int(value) for value in ranges["db"].unique()),
        "missing_nr_ref": int(ranges["nr_ref"].isna().sum() + (ranges["nr_ref"] == "").sum()),
        "sum_area_m2": round(sum_area, 2),
        "union_area_m2": round(union_area, 2),
        "overlap_ratio": round(overlap_ratio, 6),
        "overlap_ratio_le_1pct": overlap_ratio <= 0.01,
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    points_csv = Path(args.points_csv).resolve()
    output_dir = Path(args.output_dir).resolve()
    osm_pbf = Path(args.osm_pbf).resolve() if args.osm_pbf else None

    points, metadata = load_points(points_csv)
    sites = deduplicate_sites(points)
    cells = build_voronoi_cells(sites)
    ranges = build_non_overlapping_ranges(sites, cells)

    stations_path = output_dir / "syreny_ATDI_sound_profiles_V9.shp"
    ranges_path = output_dir / "zasiegi_teoretyczne_65_70_75_nonoverlap_V9.shp"
    write_shp(points, stations_path)
    write_shp(ranges, ranges_path)

    validation = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "points_csv": str(points_csv),
        "osm_pbf": str(osm_pbf) if osm_pbf else None,
        "stations_shp": str(stations_path),
        "ranges_shp": str(ranges_path),
        "station_records": int(len(points)),
        "active_sites_after_deduplication": int(len(sites)),
        **metadata,
        "ranges_validation": validate_ranges(ranges),
        "spl_profiles": PROFILE_TABLE,
        "thresholds_db": THRESHOLDS,
        "model_note": (
            "Teoretyczny free-field SPL; OSM PBF jest wskazany jako zrodlo warstw "
            "budynkow/clutteru do kolejnego etapu tlumien terenowych."
        ),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "sound_profiles_and_ranges_V9.validation.json").write_text(
        json.dumps(validation, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return validation


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = run(args)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
