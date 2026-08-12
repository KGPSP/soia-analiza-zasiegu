from __future__ import annotations

import hashlib
import json
import shutil
import urllib.request
import zipfile
from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from typing import Any


class ReleaseError(RuntimeError):
    """Błąd przygotowania albo pobierania wydania danych."""


class ChecksumError(ReleaseError):
    """Pobrany plik nie odpowiada manifestowi."""


class UnsafeArchiveError(ReleaseError):
    """Archiwum zawiera ścieżkę wychodzącą poza katalog docelowy."""


EXCLUDED_NAMES = {".DS_Store", "Thumbs.db"}
EXCLUDED_PARTS = {"tmp", "node_modules", "__pycache__", ".venv"}
EXCLUDED_NAME_FRAGMENTS = ("invoice", ".~lock.")


def _publishable(path: Path) -> bool:
    if path.name in EXCLUDED_NAMES or any(part in EXCLUDED_PARTS for part in path.parts):
        return False
    lowered = path.name.lower()
    return not any(fragment in lowered for fragment in EXCLUDED_NAME_FRAGMENTS)


def expand_package_specification(root: Path, specification: dict[str, Any]) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for group in specification.get("groups", []):
        for path in sorted(root.glob(group["glob"])):
            if not path.is_file():
                continue
            relative = path.relative_to(root)
            if not _publishable(relative):
                continue
            key = (relative.as_posix(), str(group["package"]))
            if key in seen:
                continue
            seen.add(key)
            item = {
                key: value for key, value in group.items()
                if key not in {"glob", "strip_prefix"}
            }
            source_path = relative.as_posix()
            strip_prefix = str(group.get("strip_prefix", "")).strip("/")
            archive_path = source_path
            if strip_prefix:
                prefix = strip_prefix + "/"
                if not source_path.startswith(prefix):
                    raise ValueError(f"{source_path} nie znajduje się pod strip_prefix={strip_prefix}")
                archive_path = source_path[len(prefix):]
            item.update(
                {
                    "path": archive_path,
                    "source_path": source_path,
                    "format": group.get("format") or path.suffix.lstrip(".").upper() or "BINARY",
                    "crs": group.get("crs"),
                    "layer": group.get("layer"),
                    "feature_count": group.get("feature_count"),
                    "dependencies": list(group.get("dependencies", [])),
                }
            )
            files.append(item)
    return {
        "release": specification["release"],
        "doi": specification.get("doi"),
        "archives": specification.get("archives", []),
        "files": files,
    }


def sha256_file(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def build_data_manifest(root: Path, specification: dict[str, Any]) -> dict[str, Any]:
    release = str(specification["release"])
    output: list[dict[str, Any]] = []
    for declared in specification.get("files", []):
        relative = Path(declared.get("source_path", declared["path"]))
        path = root / relative
        if not path.is_file():
            raise FileNotFoundError(path)
        item = dict(declared)
        item.update(
            {
                "path": declared["path"],
                "source_path": relative.as_posix(),
                "release": release,
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
        output.append(item)
    return {
        "schema_version": "1.0.0",
        "release": release,
        "doi": specification.get("doi"),
        "archives": specification.get("archives", []),
        "files": output,
    }


def build_zip64(
    output: Path,
    members: Iterable[tuple[Path, str]],
    *,
    force: bool,
    compression: int = zipfile.ZIP_DEFLATED,
    compresslevel: int = 1,
) -> Path:
    if output.exists() and not force:
        raise FileExistsError(f"Archiwum już istnieje: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".partial")
    if temporary.exists():
        temporary.unlink()
    try:
        with zipfile.ZipFile(
            temporary,
            "w",
            compression=compression,
            allowZip64=True,
            compresslevel=compresslevel,
        ) as archive:
            for source, archive_name in sorted(members, key=lambda pair: pair[1]):
                if not source.is_file():
                    raise FileNotFoundError(source)
                normalized = PurePosixPath(archive_name)
                if normalized.is_absolute() or ".." in normalized.parts:
                    raise UnsafeArchiveError(f"Niebezpieczna ścieżka ZIP: {archive_name}")
                archive.write(source, normalized.as_posix())
        temporary.replace(output)
    except Exception:
        if temporary.exists():
            temporary.unlink()
        raise
    return output


def _download(url: str, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".partial")
    if temporary.exists():
        temporary.unlink()
    with urllib.request.urlopen(url) as response, temporary.open("wb") as target:
        shutil.copyfileobj(response, target, length=8 * 1024 * 1024)
    temporary.replace(output)


def _verify_archive(path: Path, declared: dict[str, Any]) -> None:
    expected_size = int(declared["size_bytes"])
    if path.stat().st_size != expected_size:
        raise ChecksumError(
            f"Nieprawidłowy rozmiar {path.name}: {path.stat().st_size} != {expected_size}"
        )
    actual = sha256_file(path)
    if actual.lower() != str(declared["sha256"]).lower():
        raise ChecksumError(f"Nieprawidłowa suma SHA-256 pliku {path.name}")


def _safe_extract(archive_path: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with zipfile.ZipFile(archive_path) as archive:
        for member in archive.infolist():
            target = (destination / member.filename).resolve()
            if root != target and root not in target.parents:
                raise UnsafeArchiveError(f"Niebezpieczna ścieżka ZIP: {member.filename}")
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output, length=8 * 1024 * 1024)


def fetch_release(
    manifest: dict[str, Any],
    data_dir: Path,
    *,
    role: str = "quickstart",
    force: bool = False,
) -> Path:
    release = str(manifest["release"])
    candidates = [item for item in manifest.get("archives", []) if item.get("role") == role]
    if not candidates:
        raise ReleaseError(f"Manifest nie zawiera paczki roli {role!r}")
    release_dir = data_dir / "releases" / release
    cache_dir = data_dir / "cache"
    marker = release_dir / f".soia-fetched-{role}.json"
    if marker.exists() and not force:
        return release_dir
    release_dir.mkdir(parents=True, exist_ok=True)
    for item in candidates:
        archive_path = cache_dir / item["name"]
        if force or not archive_path.exists():
            if not item.get("url"):
                raise ReleaseError(
                    f"Paczka {item['name']} nie ma jeszcze URL; najpierw uzupełnij draft Zenodo"
                )
            _download(item["url"], archive_path)
        _verify_archive(archive_path, item)
        _safe_extract(archive_path, release_dir)
    marker.write_text(
        json.dumps({
            "release": release, "role": role,
            "archives": [{key: item.get(key) for key in ("name", "size_bytes", "sha256", "url")} for item in candidates],
        }, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return release_dir


def load_manifest(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
