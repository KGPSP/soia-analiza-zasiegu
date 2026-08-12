#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import unicodedata
from pathlib import Path
from typing import Any

import pandas as pd


SOURCE_COLUMNS = [
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

STATUS_COLUMN_MAP = {
    "GSM": "gsm_status",
    "SK PSP": "sk_psp_status",
    "Sygn. OSP": "sygn_osp_status",
    "Sygn. OL": "sygn_ol_status",
    "Wgr. sygn.": "wgr_sygn_status",
}

POLISH_CHAR_MAP = str.maketrans(
    {
        "ą": "a",
        "ć": "c",
        "ę": "e",
        "ł": "l",
        "ń": "n",
        "ó": "o",
        "ś": "s",
        "ź": "z",
        "ż": "z",
        "Ą": "A",
        "Ć": "C",
        "Ę": "E",
        "Ł": "L",
        "Ń": "N",
        "Ó": "O",
        "Ś": "S",
        "Ź": "Z",
        "Ż": "Z",
    }
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Normalizacja CSV inwentaryzacji syren do analizy.")
    parser.add_argument("--input", required=True, help="Poprawiony plik CSV, najlepiej wariant conservative.")
    parser.add_argument("--anomalies", required=False, help="anomalies.csv po korekcie do dolaczenia flag jakosci.")
    parser.add_argument("--output", required=True, help="Wyjsciowy znormalizowany plik CSV.")
    parser.add_argument("--schema-json", required=True, help="Slownik kolumn i typow wyjsciowych.")
    return parser.parse_args(argv)


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).replace("\xa0", " ").strip()
    return " ".join(text.split())


def canonical_text(value: Any) -> str:
    text = clean_text(value).lower()
    return text


def ascii_key(value: Any) -> str:
    text = canonical_text(value)
    text = text.translate(POLISH_CHAR_MAP)
    normalized = unicodedata.normalize("NFKD", text)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    ascii_text = re.sub(r"[^a-z0-9]+", "_", ascii_text)
    return ascii_text.strip("_")


def status_to_flag(value: Any) -> pd._libs.missing.NAType | int:
    text = clean_text(value).upper()
    if text == "TAK":
        return 1
    if text in {"NIE", "N/D", "", "0"}:
        return 0
    return pd.NA


def parse_date_iso(series: pd.Series) -> pd.Series:
    parsed = pd.to_datetime(series.map(clean_text), dayfirst=True, errors="coerce")
    return parsed.dt.strftime("%Y-%m-%d").fillna("")


def parse_numeric(series: pd.Series, *, dtype: str) -> pd.Series:
    parsed = pd.to_numeric(series.map(lambda value: clean_text(value).replace(",", ".")), errors="coerce")
    if dtype == "int":
        return parsed.round().astype("Int64")
    return parsed.astype("Float64")


def build_quality_flags(anomalies_path: Path | None) -> pd.DataFrame:
    columns = [
        "nr_ref",
        "anomaly_count",
        "anomaly_high_count",
        "anomaly_medium_count",
        "anomaly_low_count",
        "has_logical_anomaly",
        "has_technical_anomaly",
        "has_geographic_anomaly",
        "has_relational_anomaly",
        "max_anomaly_severity",
        "record_status",
    ]
    if anomalies_path is None:
        return pd.DataFrame(columns=columns)

    anomalies_df = pd.read_csv(anomalies_path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    if anomalies_df.empty:
        return pd.DataFrame(columns=columns)

    anomalies_df["confidence"] = pd.to_numeric(anomalies_df["confidence"], errors="coerce")
    grouped = anomalies_df.groupby("nr_ref", dropna=False)
    rows: list[dict[str, Any]] = []
    severity_rank = {"high": 3, "medium": 2, "low": 1, "info": 0}
    rank_to_severity = {value: key for key, value in severity_rank.items()}

    for nr_ref, group in grouped:
        severities = group["severity"].map(lambda value: severity_rank.get(str(value), -1))
        max_rank = int(severities.max()) if not severities.empty else -1
        rows.append(
            {
                "nr_ref": nr_ref,
                "anomaly_count": int(len(group)),
                "anomaly_high_count": int((group["severity"] == "high").sum()),
                "anomaly_medium_count": int((group["severity"] == "medium").sum()),
                "anomaly_low_count": int((group["severity"] == "low").sum()),
                "has_logical_anomaly": int((group["category"] == "logical").any()),
                "has_technical_anomaly": int((group["category"] == "technical").any()),
                "has_geographic_anomaly": int((group["category"] == "geographic").any()),
                "has_relational_anomaly": int((group["category"] == "relational").any()),
                "max_anomaly_severity": rank_to_severity.get(max_rank, ""),
                "record_status": "REVIEW" if len(group) else "OK",
            }
        )

    return pd.DataFrame(rows, columns=columns)


def normalize_inventory(source_df: pd.DataFrame, quality_df: pd.DataFrame) -> pd.DataFrame:
    for column in SOURCE_COLUMNS:
        if column not in source_df.columns:
            raise ValueError(f"Brak wymaganej kolumny w pliku zrodlowym: {column}")

    normalized = pd.DataFrame()
    normalized["lp"] = parse_numeric(source_df["Lp."], dtype="int")
    normalized["nr_ref"] = source_df["Nr ref."].map(clean_text)
    normalized["data_iso"] = parse_date_iso(source_df["Data"])
    normalized["wojewodztwo"] = source_df["Województwo"].map(clean_text)
    normalized["wojewodztwo_key"] = source_df["Województwo"].map(ascii_key)
    normalized["powiat"] = source_df["Powiat"].map(clean_text)
    normalized["powiat_key"] = source_df["Powiat"].map(ascii_key)
    normalized["gmina"] = source_df["Gmina"].map(clean_text)
    normalized["gmina_key"] = source_df["Gmina"].map(ascii_key)
    normalized["wlasciciel"] = source_df["Właściciel"].map(clean_text)
    normalized["wlasciciel_key"] = source_df["Właściciel"].map(ascii_key)
    normalized["rodzaj_syreny"] = source_df["Rodzaj syreny"].map(clean_text)
    normalized["rodzaj_syreny_key"] = source_df["Rodzaj syreny"].map(ascii_key)
    normalized["moc_w"] = parse_numeric(source_df["Moc [W]"], dtype="int")
    normalized["wysokosc_nad_terenem_m"] = parse_numeric(source_df["Wysokość nad terenem [m]"], dtype="float")
    normalized["lat"] = parse_numeric(source_df["Lat"], dtype="float")
    normalized["lon"] = parse_numeric(source_df["Long"], dtype="float")
    normalized["jednostka_psp"] = source_df["Jednostka PSP"].map(clean_text)
    normalized["jednostka_psp_key"] = source_df["Jednostka PSP"].map(ascii_key)
    normalized["kod_swd"] = source_df["Kod SWD"].map(clean_text)

    for source_column, target_column in STATUS_COLUMN_MAP.items():
        normalized[target_column] = source_df[source_column].map(lambda value: clean_text(value).upper())
        normalized[target_column.replace("_status", "_flag")] = normalized[target_column].map(status_to_flag).astype("Int64")

    normalized = normalized.merge(quality_df, on="nr_ref", how="left")

    quality_defaults = {
        "anomaly_count": 0,
        "anomaly_high_count": 0,
        "anomaly_medium_count": 0,
        "anomaly_low_count": 0,
        "has_logical_anomaly": 0,
        "has_technical_anomaly": 0,
        "has_geographic_anomaly": 0,
        "has_relational_anomaly": 0,
    }
    for column, default_value in quality_defaults.items():
        normalized[column] = normalized[column].fillna(default_value).astype("Int64")
    normalized["max_anomaly_severity"] = normalized["max_anomaly_severity"].fillna("0")
    normalized["record_status"] = normalized["record_status"].fillna("OK")
    normalized["is_clean_record"] = (normalized["record_status"] == "OK").astype("Int64")

    # Finalny plik analityczny ma nie zawierac pustych komorek.
    object_columns = normalized.select_dtypes(include=["object", "string"]).columns
    normalized[object_columns] = normalized[object_columns].replace("", "0").fillna("0")
    numeric_like_columns = normalized.columns.difference(object_columns)
    normalized[numeric_like_columns] = normalized[numeric_like_columns].fillna(0)

    return normalized


def build_schema() -> dict[str, Any]:
    return {
        "description": "Znormalizowany zbior syren gotowy do analizy tabelarycznej.",
        "columns": [
            {"name": "lp", "type": "Int64", "description": "Numer porzadkowy rekordu."},
            {"name": "nr_ref", "type": "string", "description": "Unikalny identyfikator referencyjny SOiA."},
            {"name": "data_iso", "type": "string", "description": "Data rekordu w formacie ISO YYYY-MM-DD."},
            {"name": "wojewodztwo", "type": "string", "description": "Nazwa wojewodztwa po trimowaniu."},
            {"name": "wojewodztwo_key", "type": "string", "description": "Kanoniczny klucz ASCII do grupowania."},
            {"name": "powiat", "type": "string", "description": "Nazwa powiatu po trimowaniu."},
            {"name": "powiat_key", "type": "string", "description": "Kanoniczny klucz ASCII do grupowania."},
            {"name": "gmina", "type": "string", "description": "Nazwa gminy po trimowaniu."},
            {"name": "gmina_key", "type": "string", "description": "Kanoniczny klucz ASCII do grupowania."},
            {"name": "wlasciciel", "type": "string", "description": "Znormalizowana etykieta wlasciciela."},
            {"name": "wlasciciel_key", "type": "string", "description": "Kanoniczny klucz ASCII wlasciciela."},
            {"name": "rodzaj_syreny", "type": "string", "description": "Rodzaj syreny po trimowaniu."},
            {"name": "rodzaj_syreny_key", "type": "string", "description": "Kanoniczny klucz ASCII rodzaju syreny."},
            {"name": "moc_w", "type": "Int64", "description": "Moc syreny w watach."},
            {"name": "wysokosc_nad_terenem_m", "type": "Float64", "description": "Wysokosc montazu nad terenem w metrach."},
            {"name": "lat", "type": "Float64", "description": "Szerokosc geograficzna."},
            {"name": "lon", "type": "Float64", "description": "Dlugosc geograficzna."},
            {"name": "jednostka_psp", "type": "string", "description": "Nazwa jednostki PSP."},
            {"name": "jednostka_psp_key", "type": "string", "description": "Kanoniczny klucz ASCII jednostki PSP."},
            {"name": "kod_swd", "type": "string", "description": "Kod SWD zachowany jako tekst."},
            {"name": "gsm_status", "type": "string", "description": "Status logiczny TAK/NIE/N/D."},
            {"name": "gsm_flag", "type": "Int64", "description": "1=TAK, 0=brak potwierdzenia TAK."},
            {"name": "sk_psp_status", "type": "string", "description": "Status logiczny TAK/NIE/N/D."},
            {"name": "sk_psp_flag", "type": "Int64", "description": "1=TAK, 0=brak potwierdzenia TAK."},
            {"name": "sygn_osp_status", "type": "string", "description": "Status logiczny TAK/NIE/N/D."},
            {"name": "sygn_osp_flag", "type": "Int64", "description": "1=TAK, 0=brak potwierdzenia TAK."},
            {"name": "sygn_ol_status", "type": "string", "description": "Status logiczny TAK/NIE/N/D."},
            {"name": "sygn_ol_flag", "type": "Int64", "description": "1=TAK, 0=brak potwierdzenia TAK."},
            {"name": "wgr_sygn_status", "type": "string", "description": "Status logiczny TAK/NIE/N/D."},
            {"name": "wgr_sygn_flag", "type": "Int64", "description": "1=TAK, 0=brak potwierdzenia TAK."},
            {"name": "anomaly_count", "type": "Int64", "description": "Liczba pozostalych anomalii po korekcie."},
            {"name": "anomaly_high_count", "type": "Int64", "description": "Liczba anomalii wysokiej wagi."},
            {"name": "anomaly_medium_count", "type": "Int64", "description": "Liczba anomalii sredniej wagi."},
            {"name": "anomaly_low_count", "type": "Int64", "description": "Liczba anomalii niskiej wagi."},
            {"name": "has_logical_anomaly", "type": "Int64", "description": "Flaga obecnosci anomalii logicznej."},
            {"name": "has_technical_anomaly", "type": "Int64", "description": "Flaga obecnosci anomalii technicznej."},
            {"name": "has_geographic_anomaly", "type": "Int64", "description": "Flaga obecnosci anomalii geograficznej."},
            {"name": "has_relational_anomaly", "type": "Int64", "description": "Flaga obecnosci anomalii relacyjnej."},
            {"name": "max_anomaly_severity", "type": "string", "description": "Najwyzsza pozostala waga anomalii; 0 oznacza brak."},
            {"name": "record_status", "type": "string", "description": "OK albo REVIEW."},
            {"name": "is_clean_record", "type": "Int64", "description": "1 dla rekordu bez pozostalych anomalii."},
        ],
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    input_path = Path(args.input).resolve()
    anomalies_path = Path(args.anomalies).resolve() if args.anomalies else None
    output_path = Path(args.output).resolve()
    schema_path = Path(args.schema_json).resolve()

    source_df = pd.read_csv(input_path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    quality_df = build_quality_flags(anomalies_path)
    normalized_df = normalize_inventory(source_df, quality_df)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    normalized_df.to_csv(output_path, index=False, encoding="utf-8-sig")
    schema_path.parent.mkdir(parents=True, exist_ok=True)
    schema_path.write_text(json.dumps(build_schema(), ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Znormalizowany plik: {output_path}")
    print(f"Slownik kolumn: {schema_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
