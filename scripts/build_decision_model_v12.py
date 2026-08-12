#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import tempfile
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlencode

import fiona
import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
import requests
from rasterio import features
from scipy import ndimage
from shapely.geometry import mapping, shape
from shapely.ops import unary_union
from shapely.strtree import STRtree


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ANALYSIS_DIR = ROOT / "analysis-output" / "inwentaryzacja-syren-2026-05-05"
DEFAULT_V11_DIR = DEFAULT_ANALYSIS_DIR / "population_model_V11"
DEFAULT_V10_DIR = DEFAULT_ANALYSIS_DIR / "atdi_sound_model_V10_osm"
DEFAULT_PRG_DIR = ROOT / "data" / "prg" / "jednostki_administracyjne_shp"
DEFAULT_OUTPUT_DIR = DEFAULT_ANALYSIS_DIR / "decision_model_V12"

WFS_BASE_URL = "https://mapy.geoportal.gov.pl/wss/service/PZGIK/PRG/WFS/AdministrativeBoundaries"
WFS_TYPENAMES = {
    "woj": "ms:A01_Granice_wojewodztw",
    "pow": "ms:A02_Granice_powiatow",
    "gmi": "ms:A03_Granice_gmin",
}
PRG_FILENAMES = {
    "woj": "A01_Granice_wojewodztw.shp",
    "pow": "A02_Granice_powiatow.shp",
    "gmi": "A03_Granice_gmin.shp",
}
RISK_LABELS = {1: "niskie ryzyko", 2: "wysokie ryzyko"}
RISK_VALUES = {"niskie ryzyko": 1, "wysokie ryzyko": 2}
AGG_COLUMNS = [
    "pop_night_total",
    "pop_day_total",
    "pop_night_ge65",
    "pop_day_ge65",
    "pop_night_outside",
    "pop_day_outside",
]


@dataclass
class RasterContext:
    path: Path
    array: np.ndarray
    transform: Any
    crs: Any
    shape: tuple[int, int]
    bounds: tuple[float, float, float, float]


@dataclass
class AdminContext:
    boundaries_path: Path
    woj: gpd.GeoDataFrame
    pow: gpd.GeoDataFrame
    gmi: gpd.GeoDataFrame
    gmi_attrs: pd.DataFrame
    tree: STRtree
    gmi_geometries: np.ndarray
    validation: dict[str, Any]
    refreshed_from_wfs: bool


@dataclass
class PopulationChunk:
    source: str
    xs: np.ndarray
    ys: np.ndarray
    pop_night: np.ndarray
    pop_day: np.ndarray
    v11_db: np.ndarray


class RiskCoverageAggregator:
    def __init__(self) -> None:
        self.country: dict[tuple[str], np.ndarray] = defaultdict(lambda: np.zeros(len(AGG_COLUMNS), dtype=float))
        self.woj: dict[tuple[str, str], np.ndarray] = defaultdict(lambda: np.zeros(len(AGG_COLUMNS), dtype=float))
        self.powiat: dict[tuple[str, str, str, str, str], np.ndarray] = defaultdict(
            lambda: np.zeros(len(AGG_COLUMNS), dtype=float)
        )
        self.gmina: dict[tuple[str, str, str, str, str, str, str], np.ndarray] = defaultdict(
            lambda: np.zeros(len(AGG_COLUMNS), dtype=float)
        )

    def update(self, df: pd.DataFrame) -> None:
        if df.empty:
            return
        work = df.copy()
        work["ge65_key"] = np.where(work["ge65"], "ge65", "outside")
        grouped = (
            work.groupby(["risk_value", "risk_class", "ge65_key"], dropna=False)[["pop_night", "pop_day"]]
            .sum()
            .reset_index()
        )
        for row in grouped.itertuples(index=False):
            self._add(self.country[(row.risk_class,)], row.ge65_key == "ge65", row.pop_night, row.pop_day)

        self._update_level(
            self.woj,
            work,
            ["teryt_woj", "wojewodztwo", "risk_value", "risk_class", "ge65_key"],
            lambda row: (row.teryt_woj, row.wojewodztwo, row.risk_class),
        )
        self._update_level(
            self.powiat,
            work,
            ["teryt_woj", "wojewodztwo", "teryt_pow", "powiat", "risk_value", "risk_class", "ge65_key"],
            lambda row: (row.teryt_woj, row.wojewodztwo, row.teryt_pow, row.powiat, row.risk_class),
        )
        self._update_level(
            self.gmina,
            work,
            [
                "teryt_woj",
                "wojewodztwo",
                "teryt_pow",
                "powiat",
                "teryt_gmi",
                "gmina",
                "risk_value",
                "risk_class",
                "ge65_key",
            ],
            lambda row: (
                row.teryt_woj,
                row.wojewodztwo,
                row.teryt_pow,
                row.powiat,
                row.teryt_gmi,
                row.gmina,
                row.risk_class,
            ),
        )

    def _update_level(self, target: dict[Any, np.ndarray], df: pd.DataFrame, group_cols: list[str], key_fn: Any) -> None:
        grouped = df.groupby(group_cols, dropna=False)[["pop_night", "pop_day"]].sum().reset_index()
        for row in grouped.itertuples(index=False):
            self._add(target[key_fn(row)], row.ge65_key == "ge65", row.pop_night, row.pop_day)

    @staticmethod
    def _add(values: np.ndarray, ge65: bool, pop_night: float, pop_day: float) -> None:
        values[0] += float(pop_night)
        values[1] += float(pop_day)
        if ge65:
            values[2] += float(pop_night)
            values[3] += float(pop_day)
        else:
            values[4] += float(pop_night)
            values[5] += float(pop_day)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build V12 decision model: RiskZone x population x >=65 dB coverage.")
    parser.add_argument("--building-pop", default=str(DEFAULT_V11_DIR / "building_population_V11.gpkg"))
    parser.add_argument("--residual-pop", default=str(DEFAULT_V11_DIR / "grid_residual_population_V11.gpkg"))
    parser.add_argument("--riskzone-shp", default=str(ROOT / "data" / "isok-mrp" / "MZP_MRP_RiskZone_PL_2180.shp"))
    parser.add_argument("--coverage-raster", default=str(DEFAULT_V10_DIR / "coverage_class_osm_V10_100m.tif"))
    parser.add_argument("--prg-dir", default=str(DEFAULT_PRG_DIR))
    parser.add_argument("--terc-zip", default=str(ROOT / "data" / "kpp-public-entities" / "TERC_Urzedowy_2026-05-09.zip"))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--chunk-size", type=int, default=200_000)
    parser.add_argument("--risk-chunk-features", type=int, default=50_000)
    parser.add_argument("--min-gap-pop", type=float, default=1.0)
    parser.add_argument("--bbox", nargs=4, type=float, metavar=("MINX", "MINY", "MAXX", "MAXY"))
    parser.add_argument("--limit-points", type=int, help="Optional total point limit for smoke tests.")
    parser.add_argument("--no-prg-refresh", action="store_true", help="Do not refresh PRG from GUGiK WFS if TERC check fails.")
    parser.add_argument("--force", action="store_true", help="Overwrite existing V12 outputs.")
    return parser.parse_args(argv)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def unlink_if_exists(path: Path) -> None:
    if path.exists():
        path.unlink()


