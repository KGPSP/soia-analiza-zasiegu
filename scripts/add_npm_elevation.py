#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import csv
import io
import json
import math
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import srtm


ELEVATION_SOURCE = "srtm.py"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Dodaje kolumne npm_m na podstawie lat/lon przez lokalne dane SRTM."
    )
    parser.add_argument("--input", required=True, help="Wejsciowy plik final CSV.")
    parser.add_argument("--output", required=True, help="Wyjsciowy plik CSV z kolumna npm_m.")
    parser.add_argument("--backup", required=True, help="Kopia pliku sprzed zmian.")
    parser.add_argument("--mapping-csv", required=True, help="CSV mapujacy lat/lon na npm_m.")
    parser.add_argument("--summary-json", required=True, help="Podsumowanie wykonania.")
    parser.add_argument(
        "--cache-dir",
        help="Katalog cache dla danych SRTM. Domyslnie: <katalog wyjsciowy>/.srtm-cache",
    )
    return parser.parse_args(argv)


def load_srtm_data(cache_dir: Path) -> Any:
    cache_dir.mkdir(parents=True, exist_ok=True)
    with contextlib.redirect_stdout(io.StringIO()):
        return srtm.get_data(local_cache_dir=str(cache_dir))


def get_srtm_elevation(srtm_data: Any, latitude: float, longitude: float) -> float:
    with contextlib.redirect_stdout(io.StringIO()):
        elevation = srtm_data.get_elevation(latitude, longitude)
    if elevation is None:
        return math.nan
    return float(elevation)


def resolve_lon_column(df: pd.DataFrame) -> str:
    if "lon" in df.columns:
        return "lon"
    if "long" in df.columns:
        return "long"
    raise ValueError("Brak wymaganej kolumny lon/long.")


def add_npm_column(df: pd.DataFrame, cache_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    if "lat" not in df.columns:
        raise ValueError("Brak wymaganej kolumny lat.")
    lon_column = resolve_lon_column(df)

    work_df = df.copy()
    work_df["_lat_num"] = pd.to_numeric(work_df["lat"], errors="coerce")
    work_df["_lon_num"] = pd.to_numeric(work_df[lon_column], errors="coerce")

    if work_df["_lat_num"].isna().any() or work_df["_lon_num"].isna().any():
        raise ValueError("Plik zawiera nieprawidlowe wspolrzedne lat/lon.")

    unique_coords = work_df.loc[:, ["_lat_num", "_lon_num"]].drop_duplicates().reset_index(drop=True)
    srtm_data = load_srtm_data(cache_dir)
    unique_coords["npm_m"] = unique_coords.apply(
        lambda row: get_srtm_elevation(srtm_data, float(row["_lat_num"]), float(row["_lon_num"])),
        axis=1,
    )
    unique_coords["npm_m"] = unique_coords["npm_m"].round(1)

    merged = work_df.merge(unique_coords, on=["_lat_num", "_lon_num"], how="left", validate="many_to_one")
    if merged["npm_m"].isna().any():
        raise ValueError("Po mergu zostaly rekordy bez wartosci npm_m.")
    merged = merged.drop(columns=["_lat_num", "_lon_num"])

    mapping_df = unique_coords.rename(columns={"_lat_num": "lat", "_lon_num": "lon"})

    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "elevation_source": ELEVATION_SOURCE,
        "cache_dir": str(cache_dir),
        "records": int(len(merged)),
        "unique_coordinates": int(len(mapping_df)),
        "npm_min_m": float(mapping_df["npm_m"].min()),
        "npm_max_m": float(mapping_df["npm_m"].max()),
        "npm_mean_m": round(float(mapping_df["npm_m"].mean()), 4),
    }
    return merged, mapping_df, summary


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    input_path = Path(args.input).resolve()
    output_path = Path(args.output).resolve()
    backup_path = Path(args.backup).resolve()
    mapping_path = Path(args.mapping_csv).resolve()
    summary_path = Path(args.summary_json).resolve()
    cache_path = (
        Path(args.cache_dir).resolve()
        if args.cache_dir
        else output_path.parent.joinpath(".srtm-cache").resolve()
    )

    input_df = pd.read_csv(input_path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    enriched_df, mapping_df, summary = add_npm_column(input_df, cache_path)

    backup_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(input_path, backup_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    enriched_df.to_csv(output_path, index=False, encoding="utf-8-sig", quoting=csv.QUOTE_MINIMAL)
    mapping_df.to_csv(mapping_path, index=False, encoding="utf-8")
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Zapisano plik z npm: {output_path}")
    print(f"Zapisano backup: {backup_path}")
    print(f"Zapisano mapowanie: {mapping_path}")
    print(f"Zapisano podsumowanie: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
