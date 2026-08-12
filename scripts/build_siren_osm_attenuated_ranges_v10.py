#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import fiona
import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio import features
from rasterio.enums import MergeAlg
from rasterio.transform import from_origin
from shapely.geometry import shape


PROFILE_TABLE = {
    300: {"spl": 103.0, "calc_rad": 3000.0},
    600: {"spl": 109.0, "calc_rad": 5000.0},
    900: {"spl": 112.0, "calc_rad": 7000.0},
    1200: {"spl": 115.0, "calc_rad": 10000.0},
}
R_REF_M = 30.0
NODATA_FLOAT = -9999.0
BUILDING_PENALTY_DB_DEFAULT = 10.0


PLAN_TEXT = """# Plan V10: Zasiegi Syren Z Korekta OSM

## Cel

Etap V10 tworzy audytowalny, niedublujacy sie przestrzennie model zasiegow syren na podstawie:

- finalnego zbioru punktow `V9.csv`,
- profili SPL dla klas 300/600/900/1200 W,
- warstw OSM Geofabrik w `EPSG:2180`: budynki, clutter, drogi.

Model V10 jest przyblizeniem inzynierskim, a nie kopia zamknietego algorytmu ATDI/HTZ.

## Algorytm

1. Wczytaj punkty syren z V9 i przypisz profil SPL:
   - 300 W -> 103 dB(A) / 30 m,
   - 600 W -> 109 dB(A) / 30 m,
   - 900 W -> 112 dB(A) / 30 m,
   - 1200 W -> 115 dB(A) / 30 m.
2. Deduplikuj identyczne wspolrzedne:
   - wygrywa najwieksza `moc_w`,
   - przy remisie najnizszy `nr_ref`.
3. Zbuduj raster w `EPSG:2180`, domyslnie 100 m.
4. Dla kazdej syreny policz poziom free-field:
   `Lp = SPL30 - 20 log10(r / 30 m)`.
5. Dla kazdej komorki wybierz syrene o najwyzszym poziomie.
6. Odejmij tlumienie OSM:
   - clutter z pola `att_db`,
   - budynki jako kara komorkowa, domyslnie 10 dB.
7. Sklasyfikuj komorki do progow 65/70/75 dB(A).
8. Wektoryzuj raster do poligonow, rozpuszczaj po `nr_ref + db`.

## Komenda odtworzeniowa

```bash
python3 scripts/build_siren_osm_attenuated_ranges_v10.py \\
  --points-csv analysis-output/inwentaryzacja-syren-2026-05-05/inwentaryzacja-syren-2026-05-05.normalized.analysis.final.V9.csv \\
  --osm-dir analysis-output/inwentaryzacja-syren-2026-05-05/osm_sound_layers_PL_2180 \\
  --output-dir analysis-output/inwentaryzacja-syren-2026-05-05/atdi_sound_model_V10_osm \\
  --cell-size-m 100 \\
  --thresholds 65 70 75
```

## Wyniki

- `syreny_ATDI_sound_profiles_V10.shp`
- `zasiegi_OSM_65_70_75_nonoverlap_V10.shp`
- `sound_level_osm_V10_100m.tif`
- `winner_siren_osm_V10_100m.tif`
- `coverage_class_osm_V10_100m.tif`
- `winner_siren_lookup_V10.csv`
- `v10_osm_model_summary.json`
- `v10_osm_model_validation.json`
- `v10_osm_model_report.md`

## Ograniczenia

- Tlumienie budynkow jest modelem komorkowym, nie pelna analiza linii widzenia.
- Clutter jest przypisany do komorki rastra z OSM i traktowany jako lokalna kara dB.
- Drogi sa zachowane jako warstwa referencyjna, ale w V10 nie zmniejszaja tlumienia.
- Wynik jest porownawczy i analityczny; certyfikowany wynik nalezy liczyc w ATDI lub innym pelnym solverze propagacyjnym.
"""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Buduje zasięgi V10 z tlumieniem OSM.")
    parser.add_argument("--points-csv", required=True, help="Finalny CSV V9.")
    parser.add_argument("--osm-dir", required=True, help="Katalog z warstwami OSM SHP w EPSG:2180.")
    parser.add_argument("--output-dir", required=True, help="Katalog wynikowy V10.")
    parser.add_argument("--cell-size-m", type=float, default=100.0, help="Rozdzielczosc rastra w metrach.")
    parser.add_argument("--thresholds", nargs="+", type=int, default=[65, 70, 75], help="Progi dB(A).")
    parser.add_argument("--building-penalty-db", type=float, default=BUILDING_PENALTY_DB_DEFAULT)
    parser.add_argument("--max-sites", type=int, help="Limit aktywnych lokalizacji do smoke testu.")
    parser.add_argument("--osm-feature-limit", type=int, help="Limit obiektow OSM na warstwe do smoke testu.")
    parser.add_argument("--chunk-features", type=int, default=50_000, help="Liczba geometrii OSM na chunk rasteryzacji.")
    parser.add_argument("--max-shp-mib", type=int, default=1500, help="Prog dzielenia SHP.")
    parser.add_argument("--force", action="store_true", help="Nadpisz istniejace artefakty.")
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


