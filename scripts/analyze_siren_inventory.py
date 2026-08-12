#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


LOGICAL_COLUMNS = ["GSM", "SK PSP", "Sygn. OSP", "Sygn. OL", "Wgr. sygn."]
SIGNAL_COLUMNS = ["Sygn. OSP", "Sygn. OL", "Wgr. sygn."]
IDENTIFIER_COLUMNS = ["Lp.", "Nr ref."]
NUMERIC_COLUMNS = ["Moc [W]", "Wysokość nad terenem [m]", "Lat", "Long"]
REQUIRED_COLUMNS = [
    "Lp.",
    "Nr ref.",
    "Data",
    "Województwo",
    "Powiat",
    "Gmina",
    "Właściciel",
    "Rodzaj syreny",
    "Moc [W]",
    "Wysokość nad terenem [m]",
    "GSM",
    "SK PSP",
    "Sygn. OSP",
    "Sygn. OL",
    "Wgr. sygn.",
    "Lat",
    "Long",
    "Jednostka PSP",
    "Kod SWD",
]

NULL_LIKE_TOKENS = {"", "-", "BRAK", "NULL", "NONE", "NAN"}
ND_TOKENS = {"N/D", "ND", "N\\D", "N-D"}
SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2, "info": 3}

ANALOG_POWER_MIN = 2000.0
ANALOG_POWER_MAX = 5000.0
DIGITAL_POWER_MIN = 300.0
DIGITAL_POWER_MAX = 5000.0
HEIGHT_TYPICAL_MIN = 3.0
HEIGHT_TYPICAL_MAX = 60.0
HEIGHT_REVIEW_MIN = 61.0
HEIGHT_REVIEW_MAX = 99.0
HEIGHT_NPM_THRESHOLD = 100.0
LAT_MIN = 49.0
LAT_MAX = 55.0
LON_MIN = 14.0
LON_MAX = 24.0
MODE_DOMINANCE_MIN = 0.8
MODE_SUPPORT_MIN = 5
NUMERIC_SUPPORT_MIN = 3
MAD_Z_THRESHOLD = 2.5
MAD_SUPPORT_MIN = 10


@dataclass(frozen=True)
class ModeSuggestion:
    value: str
    share: float
    count: int
    scope: str


@dataclass(frozen=True)
class MeanSuggestion:
    value: float
    count: int
    scope: str


def clean_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    text = str(value).replace("\xa0", " ").strip()
    return text or None


def key_token(value: Any) -> str | None:
    text = clean_text(value)
    if text is None:
        return None
    return "".join(text.upper().split())


def label_key(value: Any) -> str | None:
    text = clean_text(value)
    if text is None:
        return None
    return " ".join(text.lower().split())


def is_nd_token(value: Any) -> bool:
    token = key_token(value)
    return token in ND_TOKENS if token else False


def is_null_like(value: Any) -> bool:
    token = key_token(value)
    if token is None:
        return True
    return token in NULL_LIKE_TOKENS or token in ND_TOKENS


def normalize_yes_no_nd(value: Any) -> str | None:
    token = key_token(value)
    if token is None or token in NULL_LIKE_TOKENS:
        return None
    if token in {"TAK", "YES", "Y"}:
        return "TAK"
    if token in {"NIE", "NO", "N"}:
        return "NIE"
    if token in ND_TOKENS:
        return "N/D"
    return clean_text(value).upper()


def parse_number(value: Any) -> tuple[float, bool]:
    text = clean_text(value)
    if text is None or is_nd_token(text):
        return np.nan, False
    normalized = text.replace(" ", "").replace(",", ".")
    try:
        return float(normalized), False
    except ValueError:
        return np.nan, True


def round_to_step(value: float, step: float) -> float:
    return round(value / step) * step


def display_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return ""
        if value.is_integer():
            return str(int(value))
        return f"{value:.2f}".rstrip("0").rstrip(".")
    return str(value)


def actionable_missing(row: pd.Series, column: str) -> bool:
    raw_value = row[column]
    if clean_text(raw_value) is None:
        return True
    if is_nd_token(raw_value):
        if column in SIGNAL_COLUMNS and row["__kind"] == "analogowa":
            return False
        return True
    token = key_token(raw_value)
    return token in NULL_LIKE_TOKENS if token else True


def add_anomaly(
    target: list[dict[str, Any]],
    row: pd.Series,
    *,
    category: str,
    severity: str,
    confidence: float,
    column: str,
    raw_value: Any,
    normalized_value: Any,
    reason: str,
) -> None:
    target.append(
        {
            "nr_ref": display_value(row.get("Nr ref.")),
            "category": category,
            "severity": severity,
            "confidence": round(float(confidence), 2),
            "column": column,
            "raw_value": display_value(raw_value),
            "normalized_value": display_value(normalized_value),
            "reason": reason,
        }
    )


def add_correction(
    target: list[dict[str, Any]],
    row: pd.Series,
    *,
    column: str,
    original_value: Any,
    suggested_value: Any,
    method: str,
    confidence: float,
    evidence_scope: str,
    reason: str,
) -> None:
    target.append(
        {
            "nr_ref": display_value(row.get("Nr ref.")),
            "column": column,
            "original_value": display_value(original_value),
            "suggested_value": display_value(suggested_value),
            "method": method,
            "confidence": round(float(confidence), 2),
            "evidence_scope": evidence_scope,
            "reason": reason,
        }
    )


def validate_required_columns(df: pd.DataFrame) -> None:
    missing = [column for column in REQUIRED_COLUMNS if column not in df.columns]
    if missing:
        raise ValueError(f"Brak wymaganych kolumn: {missing}")


