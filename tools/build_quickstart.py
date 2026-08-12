from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from pathlib import Path

import geopandas as gpd
import pandas as pd
from rasterio.shutil import copy as copy_raster

from soia.data import read_csv
from soia.release import sha256_file


def _write_table(source: Path, csv_output: Path, parquet_output: Path) -> dict[str, object]:
    rows = read_csv(source)
    frame = pd.DataFrame(rows)
    csv_output.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(csv_output, index=False, encoding="utf-8")
    frame.to_parquet(parquet_output, index=False, compression="zstd")
    return {"name": csv_output.stem, "rows": len(frame), "columns": list(frame.columns)}


def _copy_layer(source: Path, source_layer: str, destination: Path, destination_layer: str) -> int:
    frame = gpd.read_file(source, layer=source_layer, engine="pyogrio")
    frame.to_file(destination, layer=destination_layer, driver="GPKG", engine="pyogrio", append=True)
    return len(frame)


def _ensure_spatial_indexes(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        layers = [row[0] for row in connection.execute("SELECT table_name FROM gpkg_geometry_columns")]
        for layer in layers:
            try:
                connection.execute("SELECT gpkgAddSpatialIndex(?, 'geom')", (layer,))
            except sqlite3.OperationalError:
                # Pyogrio/GDAL zazwyczaj tworzy RTree podczas zapisu. Wbudowany SQLite
                # nie musi udostępniać funkcji rozszerzenia SpatiaLite.
                pass


def build_quickstart(source_root: Path, output: Path, *, force: bool = False) -> dict[str, object]:
    if output.exists():
        if not force:
            raise FileExistsError(output)
        shutil.rmtree(output)
    output.mkdir(parents=True)
    analysis = source_root / "analysis-output/inwentaryzacja-syren-2026-05-05"
    tables: list[dict[str, object]] = []
    inventory = analysis / "inwentaryzacja-syren-2026-05-05.normalized.analysis.final.V9.csv"
    tables.append(_write_table(inventory, output / "tables/inventory-v9.csv", output / "tables/inventory-v9.parquet"))
    for source in sorted((source_root / "ANALIZA_DOC/zalaczniki/tabele").glob("*.csv")):
        tables.append(_write_table(source, output / "tables" / source.name, output / "tables" / f"{source.stem}.parquet"))
    for source in sorted((source_root / "ANALIZA_DOC/zalaczniki/csv").glob("A*.csv")):
        tables.append(_write_table(source, output / "tables" / source.name, output / "tables" / f"{source.stem}.parquet"))

    gpkg = output / "layers/soia-quickstart.gpkg"
    gpkg.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(analysis / "decision_model_V12/admin_boundaries_PL_2180.gpkg", gpkg)
    layer_counts: dict[str, int] = {}
    layer_counts["coverage_ranges"] = _copy_layer(
        analysis / "atdi_sound_model_V10_osm/zasiegi_OSM_65_70_75_nonoverlap_V10.shp",
        "zasiegi_OSM_65_70_75_nonoverlap_V10", gpkg, "coverage_ranges",
    )
    layer_counts["priority_gaps"] = _copy_layer(
        analysis / "decision_model_V12/priority_gaps_V12.gpkg", "priority_gaps", gpkg, "priority_gaps",
    )
    layer_counts["candidates"] = _copy_layer(
        analysis / "report_SOIA_V13/candidate_mount_sites_ranked_V13.gpkg",
        "candidate_mount_sites_ranked", gpkg, "candidates",
    )
    layer_counts["sensitive_objects"] = _copy_layer(
        analysis / "report_SOIA_V13/sensitive_objects_PL_2180.gpkg",
        "sensitive_objects", gpkg, "sensitive_objects",
    )
    _ensure_spatial_indexes(gpkg)

    raster_dir = output / "rasters"
    raster_dir.mkdir()
    raster_sources = {
        "coverage-class-v10.cog.tif": "coverage_class_osm_V10_100m.tif",
        "sound-level-v10.cog.tif": "sound_level_osm_V10_100m.tif",
        "winner-siren-v10.cog.tif": "winner_siren_osm_V10_100m.tif",
    }
    for target_name, source_name in raster_sources.items():
        copy_raster(
            analysis / "atdi_sound_model_V10_osm" / source_name,
            raster_dir / target_name,
            driver="COG", compress="DEFLATE", blocksize=512,
            overview_resampling="nearest",
        )

    manifest = {
        "schema_version": "1.0.0", "release": "2026.05",
        "tables": tables, "layers": layer_counts,
        "files": [
            {"path": path.relative_to(output).as_posix(), "size_bytes": path.stat().st_size, "sha256": sha256_file(path)}
            for path in sorted(output.rglob("*")) if path.is_file()
        ],
    }
    (output / "quickstart-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Buduje zoptymalizowany snapshot quickstart SOIA.")
    parser.add_argument("--source-root", type=Path, default=Path(".."))
    parser.add_argument("--output", type=Path, default=Path("data/quickstart-2026.05"))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    result = build_quickstart(args.source_root.resolve(), args.output.resolve(), force=args.force)
    print(json.dumps({"tables": len(result["tables"]), "layers": result["layers"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
