#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import fiona
import requests
from fiona.transform import transform_geom


ATOM_URL = (
    "https://wody.isok.gov.pl/atom_web/atom/NZ_HY_MRP"
    "?spatial_dataset_identifier_code=MZP-MRP"
    "&spatial_dataset_identifier_namespace=http://iip.wody.gov.pl/id/dataset/PL.ZIPGW.6453"
)

ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}

FIELD_SCHEMA = {
    "geometry": "Polygon",
    "properties": {
        "gml_id": "str:80",
        "local_id": "str:64",
        "risk": "str:32",
        "begin_date": "str:10",
        "end_date": "str:10",
        "src_file": "str:40",
    },
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Pobiera i scala krajowe dane ISOK MRP RiskZone do jednego SHP."
    )
    parser.add_argument(
        "--output-shp",
        default="data/isok-mrp/MZP_MRP_RiskZone_PL_2180.shp",
        help="Docelowy SHP. Pliki towarzyszące zostaną zapisane obok.",
    )
    parser.add_argument("--target-crs", default="EPSG:2180", help="CRS wyniku.")
    parser.add_argument("--atom-url", default=ATOM_URL, help="Adres feedu ATOM ISOK MZP/MRP.")
    parser.add_argument("--timeout", type=int, default=240, help="Timeout pobierania pojedynczej paczki.")
    parser.add_argument("--max-files", type=int, help="Limit paczek do testów.")
    parser.add_argument("--max-features", type=int, help="Limit obiektów do testów.")
    parser.add_argument("--force", action="store_true", help="Nadpisz istniejący SHP.")
    parser.add_argument(
        "--summary-json",
        default="data/isok-mrp/MZP_MRP_RiskZone_PL_2180.summary.json",
        help="Plik podsumowania.",
    )
    return parser.parse_args(argv)


def clean_shp(path: Path) -> None:
    for suffix in [".shp", ".shx", ".dbf", ".prj", ".cpg", ".fix", ".qix"]:
        candidate = path.with_suffix(suffix)
        if candidate.exists():
            candidate.unlink()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def fetch_atom_links(atom_url: str, timeout: int) -> list[dict[str, str]]:
    response = requests.get(atom_url, timeout=timeout)
    response.raise_for_status()
    root = ET.fromstring(response.content)
    links: list[dict[str, str]] = []
    for link in root.findall(".//atom:link", ATOM_NS):
        title = link.attrib.get("title", "")
        href = link.attrib.get("href", "")
        if "MZP_MRP_RiskZone_" not in title or not href:
            continue
        match = re.search(r"MZP_MRP_RiskZone_(\d+)\.zip", title)
        if not match:
            continue
        links.append({"title": title, "href": href, "offset": int(match.group(1))})
    return sorted(links, key=lambda item: int(item["offset"]))


def value_to_str(value: Any, limit: int) -> str:
    if value in (None, ""):
        return ""
    return str(value)[:limit]


def output_properties(properties: Any, source_name: str) -> dict[str, Any]:
    return {
        "gml_id": value_to_str(properties.get("gml_id"), 80),
        "local_id": value_to_str(properties.get("localId"), 64),
        "risk": value_to_str(properties.get("qualitativeValue"), 32),
        "begin_date": value_to_str(properties.get("beginPosition"), 10),
        "end_date": value_to_str(properties.get("endPosition"), 10),
        "src_file": value_to_str(source_name.replace(".xml", ""), 40),
    }


def iter_extracted_xml(zip_path: Path, work_dir: Path) -> Path:
    with zipfile.ZipFile(zip_path) as archive:
        members = [name for name in archive.namelist() if name.lower().endswith(".xml")]
        if len(members) != 1:
            raise RuntimeError(f"Expected one XML in {zip_path.name}, got {members}")
        archive.extract(members[0], work_dir)
        return work_dir / members[0]


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    output_shp = Path(args.output_shp)
    summary_path = Path(args.summary_json)
    output_shp.parent.mkdir(parents=True, exist_ok=True)

    if output_shp.exists() and not args.force:
        raise SystemExit(f"Output exists: {output_shp}. Use --force to overwrite.")

    clean_shp(output_shp)

    links = fetch_atom_links(args.atom_url, args.timeout)
    if args.max_files is not None:
        links = links[: args.max_files]
    if not links:
        raise SystemExit("No RiskZone ATOM packages found.")

    started_at = datetime.now(timezone.utc).isoformat()
    feature_count = 0
    package_summaries: list[dict[str, Any]] = []
    risk_counts: dict[str, int] = {}

    print(f"Found {len(links)} RiskZone packages.")
    print(f"Writing {output_shp} in {args.target_crs}.")

    with fiona.open(
        output_shp,
        "w",
        driver="ESRI Shapefile",
        crs=args.target_crs,
        schema=FIELD_SCHEMA,
        encoding="UTF-8",
        layer_options=["2GB_LIMIT=NO"],
    ) as dst:
        for package_no, link in enumerate(links, start=1):
            with tempfile.TemporaryDirectory() as temp_name:
                temp_dir = Path(temp_name)
                zip_path = temp_dir / f"riskzone_{link['offset']}.zip"
                print(f"[{package_no}/{len(links)}] Downloading {link['title']}")
                response = requests.get(link["href"], timeout=args.timeout)
                response.raise_for_status()
                zip_path.write_bytes(response.content)
                xml_path = iter_extracted_xml(zip_path, temp_dir)

                package_count = 0
                with fiona.open(xml_path) as src:
                    source_crs = src.crs_wkt or src.crs
                    for feature in src:
                        if args.max_features is not None and feature_count >= args.max_features:
                            break
                        properties = output_properties(feature["properties"], xml_path.name)
                        risk = properties["risk"] or "unknown"
                        risk_counts[risk] = risk_counts.get(risk, 0) + 1
                        geometry = transform_geom(source_crs, args.target_crs, feature["geometry"])
                        dst.write({"geometry": geometry, "properties": properties})
                        feature_count += 1
                        package_count += 1
                        if feature_count % 50_000 == 0:
                            print(f"  written features: {feature_count}")

                package_summaries.append(
                    {
                        "title": link["title"],
                        "offset": link["offset"],
                        "features_written": package_count,
                        "zip_size_bytes": zip_path.stat().st_size,
                    }
                )
                if args.max_features is not None and feature_count >= args.max_features:
                    print(f"Reached --max-features={args.max_features}.")
                    break

    shapefile_sizes = {
        output_shp.with_suffix(suffix).name: output_shp.with_suffix(suffix).stat().st_size
        for suffix in [".shp", ".shx", ".dbf", ".prj", ".cpg"]
        if output_shp.with_suffix(suffix).exists()
    }
    summary = {
        "source": "ISOK/Wody Polskie ATOM MZP/MRP RiskZone",
        "atom_url": args.atom_url,
        "output_shp": str(output_shp),
        "target_crs": args.target_crs,
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "packages_found": len(fetch_atom_links(args.atom_url, args.timeout)),
        "packages_processed": len(package_summaries),
        "features_written": feature_count,
        "risk_counts": dict(sorted(risk_counts.items())),
        "files": shapefile_sizes,
        "packages": package_summaries,
    }
    write_json(summary_path, summary)
    print(f"Done. Features written: {feature_count}")
    print(f"Summary: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
