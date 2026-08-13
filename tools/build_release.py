from __future__ import annotations

import argparse
import json
from pathlib import Path

from soia.release import (
    build_data_manifest,
    build_zip64,
    expand_package_specification,
    sha256_file,
    write_manifest,
)


def build_release(
    source_root: Path,
    specification_path: Path,
    manifest_path: Path,
    artifacts_dir: Path,
    *,
    build_archives: bool,
    force: bool,
    roles: set[str] | None = None,
) -> dict:
    specification = json.loads(specification_path.read_text(encoding="utf-8"))
    expanded = expand_package_specification(source_root, specification)
    manifest = build_data_manifest(source_root, expanded)
    if build_archives:
        for archive in manifest["archives"]:
            role = archive["role"]
            if roles and role not in roles:
                existing = artifacts_dir / archive["name"]
                if existing.exists():
                    archive["size_bytes"] = existing.stat().st_size
                    archive["sha256"] = sha256_file(existing)
                continue
            members = [
                (source_root / item.get("source_path", item["path"]), item["path"])
                for item in manifest["files"]
                if item["package"] == role
            ]
            output = artifacts_dir / archive["name"]
            build_zip64(output, members, force=force)
            archive["size_bytes"] = output.stat().st_size
            archive["sha256"] = sha256_file(output)
    else:
        for archive in manifest["archives"]:
            existing = artifacts_dir / archive["name"]
            if existing.exists():
                archive["size_bytes"] = existing.stat().st_size
                archive["sha256"] = sha256_file(existing)
    write_manifest(manifest_path, manifest)
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Buduje manifest i archiwa wydania SOIA.")
    parser.add_argument("--source-root", type=Path, default=Path(".."))
    parser.add_argument(
        "--specification", type=Path, default=Path("configs/package-groups-2026.05.json")
    )
    parser.add_argument(
        "--manifest", type=Path, default=Path("data/manifests/data-manifest.json")
    )
    parser.add_argument("--artifacts-dir", type=Path, default=Path("release-artifacts"))
    parser.add_argument("--build-archives", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--role", action="append", choices=["sources", "derived", "quickstart"])
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = build_release(
        args.source_root.resolve(),
        args.specification.resolve(),
        args.manifest.resolve(),
        args.artifacts_dir.resolve(),
        build_archives=args.build_archives,
        force=args.force,
        roles=set(args.role) if args.role else None,
    )
    totals: dict[str, int] = {}
    for item in manifest["files"]:
        totals[item["package"]] = totals.get(item["package"], 0) + item["size_bytes"]
    print(json.dumps({"files": len(manifest["files"]), "bytes_by_package": totals}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
