#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


POWER_WEIGHT_STEP = 100.0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Imputacja zbyt niskich wysokosci srednia wazona po rodzaju syreny i mocy."
    )
    parser.add_argument("--input", required=True, help="Wejsciowy plik normalized.analysis.csv")
    parser.add_argument("--output", required=True, help="Wyjsciowy plik CSV po imputacji")
    parser.add_argument("--backup", required=True, help="Kopia pliku sprzed zmian")
    parser.add_argument("--report-csv", required=True, help="Raport CSV z dokonanymi zmianami")
    parser.add_argument("--summary-json", required=True, help="Podsumowanie JSON")
    parser.add_argument(
        "--source-max-height",
        type=float,
        default=0.0,
        help="Imputuj rekordy z wysokoscia <= tej wartosci. Domyslnie 0.",
    )
    parser.add_argument(
        "--reference-min-height",
        type=float,
        default=0.0,
        help="Rekordy referencyjne musza miec wysokosc > tej wartosci. Domyslnie 0.",
    )
    return parser.parse_args(argv)


def weighted_height_for_row(target_row: pd.Series, candidates: pd.DataFrame) -> tuple[float, int, int]:
    same_type = candidates[candidates["rodzaj_syreny"] == target_row["rodzaj_syreny"]].copy()
    if same_type.empty:
        raise ValueError(f"Brak rekordow referencyjnych dla rodzaju syreny: {target_row['rodzaj_syreny']}")

    power_distance = (same_type["moc_w"] - target_row["moc_w"]).abs()
    same_type["weight"] = 1.0 / (1.0 + (power_distance / POWER_WEIGHT_STEP))
    weighted_mean = (
        (same_type["wysokosc_nad_terenem_m"] * same_type["weight"]).sum()
        / same_type["weight"].sum()
    )
    exact_power_peer_count = int((same_type["moc_w"] == target_row["moc_w"]).sum())
    type_peer_count = int(len(same_type))
    return float(weighted_mean), exact_power_peer_count, type_peer_count


def impute_low_heights(
    df: pd.DataFrame,
    *,
    source_max_height: float,
    reference_min_height: float,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    required_columns = {"nr_ref", "rodzaj_syreny", "moc_w", "wysokosc_nad_terenem_m"}
    missing = required_columns - set(df.columns)
    if missing:
        raise ValueError(f"Brak wymaganych kolumn w pliku wejsciowym: {sorted(missing)}")

    work_df = df.copy()
    work_df["moc_w"] = pd.to_numeric(work_df["moc_w"], errors="coerce")
    work_df["wysokosc_nad_terenem_m"] = pd.to_numeric(
        work_df["wysokosc_nad_terenem_m"], errors="coerce"
    )

    zero_mask = work_df["wysokosc_nad_terenem_m"] <= source_max_height
    reference_mask = work_df["wysokosc_nad_terenem_m"] > reference_min_height
    reference_df = work_df.loc[reference_mask, ["rodzaj_syreny", "moc_w", "wysokosc_nad_terenem_m"]].copy()

    report_rows: list[dict[str, Any]] = []

    for idx, row in work_df.loc[zero_mask].iterrows():
        weighted_mean, exact_power_peer_count, type_peer_count = weighted_height_for_row(
            row, reference_df
        )
        imputed_height = round(weighted_mean * 2) / 2
        work_df.at[idx, "wysokosc_nad_terenem_m"] = imputed_height
        report_rows.append(
            {
                "nr_ref": row["nr_ref"],
                "rodzaj_syreny": row["rodzaj_syreny"],
                "moc_w": int(row["moc_w"]),
                "original_height": float(row["wysokosc_nad_terenem_m"]),
                "imputed_height": imputed_height,
                "weighted_mean_raw": round(weighted_mean, 4),
                "exact_power_peer_count": exact_power_peer_count,
                "type_peer_count": type_peer_count,
                "method": "srednia_wazona_rodzaj_i_moc",
                "weight_formula": "1/(1+abs(moc_docelowa-moc_ref)/100)",
            }
        )

    report_df = pd.DataFrame(report_rows).sort_values(
        by=["rodzaj_syreny", "moc_w", "nr_ref"]
    )
    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_records": int(len(df)),
        "source_max_height": float(source_max_height),
        "reference_min_height": float(reference_min_height),
        "records_selected_for_imputation": int(zero_mask.sum()),
        "changes_applied": int(len(report_df)),
        "weight_formula": "1/(1+abs(moc_docelowa-moc_ref)/100)",
        "rounding_rule_m": 0.5,
        "by_type": report_df["rodzaj_syreny"].value_counts().to_dict(),
        "top_power_groups": report_df["moc_w"].value_counts().head(20).to_dict(),
        "imputed_height_stats": (
            {
                "min": float(report_df["imputed_height"].min()),
                "max": float(report_df["imputed_height"].max()),
                "mean": round(float(report_df["imputed_height"].mean()), 4),
                "median": round(float(report_df["imputed_height"].median()), 4),
            }
            if not report_df.empty
            else {"min": 0.0, "max": 0.0, "mean": 0.0, "median": 0.0}
        ),
    }
    return work_df, report_df, summary


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    input_path = Path(args.input).resolve()
    output_path = Path(args.output).resolve()
    backup_path = Path(args.backup).resolve()
    report_path = Path(args.report_csv).resolve()
    summary_path = Path(args.summary_json).resolve()

    input_df = pd.read_csv(input_path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    corrected_df, report_df, summary = impute_low_heights(
        input_df,
        source_max_height=args.source_max_height,
        reference_min_height=args.reference_min_height,
    )

    backup_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(input_path, backup_path)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    corrected_df.to_csv(output_path, index=False, encoding="utf-8-sig", quoting=csv.QUOTE_MINIMAL)
    report_df.to_csv(report_path, index=False, encoding="utf-8")
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Zapisano plik po imputacji: {output_path}")
    print(f"Zapisano backup: {backup_path}")
    print(f"Zapisano raport: {report_path}")
    print(f"Zapisano podsumowanie: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