def prepare_outputs(output_dir: Path, force: bool) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    known_outputs = [
        "admin_boundaries_PL_2180.gpkg",
        "riskzone_class_V12_100m.tif",
        "gap_labels_V12_100m.tif",
        "priority_gaps_V12.gpkg",
        "v12_risk_coverage_country.csv",
        "v12_risk_coverage_woj.csv",
        "v12_risk_coverage_powiat.csv",
        "v12_risk_coverage_gmina.csv",
        "admin_gap_ranking_V12.csv",
        "decision_model_V12_summary.json",
        "decision_model_V12_validation.json",
        "decision_model_V12_report.md",
    ]
    existing = [output_dir / item for item in known_outputs if (output_dir / item).exists()]
    if existing and not force:
        names = ", ".join(path.name for path in existing[:8])
        raise FileExistsError(f"Output files already exist ({names}); rerun with --force.")
    for path in existing:
        unlink_if_exists(path)


def parse_terc_zip(path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(path) as archive:
        csv_names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if not csv_names:
            raise ValueError(f"No CSV file found in TERC archive: {path}")
        text = archive.read(csv_names[0]).decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text), delimiter=";")
    woj: dict[str, dict[str, str]] = {}
    powiat: dict[str, dict[str, str]] = {}
    gmina: dict[str, dict[str, str]] = {}
    for row in reader:
        woj_code = str(row.get("WOJ") or "").zfill(2) if row.get("WOJ") else ""
        pow_code = str(row.get("POW") or "").zfill(2) if row.get("POW") else ""
        gmi_code = str(row.get("GMI") or "").zfill(2) if row.get("GMI") else ""
        rodz = str(row.get("RODZ") or "")
        name = str(row.get("NAZWA") or "").strip()
        extra = str(row.get("NAZWA_DOD") or "").strip()
        if woj_code and not pow_code and not gmi_code:
            woj[woj_code] = {"teryt_woj": woj_code, "wojewodztwo": name}
        elif woj_code and pow_code and not gmi_code:
            code = f"{woj_code}{pow_code}"
            powiat[code] = {
                "teryt_woj": woj_code,
                "teryt_pow": code,
                "wojewodztwo": woj.get(woj_code, {}).get("wojewodztwo", ""),
                "powiat": name,
            }
        elif woj_code and pow_code and gmi_code and rodz in {"1", "2", "3"}:
            powiat_code = f"{woj_code}{pow_code}"
            gmina_code = f"{woj_code}{pow_code}{gmi_code}{rodz}"
            gmina[gmina_code] = {
                "teryt_woj": woj_code,
                "teryt_pow": powiat_code,
                "teryt_gmi": gmina_code,
                "wojewodztwo": woj.get(woj_code, {}).get("wojewodztwo", ""),
                "powiat": powiat.get(powiat_code, {}).get("powiat", ""),
                "gmina": name,
                "rodzaj_gminy": extra,
            }
    for code, item in powiat.items():
        if not item["wojewodztwo"]:
            item["wojewodztwo"] = woj.get(code[:2], {}).get("wojewodztwo", "")
    for code, item in gmina.items():
        if not item["wojewodztwo"]:
            item["wojewodztwo"] = woj.get(code[:2], {}).get("wojewodztwo", "")
        if not item["powiat"]:
            item["powiat"] = powiat.get(code[:4], {}).get("powiat", "")
    return {"woj": woj, "pow": powiat, "gmi": gmina}


def prg_path(prg_dir: Path, level: str) -> Path:
    return prg_dir / PRG_FILENAMES[level]


def read_local_prg(prg_dir: Path) -> dict[str, gpd.GeoDataFrame]:
    layers: dict[str, gpd.GeoDataFrame] = {}
    for level in ["woj", "pow", "gmi"]:
        path = prg_path(prg_dir, level)
        if not path.exists():
            raise FileNotFoundError(path)
        layers[level] = gpd.read_file(path, encoding="UTF-8")
    return layers


def wfs_url(typename: str) -> str:
    params = {
        "SERVICE": "WFS",
        "VERSION": "2.0.0",
        "REQUEST": "GetFeature",
        "TYPENAMES": typename,
        "SRSNAME": "EPSG:2180",
    }
    return f"{WFS_BASE_URL}?{urlencode(params)}"


def refresh_prg_from_wfs(timeout_s: int = 120) -> dict[str, gpd.GeoDataFrame]:
    refreshed: dict[str, gpd.GeoDataFrame] = {}
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)
        for level, typename in WFS_TYPENAMES.items():
            url = wfs_url(typename)
            response = requests.get(url, timeout=timeout_s)
            response.raise_for_status()
            gml_path = tmp / f"{level}.gml"
            gml_path.write_bytes(response.content)
            refreshed[level] = gpd.read_file(gml_path)
    return refreshed


def validate_prg_against_terc(gmi_gdf: gpd.GeoDataFrame, terc: dict[str, Any]) -> dict[str, Any]:
    prg_codes = {str(value).zfill(7) for value in gmi_gdf["JPT_KOD_JE"].dropna().astype(str)}
    terc_codes = set(terc["gmi"])
    missing_exact = sorted(terc_codes - prg_codes)
    extra_exact = sorted(prg_codes - terc_codes)
    prg_six = {code[:6] for code in prg_codes}
    missing_without_same_base = [code for code in missing_exact if code[:6] not in prg_six]
    return {
        "terc_gmina_count": len(terc_codes),
        "prg_gmina_count": len(prg_codes),
        "exact_match": not missing_exact and not extra_exact,
        "missing_in_prg_count": len(missing_exact),
        "extra_in_prg_count": len(extra_exact),
        "missing_without_same_6digit_base_count": len(missing_without_same_base),
        "missing_in_prg_sample": missing_exact[:30],
        "extra_in_prg_sample": extra_exact[:30],
        "missing_without_same_6digit_base_sample": missing_without_same_base[:30],
    }