def read_inventory(input_path: Path) -> pd.DataFrame:
    df = pd.read_csv(input_path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    validate_required_columns(df)
    df["__row_id"] = np.arange(len(df))
    df["__woj_key"] = df["Województwo"].map(label_key)
    df["__powiat_key"] = df["Powiat"].map(label_key)
    df["__gmina_key"] = df["Gmina"].map(label_key)
    df["__owner"] = df["Właściciel"].map(label_key)
    df["__kind"] = df["Rodzaj syreny"].map(label_key)

    for column in LOGICAL_COLUMNS:
        df[f"__norm__{column}"] = df[column].map(normalize_yes_no_nd)

    for column in NUMERIC_COLUMNS:
        parsed = df[column].map(parse_number)
        df[f"__num__{column}"] = [item[0] for item in parsed]
        df[f"__parse_error__{column}"] = [item[1] for item in parsed]

    return df


def compute_mode_lookup(
    df: pd.DataFrame,
    *,
    group_columns: list[str],
    value_column: str,
    scope: str,
) -> dict[tuple[Any, ...], ModeSuggestion]:
    work = df.loc[:, group_columns + [value_column]].copy()
    work = work[work[value_column].notna()]
    if work.empty:
        return {}

    counts = (
        work.groupby(group_columns + [value_column], dropna=False)
        .size()
        .reset_index(name="value_count")
    )
    totals = (
        work.groupby(group_columns, dropna=False)
        .size()
        .reset_index(name="group_total")
    )
    merged = counts.merge(totals, on=group_columns, how="left")
    merged["share"] = merged["value_count"] / merged["group_total"]
    merged = merged.sort_values(
        by=group_columns + ["value_count", value_column],
        ascending=[True] * len(group_columns) + [False, True],
    )

    suggestions: dict[tuple[Any, ...], ModeSuggestion] = {}
    for _, row in merged.iterrows():
        key = tuple(row[column] for column in group_columns)
        if key in suggestions:
            continue
        if row["value_count"] >= MODE_SUPPORT_MIN and row["share"] >= MODE_DOMINANCE_MIN:
            suggestions[key] = ModeSuggestion(
                value=str(row[value_column]),
                share=float(row["share"]),
                count=int(row["value_count"]),
                scope=scope,
            )
    return suggestions


def compute_mean_lookup(
    df: pd.DataFrame,
    *,
    group_columns: list[str],
    value_column: str,
    valid_mask: pd.Series,
    scope: str,
) -> dict[tuple[Any, ...], MeanSuggestion]:
    work = df.loc[valid_mask, group_columns + [value_column]].copy()
    work = work[work[value_column].notna()]
    if work.empty:
        return {}
    grouped = (
        work.groupby(group_columns, dropna=False)[value_column]
        .agg(["mean", "count"])
        .reset_index()
    )
    suggestions: dict[tuple[Any, ...], MeanSuggestion] = {}
    for _, row in grouped.iterrows():
        if int(row["count"]) < NUMERIC_SUPPORT_MIN:
            continue
        key = tuple(row[column] for column in group_columns)
        suggestions[key] = MeanSuggestion(
            value=float(row["mean"]),
            count=int(row["count"]),
            scope=scope,
        )
    return suggestions


def choose_mode(
    row: pd.Series,
    lookups: list[tuple[list[str], dict[tuple[Any, ...], ModeSuggestion]]],
) -> ModeSuggestion | None:
    for columns, lookup in lookups:
        key = tuple(row[column] for column in columns)
        suggestion = lookup.get(key)
        if suggestion:
            return suggestion
    return None


def choose_mean(
    row: pd.Series,
    lookups: list[tuple[list[str], dict[tuple[Any, ...], MeanSuggestion]]],
) -> MeanSuggestion | None:
    for columns, lookup in lookups:
        key = tuple(row[column] for column in columns)
        suggestion = lookup.get(key)
        if suggestion:
            return suggestion
    return None


def get_group_mad_stats(
    df: pd.DataFrame,
    *,
    value_column: str,
    valid_mask: pd.Series,
) -> list[tuple[list[str], dict[tuple[Any, ...], dict[str, Any]]]]:
    scopes = [
        (["__woj_key", "__powiat_key", "__kind"], "powiat+rodzaj"),
        (["__woj_key", "__kind"], "wojewodztwo+rodzaj"),
        (["__kind"], "rodzaj"),
    ]
    results: list[tuple[list[str], dict[tuple[Any, ...], dict[str, Any]]]] = []
    all_group_columns = sorted({column for columns, _ in scopes for column in columns})
    work = df.loc[valid_mask, all_group_columns + [value_column]].copy()
    work = work[work[value_column].notna()]

    for columns, scope in scopes:
        grouped = (
            work.groupby(columns, dropna=False)[value_column]
            .agg(list)
            .reset_index(name="values")
        )
        stats_lookup: dict[tuple[Any, ...], dict[str, Any]] = {}
        for _, row in grouped.iterrows():
            values = np.array(row["values"], dtype=float)
            if len(values) < MAD_SUPPORT_MIN:
                continue
            median = float(np.median(values))
            mad = float(np.median(np.abs(values - median)))
            stats_lookup[tuple(row[column] for column in columns)] = {
                "median": median,
                "mad": mad,
                "count": int(len(values)),
                "scope": scope,
            }
        results.append((columns, stats_lookup))
    return results


def choose_mad_stat(
    row: pd.Series,
    stats: list[tuple[list[str], dict[tuple[Any, ...], dict[str, Any]]]],
) -> dict[str, Any] | None:
    for columns, lookup in stats:
        key = tuple(row[column] for column in columns)
        result = lookup.get(key)
        if result:
            return result
    return None


def is_power_in_expected_range(row: pd.Series, value: float) -> bool:
    if math.isnan(value):
        return False
    if row["__kind"] == "analogowa":
        return ANALOG_POWER_MIN <= value <= ANALOG_POWER_MAX
    return DIGITAL_POWER_MIN <= value <= DIGITAL_POWER_MAX


def make_crosstab(df: pd.DataFrame, column: str) -> list[dict[str, Any]]:
    table = (
        df.groupby(["Rodzaj syreny", f"__norm__{column}"], dropna=False)
        .size()
        .reset_index(name="count")
        .rename(columns={f"__norm__{column}": column})
    )
    records = []
    for _, row in table.iterrows():
        records.append(
            {
                "rodzaj_syreny": display_value(row["Rodzaj syreny"]) or "BRAK",
                column: display_value(row[column]) or "BRAK",
                "count": int(row["count"]),
            }
        )
    return records


def build_typical_profiles(df: pd.DataFrame) -> list[dict[str, Any]]:
    profile_df = pd.DataFrame(
        {
            "Rodzaj syreny": df["Rodzaj syreny"].map(clean_text).fillna("BRAK"),
            "Właściciel": df["Właściciel"].map(clean_text).fillna("BRAK"),
            "GSM": df["__norm__GSM"].fillna("BRAK"),
            "SK PSP": df["__norm__SK PSP"].fillna("BRAK"),
            "Sygn. OSP": df["__norm__Sygn. OSP"].fillna("BRAK"),
            "Sygn. OL": df["__norm__Sygn. OL"].fillna("BRAK"),
            "Wgr. sygn.": df["__norm__Wgr. sygn."].fillna("BRAK"),
        }
    )
    grouped = (
        profile_df.groupby(list(profile_df.columns), dropna=False)
        .size()
        .reset_index(name="count")
        .sort_values("count", ascending=False)
    )
    grouped = grouped[grouped["count"] >= 50].copy()
    if grouped.empty:
        return []
    grouped["share_pct"] = (grouped["count"] / len(df) * 100).round(2)
    return grouped.head(20).to_dict(orient="records")


def collect_profile_metrics(df: pd.DataFrame) -> dict[str, Any]:
    raw_missing_counts = {column: int(df[column].map(is_null_like).sum()) for column in df.columns if not column.startswith("__")}
    actionable_missing_counts = {
        column: int(df.apply(lambda row: actionable_missing(row, column), axis=1).sum())
        for column in df.columns
        if not column.startswith("__")
    }

    duplicates_lp = int(df["Lp."].map(clean_text).duplicated(keep=False).sum())
    duplicates_ref = int(df["Nr ref."].map(clean_text).duplicated(keep=False).sum())
    return {
        "raw_missing_by_column": raw_missing_counts,
        "actionable_missing_by_column": actionable_missing_counts,
        "duplicates": {
            "Lp.": duplicates_lp,
            "Nr ref.": duplicates_ref,
        },
        "rodzaj_syreny": df["Rodzaj syreny"].map(clean_text).value_counts(dropna=False).to_dict(),
        "wlasciciel": df["Właściciel"].map(clean_text).value_counts(dropna=False).to_dict(),
    }


def analyze_inventory(df: pd.DataFrame, source_path: Path) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    anomalies: list[dict[str, Any]] = []
    corrections: list[dict[str, Any]] = []
    metrics = collect_profile_metrics(df)

    lp_duplicates = df["Lp."].map(clean_text).duplicated(keep=False)
    ref_duplicates = df["Nr ref."].map(clean_text).duplicated(keep=False)

    power_column = "__num__Moc [W]"
    height_column = "__num__Wysokość nad terenem [m]"
    lat_column = "__num__Lat"
    lon_column = "__num__Long"

    analog_power_valid = (
        (df["__kind"] == "analogowa")
        & df[power_column].notna()
        & df[power_column].between(ANALOG_POWER_MIN, ANALOG_POWER_MAX, inclusive="both")
    )
    digital_power_valid = (
        (df["__kind"] != "analogowa")
        & df[power_column].notna()
        & df[power_column].between(DIGITAL_POWER_MIN, DIGITAL_POWER_MAX, inclusive="both")
    )
    height_valid = (
        df[height_column].notna()
        & df[height_column].between(HEIGHT_TYPICAL_MIN, HEIGHT_TYPICAL_MAX, inclusive="both")
    )
    correlation_mask = (analog_power_valid | digital_power_valid) & height_valid
    correlation_df = df.loc[correlation_mask, [power_column, height_column]].copy()
    pearson = correlation_df[power_column].corr(correlation_df[height_column], method="pearson")
    spearman = correlation_df[power_column].corr(correlation_df[height_column], method="spearman")

    metrics["correlations"] = {
        "power_height": {
            "n": int(len(correlation_df)),
            "pearson": None if pd.isna(pearson) else round(float(pearson), 4),
            "spearman": None if pd.isna(spearman) else round(float(spearman), 4),
        }
    }
    metrics["crosstabs"] = {
        "rodzaj_gsm": make_crosstab(df, "GSM"),
        "rodzaj_sk_psp": make_crosstab(df, "SK PSP"),
    }
    metrics["typical_profiles"] = build_typical_profiles(df)

    mode_lookups = {
        "Jednostka PSP": [
            (
                ["__woj_key", "__powiat_key"],
                compute_mode_lookup(
                    df,
                    group_columns=["__woj_key", "__powiat_key"],
                    value_column="Jednostka PSP",
                    scope="wojewodztwo+powiat",
                ),
            )
        ],
        "Kod SWD": [
            (
                ["__woj_key", "__powiat_key"],
                compute_mode_lookup(
                    df,
                    group_columns=["__woj_key", "__powiat_key"],
                    value_column="Kod SWD",
                    scope="wojewodztwo+powiat",
                ),
            )
        ],
    }

    for column in LOGICAL_COLUMNS:
        mode_lookups[column] = [
            (
                ["__woj_key", "__powiat_key", "__kind"],
                compute_mode_lookup(
                    df,
                    group_columns=["__woj_key", "__powiat_key", "__kind"],
                    value_column=f"__norm__{column}",
                    scope="powiat+rodzaj",
                ),
            ),
            (
                ["__woj_key", "__kind"],
                compute_mode_lookup(
                    df,
                    group_columns=["__woj_key", "__kind"],
                    value_column=f"__norm__{column}",
                    scope="wojewodztwo+rodzaj",
                ),
            ),
        ]

    local_power_means = [
        (
            ["__woj_key", "__powiat_key", "__kind"],
            compute_mean_lookup(
                df,
                group_columns=["__woj_key", "__powiat_key", "__kind"],
                value_column=power_column,
                valid_mask=analog_power_valid | digital_power_valid,
                scope="powiat+rodzaj",
            ),
        ),
        (
            ["__woj_key", "__kind"],
            compute_mean_lookup(
                df,
                group_columns=["__woj_key", "__kind"],
                value_column=power_column,
                valid_mask=analog_power_valid | digital_power_valid,
                scope="wojewodztwo+rodzaj",
            ),
        ),
    ]
    local_height_means = [
        (
            ["__woj_key", "__powiat_key", "__kind"],
            compute_mean_lookup(
                df,
                group_columns=["__woj_key", "__powiat_key", "__kind"],
                value_column=height_column,
                valid_mask=height_valid,
                scope="powiat+rodzaj",
            ),
        ),
        (
            ["__woj_key", "__kind"],
            compute_mean_lookup(
                df,
                group_columns=["__woj_key", "__kind"],
                value_column=height_column,
                valid_mask=height_valid,
                scope="wojewodztwo+rodzaj",
            ),
        ),
    ]

    analog_power_mean = float(df.loc[analog_power_valid, power_column].mean()) if analog_power_valid.any() else math.nan
    height_mean_by_kind = (
        df.loc[height_valid].groupby("__kind")[height_column].mean().to_dict()
        if height_valid.any()
        else {}
    )
    overall_height_mean = float(df.loc[height_valid, height_column].mean()) if height_valid.any() else math.nan

    power_mad_stats = get_group_mad_stats(
        df,
        value_column=power_column,
        valid_mask=analog_power_valid | digital_power_valid,
    )
    height_mad_stats = get_group_mad_stats(
        df,
        value_column=height_column,
        valid_mask=height_valid,
    )

    for _, row in df.iterrows():
        nr_ref = display_value(row["Nr ref."])

        if lp_duplicates.loc[row.name]:
            add_anomaly(
                anomalies,
                row,
                category="administrative",
                severity="high",
                confidence=1.0,
                column="Lp.",
                raw_value=row["Lp."],
                normalized_value=clean_text(row["Lp."]),
                reason="Duplikat identyfikatora Lp.",
            )

        if ref_duplicates.loc[row.name]:
            add_anomaly(
                anomalies,
                row,
                category="administrative",
                severity="high",
                confidence=1.0,
                column="Nr ref.",
                raw_value=row["Nr ref."],
                normalized_value=clean_text(row["Nr ref."]),
                reason="Duplikat identyfikatora Nr ref.",
            )

        for column in LOGICAL_COLUMNS:
            normalized_value = row[f"__norm__{column}"]
            raw_value = clean_text(row[column])
            if raw_value is not None and normalized_value is not None and raw_value != normalized_value:
                add_correction(
                    corrections,
                    row,
                    column=column,
                    original_value=row[column],
                    suggested_value=normalized_value,
                    method="standaryzacja TAK/NIE/N/D",
                    confidence=1.0,
                    evidence_scope="regula logiczna",
                    reason="Ujednolicenie wariantu zapisu wartosci logicznej.",
                )

        for column in NUMERIC_COLUMNS:
            if row[f"__parse_error__{column}"]:
                add_anomaly(
                    anomalies,
                    row,
                    category="technical",
                    severity="high",
                    confidence=1.0,
                    column=column,
                    raw_value=row[column],
                    normalized_value="",
                    reason="Nie udalo sie sparsowac wartosci liczbowej.",
                )

        power = row[power_column]
        height = row[height_column]
        lat = row[lat_column]
        lon = row[lon_column]

        if pd.isna(power) and actionable_missing(row, "Moc [W]"):
            add_anomaly(
                anomalies,
                row,
                category="technical",
                severity="high",
                confidence=1.0,
                column="Moc [W]",
                raw_value=row["Moc [W]"],
                normalized_value="",
                reason="Brak mocy syreny.",
            )
            local_mean = choose_mean(row, local_power_means)
            if local_mean:
                add_correction(
                    corrections,
                    row,
                    column="Moc [W]",
                    original_value=row["Moc [W]"],
                    suggested_value=round_to_step(local_mean.value, 100.0),
                    method="srednia lokalna",
                    confidence=0.55,
                    evidence_scope=local_mean.scope,
                    reason="Uzupelnienie brakujacej mocy na podstawie sredniej lokalnej dla rodzaju syreny.",
                )

        if pd.isna(height) and actionable_missing(row, "Wysokość nad terenem [m]"):
            add_anomaly(
                anomalies,
                row,
                category="technical",
                severity="high",
                confidence=1.0,
                column="Wysokość nad terenem [m]",
                raw_value=row["Wysokość nad terenem [m]"],
                normalized_value="",
                reason="Brak wysokosci montazu syreny.",
            )
            local_height = choose_mean(row, local_height_means)
            if local_height:
                add_correction(
                    corrections,
                    row,
                    column="Wysokość nad terenem [m]",
                    original_value=row["Wysokość nad terenem [m]"],
                    suggested_value=round_to_step(local_height.value, 0.5),
                    method="srednia lokalna",
                    confidence=0.55,
                    evidence_scope=local_height.scope,
                    reason="Uzupelnienie brakujacej wysokosci na podstawie sredniej lokalnej dla rodzaju syreny.",
                )

        if row["__kind"] == "analogowa" and not pd.isna(power):
            if power < ANALOG_POWER_MIN or power > ANALOG_POWER_MAX:
                add_anomaly(
                    anomalies,
                    row,
                    category="technical",
                    severity="high",
                    confidence=1.0,
                    column="Moc [W]",
                    raw_value=row["Moc [W]"],
                    normalized_value=power,
                    reason="Moc syreny analogowej poza dopuszczalnym zakresem 2000-5000 W.",
                )
                if not math.isnan(analog_power_mean):
                    add_correction(
                        corrections,
                        row,
                        column="Moc [W]",
                        original_value=row["Moc [W]"],
                        suggested_value=round_to_step(analog_power_mean, 100.0),
                        method="srednia analogowych",
                        confidence=0.9,
                        evidence_scope="global analogowa",
                        reason="Korekta na podstawie sredniej innych poprawnych syren analogowych.",
                    )
        elif row["__kind"] != "analogowa" and not pd.isna(power):
            if power < DIGITAL_POWER_MIN or power > DIGITAL_POWER_MAX:
                add_anomaly(
                    anomalies,
                    row,
                    category="technical",
                    severity="medium",
                    confidence=0.75,
                    column="Moc [W]",
                    raw_value=row["Moc [W]"],
                    normalized_value=power,
                    reason="Moc syreny cyfrowej poza typowym zakresem 300-5000 W.",
                )
                local_mean = choose_mean(row, local_power_means)
                if local_mean:
                    add_correction(
                        corrections,
                        row,
                        column="Moc [W]",
                        original_value=row["Moc [W]"],
                        suggested_value=round_to_step(local_mean.value, 100.0),
                        method="srednia lokalna",
                        confidence=0.45,
                        evidence_scope=local_mean.scope,
                        reason="Niska pewnosc: korekta cyfrowej mocy do sredniej lokalnej dla rodzaju syreny.",
                    )

        if not pd.isna(height):
            if height >= HEIGHT_NPM_THRESHOLD:
                add_anomaly(
                    anomalies,
                    row,
                    category="technical",
                    severity="high",
                    confidence=0.98,
                    column="Wysokość nad terenem [m]",
                    raw_value=row["Wysokość nad terenem [m]"],
                    normalized_value=height,
                    reason="Prawdopodobnie wpisano wysokosc npm zamiast wysokosci montazu nad terenem.",
                )
                kind_height_mean = height_mean_by_kind.get(row["__kind"])
                if kind_height_mean is None or math.isnan(kind_height_mean):
                    kind_height_mean = overall_height_mean
                    scope = "global wszystkie rodzaje"
                else:
                    scope = f"rodzaj={row['__kind']}"
                if not math.isnan(kind_height_mean):
                    add_correction(
                        corrections,
                        row,
                        column="Wysokość nad terenem [m]",
                        original_value=row["Wysokość nad terenem [m]"],
                        suggested_value=round_to_step(float(kind_height_mean), 0.5),
                        method="srednia montazu nad terenem",
                        confidence=0.88,
                        evidence_scope=scope,
                        reason="Korekta na podstawie sredniej poprawnych wysokosci montazu nad terenem.",
                    )
            elif HEIGHT_REVIEW_MIN <= height <= HEIGHT_REVIEW_MAX:
                add_anomaly(
                    anomalies,
                    row,
                    category="technical",
                    severity="medium",
                    confidence=0.55,
                    column="Wysokość nad terenem [m]",
                    raw_value=row["Wysokość nad terenem [m]"],
                    normalized_value=height,
                    reason="Wysokosc montazu w zakresie 61-99 m wymaga przegladu.",
                )
            elif height < HEIGHT_TYPICAL_MIN:
                add_anomaly(
                    anomalies,
                    row,
                    category="technical",
                    severity="high",
                    confidence=0.95,
                    column="Wysokość nad terenem [m]",
                    raw_value=row["Wysokość nad terenem [m]"],
                    normalized_value=height,
                    reason="Wysokosc montazu ponizej typowego minimum 3 m.",
                )

        lat_missing = pd.isna(lat) and actionable_missing(row, "Lat")
        lon_missing = pd.isna(lon) and actionable_missing(row, "Long")
        if lat_missing or lon_missing:
            missing_columns = []
            if lat_missing:
                missing_columns.append("Lat")
            if lon_missing:
                missing_columns.append("Long")
            add_anomaly(
                anomalies,
                row,
                category="geographic",
                severity="high",
                confidence=1.0,
                column="|".join(missing_columns),
                raw_value=" / ".join(display_value(row[column]) for column in missing_columns),
                normalized_value="",
                reason="Brak wspolrzednych geograficznych.",
            )
        elif not pd.isna(lat) and not pd.isna(lon):
            lat_ok = LAT_MIN <= lat <= LAT_MAX
            lon_ok = LON_MIN <= lon <= LON_MAX
            if not lat_ok or not lon_ok:
                swap_ok = LAT_MIN <= lon <= LAT_MAX and LON_MIN <= lat <= LON_MAX
                if swap_ok:
                    add_anomaly(
                        anomalies,
                        row,
                        category="geographic",
                        severity="high",
                        confidence=0.99,
                        column="Lat|Long",
                        raw_value=f"{display_value(row['Lat'])}, {display_value(row['Long'])}",
                        normalized_value=f"{display_value(lat)}, {display_value(lon)}",
                        reason="Wspolrzedne wygladaja na zamienione miejscami Lat/Long.",
                    )
                    add_correction(
                        corrections,
                        row,
                        column="Lat",
                        original_value=row["Lat"],
                        suggested_value=lon,
                        method="regula logiczna",
                        confidence=0.99,
                        evidence_scope="lat/long swap",
                        reason="Zamiana Lat na wartosc z pola Long.",
                    )
                    add_correction(
                        corrections,
                        row,
                        column="Long",
                        original_value=row["Long"],
                        suggested_value=lat,
                        method="regula logiczna",
                        confidence=0.99,
                        evidence_scope="lat/long swap",
                        reason="Zamiana Long na wartosc z pola Lat.",
                    )
                else:
                    if not lat_ok:
                        add_anomaly(
                            anomalies,
                            row,
                            category="geographic",
                            severity="high",
                            confidence=1.0,
                            column="Lat",
                            raw_value=row["Lat"],
                            normalized_value=lat,
                            reason="Szerokosc geograficzna poza zakresem Polski 49-55.",
                        )
                    if not lon_ok:
                        add_anomaly(
                            anomalies,
                            row,
                            category="geographic",
                            severity="high",
                            confidence=1.0,
                            column="Long",
                            raw_value=row["Long"],
                            normalized_value=lon,
                            reason="Dlugosc geograficzna poza zakresem Polski 14-24.",
                        )

        for column in ["Jednostka PSP", "Kod SWD"]:
            if actionable_missing(row, column):
                add_anomaly(
                    anomalies,
                    row,
                    category="administrative",
                    severity="high",
                    confidence=1.0,
                    column=column,
                    raw_value=row[column],
                    normalized_value="",
                    reason=f"Brak wartosci w kolumnie {column}.",
                )
                suggestion = choose_mode(row, mode_lookups[column])
                if suggestion:
                    add_correction(
                        corrections,
                        row,
                        column=column,
                        original_value=row[column],
                        suggested_value=suggestion.value,
                        method="dominanta lokalna",
                        confidence=min(0.95, suggestion.share),
                        evidence_scope=suggestion.scope,
                        reason=f"Uzupelnienie na podstawie dominujacej wartosci w grupie ({suggestion.count} rekordow).",
                    )

        gsm = row["__norm__GSM"]
        sk_psp = row["__norm__SK PSP"]
        sygn_osp = row["__norm__Sygn. OSP"]
        sygn_ol = row["__norm__Sygn. OL"]
        wgr = row["__norm__Wgr. sygn."]

        if row["__kind"] == "cyfrowa" and gsm != "TAK":
            add_anomaly(
                anomalies,
                row,
                category="logical",
                severity="medium",
                confidence=0.75,
                column="GSM",
                raw_value=row["GSM"],
                normalized_value=gsm,
                reason="Syrena cyfrowa bez potwierdzonego GSM.",
            )

        if wgr == "TAK" and sygn_osp != "TAK" and sygn_ol != "TAK":
            add_anomaly(
                anomalies,
                row,
                category="logical",
                severity="high",
                confidence=0.95,
                column="Wgr. sygn.|Sygn. OSP|Sygn. OL",
                raw_value=f"{display_value(row['Wgr. sygn.'])} / {display_value(row['Sygn. OSP'])} / {display_value(row['Sygn. OL'])}",
                normalized_value=f"{display_value(wgr)} / {display_value(sygn_osp)} / {display_value(sygn_ol)}",
                reason="Wgr. sygn. = TAK, ale brak aktywnego Sygn. OSP i Sygn. OL.",
            )

        if row["__owner"] == "osp" and sygn_osp == "NIE":
            add_anomaly(
                anomalies,
                row,
                category="logical",
                severity="medium",
                confidence=0.7,
                column="Właściciel|Sygn. OSP",
                raw_value=f"{display_value(row['Właściciel'])} / {display_value(row['Sygn. OSP'])}",
                normalized_value=f"{display_value(row['__owner'])} / {display_value(sygn_osp)}",
                reason="Wlasciciel OSP, ale Sygn. OSP = NIE.",
            )

        if gsm != "TAK" and sk_psp != "TAK":
            add_anomaly(
                anomalies,
                row,
                category="logical",
                severity="low",
                confidence=0.6,
                column="GSM|SK PSP",
                raw_value=f"{display_value(row['GSM'])} / {display_value(row['SK PSP'])}",
                normalized_value=f"{display_value(gsm)} / {display_value(sk_psp)}",
                reason="Brak GSM i brak SK PSP - rekord do przegladu pod katem lacznosci lub analogowego sterowania.",
            )

        for column in LOGICAL_COLUMNS:
            if actionable_missing(row, column):
                add_anomaly(
                    anomalies,
                    row,
                    category="technical",
                    severity="medium",
                    confidence=0.8,
                    column=column,
                    raw_value=row[column],
                    normalized_value=row[f"__norm__{column}"],
                    reason="Brak wartosci logicznej wymagajacej standaryzacji lub uzupelnienia.",
                )
                suggestion = choose_mode(row, mode_lookups[column])
                if suggestion:
                    add_correction(
                        corrections,
                        row,
                        column=column,
                        original_value=row[column],
                        suggested_value=suggestion.value,
                        method="dominanta lokalna",
                        confidence=min(0.9, suggestion.share),
                        evidence_scope=suggestion.scope,
                        reason=f"Uzupelnienie na podstawie dominujacego wzorca w grupie ({suggestion.count} rekordow).",
                    )

        if not pd.isna(power):
            mad_stat = choose_mad_stat(row, power_mad_stats)
            if mad_stat and mad_stat["mad"] > 0 and is_power_in_expected_range(row, power):
                z_score = abs(power - mad_stat["median"]) / mad_stat["mad"]
                if z_score > MAD_Z_THRESHOLD:
                    add_anomaly(
                        anomalies,
                        row,
                        category="relational",
                        severity="medium",
                        confidence=0.72,
                        column="Moc [W]",
                        raw_value=row["Moc [W]"],
                        normalized_value=power,
                        reason=(
                            f"Moc odbiega od mediany grupy ({mad_stat['scope']}, n={mad_stat['count']}) "
                            f"o wiecej niz {MAD_Z_THRESHOLD} MAD."
                        ),
                    )

        if not pd.isna(height):
            mad_stat = choose_mad_stat(row, height_mad_stats)
            if mad_stat and mad_stat["mad"] > 0 and HEIGHT_TYPICAL_MIN <= height <= HEIGHT_TYPICAL_MAX:
                z_score = abs(height - mad_stat["median"]) / mad_stat["mad"]
                if z_score > MAD_Z_THRESHOLD:
                    add_anomaly(
                        anomalies,
                        row,
                        category="relational",
                        severity="low",
                        confidence=0.68,
                        column="Wysokość nad terenem [m]",
                        raw_value=row["Wysokość nad terenem [m]"],
                        normalized_value=height,
                        reason=(
                            f"Wysokosc odbiega od mediany grupy ({mad_stat['scope']}, n={mad_stat['count']}) "
                            f"o wiecej niz {MAD_Z_THRESHOLD} MAD."
                        ),
                    )

    anomalies_df = pd.DataFrame(anomalies)
    if anomalies_df.empty:
        anomalies_df = pd.DataFrame(
            columns=["nr_ref", "category", "severity", "confidence", "column", "raw_value", "normalized_value", "reason"]
        )
    anomalies_df = anomalies_df.drop_duplicates().copy()
    anomalies_df["__severity_rank"] = anomalies_df["severity"].map(SEVERITY_ORDER)
    anomalies_df = anomalies_df.sort_values(
        by=["__severity_rank", "category", "nr_ref", "column", "reason"]
    ).drop(columns="__severity_rank")

    corrections_df = pd.DataFrame(corrections)
    if corrections_df.empty:
        corrections_df = pd.DataFrame(
            columns=[
                "nr_ref",
                "column",
                "original_value",
                "suggested_value",
                "method",
                "confidence",
                "evidence_scope",
                "reason",
            ]
        )
    corrections_df = corrections_df.drop_duplicates().copy()
    corrections_df = corrections_df.sort_values(
        by=["nr_ref", "column", "method", "reason"]
    )

    records_with_anomalies = int(anomalies_df["nr_ref"].nunique()) if not anomalies_df.empty else 0
    records_with_corrections = int(corrections_df["nr_ref"].nunique()) if not corrections_df.empty else 0
    total_cells = len(df) * len(REQUIRED_COLUMNS)
    actionable_missing_total = int(sum(metrics["actionable_missing_by_column"].values()))
    raw_missing_total = int(sum(metrics["raw_missing_by_column"].values()))

    metrics["source_file"] = str(source_path)
    metrics["generated_at_utc"] = datetime.now(timezone.utc).isoformat()
    metrics["records"] = int(len(df))
    metrics["columns"] = int(len(REQUIRED_COLUMNS))
    metrics["missing"] = {
        "raw_total": raw_missing_total,
        "raw_pct_cells": round(raw_missing_total / total_cells * 100, 2),
        "actionable_total": actionable_missing_total,
        "actionable_pct_cells": round(actionable_missing_total / total_cells * 100, 2),
    }
    metrics["anomalies"] = {
        "total_entries": int(len(anomalies_df)),
        "records_with_anomalies": records_with_anomalies,
        "records_with_anomalies_pct": round(records_with_anomalies / len(df) * 100, 2),
        "entries_pct_cells": round((len(anomalies_df) / total_cells * 100), 2),
        "by_category": anomalies_df["category"].value_counts().to_dict(),
        "by_severity": anomalies_df["severity"].value_counts().to_dict(),
        "top_reasons": anomalies_df["reason"].value_counts().head(10).to_dict(),
    }
    metrics["corrections"] = {
        "total_entries": int(len(corrections_df)),
        "records_with_corrections": records_with_corrections,
        "records_with_corrections_pct": round(records_with_corrections / len(df) * 100, 2),
        "by_method": corrections_df["method"].value_counts().to_dict(),
        "low_confidence_count": int((corrections_df["confidence"] < 0.5).sum()),
    }
    metrics["thresholds"] = {
        "analog_power_range": [ANALOG_POWER_MIN, ANALOG_POWER_MAX],
        "digital_power_range": [DIGITAL_POWER_MIN, DIGITAL_POWER_MAX],
        "height_typical_range": [HEIGHT_TYPICAL_MIN, HEIGHT_TYPICAL_MAX],
        "height_review_range": [HEIGHT_REVIEW_MIN, HEIGHT_REVIEW_MAX],
        "height_npm_threshold": HEIGHT_NPM_THRESHOLD,
        "lat_range": [LAT_MIN, LAT_MAX],
        "long_range": [LON_MIN, LON_MAX],
        "mode_dominance_min": MODE_DOMINANCE_MIN,
        "mode_support_min": MODE_SUPPORT_MIN,
        "numeric_support_min": NUMERIC_SUPPORT_MIN,
        "mad_z_threshold": MAD_Z_THRESHOLD,
        "mad_support_min": MAD_SUPPORT_MIN,
    }

    return anomalies_df, corrections_df, metrics


def markdown_table(records: list[dict[str, Any]], columns: list[str]) -> str:
    if not records:
        return "_Brak._"
    header = "| " + " | ".join(columns) + " |"
    separator = "| " + " | ".join(["---"] * len(columns)) + " |"
    lines = [header, separator]
    for record in records:
        row = []
        for column in columns:
            value = display_value(record.get(column, "")).replace("|", "/")
            row.append(value)
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def build_summary_markdown(
    source_path: Path,
    anomalies_df: pd.DataFrame,
    corrections_df: pd.DataFrame,
    metrics: dict[str, Any],
) -> str:
    top_anomaly_reasons = [
        {"powod": key, "liczba": value}
        for key, value in list(metrics["anomalies"]["top_reasons"].items())[:8]
    ]
    top_anomalies = anomalies_df.head(20).to_dict(orient="records")
    top_corrections = corrections_df.head(20).to_dict(orient="records")

    main_recommendations = [
        "W formularzu wymusic slownikowe wartosci TAK/NIE/N/D zamiast pola tekstowego.",
        "Dla syren analogowych blokowac zapis mocy poza zakresem 2000-5000 W.",
        "Dla wysokosci montazu wyswietlac ostrzezenie przy 61-99 m i blokade przy >=100 m.",
        "Dla syren cyfrowych wymagac potwierdzenia GSM przed zapisem rekordu.",
        "Dla Wgr. sygn. = TAK wymagac zaznaczenia co najmniej jednej sciezki Sygn. OSP lub Sygn. OL.",
        "Jednostka PSP i Kod SWD uzupelniac automatycznie po wojewodztwie i powiecie.",
        "Walidowac wspolrzedne na mapie i wykrywac prawdopodobna zamiane Lat/Long.",
    ]

    rodaj_counts = [
        {"rodzaj": key or "BRAK", "liczba": value}
        for key, value in metrics["rodzaj_syreny"].items()
    ]
    wlasciciel_counts = [
        {"wlasciciel": key or "BRAK", "liczba": value}
        for key, value in list(metrics["wlasciciel"].items())[:8]
    ]

    summary_lines = [
        "# Audyt i korekta CSV inwentaryzacji syren SOiA",
        "",
        "## 1. Podsumowanie danych",
        f"- Plik zrodlowy: `{source_path}`",
        f"- Liczba rekordow: **{metrics['records']}**",
        f"- Liczba kolumn: **{metrics['columns']}**",
        (
            f"- Surowe braki (`N/D`, puste, brak): **{metrics['missing']['raw_total']}** "
            f"komorek (**{metrics['missing']['raw_pct_cells']}%** wszystkich pol)."
        ),
        (
            f"- Braki wymagajace interwencji: **{metrics['missing']['actionable_total']}** "
            f"komorek (**{metrics['missing']['actionable_pct_cells']}%** wszystkich pol)."
        ),
        (
            f"- Rekordy z co najmniej jedna anomalia: **{metrics['anomalies']['records_with_anomalies']}** "
            f"(**{metrics['anomalies']['records_with_anomalies_pct']}%** rekordow)."
        ),
        (
            f"- Rekordy z co najmniej jedna sugerowana korekta: **{metrics['corrections']['records_with_corrections']}** "
            f"(**{metrics['corrections']['records_with_corrections_pct']}%** rekordow)."
        ),
        (
            f"- Duplikaty identyfikatorow: `Lp.` = **{metrics['duplicates']['Lp.']}**, "
            f"`Nr ref.` = **{metrics['duplicates']['Nr ref.']}**."
        ),
        (
            f"- Korelacja `Moc [W]` ↔ `Wysokosc`: Pearson = **{metrics['correlations']['power_height']['pearson']}**, "
            f"Spearman = **{metrics['correlations']['power_height']['spearman']}** "
            f"na probie **{metrics['correlations']['power_height']['n']}** rekordow."
        ),
        "",
        "Rozklad `Rodzaj syreny`:",
        markdown_table(rodaj_counts, ["rodzaj", "liczba"]),
        "",
        "Najczestszy `Wlasciciel`:",
        markdown_table(wlasciciel_counts, ["wlasciciel", "liczba"]),
        "",
        "## 2. Lista bledow i niespojnosci",
        f"- Lacznie wpisow w `anomalies.csv`: **{metrics['anomalies']['total_entries']}**",
        f"- Lacznie wpisow w `corrections.csv`: **{metrics['corrections']['total_entries']}**",
        "",
        "Najczestsze powody bledu:",
        markdown_table(top_anomaly_reasons, ["powod", "liczba"]),
        "",
        "Podzial anomalii wg kategorii:",
        markdown_table(
            [
                {"kategoria": key, "liczba": value}
                for key, value in metrics["anomalies"]["by_category"].items()
            ],
            ["kategoria", "liczba"],
        ),
        "",
        "## 3. Lista anomalii",
        "_Pelna lista znajduje sie w `anomalies.csv`._",
        markdown_table(
            top_anomalies,
            ["nr_ref", "category", "severity", "column", "raw_value", "reason"],
        ),
        "",
        "## 4. Tabela korekt (oryginal -> propozycja)",
        "_Pelna lista znajduje sie w `corrections.csv`._",
        markdown_table(
            top_corrections,
            ["nr_ref", "column", "original_value", "suggested_value", "method", "confidence"],
        ),
        "",
        "## 5. Wnioski i rekomendacje",
        (
            f"- Korekty niskiej pewnosci (`confidence < 0.5`): **{metrics['corrections']['low_confidence_count']}** "
            "i powinny pozostac do recznej weryfikacji."
        ),
        "- Glowny wzorzec bledow to kombinacja bledow technicznych, logicznych i administracyjnych ujawnionych w tabelach powyzej.",
        "- Zalecane reguly walidacyjne do systemu SOiA:",
    ]
    summary_lines.extend([f"  - {item}" for item in main_recommendations])

    return "\n".join(summary_lines) + "\n"


def write_outputs(
    output_dir: Path,
    source_path: Path,
    anomalies_df: pd.DataFrame,
    corrections_df: pd.DataFrame,
    metrics: dict[str, Any],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    anomalies_path = output_dir / "anomalies.csv"
    corrections_path = output_dir / "corrections.csv"
    metrics_path = output_dir / "quality_metrics.json"
    summary_path = output_dir / "summary.md"

    anomalies_df.to_csv(anomalies_path, index=False, encoding="utf-8")
    corrections_df.to_csv(corrections_path, index=False, encoding="utf-8")
    metrics_path.write_text(
        json.dumps(metrics, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    summary_path.write_text(
        build_summary_markdown(source_path, anomalies_df, corrections_df, metrics),
        encoding="utf-8",
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audyt i korekta CSV inwentaryzacji syren SOiA")
    parser.add_argument("--input", required=True, help="Sciezka do pliku CSV z inwentaryzacja.")
    parser.add_argument("--output-dir", required=True, help="Katalog wyjsciowy na raport i eksporty.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    source_path = Path(args.input).resolve()
    output_dir = Path(args.output_dir).resolve()

    df = read_inventory(source_path)
    anomalies_df, corrections_df, metrics = analyze_inventory(df, source_path)
    write_outputs(output_dir, source_path, anomalies_df, corrections_df, metrics)

    print(f"Wygenerowano raport: {output_dir / 'summary.md'}")
    print(f"Wygenerowano anomalie: {output_dir / 'anomalies.csv'}")
    print(f"Wygenerowano korekty: {output_dir / 'corrections.csv'}")
    print(f"Wygenerowano metryki: {output_dir / 'quality_metrics.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
