import hashlib
import zipfile
from pathlib import Path

import pytest

from soia.release import (
    ChecksumError,
    UnsafeArchiveError,
    build_data_manifest,
    build_zip64,
    expand_package_specification,
    fetch_release,
)


def test_build_data_manifest_records_file_metadata(tmp_path: Path) -> None:
    source = tmp_path / "source.csv"
    source.write_text("a,b\n1,2\n", encoding="utf-8")
    groups = {
        "release": "2026.05",
        "owner": "Komenda Główna Państwowej Straży Pożarnej (KG PSP)",
        "responsible_unit": "Biuro Informatyki i Łączności KG PSP (BIŁ KG PSP)",
        "prepared_by": "Zespół pod kierownictwem st. bryg. Michała Kłosińskiego",
        "files": [
            {
                "path": "source.csv",
                "package": "sources",
                "stage": "source",
                "source": "test",
                "acquired": "2026-05-05",
                "license": "CC-BY-4.0",
                "format": "CSV",
                "crs": None,
                "layer": None,
                "feature_count": 1,
                "dependencies": [],
                "kind": "source"
            }
        ],
    }

    manifest = build_data_manifest(tmp_path, groups)

    item = manifest["files"][0]
    assert item["size_bytes"] == source.stat().st_size
    assert item["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert item["release"] == "2026.05"
    assert item["crs"] is None
    assert manifest["owner"] == "Komenda Główna Państwowej Straży Pożarnej (KG PSP)"
    assert manifest["responsible_unit"] == "Biuro Informatyki i Łączności KG PSP (BIŁ KG PSP)"
    assert manifest["prepared_by"] == "Zespół pod kierownictwem st. bryg. Michała Kłosińskiego"


def test_build_zip64_excludes_technical_files(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "data.csv").write_text("ok", encoding="utf-8")
    (source / ".DS_Store").write_text("no", encoding="utf-8")
    (source / "invoice.pdf").write_text("no", encoding="utf-8")
    output = tmp_path / "package.zip"

    build_zip64(
        output,
        [(source / "data.csv", "data/data.csv")],
        force=False,
    )

    with zipfile.ZipFile(output) as archive:
        assert archive.namelist() == ["data/data.csv"]


def test_fetch_release_verifies_checksum_and_extracts_safely(tmp_path: Path) -> None:
    package = tmp_path / "quickstart.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("quickstart/metrics.csv", "value\n1\n")
    digest = hashlib.sha256(package.read_bytes()).hexdigest()
    manifest = {
        "release": "2026.05",
        "archives": [
            {
                "role": "quickstart",
                "name": package.name,
                "url": package.as_uri(),
                "size_bytes": package.stat().st_size,
                "sha256": digest,
            }
        ],
    }

    result = fetch_release(manifest, tmp_path / "data", role="quickstart")

    assert (result / "quickstart" / "metrics.csv").read_text() == "value\n1\n"


def test_fetch_release_rejects_bad_checksum(tmp_path: Path) -> None:
    package = tmp_path / "quickstart.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("data.csv", "x")
    manifest = {
        "release": "2026.05",
        "archives": [{
            "role": "quickstart", "name": package.name, "url": package.as_uri(),
            "size_bytes": package.stat().st_size, "sha256": "0" * 64
        }],
    }

    with pytest.raises(ChecksumError):
        fetch_release(manifest, tmp_path / "data", role="quickstart")


def test_fetch_release_rejects_zip_path_traversal(tmp_path: Path) -> None:
    package = tmp_path / "bad.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("../escape.txt", "bad")
    manifest = {
        "release": "2026.05",
        "archives": [{
            "role": "quickstart", "name": package.name, "url": package.as_uri(),
            "size_bytes": package.stat().st_size,
            "sha256": hashlib.sha256(package.read_bytes()).hexdigest()
        }],
    }

    with pytest.raises(UnsafeArchiveError):
        fetch_release(manifest, tmp_path / "data", role="quickstart")


def test_fetch_release_tracks_each_role_independently(tmp_path: Path) -> None:
    archives = []
    for role in ("sources", "derived"):
        package = tmp_path / f"{role}.zip"
        with zipfile.ZipFile(package, "w") as archive:
            archive.writestr(f"{role}.txt", role)
        archives.append({
            "role": role, "name": package.name, "url": package.as_uri(),
            "size_bytes": package.stat().st_size,
            "sha256": hashlib.sha256(package.read_bytes()).hexdigest(),
        })
    manifest = {"release": "2026.05", "archives": archives}

    release = fetch_release(manifest, tmp_path / "data", role="sources")
    fetch_release(manifest, tmp_path / "data", role="derived")

    assert (release / "sources.txt").read_text() == "sources"
    assert (release / "derived.txt").read_text() == "derived"
    assert (release / ".soia-fetched-sources.json").exists()
    assert (release / ".soia-fetched-derived.json").exists()


def test_expand_package_specification_excludes_technical_files(tmp_path: Path) -> None:
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "layer.shp").write_text("ok")
    (tmp_path / "data" / ".DS_Store").write_text("no")
    (tmp_path / "data" / "Invoice-test.pdf").write_text("no")
    spec = {
        "release": "2026.05",
        "groups": [{
            "glob": "data/*",
            "package": "sources",
            "stage": "source",
            "source": "test",
            "acquired": "2026-05-05",
            "license": "CC-BY-4.0",
            "kind": "source"
        }]
    }

    expanded = expand_package_specification(tmp_path, spec)

    assert [item["path"] for item in expanded["files"]] == ["data/layer.shp"]
    assert expanded["files"][0]["format"] == "SHP"
