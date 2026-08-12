from __future__ import annotations

import sqlite3
import struct
from collections.abc import Iterable
from pathlib import Path

LAYERS = {
    "admin_boundary": ("MULTIPOLYGON", 2180),
    "sirens": ("POINT", 4326),
    "coverage_ranges": ("MULTIPOLYGON", 2180),
    "priority_gaps": ("POINT", 4326),
    "candidates": ("POINT", 4326),
    "sensitive_objects": ("POINT", 4326),
}


def point_blob(lon: float, lat: float, srs_id: int = 4326) -> bytes:
    header = b"GP" + bytes((0, 1)) + struct.pack("<i", srs_id)
    wkb = struct.pack("<BI2d", 1, 1, lon, lat)
    return header + wkb


def _core_tables(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        PRAGMA application_id=1196444487;
        PRAGMA user_version=10300;
        CREATE TABLE gpkg_spatial_ref_sys (
          srs_name TEXT NOT NULL, srs_id INTEGER NOT NULL PRIMARY KEY,
          organization TEXT NOT NULL, organization_coordsys_id INTEGER NOT NULL,
          definition TEXT NOT NULL, description TEXT
        );
        CREATE TABLE gpkg_contents (
          table_name TEXT NOT NULL PRIMARY KEY, data_type TEXT NOT NULL,
          identifier TEXT UNIQUE, description TEXT DEFAULT '',
          last_change DATETIME NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
          min_x DOUBLE, min_y DOUBLE, max_x DOUBLE, max_y DOUBLE, srs_id INTEGER
        );
        CREATE TABLE gpkg_geometry_columns (
          table_name TEXT NOT NULL, column_name TEXT NOT NULL,
          geometry_type_name TEXT NOT NULL, srs_id INTEGER NOT NULL,
          z TINYINT NOT NULL, m TINYINT NOT NULL,
          PRIMARY KEY (table_name, column_name)
        );
        """
    )
    connection.executemany(
        "INSERT INTO gpkg_spatial_ref_sys VALUES (?,?,?,?,?,?)",
        [
            ("Undefined Cartesian", -1, "NONE", -1, "undefined", None),
            ("Undefined Geographic", 0, "NONE", 0, "undefined", None),
            ("WGS 84", 4326, "EPSG", 4326, 'GEOGCS["WGS 84"]', "longitude/latitude"),
            ("ETRS89 / Poland CS92", 2180, "EPSG", 2180, 'PROJCS["ETRS89 / Poland CS92"]', "PUWG 1992"),
        ],
    )


def _add_layer(connection: sqlite3.Connection, name: str, geometry: str, srs_id: int) -> None:
    connection.execute(
        f'CREATE TABLE "{name}" (fid INTEGER PRIMARY KEY AUTOINCREMENT, geom BLOB, attributes_json TEXT)'
    )
    connection.execute(
        "INSERT INTO gpkg_contents(table_name,data_type,identifier,srs_id) VALUES (?,?,?,?)",
        (name, "features", name, srs_id),
    )
    connection.execute(
        "INSERT INTO gpkg_geometry_columns VALUES (?,?,?,?,0,0)",
        (name, "geom", geometry, srs_id),
    )


def create_geopackage(
    path: Path,
    *,
    points: dict[str, Iterable[tuple[float, float, str]]],
    admin_geometry: bytes | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    with sqlite3.connect(path) as connection:
        _core_tables(connection)
        for name, (geometry, srs_id) in LAYERS.items():
            _add_layer(connection, name, geometry, srs_id)
        if admin_geometry:
            connection.execute(
                "INSERT INTO admin_boundary(geom,attributes_json) VALUES (?,?)",
                (admin_geometry, "{}"),
            )
        for layer, rows in points.items():
            connection.executemany(
                f'INSERT INTO "{layer}"(geom,attributes_json) VALUES (?,?)',
                [(point_blob(lon, lat), attributes) for lon, lat, attributes in rows],
            )


def read_admin_geometry(source: Path, level: str, code: str) -> bytes | None:
    if not source.exists():
        return None
    mapping = {
        "gmina": ("gminy", "teryt_gmi"),
        "powiat": ("powiaty", "teryt_pow"),
        "wojewodztwo": ("wojewodztwa", "teryt_woj"),
    }
    table, field = mapping[level]
    with sqlite3.connect(source) as connection:
        row = connection.execute(f'SELECT geom FROM "{table}" WHERE "{field}"=?', (code,)).fetchone()
    return row[0] if row else None


def create_spatial_geopackage(
    path: Path,
    *,
    source: Path,
    level: str,
    code: str,
    inventory: list[dict[str, str]],
    buffer_km: float = 10.0,
) -> dict[str, int] | None:
    """Tworzy rzeczywisty wycinek GIS, jeśli dostępny jest backend GeoPandas.

    Funkcja zwraca ``False`` wyłącznie, gdy pakiety GIS nie są zainstalowane;
    dzięki temu małe testy jednostkowe mogą używać minimalnego writer-a SQLite.
    """
    try:
        import geopandas as gpd
        from shapely.geometry import Point
    except ImportError:
        return None

    mapping = {
        "gmina": ("gminy", "teryt_gmi"),
        "powiat": ("powiaty", "teryt_pow"),
        "wojewodztwo": ("wojewodztwa", "teryt_woj"),
    }
    admin_layer, field = mapping[level]
    boundary = gpd.read_file(
        source, layer=admin_layer, where=f"{field} = '{code}'", engine="pyogrio"
    )
    if boundary.empty:
        raise ValueError(f"Brak geometrii granicy TERYT {code}")
    if boundary.crs is None:
        boundary = boundary.set_crs(2180)
    boundary = boundary.to_crs(2180)
    context_geometry = boundary.geometry.union_all().buffer(buffer_km * 1000)

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    boundary.to_file(path, layer="admin_boundary", driver="GPKG", engine="pyogrio")

    point_rows = []
    geometries = []
    for row in inventory:
        try:
            lon, lat = float(row["lon"]), float(row["lat"])
        except (KeyError, TypeError, ValueError):
            continue
        point_rows.append(row)
        geometries.append(Point(lon, lat))
    sirens = gpd.GeoDataFrame(point_rows, geometry=geometries, crs=4326).to_crs(2180)
    sirens = sirens[sirens.geometry.intersects(context_geometry)]
    sirens.to_file(path, layer="sirens", driver="GPKG", engine="pyogrio", append=True)
    counts = {"sirens_context": len(sirens)}

    for source_layer, target_layer in (
        ("coverage_ranges", "coverage_ranges"),
        ("priority_gaps", "priority_gaps"),
        ("candidates", "candidates"),
        ("sensitive_objects", "sensitive_objects"),
    ):
        available = {item[0] for item in __import__("pyogrio").list_layers(source)}
        if source_layer not in available:
            frame = gpd.GeoDataFrame({"status": []}, geometry=[], crs=2180)
        else:
            frame = gpd.read_file(source, layer=source_layer, mask=context_geometry, engine="pyogrio")
            if frame.crs is None:
                frame = frame.set_crs(2180)
            else:
                frame = frame.to_crs(2180)
        frame.to_file(path, layer=target_layer, driver="GPKG", engine="pyogrio", append=True)
        counts[target_layer] = len(frame)
    return counts