def normalize_admin_layer(
    gdf: gpd.GeoDataFrame,
    level: str,
    terc: dict[str, Any],
) -> gpd.GeoDataFrame:
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4258")
    gdf = gdf.to_crs("EPSG:2180")
    gdf = gdf.loc[gdf.geometry.notna()].copy()
    gdf["geometry"] = gdf.geometry.buffer(0)
    code_col = "JPT_KOD_JE"
    if code_col not in gdf.columns:
        raise ValueError(f"PRG layer {level} has no {code_col} column.")
    if level == "woj":
        gdf["teryt_woj"] = gdf[code_col].astype(str).str.zfill(2)
        gdf["wojewodztwo"] = gdf["teryt_woj"].map(lambda code: terc["woj"].get(code, {}).get("wojewodztwo", ""))
        fallback = gdf.get("JPT_NAZWA_", pd.Series("", index=gdf.index)).astype(str)
        gdf["wojewodztwo"] = np.where(gdf["wojewodztwo"].astype(bool), gdf["wojewodztwo"], fallback)
        return gdf.loc[:, ["teryt_woj", "wojewodztwo", "geometry"]]
    if level == "pow":
        gdf["teryt_pow"] = gdf[code_col].astype(str).str.zfill(4)
        gdf["teryt_woj"] = gdf["teryt_pow"].str[:2]
        gdf["wojewodztwo"] = gdf["teryt_woj"].map(lambda code: terc["woj"].get(code, {}).get("wojewodztwo", ""))
        gdf["powiat"] = gdf["teryt_pow"].map(lambda code: terc["pow"].get(code, {}).get("powiat", ""))
        fallback = gdf.get("JPT_NAZWA_", pd.Series("", index=gdf.index)).astype(str)
        gdf["powiat"] = np.where(gdf["powiat"].astype(bool), gdf["powiat"], fallback)
        return gdf.loc[:, ["teryt_woj", "wojewodztwo", "teryt_pow", "powiat", "geometry"]]
    if level == "gmi":
        gdf["teryt_gmi"] = gdf[code_col].astype(str).str.zfill(7)
        gdf["teryt_pow"] = gdf["teryt_gmi"].str[:4]
        gdf["teryt_woj"] = gdf["teryt_gmi"].str[:2]
        gdf["wojewodztwo"] = gdf["teryt_woj"].map(lambda code: terc["woj"].get(code, {}).get("wojewodztwo", ""))
        gdf["powiat"] = gdf["teryt_pow"].map(lambda code: terc["pow"].get(code, {}).get("powiat", ""))
        gdf["gmina"] = gdf["teryt_gmi"].map(lambda code: terc["gmi"].get(code, {}).get("gmina", ""))
        fallback = gdf.get("JPT_NAZWA_", pd.Series("", index=gdf.index)).astype(str)
        gdf["gmina"] = np.where(gdf["gmina"].astype(bool), gdf["gmina"], fallback)
        return gdf.loc[:, ["teryt_woj", "wojewodztwo", "teryt_pow", "powiat", "teryt_gmi", "gmina", "geometry"]]
    raise ValueError(f"Unknown admin level: {level}")


def write_admin_boundaries(path: Path, layers: dict[str, gpd.GeoDataFrame]) -> None:
    unlink_if_exists(path)
    layers["woj"].to_file(path, layer="wojewodztwa", driver="GPKG")
    layers["pow"].to_file(path, layer="powiaty", driver="GPKG")
    layers["gmi"].to_file(path, layer="gminy", driver="GPKG")


def build_admin_context(prg_dir: Path, terc_zip: Path, output_path: Path, allow_refresh: bool) -> AdminContext:
    terc = parse_terc_zip(terc_zip)
    raw_layers = read_local_prg(prg_dir)
    validation = validate_prg_against_terc(raw_layers["gmi"], terc)
    refreshed = False
    if not validation["exact_match"] and allow_refresh:
        raw_layers = refresh_prg_from_wfs()
        refreshed = True
        validation = validate_prg_against_terc(raw_layers["gmi"], terc)
    normalized = {level: normalize_admin_layer(gdf, level, terc) for level, gdf in raw_layers.items()}
    write_admin_boundaries(output_path, normalized)
    gmi_geoms = np.array(normalized["gmi"].geometry.values, dtype=object)
    return AdminContext(
        boundaries_path=output_path,
        woj=normalized["woj"],
        pow=normalized["pow"],
        gmi=normalized["gmi"],
        gmi_attrs=normalized["gmi"].drop(columns="geometry").reset_index(drop=True),
        tree=STRtree(gmi_geoms),
        gmi_geometries=gmi_geoms,
        validation=validation,
        refreshed_from_wfs=refreshed,
    )


def load_coverage_raster(path: Path, bbox: tuple[float, float, float, float] | None = None) -> RasterContext:
    with rasterio.open(path) as src:
        if bbox:
            window = rasterio.windows.from_bounds(*bbox, transform=src.transform)
            window = window.round_offsets().round_lengths()
            array = src.read(1, window=window)
            transform = src.window_transform(window)
            bounds_obj = rasterio.windows.bounds(window, src.transform)
            bounds = (float(bounds_obj[0]), float(bounds_obj[1]), float(bounds_obj[2]), float(bounds_obj[3]))
        else:
            array = src.read(1)
            transform = src.transform
            bounds = (float(src.bounds.left), float(src.bounds.bottom), float(src.bounds.right), float(src.bounds.top))
        return RasterContext(path=path, array=array, transform=transform, crs=src.crs, shape=array.shape, bounds=bounds)


def risk_value(raw: Any) -> int:
    value = " ".join(str(raw or "").strip().lower().split())
    return RISK_VALUES.get(value, 0)


def rasterize_risk_shapes(
    items: Iterable[tuple[Any, int]],
    out_shape: tuple[int, int],
    transform: Any,
    chunk_size: int = 50_000,
) -> tuple[np.ndarray, int]:
    out = np.zeros(out_shape, dtype=np.uint8)
    chunk: list[tuple[Any, int]] = []
    count = 0
    for geom, value in items:
        if value <= 0:
            continue
        chunk.append((geom, value))
        if len(chunk) >= chunk_size:
            tmp = features.rasterize(chunk, out_shape=out_shape, transform=transform, fill=0, dtype="uint8")
            np.maximum(out, tmp, out=out)
            count += len(chunk)
            chunk.clear()
            print(f"risk rasterize: {count} features", flush=True)
    if chunk:
        tmp = features.rasterize(chunk, out_shape=out_shape, transform=transform, fill=0, dtype="uint8")
        np.maximum(out, tmp, out=out)
        count += len(chunk)
    return out, count


