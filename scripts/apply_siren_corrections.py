#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


def read_csv_as_text(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")


def write_csv_as_text(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig", quoting=csv.QUOTE_ALL)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Zastosowanie korekt do CSV inwentaryzacji syren.")
    parser.add_argument("--input", required=True, help="Oryginalny plik CSV.")
    parser.add_argument("--corrections", required=True, help="Plik corrections.csv wygenerowany przez analizator.")
    parser.add_argument("--output", required=True, help="Docelowy poprawiony plik CSV.")
    parser.add_argument("--applied-log", required=True, help="CSV ze zmianami rzeczywiscie zastosowanymi.")
    parser.add_argument("--skipped-log", required=True, help="CSV z korektami pominiętymi.")
    parser.add_argument("--summary-json", required=True, help="JSON z podsumowaniem zastosowanych zmian.")
    parser.add_argument(
        "--min-confidence",
        type=float,
        default=0.5,
        help="Minimalna pewnosc korekty, aby zastosowac ja automatycznie. Domyslnie 0.5.",
    )
    return parser.parse_args(argv)


def apply_corrections(
    source_df: pd.DataFrame,
    corrections_df: pd.DataFrame,
    *,
    min_confidence: float,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    if "Nr ref." not in source_df.columns:
        raise ValueError("Plik zrodlowy nie zawiera kolumny 'Nr ref.'.")

    corrected_df = source_df.copy()
    if corrected_df["Nr ref."].duplicated().any():
        duplicates = corrected_df.loc[corrected_df["Nr ref."].duplicated(), "Nr ref."].tolist()
        raise ValueError(f"Plik zrodlowy ma duplikaty 'Nr ref.': {duplicates[:10]}")

    if corrections_df.duplicated(subset=["nr_ref", "column"]).any():
        duplicate_rows = corrections_df.loc[
            corrections_df.duplicated(subset=["nr_ref", "column"], keep=False),
            ["nr_ref", "column", "method", "confidence"],
        ]
        raise ValueError(
            "Plik corrections.csv ma sprzeczne korekty dla tej samej komorki: "
            f"{duplicate_rows.head(10).to_dict(orient='records')}"
        )

    correction_columns = {
        "nr_ref",
        "column",
        "original_value",
        "suggested_value",
        "method",
        "confidence",
        "evidence_scope",
        "reason",
    }
    missing = correction_columns - set(corrections_df.columns)
    if missing:
        raise ValueError(f"Brak kolumn w corrections.csv: {sorted(missing)}")

    corrected_df = corrected_df.set_index("Nr ref.", drop=False)
    applied_rows: list[dict[str, Any]] = []
    skipped_rows: list[dict[str, Any]] = []

    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_records": int(len(source_df)),
        "candidate_corrections": int(len(corrections_df)),
        "min_confidence": float(min_confidence),
        "applied": 0,
        "skipped": 0,
        "applied_by_method": {},
        "skipped_by_reason": {},
    }

    for correction in corrections_df.sort_values(
        by=["confidence", "nr_ref", "column"],
        ascending=[False, True, True],
    ).to_dict(orient="records"):
        nr_ref = str(correction["nr_ref"])
        column = str(correction["column"])
        confidence = float(correction["confidence"])

        skip_reason = None
        if confidence < min_confidence:
            skip_reason = "confidence_below_threshold"
        elif nr_ref not in corrected_df.index:
            skip_reason = "nr_ref_not_found"
        elif column not in corrected_df.columns:
            skip_reason = "column_not_found"

        current_value = ""
        if skip_reason is None:
            current_value = corrected_df.at[nr_ref, column]
            corrected_df.at[nr_ref, column] = str(correction["suggested_value"])
            applied_rows.append(
                {
                    **correction,
                    "input_value_before_apply": current_value,
                    "original_matches_input": str(current_value) == str(correction["original_value"]),
                }
            )
            summary["applied"] += 1
            summary["applied_by_method"][correction["method"]] = (
                summary["applied_by_method"].get(correction["method"], 0) + 1
            )
        else:
            if nr_ref in corrected_df.index and column in corrected_df.columns:
                current_value = corrected_df.at[nr_ref, column]
            skipped_rows.append(
                {
                    **correction,
                    "input_value_at_skip": current_value,
                    "skip_reason": skip_reason,
                }
            )
            summary["skipped"] += 1
            summary["skipped_by_reason"][skip_reason] = (
                summary["skipped_by_reason"].get(skip_reason, 0) + 1
            )

    corrected_df = corrected_df.reset_index(drop=True)
    return (
        corrected_df,
        pd.DataFrame(applied_rows),
        pd.DataFrame(skipped_rows),
        summary,
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    input_path = Path(args.input).resolve()
    corrections_path = Path(args.corrections).resolve()
    output_path = Path(args.output).resolve()
    applied_log_path = Path(args.applied_log).resolve()
    skipped_log_path = Path(args.skipped_log).resolve()
    summary_json_path = Path(args.summary_json).resolve()

    source_df = read_csv_as_text(input_path)
    corrections_df = read_csv_as_text(corrections_path)

    corrected_df, applied_df, skipped_df, summary = apply_corrections(
        source_df,
        corrections_df,
        min_confidence=args.min_confidence,
    )

    write_csv_as_text(corrected_df, output_path)
    write_csv_as_text(applied_df, applied_log_path)
    write_csv_as_text(skipped_df, skipped_log_path)
    summary_json_path.parent.mkdir(parents=True, exist_ok=True)
    summary_json_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"Poprawiony plik: {output_path}")
    print(f"Zastosowane korekty: {applied_log_path}")
    print(f"Pominiete korekty: {skipped_log_path}")
    print(f"Podsumowanie: {summary_json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
