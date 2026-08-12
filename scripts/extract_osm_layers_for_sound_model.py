#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import fiona
import osmium
from pyproj import Transformer
from shapely import wkb
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Polygon, mapping
from shapely.ops import transform


BUILDING_TAG = "building"
DEFAULT_MAX_BUILDING_SHP_MIB = 1500
ROAD_VALUES = {
    "motorway",
    "trunk",
    "primary",
    "secondary",
    "tertiary",
    "unclassified",
    "residential",
    "service",
    "living_street",
    "pedestrian",
    "track",
}
CLUTTER_RULES = {
    "landuse": {
        "residential": "residential",
        "industrial": "industrial",
        "commercial": "commercial",
        "retail": "commercial",
        "forest": "forest",
        "farmland": "farmland",
        "farmyard": "farmyard",
        "meadow": "open_green",
        "grass": "open_green",
        "orchard": "orchard",
        "vineyard": "orchard",
        "cemetery": "open_green",
        "quarry": "industrial",
        "railway": "industrial",
        "construction": "construction",
        "military": "restricted",
        "allotments": "open_green",
    },
    "natural": {
        "wood": "forest",
        "scrub": "scrub",
        "grassland": "open_green",
        "heath": "scrub",
        "wetland": "wetland",
        "water": "water",
        "bare_rock": "bare",
        "sand": "bare",
        "beach": "bare",
    },
    "leisure": {
        "park": "open_green",
        "garden": "open_green",
        "golf_course": "open_green",
        "sports_centre": "open_green",
        "pitch": "open_green",
        "nature_reserve": "open_green",
    },
    "amenity": {
        "parking": "parking",
        "school": "public",
        "university": "public",
        "hospital": "public",
    },
}
CLUTTER_ATTENUATION_DB = {
    "residential": 6.0,
    "industrial": 8.0,
    "commercial": 7.0,
    "forest": 10.0,
    "farmland": 1.0,
    "farmyard": 3.0,
    "open_green": 1.5,
    "orchard": 4.0,
    "scrub": 5.0,
    "wetland": 2.0,
    "water": 0.0,
    "bare": 0.5,
    "construction": 5.0,
    "restricted": 5.0,
    "parking": 1.0,
    "public": 5.0,
}


class ExtractionLimitReached(Exception):
    pass


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ekstrahuje warstwy OSM do modelu akustycznego syren.")
    parser.add_argument("--pbf", required=True, help="Plik OSM PBF z Geofabrik.")
    parser.add_argument("--output-dir", required=True, help="Katalog wyjsciowy SHP.")
    parser.add_argument(
        "--limit",
        type=int,
        help="Opcjonalny limit zapisywanych obiektow na warstwe do testow.",
    )
    parser.add_argument(
        "--max-building-shp-mib",
        type=int,
        default=DEFAULT_MAX_BUILDING_SHP_MIB,
        help="Maksymalny rozmiar komponentu .shp dla jednej czesci budynkow.",
    )
    parser.add_argument(
        "--max-clutter-shp-mib",
        type=int,
        default=DEFAULT_MAX_BUILDING_SHP_MIB,
        help="Maksymalny rozmiar komponentu .shp dla jednej czesci clutteru.",
    )
    return parser.parse_args(argv)