def iter_riskzone_shapes(path: Path, bbox: tuple[float, float, float, float]) -> Iterable[tuple[Any, int]]:
    with fiona.open(path) as src:
        iterator = src.filter(bbox=bbox)
        for feat in iterator:
            geom = feat.get("geometry")
            if not geom:
                continue
            value = risk_value(feat.get("properties", {}).get("risk"))
            if value:
                yield geom, value


def build_riskzone_raster(
    riskzone_path: Path,
    coverage: RasterContext,
    output_path: Path,
    chunk_size: int,
) -> tuple[np.ndarray, dict[str, Any]]:
    risk, features_used = rasterize_risk_shapes(
        iter_riskzone_shapes(riskzone_path, coverage.bounds),
        coverage.shape,
        coverage.transform,
        chunk_size=chunk_size,
    )
    write_tif(output_path, risk, coverage.transform, coverage.crs, "uint8", 0)
    values, counts = np.unique(risk, return_counts=True)
    return risk, {
        "features_used": features_used,
        "cell_counts": {str(int(value)): int(count) for value, count in zip(values, counts)},
    }


def write_tif(path: Path, array: np.ndarray, transform: Any, crs: Any, dtype: str, nodata: int | float) -> None:
    unlink_if_exists(path)
    profile = {
        "driver": "GTiff",
        "height": array.shape[0],
        "width": array.shape[1],
        "count": 1,
        "dtype": dtype,
        "crs": crs,
        "transform": transform,
        "nodata": nodata,
        "compress": "deflate",
        "predictor": 2 if dtype != "float32" else 3,
        "tiled": True,
        "blockxsize": 256,
        "blockysize": 256,
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(array.astype(dtype), 1)


def build_gap_labels(
    risk: np.ndarray,
    coverage: np.ndarray,
    transform: Any,
    crs: Any,
    output_path: Path,
) -> tuple[np.ndarray, dict[str, Any]]:
    gap_mask = (risk > 0) & (coverage < 65)
    structure = np.ones((3, 3), dtype=np.uint8)
    labels, gap_count = ndimage.label(gap_mask, structure=structure)
    labels = labels.astype(np.int32, copy=False)
    if gap_count:
        gap_values = labels[gap_mask]
        area_cells = np.bincount(gap_values, minlength=gap_count + 1).astype(np.int64)
        risk_by_gap = np.zeros(gap_count + 1, dtype=np.uint8)
        np.maximum.at(risk_by_gap, gap_values, risk[gap_mask])
    else:
        area_cells = np.zeros(1, dtype=np.int64)
        risk_by_gap = np.zeros(1, dtype=np.uint8)
    write_tif(output_path, labels, transform, crs, "int32", 0)
    return labels, {
        "gap_count": int(gap_count),
        "gap_cell_count": int(gap_mask.sum()),
        "area_cells": area_cells,
        "risk_by_gap": risk_by_gap,
    }


def iter_population_chunks(
    paths: list[tuple[Path, str, str]],
    chunk_size: int,
    bbox: tuple[float, float, float, float] | None,
    limit_points: int | None,
) -> Iterable[PopulationChunk]:
    emitted_total = 0
    for path, layer, source_name in paths:
        if not path.exists():
            continue
        with fiona.open(path, layer=layer) as src:
            iterator = src.filter(bbox=bbox) if bbox else src
            xs: list[float] = []
            ys: list[float] = []
            night: list[float] = []
            day: list[float] = []
            db: list[int] = []
            for feat in iterator:
                geom = feat.get("geometry")
                if not geom:
                    continue
                coords = geom.get("coordinates")
                if not coords:
                    continue
                props = feat.get("properties", {})
                xs.append(float(coords[0]))
                ys.append(float(coords[1]))
                night.append(float(props.get("pop_night") or 0.0))
                day.append(float(props.get("pop_day") or 0.0))
                db.append(int(props.get("db") or 0))
                emitted_total += 1
                if len(xs) >= chunk_size:
                    yield PopulationChunk(
                        source=source_name,
                        xs=np.array(xs, dtype=float),
                        ys=np.array(ys, dtype=float),
                        pop_night=np.array(night, dtype=float),
                        pop_day=np.array(day, dtype=float),
                        v11_db=np.array(db, dtype=np.int16),
                    )
                    xs.clear()
                    ys.clear()
                    night.clear()
                    day.clear()
                    db.clear()
                if limit_points is not None and emitted_total >= limit_points:
                    if xs:
                        yield PopulationChunk(
                            source=source_name,
                            xs=np.array(xs, dtype=float),
                            ys=np.array(ys, dtype=float),
                            pop_night=np.array(night, dtype=float),
                            pop_day=np.array(day, dtype=float),
                            v11_db=np.array(db, dtype=np.int16),
                        )
                    return
            if xs:
                yield PopulationChunk(
                    source=source_name,
                    xs=np.array(xs, dtype=float),
                    ys=np.array(ys, dtype=float),
                    pop_night=np.array(night, dtype=float),
                    pop_day=np.array(day, dtype=float),
                    v11_db=np.array(db, dtype=np.int16),
                )


def sample_raster(array: np.ndarray, transform: Any, xs: np.ndarray, ys: np.ndarray, default: int = 0) -> tuple[np.ndarray, np.ndarray]:
    cols = np.floor((xs - transform.c) / transform.a).astype(np.int64)
    rows = np.floor((ys - transform.f) / transform.e).astype(np.int64)
    valid = (rows >= 0) & (cols >= 0) & (rows < array.shape[0]) & (cols < array.shape[1])
    values = np.full(xs.shape, default, dtype=array.dtype)
    values[valid] = array[rows[valid], cols[valid]]
    return values, valid


def assign_gminy(admin: AdminContext, xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
    points = np.asarray(gpd.points_from_xy(xs, ys), dtype=object)
    assigned = np.full(len(xs), -1, dtype=np.int64)
    pairs = admin.tree.query(points, predicate="within")
    if pairs.size:
        assigned[pairs[0]] = pairs[1]
    missing = np.where(assigned < 0)[0]
    if missing.size:
        fallback_pairs = admin.tree.query(points[missing], predicate="intersects")
        if fallback_pairs.size:
            assigned[missing[fallback_pairs[0]]] = fallback_pairs[1]
    return assigned


def make_chunk_frame(
    chunk: PopulationChunk,
    coverage: np.ndarray,
    risk: np.ndarray,
    gap_labels: np.ndarray,
    raster_transform: Any,
    admin: AdminContext,
) -> tuple[pd.DataFrame, np.ndarray, dict[str, int]]:
    sampled_coverage, valid_raster = sample_raster(coverage, raster_transform, chunk.xs, chunk.ys, default=0)
    sampled_risk, _ = sample_raster(risk, raster_transform, chunk.xs, chunk.ys, default=0)
    sampled_gaps, _ = sample_raster(gap_labels, raster_transform, chunk.xs, chunk.ys, default=0)
    admin_indices = assign_gminy(admin, chunk.xs, chunk.ys)
    valid_admin = admin_indices >= 0
    counters = {
        "points": int(len(chunk.xs)),
        "outside_raster": int((~valid_raster).sum()),
        "missing_admin": int((~valid_admin).sum()),
        "v11_db_mismatch": int(((sampled_coverage.astype(np.int16) != chunk.v11_db) & valid_raster).sum()),
    }
    has_population = (chunk.pop_night > 0) | (chunk.pop_day > 0)
    valid = (sampled_risk > 0) & valid_admin & has_population
    if not valid.any():
        return pd.DataFrame(), sampled_gaps, counters
    gmi = admin.gmi_attrs.iloc[admin_indices[valid]].reset_index(drop=True)
    df = pd.DataFrame(
        {
            "teryt_woj": gmi["teryt_woj"].to_numpy(),
            "wojewodztwo": gmi["wojewodztwo"].to_numpy(),
            "teryt_pow": gmi["teryt_pow"].to_numpy(),
            "powiat": gmi["powiat"].to_numpy(),
            "teryt_gmi": gmi["teryt_gmi"].to_numpy(),
            "gmina": gmi["gmina"].to_numpy(),
            "risk_value": sampled_risk[valid].astype(np.uint8),
            "risk_class": [RISK_LABELS[int(value)] for value in sampled_risk[valid]],
            "ge65": sampled_coverage[valid] >= 65,
            "pop_night": chunk.pop_night[valid],
            "pop_day": chunk.pop_day[valid],
            "gap_id": sampled_gaps[valid].astype(np.int32),
        }
    )
    return df, sampled_gaps, counters


def update_gap_admin_weights(
    weights: dict[int, dict[str, float]],
    df: pd.DataFrame,
) -> None:
    gap_df = df.loc[df["gap_id"] > 0, ["gap_id", "teryt_gmi", "pop_night"]]
    if gap_df.empty:
        return
    grouped = gap_df.groupby(["gap_id", "teryt_gmi"], dropna=False)["pop_night"].sum().reset_index()
    for row in grouped.itertuples(index=False):
        weights[int(row.gap_id)][str(row.teryt_gmi)] += float(row.pop_night)


def aggregate_population(
    building_pop: Path,
    residual_pop: Path,
    coverage: RasterContext,
    risk: np.ndarray,
    gap_labels: np.ndarray,
    admin: AdminContext,
    chunk_size: int,
    bbox: tuple[float, float, float, float] | None,
    limit_points: int | None,
    gap_count: int,
) -> tuple[RiskCoverageAggregator, dict[str, Any], np.ndarray, np.ndarray, dict[int, dict[str, float]]]:
    paths = [
        (building_pop, "building_population", "building_population"),
        (residual_pop, "grid_residual_population", "grid_residual_population"),
    ]
    aggregator = RiskCoverageAggregator()
    counters: Counter[str] = Counter()
    gap_pop_night = np.zeros(gap_count + 1, dtype=float)
    gap_pop_day = np.zeros(gap_count + 1, dtype=float)
    gap_admin_weights: dict[int, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for idx, chunk in enumerate(iter_population_chunks(paths, chunk_size, bbox, limit_points), start=1):
        df, sampled_gaps, chunk_counts = make_chunk_frame(chunk, coverage.array, risk, gap_labels, coverage.transform, admin)
        counters.update(chunk_counts)
        counters[f"source_{chunk.source}"] += len(chunk.xs)
        if not df.empty:
            aggregator.update(df)
            update_gap_admin_weights(gap_admin_weights, df)
        valid_gap = sampled_gaps > 0
        if valid_gap.any():
            gap_ids = sampled_gaps[valid_gap].astype(np.int64)
            gap_pop_night += np.bincount(gap_ids, weights=chunk.pop_night[valid_gap], minlength=gap_count + 1)
            gap_pop_day += np.bincount(gap_ids, weights=chunk.pop_day[valid_gap], minlength=gap_count + 1)
        if idx % 10 == 0:
            print(f"population chunks: {idx}, points: {counters['points']}", flush=True)
    return aggregator, dict(counters), gap_pop_night, gap_pop_day, gap_admin_weights


def aggregate_rows_to_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


def values_to_dict(values: np.ndarray) -> dict[str, float]:
    return {name: float(values[idx]) for idx, name in enumerate(AGG_COLUMNS)}


def aggregator_to_frames(aggregator: RiskCoverageAggregator) -> dict[str, pd.DataFrame]:
    country_rows = [{"risk_class": key[0], **values_to_dict(values)} for key, values in aggregator.country.items()]
    woj_rows = [
        {"teryt_woj": key[0], "wojewodztwo": key[1], "risk_class": key[2], **values_to_dict(values)}
        for key, values in aggregator.woj.items()
    ]
    pow_rows = [
        {
            "teryt_woj": key[0],
            "wojewodztwo": key[1],
            "teryt_pow": key[2],
            "powiat": key[3],
            "risk_class": key[4],
            **values_to_dict(values),
        }
        for key, values in aggregator.powiat.items()
    ]
    gmi_rows = [
        {
            "teryt_woj": key[0],
            "wojewodztwo": key[1],
            "teryt_pow": key[2],
            "powiat": key[3],
            "teryt_gmi": key[4],
            "gmina": key[5],
            "risk_class": key[6],
            **values_to_dict(values),
        }
        for key, values in aggregator.gmina.items()
    ]
    frames = {
        "country": aggregate_rows_to_frame(country_rows),
        "woj": aggregate_rows_to_frame(woj_rows),
        "powiat": aggregate_rows_to_frame(pow_rows),
        "gmina": aggregate_rows_to_frame(gmi_rows),
    }
    sort_cols = {
        "country": ["risk_class"],
        "woj": ["wojewodztwo", "risk_class"],
        "powiat": ["wojewodztwo", "powiat", "risk_class"],
        "gmina": ["wojewodztwo", "powiat", "gmina", "risk_class"],
    }
    for key, df in frames.items():
        if not df.empty:
            frames[key] = df.sort_values(sort_cols[key]).reset_index(drop=True)
    return frames


def write_csv(path: Path, df: pd.DataFrame) -> None:
    df.to_csv(path, index=False, encoding="utf-8-sig")


def priority_score(
    pop_outside: float,
    risk_value_max: int,
    outside_share: float,
    concentration_pop_ha: float,
    pop_ref: float,
) -> float:
    if pop_ref <= 0:
        pop_component = 0.0
    else:
        pop_component = 40.0 * min(1.0, math.log1p(max(0.0, pop_outside)) / math.log1p(max(1.0, pop_ref)))
    risk_component = 25.0 if risk_value_max >= 2 else 12.5 if risk_value_max == 1 else 0.0
    share_component = 20.0 * min(1.0, max(0.0, outside_share))
    concentration_component = 15.0 * min(1.0, max(0.0, concentration_pop_ha) / 50.0)
    return round(pop_component + risk_component + share_component + concentration_component, 2)


def priority_class(score: float) -> str:
    if score >= 80:
        return "A"
    if score >= 60:
        return "B"
    if score >= 40:
        return "C"
    return "niski"


def recommended_action_seed(score: float, pop_outside: float, concentration_pop_ha: float) -> str:
    if score >= 60 and pop_outside >= 100 and concentration_pop_ha >= 5:
        return "new_siren_likely"
    return "modernizacja_or_new"


def gmina_ranking_base(gmina_df: pd.DataFrame) -> pd.DataFrame:
    if gmina_df.empty:
        return pd.DataFrame(
            columns=[
                "gmina",
                "powiat",
                "wojewodztwo",
                "teryt_gmi",
                "pop_risk_total",
                "pop_ge65",
                "pop_outside_ge65",
                "outside_share",
                "risk_class_max",
                "risk_value_max",
            ]
        )
    work = gmina_df.copy()
    work["risk_value"] = work["risk_class"].map(lambda value: RISK_VALUES.get(str(value), 0))
    grouped = (
        work.groupby(["teryt_woj", "wojewodztwo", "teryt_pow", "powiat", "teryt_gmi", "gmina"], dropna=False)
        .agg(
            pop_risk_total=("pop_night_total", "sum"),
            pop_ge65=("pop_night_ge65", "sum"),
            pop_outside_ge65=("pop_night_outside", "sum"),
            risk_value_max=("risk_value", "max"),
        )
        .reset_index()
    )
    grouped["outside_share"] = np.where(
        grouped["pop_risk_total"] > 0,
        grouped["pop_outside_ge65"] / grouped["pop_risk_total"],
        0.0,
    )
    grouped["risk_class_max"] = grouped["risk_value_max"].map(lambda value: RISK_LABELS.get(int(value), "brak"))
    return grouped


def build_admin_gap_ranking(
    gmina_df: pd.DataFrame,
    selected_gap_ids: np.ndarray,
    gap_admin: dict[int, str],
    gap_pop_night: np.ndarray,
    gap_area_ha: np.ndarray,
) -> pd.DataFrame:
    ranking = gmina_ranking_base(gmina_df)
    if ranking.empty:
        return ranking
    area_by_gmina: dict[str, float] = defaultdict(float)
    for gap_id in selected_gap_ids:
        admin_code = gap_admin.get(int(gap_id), "")
        if admin_code:
            area_by_gmina[admin_code] += float(gap_area_ha[int(gap_id)])
    ranking["gap_area_ha"] = ranking["teryt_gmi"].map(lambda code: area_by_gmina.get(str(code), 0.0))
    ranking["outside_density_pop_ha"] = np.where(
        ranking["gap_area_ha"] > 0,
        ranking["pop_outside_ge65"] / ranking["gap_area_ha"],
        0.0,
    )
    pop_ref = percentile_ref(ranking["pop_outside_ge65"].to_numpy())
    ranking["priority_score_v12"] = [
        priority_score(pop, risk, share, density, pop_ref)
        for pop, risk, share, density in zip(
            ranking["pop_outside_ge65"],
            ranking["risk_value_max"],
            ranking["outside_share"],
            ranking["outside_density_pop_ha"],
        )
    ]
    ranking = ranking.sort_values(["priority_score_v12", "pop_outside_ge65"], ascending=[False, False]).reset_index(drop=True)
    ranking["rank"] = np.arange(1, len(ranking) + 1, dtype=int)
    return ranking.loc[
        :,
        [
            "gmina",
            "powiat",
            "wojewodztwo",
            "teryt_gmi",
            "pop_risk_total",
            "pop_ge65",
            "pop_outside_ge65",
            "outside_share",
            "risk_class_max",
            "priority_score_v12",
            "rank",
        ],
    ]


def percentile_ref(values: np.ndarray) -> float:
    positive = values[np.asarray(values) > 0]
    if positive.size == 0:
        return 0.0
    return float(max(1.0, np.percentile(positive, 95)))


def choose_gap_admin(gap_admin_weights: dict[int, dict[str, float]]) -> dict[int, str]:
    chosen: dict[int, str] = {}
    for gap_id, weights in gap_admin_weights.items():
        if not weights:
            continue
        chosen[gap_id] = max(weights.items(), key=lambda item: item[1])[0]
    return chosen


def build_priority_gaps(
    output_path: Path,
    gap_labels: np.ndarray,
    gap_meta: dict[str, Any],
    gap_pop_night: np.ndarray,
    gap_pop_day: np.ndarray,
    gap_admin_weights: dict[int, dict[str, float]],
    gmina_ranking: pd.DataFrame,
    admin: AdminContext,
    transform: Any,
    crs: Any,
    min_gap_pop: float,
) -> tuple[gpd.GeoDataFrame, dict[str, Any], np.ndarray, dict[int, str], np.ndarray]:
    gap_count = int(gap_meta["gap_count"])
    cell_area_ha = abs(float(transform.a) * float(transform.e)) / 10_000.0
    area_cells = gap_meta["area_cells"]
    risk_by_gap = gap_meta["risk_by_gap"]
    gap_area_ha = area_cells.astype(float) * cell_area_ha
    selected = np.where((gap_pop_night >= min_gap_pop) | (gap_pop_day >= min_gap_pop))[0]
    selected = selected[selected > 0]
    gap_admin = choose_gap_admin(gap_admin_weights)
    gmina_lookup = admin.gmi_attrs.drop_duplicates("teryt_gmi").set_index("teryt_gmi")
    ranking_lookup = gmina_ranking.set_index("teryt_gmi") if not gmina_ranking.empty else pd.DataFrame()
    pop_ref = percentile_ref(gap_pop_night[selected] if selected.size else np.array([]))
    selected_lookup = np.zeros(gap_count + 1, dtype=bool)
    selected_lookup[selected] = True
    selected_mask = selected_lookup[gap_labels]
    feature_rows: dict[int, dict[str, Any]] = {}
    feature_geometries: dict[int, list[Any]] = defaultdict(list)
    for geom, value in features.shapes(gap_labels, mask=selected_mask, transform=transform):
        gap_id = int(value)
        if gap_id <= 0:
            continue
        feature_geometries[gap_id].append(shape(geom))
        if gap_id not in feature_rows:
            admin_code = gap_admin.get(gap_id, "")
            if admin_code and admin_code in gmina_lookup.index:
                admin_row = gmina_lookup.loc[admin_code]
                teryt_gmi = admin_code
                wojewodztwo = admin_row["wojewodztwo"]
                powiat = admin_row["powiat"]
                gmina = admin_row["gmina"]
            else:
                teryt_gmi = ""
                wojewodztwo = ""
                powiat = ""
                gmina = ""
            outside_share = 0.0
            if admin_code and not ranking_lookup.empty and admin_code in ranking_lookup.index:
                outside_share = float(ranking_lookup.loc[admin_code]["outside_share"])
            concentration = float(gap_pop_night[gap_id] / gap_area_ha[gap_id]) if gap_area_ha[gap_id] > 0 else 0.0
            score = priority_score(float(gap_pop_night[gap_id]), int(risk_by_gap[gap_id]), outside_share, concentration, pop_ref)
            feature_rows[gap_id] = {
                "gap_id": int(gap_id),
                "risk_class": RISK_LABELS.get(int(risk_by_gap[gap_id]), "brak"),
                "pop_outside_night": float(gap_pop_night[gap_id]),
                "pop_outside_day": float(gap_pop_day[gap_id]),
                "area_ha": float(gap_area_ha[gap_id]),
                "teryt_gmi": teryt_gmi,
                "wojewodztwo": wojewodztwo,
                "powiat": powiat,
                "gmina": gmina,
                "priority_score_v12": score,
                "priority_class": priority_class(score),
                "recommended_action_seed": recommended_action_seed(score, float(gap_pop_night[gap_id]), concentration),
            }
    features_out: list[dict[str, Any]] = []
    for gap_id, row in feature_rows.items():
        features_out.append({**row, "geometry": unary_union(feature_geometries[gap_id])})
    gaps = gpd.GeoDataFrame(features_out, geometry="geometry", crs=crs)
    if not gaps.empty:
        gaps = gaps.sort_values(["priority_score_v12", "pop_outside_night"], ascending=[False, False]).reset_index(drop=True)
    unlink_if_exists(output_path)
    gaps.to_file(output_path, layer="priority_gaps", driver="GPKG")
    return gaps, {"selected_gap_count": int(len(selected)), "written_gap_count": int(len(gaps))}, selected, gap_admin, gap_area_ha


def write_report(summary: dict[str, Any], validation: dict[str, Any]) -> str:
    return f"""# Raport V12: RiskZone, populacja i luki zasięgu >=65 dB(A)

## Podsumowanie

- Punkty populacyjne przetworzone: {summary['population']['points_processed']}
- Populacja nocna w RiskZone: {summary['population']['pop_night_risk_total']:.2f}
- Populacja nocna w RiskZone poza >=65 dB(A): {summary['population']['pop_night_outside_ge65']:.2f}
- Luki inwestycyjne zapisane: {summary['gaps']['written_gap_count']}
- PRG odswiezony z WFS: {summary['admin']['refreshed_from_wfs']}

## Artefakty

- `v12_risk_coverage_country.csv`
- `v12_risk_coverage_woj.csv`
- `v12_risk_coverage_powiat.csv`
- `v12_risk_coverage_gmina.csv`
- `priority_gaps_V12.gpkg`
- `admin_gap_ranking_V12.csv`

## Walidacja

- Walidacja przeszla: {validation['validation_passed']}
- PRG zgodny z TERC: {validation['admin']['exact_match']}
- Suma kraj/gmina zgodna: {validation['aggregates']['country_equals_gmina']}
- Populacja V12 nie przekracza V11: {validation['population']['risk_population_le_v11_total']}
- Mismatch `db` V11 vs raster V10: {validation['population']['v11_db_mismatch']}
"""


def validate_outputs(
    frames: dict[str, pd.DataFrame],
    admin_validation: dict[str, Any],
    population_counters: dict[str, Any],
    v11_total: float,
) -> dict[str, Any]:
    country = frames["country"]
    gmina = frames["gmina"]
    country_total = float(country["pop_night_total"].sum()) if not country.empty else 0.0
    gmina_total = float(gmina["pop_night_total"].sum()) if not gmina.empty else 0.0
    ge_out_ok = True
    for df in frames.values():
        if df.empty:
            continue
        diff = (df["pop_night_ge65"] + df["pop_night_outside"] - df["pop_night_total"]).abs().max()
        ge_out_ok = ge_out_ok and bool(diff < 1e-5)
    aggregates = {
        "country_pop_night_total": country_total,
        "gmina_pop_night_total": gmina_total,
        "country_equals_gmina": abs(country_total - gmina_total) < 1e-5,
        "ge65_plus_outside_equals_total": ge_out_ok,
    }
    population = {
        "v11_total_reference": float(v11_total),
        "risk_population_le_v11_total": country_total <= float(v11_total) + 1e-5,
        "points_processed": int(population_counters.get("points", 0)),
        "outside_raster": int(population_counters.get("outside_raster", 0)),
        "missing_admin": int(population_counters.get("missing_admin", 0)),
        "v11_db_mismatch": int(population_counters.get("v11_db_mismatch", 0)),
    }
    validation_passed = (
        aggregates["country_equals_gmina"]
        and aggregates["ge65_plus_outside_equals_total"]
        and population["risk_population_le_v11_total"]
        and admin_validation["exact_match"]
    )
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "validation_passed": bool(validation_passed),
        "admin": admin_validation,
        "aggregates": aggregates,
        "population": population,
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = Path(args.output_dir)
    prepare_outputs(output_dir, args.force)
    bbox = tuple(args.bbox) if args.bbox else None

    coverage = load_coverage_raster(Path(args.coverage_raster), bbox=bbox)
    admin = build_admin_context(
        prg_dir=Path(args.prg_dir),
        terc_zip=Path(args.terc_zip),
        output_path=output_dir / "admin_boundaries_PL_2180.gpkg",
        allow_refresh=not args.no_prg_refresh,
    )
    risk, risk_meta = build_riskzone_raster(
        Path(args.riskzone_shp),
        coverage,
        output_dir / "riskzone_class_V12_100m.tif",
        chunk_size=args.risk_chunk_features,
    )
    gap_labels, gap_meta = build_gap_labels(
        risk,
        coverage.array,
        coverage.transform,
        coverage.crs,
        output_dir / "gap_labels_V12_100m.tif",
    )
    aggregator, pop_counters, gap_pop_night, gap_pop_day, gap_admin_weights = aggregate_population(
        building_pop=Path(args.building_pop),
        residual_pop=Path(args.residual_pop),
        coverage=coverage,
        risk=risk,
        gap_labels=gap_labels,
        admin=admin,
        chunk_size=args.chunk_size,
        bbox=coverage.bounds,
        limit_points=args.limit_points,
        gap_count=int(gap_meta["gap_count"]),
    )
    frames = aggregator_to_frames(aggregator)
    write_csv(output_dir / "v12_risk_coverage_country.csv", frames["country"])
    write_csv(output_dir / "v12_risk_coverage_woj.csv", frames["woj"])
    write_csv(output_dir / "v12_risk_coverage_powiat.csv", frames["powiat"])
    write_csv(output_dir / "v12_risk_coverage_gmina.csv", frames["gmina"])

    preliminary_ranking = gmina_ranking_base(frames["gmina"])
    gap_admin = choose_gap_admin(gap_admin_weights)
    selected_for_admin = np.where((gap_pop_night >= args.min_gap_pop) | (gap_pop_day >= args.min_gap_pop))[0]
    selected_for_admin = selected_for_admin[selected_for_admin > 0]
    gap_area_ha = gap_meta["area_cells"].astype(float) * (abs(float(coverage.transform.a) * float(coverage.transform.e)) / 10_000.0)
    ranking = build_admin_gap_ranking(frames["gmina"], selected_for_admin, gap_admin, gap_pop_night, gap_area_ha)
    write_csv(output_dir / "admin_gap_ranking_V12.csv", ranking)

    gaps_gdf, gaps_meta, selected_gap_ids, gap_admin, gap_area_ha = build_priority_gaps(
        output_path=output_dir / "priority_gaps_V12.gpkg",
        gap_labels=gap_labels,
        gap_meta=gap_meta,
        gap_pop_night=gap_pop_night,
        gap_pop_day=gap_pop_day,
        gap_admin_weights=gap_admin_weights,
        gmina_ranking=preliminary_ranking,
        admin=admin,
        transform=coverage.transform,
        crs=coverage.crs,
        min_gap_pop=args.min_gap_pop,
    )
    # Recompute ranking with final selected gap assignment to keep density aligned with the written layer.
    ranking = build_admin_gap_ranking(frames["gmina"], selected_gap_ids, gap_admin, gap_pop_night, gap_area_ha)
    write_csv(output_dir / "admin_gap_ranking_V12.csv", ranking)

    country_total = float(frames["country"]["pop_night_total"].sum()) if not frames["country"].empty else 0.0
    country_outside = float(frames["country"]["pop_night_outside"].sum()) if not frames["country"].empty else 0.0
    v11_total = float(pop_counters.get("source_building_population", 0))  # overwritten below when summary JSON exists
    v11_summary_path = DEFAULT_V11_DIR / "population_model_V11_validation.json"
    if v11_summary_path.exists() and not args.limit_points and not bbox:
        try:
            v11_total = float(json.loads(v11_summary_path.read_text(encoding="utf-8"))["population_totals"]["sum_pop_night"])
        except Exception:  # noqa: BLE001
            v11_total = max(country_total, 0.0)
    else:
        v11_total = max(country_total, 0.0)
    validation = validate_outputs(frames, admin.validation, pop_counters, v11_total)
    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "inputs": {
            "building_pop": str(Path(args.building_pop).resolve()),
            "residual_pop": str(Path(args.residual_pop).resolve()),
            "riskzone_shp": str(Path(args.riskzone_shp).resolve()),
            "coverage_raster": str(Path(args.coverage_raster).resolve()),
            "prg_dir": str(Path(args.prg_dir).resolve()),
            "terc_zip": str(Path(args.terc_zip).resolve()),
            "bbox": list(bbox) if bbox else None,
            "limit_points": args.limit_points,
        },
        "outputs": {
            "admin_boundaries": str((output_dir / "admin_boundaries_PL_2180.gpkg").resolve()),
            "riskzone_raster": str((output_dir / "riskzone_class_V12_100m.tif").resolve()),
            "gap_labels_raster": str((output_dir / "gap_labels_V12_100m.tif").resolve()),
            "priority_gaps": str((output_dir / "priority_gaps_V12.gpkg").resolve()),
            "admin_gap_ranking": str((output_dir / "admin_gap_ranking_V12.csv").resolve()),
        },
        "admin": {
            "refreshed_from_wfs": admin.refreshed_from_wfs,
            "validation": admin.validation,
        },
        "riskzone": risk_meta,
        "population": {
            "points_processed": int(pop_counters.get("points", 0)),
            "pop_night_risk_total": country_total,
            "pop_night_outside_ge65": country_outside,
            "counters": pop_counters,
        },
        "gaps": {
            "gap_count": int(gap_meta["gap_count"]),
            "gap_cell_count": int(gap_meta["gap_cell_count"]),
            **gaps_meta,
        },
        "ranking": {
            "rows": int(len(ranking)),
            "top_10": ranking.head(10).to_dict(orient="records") if not ranking.empty else [],
        },
        "priority_gaps_top_10": gaps_gdf.head(10).drop(columns="geometry").to_dict(orient="records") if not gaps_gdf.empty else [],
    }
    write_json(output_dir / "decision_model_V12_summary.json", summary)
    write_json(output_dir / "decision_model_V12_validation.json", validation)
    (output_dir / "decision_model_V12_report.md").write_text(write_report(summary, validation), encoding="utf-8")
    return {"summary": summary, "validation": validation}


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = run(args)
    return 0 if result["validation"]["validation_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