def clean_shp(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    for suffix in [".shp", ".shx", ".dbf", ".prj", ".cpg", ".fix", ".qix"]:
        candidate = path.with_suffix(suffix)
        if candidate.exists():
            candidate.unlink()


def clean_tif(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def load_points(csv_path: Path, thresholds: list[int]) -> tuple[gpd.GeoDataFrame, dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    duplicate_groups: dict[tuple[float, float], int] = defaultdict(int)
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            lat = float(row["lat"])
            lon = float(row["lon"])
            power = float(row["moc_w"])
            profile_w, profile, rule = profile_for_power(power)
            duplicate_groups[(lat, lon)] += 1
            radii = {f"r{threshold}_m": radius_for_threshold(profile["spl"], threshold) for threshold in thresholds}
            rows.append(
                {
                    "nr_ref": row["nr_ref"],
                    "typ": row["rodzaj_syreny"],
                    "pow_w": power,
                    "prof_w": profile_w,
                    "spl30": profile["spl"],
                    "calc_rad": profile["calc_rad"],
                    "prof_rule": rule,
                    "h_m": float(row["wysokosc_nad_terenem_m"]),
                    "npm_m": float(row["npm_m"]),
                    "woj": row["wojewodztwo"],
                    "powiat": row["powiat"],
                    "gmina": row["gmina"],
                    "lat": lat,
                    "lon": lon,
                    **radii,
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
        "source_records": len(rows),
        "duplicate_coordinate_groups": len(coord_counts),
        "duplicate_coordinate_records": sum(coord_counts.values()),
    }
    return gdf, metadata


def deduplicate_sites(points: gpd.GeoDataFrame, max_sites: int | None = None) -> gpd.GeoDataFrame:
    records: list[gpd.GeoDataFrame] = []
    for _, group in points.groupby(["lat", "lon"], sort=False):
        selected = group.sort_values(["pow_w", "nr_ref"], ascending=[False, True]).head(1)
        records.append(selected)
    sites = gpd.GeoDataFrame(pd.concat(records, ignore_index=True), geometry="geometry", crs=points.crs)
    sites = sites.reset_index(drop=True)
    if max_sites is not None:
        sites = sites.head(max_sites).copy()
    sites["site_uid"] = np.arange(1, len(sites) + 1, dtype=np.int32)
    return sites


def build_grid(sites: gpd.GeoDataFrame, cell_size: float, min_threshold: int) -> tuple[Any, tuple[int, int], tuple[float, float, float, float]]:
    max_radius = float(sites[f"r{min_threshold}_m"].max())
    minx, miny, maxx, maxy = sites.total_bounds
    minx = math.floor((minx - max_radius) / cell_size) * cell_size
    miny = math.floor((miny - max_radius) / cell_size) * cell_size
    maxx = math.ceil((maxx + max_radius) / cell_size) * cell_size
    maxy = math.ceil((maxy + max_radius) / cell_size) * cell_size
    width = int(round((maxx - minx) / cell_size))
    height = int(round((maxy - miny) / cell_size))
    transform = from_origin(minx, maxy, cell_size, cell_size)
    return transform, (height, width), (minx, miny, maxx, maxy)


def compute_best_free_field(
    sites: gpd.GeoDataFrame,
    transform: Any,
    out_shape: tuple[int, int],
    cell_size: float,
    min_threshold: int,
) -> tuple[np.ndarray, np.ndarray]:
    height, width = out_shape
    minx = transform.c
    maxy = transform.f
    best = np.full(out_shape, -np.inf, dtype=np.float32)
    winner = np.zeros(out_shape, dtype=np.int32)

    for idx, row in sites.iterrows():
        x = float(row.geometry.x)
        y = float(row.geometry.y)
        radius = float(row[f"r{min_threshold}_m"])
        spl = float(row["spl30"])
        site_uid = int(row["site_uid"])

        col0 = max(0, int(math.floor((x - radius - minx) / cell_size)))
        col1 = min(width, int(math.ceil((x + radius - minx) / cell_size)))
        row0 = max(0, int(math.floor((maxy - (y + radius)) / cell_size)))
        row1 = min(height, int(math.ceil((maxy - (y - radius)) / cell_size)))
        if col0 >= col1 or row0 >= row1:
            continue

        cols = np.arange(col0, col1, dtype=np.float32)
        rows = np.arange(row0, row1, dtype=np.float32)
        xs = minx + (cols + 0.5) * cell_size
        ys = maxy - (rows + 0.5) * cell_size
        dx = xs[None, :] - x
        dy = ys[:, None] - y
        dist = np.hypot(dx, dy).astype(np.float32)
        inside = dist <= radius
        if not inside.any():
            continue

        dist_for_loss = np.maximum(dist, R_REF_M)
        level = spl - 20.0 * np.log10(dist_for_loss / R_REF_M)
        window = best[row0:row1, col0:col1]
        update = inside & (level > window)
        if update.any():
            window[update] = level[update]
            winner[row0:row1, col0:col1][update] = site_uid

        if (idx + 1) % 500 == 0 or idx + 1 == len(sites):
            print(f"free-field: {idx + 1}/{len(sites)} sites", flush=True)

    return best, winner


def iter_osm_paths(osm_dir: Path, pattern: str) -> list[Path]:
    return sorted(osm_dir.glob(pattern))


def iter_features_for_raster(
    paths: list[Path],
    bbox: tuple[float, float, float, float],
    value_property: str | None,
    fixed_value: float | int,
    feature_limit: int | None,
) -> Iterable[tuple[dict[str, Any], float | int]]:
    emitted = 0
    for path in paths:
        with fiona.open(path) as src:
            for feat in src.filter(bbox=bbox):
                geom = feat.get("geometry")
                if not geom:
                    continue
                if value_property is None:
                    value = fixed_value
                else:
                    raw_value = feat.get("properties", {}).get(value_property)
                    if raw_value in (None, ""):
                        continue
                    value = float(raw_value)
                    if value <= 0:
                        continue
                yield geom, value
                emitted += 1
                if feature_limit is not None and emitted >= feature_limit:
                    return


def rasterize_chunked(
    paths: list[Path],
    transform: Any,
    out_shape: tuple[int, int],
    bbox: tuple[float, float, float, float],
    dtype: str,
    value_property: str | None,
    fixed_value: float | int,
    chunk_features: int,
    all_touched: bool,
    feature_limit: int | None,
    layer_name: str,
) -> tuple[np.ndarray, int]:
    out = np.zeros(out_shape, dtype=dtype)
    chunk: list[tuple[dict[str, Any], float | int]] = []
    count = 0
    for geom, value in iter_features_for_raster(paths, bbox, value_property, fixed_value, feature_limit):
        chunk.append((geom, value))
        count += 1
        if len(chunk) >= chunk_features:
            features.rasterize(
                chunk,
                out=out,
                transform=transform,
                all_touched=all_touched,
                merge_alg=MergeAlg.replace,
            )
            chunk = []
            print(f"rasterize {layer_name}: {count} features", flush=True)
    if chunk:
        features.rasterize(
            chunk,
            out=out,
            transform=transform,
            all_touched=all_touched,
            merge_alg=MergeAlg.replace,
        )
    print(f"rasterize {layer_name}: done, {count} features", flush=True)
    return out, count


def write_tif(path: Path, array: np.ndarray, transform: Any, dtype: str, nodata: float | int) -> None:
    clean_tif(path)
    profile = {
        "driver": "GTiff",
        "height": array.shape[0],
        "width": array.shape[1],
        "count": 1,
        "dtype": dtype,
        "crs": "EPSG:2180",
        "transform": transform,
        "compress": "deflate",
        "predictor": 2 if dtype.startswith("float") else 1,
        "tiled": True,
        "blockxsize": 256,
        "blockysize": 256,
        "nodata": nodata,
        "BIGTIFF": "IF_SAFER",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(array.astype(dtype, copy=False), 1)


def write_shp(gdf: gpd.GeoDataFrame, path: Path) -> list[Path]:
    clean_shp(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(path, driver="ESRI Shapefile", encoding="UTF-8")
    path.with_suffix(".cpg").write_text("UTF-8", encoding="ascii")
    return [path]


def write_shp_maybe_split(gdf: gpd.GeoDataFrame, path: Path, max_mib: int) -> list[Path]:
    paths = write_shp(gdf, path)
    shp_size = path.with_suffix(".shp").stat().st_size
    if shp_size <= max_mib * 1024 * 1024:
        return paths

    for suffix in [".shp", ".shx", ".dbf", ".prj", ".cpg", ".fix", ".qix"]:
        candidate = path.with_suffix(suffix)
        if candidate.exists():
            candidate.unlink()

    part_count = int(math.ceil(shp_size / (max_mib * 1024 * 1024)))
    chunk_size = int(math.ceil(len(gdf) / part_count))
    out_paths: list[Path] = []
    for part_idx, start in enumerate(range(0, len(gdf), chunk_size), start=1):
        part_path = path.with_name(f"{path.stem}_part{part_idx:03d}{path.suffix}")
        out_paths.extend(write_shp(gdf.iloc[start : start + chunk_size].copy(), part_path))
    return out_paths


def station_export_columns(sites: gpd.GeoDataFrame, thresholds: list[int]) -> gpd.GeoDataFrame:
    cols = [
        "site_uid",
        "nr_ref",
        "typ",
        "pow_w",
        "prof_w",
        "spl30",
        "calc_rad",
        "h_m",
        "npm_m",
        "site_cnt",
        "woj",
        "powiat",
        "gmina",
        "lat",
        "lon",
    ] + [f"r{threshold}_m" for threshold in thresholds]
    return sites[cols + ["geometry"]].copy()


def vectorize_ranges(
    code: np.ndarray,
    transform: Any,
    sites: gpd.GeoDataFrame,
    output_path: Path,
    max_shp_mib: int,
) -> tuple[list[Path], gpd.GeoDataFrame]:
    attrs_by_uid = sites.drop(columns="geometry").set_index("site_uid").to_dict("index")
    records: list[dict[str, Any]] = []
    mask = code > 0
    for geom, value in features.shapes(code.astype(np.int32, copy=False), mask=mask, transform=transform):
        value_int = int(value)
        site_uid = value_int // 1000
        db = value_int % 1000
        attrs = attrs_by_uid.get(site_uid)
        if attrs is None:
            continue
        records.append(
            {
                "site_uid": site_uid,
                "nr_ref": attrs["nr_ref"],
                "db": db,
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
                "model": "osm_v10",
                "geometry": shape(geom),
            }
        )
    if not records:
        ranges = gpd.GeoDataFrame(records, geometry="geometry", crs="EPSG:2180")
        return write_shp(ranges, output_path), ranges

    ranges = gpd.GeoDataFrame(records, geometry="geometry", crs="EPSG:2180")
    ranges = ranges[ranges.geometry.notna() & ~ranges.geometry.is_empty].copy()
    ranges["geometry"] = ranges.geometry.buffer(0)
    ranges = ranges[ranges.geometry.notna() & ~ranges.geometry.is_empty].copy()
    print(f"vectorize: {len(ranges)} raw polygons before dissolve", flush=True)

    agg = {
        "nr_ref": "first",
        "typ": "first",
        "pow_w": "first",
        "prof_w": "first",
        "spl30": "first",
        "h_m": "first",
        "npm_m": "first",
        "site_cnt": "first",
        "woj": "first",
        "powiat": "first",
        "gmina": "first",
        "model": "first",
    }
    ranges = ranges.dissolve(by=["site_uid", "db"], as_index=False, aggfunc=agg)
    ranges["area_m2"] = ranges.geometry.area.round(2)
    ranges["area_ha"] = (ranges["area_m2"] / 10_000.0).round(4)
    ranges = ranges.sort_values(["nr_ref", "db"]).reset_index(drop=True)
    print(f"vectorize: {len(ranges)} dissolved polygons", flush=True)
    paths = write_shp_maybe_split(ranges, output_path, max_shp_mib)
    return paths, ranges


def validate_shp_paths(paths: list[Path]) -> dict[str, Any]:
    components = [".shp", ".shx", ".dbf", ".prj", ".cpg"]
    layer_results: list[dict[str, Any]] = []
    total_features = 0
    db_values: set[int] = set()
    missing_nr_ref = 0
    empty = 0
    invalid = 0
    sum_area = 0.0
    geoms = []
    collect_union = True

    for path in paths:
        missing = [suffix for suffix in components if not path.with_suffix(suffix).exists()]
        with fiona.open(path) as src:
            crs_epsg = src.crs.to_epsg() if src.crs else None
            count = len(src)
            total_features += count
            layer_result = {
                "path": str(path.resolve()),
                "missing_components": missing,
                "crs_epsg": crs_epsg,
                "feature_count": count,
                "schema_geometry": src.schema.get("geometry"),
            }
            layer_results.append(layer_result)
            if total_features > 50_000:
                collect_union = False
            for feat in src:
                props = feat.get("properties", {})
                geom_obj = feat.get("geometry")
                db_value = props.get("db")
                if db_value is not None:
                    db_values.add(int(db_value))
                nr_ref = props.get("nr_ref")
                if nr_ref in (None, ""):
                    missing_nr_ref += 1
                if not geom_obj:
                    empty += 1
                    continue
                shp = shape(geom_obj)
                if shp.is_empty:
                    empty += 1
                if not shp.is_valid:
                    invalid += 1
                area = float(shp.area)
                sum_area += area
                if collect_union:
                    geoms.append(shp)

    if collect_union and geoms:
        union_area = float(gpd.GeoSeries(geoms, crs="EPSG:2180").unary_union.area)
        overlap_ratio = 0.0 if sum_area == 0 else max(0.0, (sum_area - union_area) / sum_area)
    else:
        union_area = None
        overlap_ratio = 0.0

    return {
        "layers": layer_results,
        "features": total_features,
        "geometry_empty": empty,
        "geometry_invalid": invalid,
        "db_values": sorted(db_values),
        "missing_nr_ref": missing_nr_ref,
        "sum_area_m2": round(sum_area, 2),
        "union_area_m2": round(union_area, 2) if union_area is not None else None,
        "overlap_ratio": round(overlap_ratio, 6),
        "overlap_ratio_le_1pct": overlap_ratio <= 0.01,
        "union_check_method": "full_union" if collect_union else "raster_construction_nonoverlap",
    }


def build_report(summary: dict[str, Any], validation: dict[str, Any]) -> str:
    area_by_db = summary["coverage_area_by_db_ha"]
    lines = [
        "# Raport V10: Zasiegi syren z korekta OSM",
        "",
        "## Podsumowanie",
        "",
        f"- Rekordy zrodlowe: {summary['source_records']}",
        f"- Aktywne lokalizacje po deduplikacji: {summary['active_sites_after_deduplication']}",
        f"- Rozdzielczosc rastra: {summary['cell_size_m']} m",
        f"- Wymiary rastra: {summary['grid_width']} x {summary['grid_height']} komorek",
        f"- Komorki z pokryciem >= {min(summary['thresholds_db'])} dB(A): {summary['covered_cells']}",
        f"- Syreny z pokryciem: {summary['sirens_with_coverage']}",
        "",
        "## Powierzchnia pokrycia",
        "",
        "| Prog dB(A) | Powierzchnia ha |",
        "|---:|---:|",
    ]
    for db in sorted(area_by_db, key=lambda x: int(x)):
        lines.append(f"| {db} | {area_by_db[db]:,.2f} |".replace(",", " "))
    lines.extend(
        [
            "",
            "## Walidacja",
            "",
            f"- Komplet SHP / CRS / geometrie: {'OK' if validation['validation_passed'] else 'DO PRZEGLADU'}",
            f"- Liczba poligonow: {validation['ranges_validation']['features']}",
            f"- Wartosci `db`: {validation['ranges_validation']['db_values']}",
            f"- Brakujace `nr_ref`: {validation['ranges_validation']['missing_nr_ref']}",
            f"- Overlap ratio: {validation['ranges_validation']['overlap_ratio']}",
            "",
            "## Ograniczenia",
            "",
            "- To model inzynierski, nie certyfikowany wynik ATDI/HTZ.",
            "- Budynki sa lokalna kara komorkowa, a nie pelna analiza ekranowania po trasie propagacji.",
            "- Clutter jest przypisany z OSM do komorki rastra; nakladanie klas OSM jest rozstrzygane przez kolejnosc rasteryzacji.",
            "- Drogi sa warstwa referencyjna, bez redukcji tlumienia w V10.",
        ]
    )
    return "\n".join(lines) + "\n"


def run(args: argparse.Namespace) -> dict[str, Any]:
    points_csv = Path(args.points_csv).resolve()
    osm_dir = Path(args.osm_dir).resolve()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    thresholds = sorted(set(args.thresholds))
    min_threshold = min(thresholds)
    max_threshold = max(thresholds)
    (output_dir / "PLAN_V10_OSM.md").write_text(PLAN_TEXT, encoding="utf-8")

    points, point_metadata = load_points(points_csv, thresholds)
    sites = deduplicate_sites(points, args.max_sites)
    transform, out_shape, bbox = build_grid(sites, args.cell_size_m, min_threshold)
    height, width = out_shape

    print(f"grid: {width} x {height} cells, bbox={bbox}", flush=True)
    best, winner = compute_best_free_field(sites, transform, out_shape, args.cell_size_m, min_threshold)

    clutter_paths = iter_osm_paths(osm_dir, "osm_clutter_PL_2180_part*.shp")
    if not clutter_paths:
        clutter_paths = iter_osm_paths(osm_dir, "osm_clutter_PL_2180.shp")
    building_paths = iter_osm_paths(osm_dir, "osm_buildings_PL_2180_part*.shp")
    if not building_paths:
        building_paths = iter_osm_paths(osm_dir, "osm_buildings_PL_2180.shp")

    clutter, clutter_features = rasterize_chunked(
        clutter_paths,
        transform,
        out_shape,
        bbox,
        "float32",
        "att_db",
        0.0,
        args.chunk_features,
        True,
        args.osm_feature_limit,
        "clutter",
    )
    buildings, building_features = rasterize_chunked(
        building_paths,
        transform,
        out_shape,
        bbox,
        "uint8",
        None,
        1,
        args.chunk_features,
        True,
        args.osm_feature_limit,
        "buildings",
    )

    valid = np.isfinite(best) & (winner > 0)
    best[valid] = best[valid] - clutter[valid] - (buildings[valid].astype(np.float32) * args.building_penalty_db)
    sound = np.where(valid, best, NODATA_FLOAT).astype(np.float32)

    coverage_class = np.zeros(out_shape, dtype=np.uint16)
    for threshold in thresholds:
        coverage_class[valid & (sound >= threshold)] = threshold

    covered = coverage_class >= min_threshold
    winner_coverage = np.where(covered, winner, 0).astype(np.int32)
    code = np.where(covered, winner.astype(np.int32) * 1000 + coverage_class.astype(np.int32), 0).astype(np.int32)

    suffix = f"{int(args.cell_size_m)}m"
    sound_path = output_dir / f"sound_level_osm_V10_{suffix}.tif"
    winner_path = output_dir / f"winner_siren_osm_V10_{suffix}.tif"
    class_path = output_dir / f"coverage_class_osm_V10_{suffix}.tif"
    write_tif(sound_path, sound, transform, "float32", NODATA_FLOAT)
    write_tif(winner_path, winner_coverage, transform, "int32", 0)
    write_tif(class_path, coverage_class, transform, "uint16", 0)

    stations_path = output_dir / "syreny_ATDI_sound_profiles_V10.shp"
    write_shp(station_export_columns(sites, thresholds), stations_path)
    lookup_path = output_dir / "winner_siren_lookup_V10.csv"
    sites.drop(columns="geometry").to_csv(lookup_path, index=False)

    ranges_path = output_dir / "zasiegi_OSM_65_70_75_nonoverlap_V10.shp"
    range_paths, ranges = vectorize_ranges(code, transform, sites, ranges_path, args.max_shp_mib)

    cell_area_ha = (args.cell_size_m * args.cell_size_m) / 10_000.0
    area_by_db = {
        str(threshold): round(float((coverage_class == threshold).sum()) * cell_area_ha, 4)
        for threshold in thresholds
    }
    free_field_validation_path = output_dir.parent / "atdi_sound_model_V9" / "sound_profiles_and_ranges_V9.validation.json"
    free_field_area_m2 = None
    if free_field_validation_path.exists():
        try:
            free_field_validation = json.loads(free_field_validation_path.read_text(encoding="utf-8"))
            free_field_area_m2 = free_field_validation.get("ranges_validation", {}).get("sum_area_m2")
        except json.JSONDecodeError:
            free_field_area_m2 = None

    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "points_csv": str(points_csv),
        "osm_dir": str(osm_dir),
        "output_dir": str(output_dir),
        "cell_size_m": args.cell_size_m,
        "thresholds_db": thresholds,
        "building_penalty_db": args.building_penalty_db,
        "source_records": point_metadata["source_records"],
        "active_sites_after_deduplication": int(len(sites)),
        "duplicate_coordinate_groups": point_metadata["duplicate_coordinate_groups"],
        "duplicate_coordinate_records": point_metadata["duplicate_coordinate_records"],
        "max_sites": args.max_sites,
        "osm_feature_limit": args.osm_feature_limit,
        "grid_width": width,
        "grid_height": height,
        "grid_cells": int(width * height),
        "grid_bbox_2180": list(map(float, bbox)),
        "osm_features_used": {
            "clutter": clutter_features,
            "buildings": building_features,
        },
        "covered_cells": int(covered.sum()),
        "sirens_with_coverage": int(np.unique(winner_coverage[winner_coverage > 0]).size),
        "coverage_area_by_db_ha": area_by_db,
        "coverage_total_area_ha": round(float(covered.sum()) * cell_area_ha, 4),
        "free_field_v9_area_m2": free_field_area_m2,
        "outputs": {
            "plan": str((output_dir / "PLAN_V10_OSM.md").resolve()),
            "stations_shp": str(stations_path.resolve()),
            "ranges_shp": [str(path.resolve()) for path in range_paths],
            "sound_tif": str(sound_path.resolve()),
            "winner_tif": str(winner_path.resolve()),
            "coverage_class_tif": str(class_path.resolve()),
            "lookup_csv": str(lookup_path.resolve()),
        },
        "model_note": "V10 is an engineering OSM attenuation approximation, not a certified ATDI/HTZ result.",
        "spl_profiles": PROFILE_TABLE,
    }

    ranges_validation = validate_shp_paths(range_paths)
    station_validation = validate_shp_paths([stations_path])
    validation = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "validation_passed": (
            ranges_validation["geometry_empty"] == 0
            and ranges_validation["geometry_invalid"] == 0
            and ranges_validation["missing_nr_ref"] == 0
            and ranges_validation["db_values"] == thresholds
            and ranges_validation["overlap_ratio_le_1pct"]
        ),
        "expected_source_records": 22614,
        "expected_active_sites_full": 22032,
        "source_records_match_expected": point_metadata["source_records"] == 22614,
        "active_sites_match_expected": (len(sites) == 22032) if args.max_sites is None else None,
        "ranges_validation": ranges_validation,
        "stations_validation": station_validation,
        "raster_validation": {
            "sound_nodata_cells": int((sound == NODATA_FLOAT).sum()),
            "winner_nodata_cells": int((winner_coverage == 0).sum()),
            "coverage_db_values": sorted(int(value) for value in np.unique(coverage_class[coverage_class > 0])),
        },
    }
    write_json(output_dir / "v10_osm_model_summary.json", summary)
    write_json(output_dir / "v10_osm_model_validation.json", validation)
    (output_dir / "v10_osm_model_report.md").write_text(build_report(summary, validation), encoding="utf-8")
    return {"summary": summary, "validation": validation}


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = run(args)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