def clean_shp(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    for suffix in [".shp", ".shx", ".dbf", ".prj", ".cpg", ".fix", ".qix"]:
        candidate = path.with_suffix(suffix)
        if candidate.exists():
            candidate.unlink()


def parse_float(value: str | None) -> float:
    if not value:
        return 0.0
    cleaned = value.replace(",", ".").strip()
    if ";" in cleaned:
        cleaned = cleaned.split(";", 1)[0]
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def building_height(tags: dict[str, str]) -> float:
    height = parse_float(tags.get("height"))
    if height > 0:
        return height
    levels = parse_float(tags.get("building:levels"))
    if levels > 0:
        return levels * 3.0
    return 0.0


def clutter_class(tags: dict[str, str]) -> tuple[str, str, str, float] | None:
    for key, mapping_by_value in CLUTTER_RULES.items():
        value = tags.get(key)
        if value in mapping_by_value:
            cls = mapping_by_value[value]
            return key, value, cls, CLUTTER_ATTENUATION_DB.get(cls, 0.0)
    return None


class OsmLayerExtractor(osmium.SimpleHandler):
    def __init__(
        self,
        output_dir: Path,
        limit: int | None = None,
        max_building_shp_mib: int = DEFAULT_MAX_BUILDING_SHP_MIB,
        max_clutter_shp_mib: int = DEFAULT_MAX_BUILDING_SHP_MIB,
    ) -> None:
        super().__init__()
        self.output_dir = output_dir
        self.limit = limit
        self.max_building_shp_bytes = max_building_shp_mib * 1024 * 1024
        self.max_clutter_shp_bytes = max_clutter_shp_mib * 1024 * 1024
        self.building_part = 1
        self.clutter_part = 1
        self.factory = osmium.geom.WKBFactory()
        self.transformer = Transformer.from_crs("EPSG:4326", "EPSG:2180", always_xy=True)
        self.counts: Counter[str] = Counter()
        self.errors: Counter[str] = Counter()
        self.value_counts: dict[str, Counter[str]] = {
            "buildings": Counter(),
            "roads": Counter(),
            "clutter": Counter(),
        }
        self.writers = self._open_writers()

    def _open_writers(self) -> dict[str, Any]:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.paths = {
            "buildings": [],
            "roads": self.output_dir / "osm_roads_PL_2180.shp",
            "clutter": [],
        }
        for path in self.output_dir.glob("osm_buildings_PL_2180_part*.shp"):
            clean_shp(path)
        for path in self.output_dir.glob("osm_clutter_PL_2180_part*.shp"):
            clean_shp(path)
        clean_shp(self.output_dir / "osm_buildings_PL_2180.shp")
        clean_shp(self.output_dir / "osm_clutter_PL_2180.shp")
        for path in [self.paths["roads"]]:
            clean_shp(path)

        common_crs = "EPSG:2180"
        writers = {
            "roads": fiona.open(
                self.paths["roads"],
                "w",
                driver="ESRI Shapefile",
                crs=common_crs,
                encoding="UTF-8",
                schema={
                    "geometry": "MultiLineString",
                    "properties": {
                        "osm_id": "str:32",
                        "highway": "str:40",
                        "name": "str:120",
                        "length_m": "float:14.2",
                    },
                },
            ),
        }
        writers["buildings"] = self._open_building_writer()
        writers["clutter"] = self._open_clutter_writer()
        for path in [self.paths["roads"]]:
            path.with_suffix(".cpg").write_text("UTF-8", encoding="ascii")
        return writers

    def _open_building_writer(self):
        path = self.output_dir / f"osm_buildings_PL_2180_part{self.building_part:03d}.shp"
        clean_shp(path)
        self.paths["buildings"].append(path)
        self.current_building_path = path
        path.with_suffix(".cpg").write_text("UTF-8", encoding="ascii")
        return fiona.open(
            path,
            "w",
            driver="ESRI Shapefile",
            crs="EPSG:2180",
            encoding="UTF-8",
            schema={
                "geometry": "MultiPolygon",
                "properties": {
                    "osm_id": "str:32",
                    "bldg": "str:40",
                    "height_m": "float:10.2",
                    "levels": "float:10.2",
                    "area_m2": "float:14.2",
                },
            },
        )

    def _open_clutter_writer(self):
        path = self.output_dir / f"osm_clutter_PL_2180_part{self.clutter_part:03d}.shp"
        clean_shp(path)
        self.paths["clutter"].append(path)
        self.current_clutter_path = path
        path.with_suffix(".cpg").write_text("UTF-8", encoding="ascii")
        return fiona.open(
            path,
            "w",
            driver="ESRI Shapefile",
            crs="EPSG:2180",
            encoding="UTF-8",
            schema={
                "geometry": "MultiPolygon",
                "properties": {
                    "osm_id": "str:32",
                    "tag_key": "str:30",
                    "tag_val": "str:50",
                    "clutter": "str:30",
                    "att_db": "float:8.2",
                    "area_m2": "float:14.2",
                },
            },
        )

    def _rotate_buildings_if_needed(self) -> None:
        if self.max_building_shp_bytes <= 0:
            return
        if not self.current_building_path.exists():
            return
        if self.current_building_path.stat().st_size < self.max_building_shp_bytes:
            return
        self.writers["buildings"].close()
        self.building_part += 1
        self.writers["buildings"] = self._open_building_writer()

    def _rotate_clutter_if_needed(self) -> None:
        if self.max_clutter_shp_bytes <= 0:
            return
        if not self.current_clutter_path.exists():
            return
        if self.current_clutter_path.stat().st_size < self.max_clutter_shp_bytes:
            return
        self.writers["clutter"].close()
        self.clutter_part += 1
        self.writers["clutter"] = self._open_clutter_writer()

    def close(self) -> None:
        for writer in self.writers.values():
            writer.close()

    def _limit_reached(self, layer: str) -> bool:
        return self.limit is not None and self.counts[layer] >= self.limit

    def _stop_if_all_limits_reached(self) -> None:
        if self.limit is None:
            return
        if all(self.counts[layer] >= self.limit for layer in ["buildings", "roads", "clutter"]):
            raise ExtractionLimitReached

    def _to_2180(self, geometry):
        return transform(self.transformer.transform, geometry)

    def _write_polygon_layer(self, layer: str, osm_id: str, geometry, properties: dict[str, Any]) -> bool:
        if self._limit_reached(layer):
            return False
        if layer == "buildings":
            self._rotate_buildings_if_needed()
        elif layer == "clutter":
            self._rotate_clutter_if_needed()
        if geometry.is_empty:
            return False
        if isinstance(geometry, Polygon):
            geometry = MultiPolygon([geometry])
        if not isinstance(geometry, MultiPolygon):
            return False
        geometry_2180 = self._to_2180(geometry)
        if geometry_2180.is_empty:
            return False
        properties = dict(properties)
        properties["osm_id"] = osm_id
        properties["area_m2"] = round(float(geometry_2180.area), 2)
        self.writers[layer].write({"geometry": mapping(geometry_2180), "properties": properties})
        self.counts[layer] += 1
        self._stop_if_all_limits_reached()
        return True

    def _write_road(self, osm_id: str, geometry, properties: dict[str, Any]) -> bool:
        if self._limit_reached("roads"):
            return False
        if geometry.is_empty:
            return False
        if isinstance(geometry, LineString):
            geometry = MultiLineString([geometry])
        if not isinstance(geometry, MultiLineString):
            return False
        geometry_2180 = self._to_2180(geometry)
        properties = dict(properties)
        properties["osm_id"] = osm_id
        properties["length_m"] = round(float(geometry_2180.length), 2)
        self.writers["roads"].write({"geometry": mapping(geometry_2180), "properties": properties})
        self.counts["roads"] += 1
        self._stop_if_all_limits_reached()
        return True

    def way(self, way) -> None:
        tags = dict(way.tags)
        highway = tags.get("highway")
        if highway in ROAD_VALUES:
            try:
                geom = wkb.loads(self.factory.create_linestring(way), hex=True)
                written = self._write_road(
                    str(way.id),
                    geom,
                    {
                        "highway": highway,
                        "name": tags.get("name", "")[:120],
                    },
                )
                if written:
                    self.value_counts["roads"][highway] += 1
            except Exception:  # noqa: BLE001
                self.errors["roads"] += 1

    def area(self, area) -> None:
        tags = dict(area.tags)
        try:
            geom = wkb.loads(self.factory.create_multipolygon(area), hex=True)
        except Exception:  # noqa: BLE001
            self.errors["areas"] += 1
            return

        osm_id = str(area.orig_id())
        building = tags.get(BUILDING_TAG)
        if building and building != "no":
            written = self._write_polygon_layer(
                "buildings",
                osm_id,
                geom,
                {
                    "bldg": building[:40],
                    "height_m": building_height(tags),
                    "levels": parse_float(tags.get("building:levels")),
                },
            )
            if written:
                self.value_counts["buildings"][building] += 1
            return

        clutter = clutter_class(tags)
        if clutter:
            tag_key, tag_value, cls, att_db = clutter
            written = self._write_polygon_layer(
                "clutter",
                osm_id,
                geom,
                {
                    "tag_key": tag_key,
                    "tag_val": tag_value[:50],
                    "clutter": cls,
                    "att_db": att_db,
                },
            )
            if written:
                self.value_counts["clutter"][cls] += 1


def run(args: argparse.Namespace) -> dict[str, Any]:
    pbf_path = Path(args.pbf).resolve()
    output_dir = Path(args.output_dir).resolve()
    extractor = OsmLayerExtractor(
        output_dir=output_dir,
        limit=args.limit,
        max_building_shp_mib=args.max_building_shp_mib,
        max_clutter_shp_mib=args.max_clutter_shp_mib,
    )
    try:
        try:
            extractor.apply_file(str(pbf_path), locations=True)
        except ExtractionLimitReached:
            pass
    finally:
        extractor.close()

    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "pbf": str(pbf_path),
        "output_dir": str(output_dir),
        "limit": args.limit,
        "max_building_shp_mib": args.max_building_shp_mib,
        "max_clutter_shp_mib": args.max_clutter_shp_mib,
        "paths": {
            name: [str(path.resolve()) for path in path_or_paths]
            if isinstance(path_or_paths, list)
            else str(path_or_paths.resolve())
            for name, path_or_paths in extractor.paths.items()
        },
        "building_parts": len(extractor.paths["buildings"]),
        "clutter_parts": len(extractor.paths["clutter"]),
        "counts": dict(extractor.counts),
        "errors": dict(extractor.errors),
        "top_values": {
            layer: counter.most_common(30) for layer, counter in extractor.value_counts.items()
        },
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "osm_layers_extraction_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return summary


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = run(args)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
