from __future__ import annotations

import argparse
import json
import resource
import shutil
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from soia.pipeline import load_pipeline_config, run_pipeline
from soia.release import fetch_release, load_manifest
from soia.validation import validate_country_release


def _directory_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def _peak_rss_mib() -> float:
    usage = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    divisor = 1024 * 1024 if sys.platform == "darwin" else 1024
    return round(usage / divisor, 2)


def _fetch_configured_data(config: dict[str, Any]) -> list[str]:
    data = config.get("data")
    if not data:
        return []
    manifest = load_manifest(Path(data["manifest"]))
    roles = list(data.get("roles", ["sources"]))
    for role in roles:
        fetch_release(manifest, Path(data.get("data_dir", "data")), role=role)
    return roles


def run_release_test(
    config_path: Path,
    result_root: Path,
    output: Path,
    *,
    force: bool,
) -> dict[str, Any]:
    if output.exists() and not force:
        raise FileExistsError(f"Raport testu już istnieje: {output}; użyj --force")
    config = load_pipeline_config(config_path)
    result_root = result_root.resolve()
    disk_root = result_root.parent if result_root.parent.exists() else Path.cwd()
    disk_before = shutil.disk_usage(disk_root)
    started_at = datetime.now(UTC)
    timer = time.monotonic()
    fetched_roles = _fetch_configured_data(config)
    stages = run_pipeline(config, resume=True, force=force)
    acceptance = validate_country_release(result_root)
    elapsed = time.monotonic() - timer
    disk_after = shutil.disk_usage(disk_root)
    report = {
        "schema_version": "1.0.0",
        "release": config.get("release"),
        "scope": "full_country_recalculation",
        "started_at": started_at.isoformat(),
        "completed_at": datetime.now(UTC).isoformat(),
        "elapsed_seconds": round(elapsed, 3),
        "fetched_roles": fetched_roles,
        "stages": [
            {
                "name": item.name,
                "status": item.status,
                "elapsed_seconds": round(item.elapsed_seconds, 3),
            }
            for item in stages
        ],
        "resource_usage": {
            "peak_child_rss_mib": _peak_rss_mib(),
            "result_bytes": _directory_bytes(result_root),
            "disk_free_before_bytes": disk_before.free,
            "disk_free_after_bytes": disk_after.free,
            "disk_consumed_bytes": max(0, disk_before.free - disk_after.free),
        },
        "acceptance": acceptance,
        "validation_passed": acceptance.get("validation_passed") is True,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Wykonuje pozaciągowy test wydania Polski i zapisuje czas, RAM oraz dysk."
    )
    parser.add_argument("--config", type=Path, default=Path("configs/poland-2026.05.yaml"))
    parser.add_argument("--result-root", type=Path, default=Path("reproduction/2026.05"))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("release-artifacts/full-recalculation-2026.05.json"),
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    report = run_release_test(
        args.config.resolve(), args.result_root, args.output, force=args.force
    )
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["validation_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
