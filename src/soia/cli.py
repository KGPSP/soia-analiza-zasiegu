from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from .generator import DATA_RELEASE, generate_jst_report
from .pipeline import load_pipeline_config, run_pipeline
from .release import ReleaseError, fetch_release, load_manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="soia", description="Analizy zasięgu syren SOIA")
    commands = parser.add_subparsers(dest="command", required=True)

    data = commands.add_parser("data", help="zarządzanie snapshotami danych")
    data_commands = data.add_subparsers(dest="data_command", required=True)
    fetch = data_commands.add_parser("fetch", help="pobierz i zweryfikuj paczkę Zenodo")
    fetch.add_argument("--release", required=True)
    fetch.add_argument("--manifest", type=Path, default=Path("data/manifests/data-manifest.json"))
    fetch.add_argument("--data-dir", type=Path, default=Path("data"))
    fetch.add_argument("--force", action="store_true")
    fetch.add_argument("--role", choices=["quickstart", "sources", "derived"], default="quickstart")

    analyze = commands.add_parser("analyze", help="wygeneruj pakiet raportowy JST")
    analyze.add_argument("--teryt", required=True)
    analyze.add_argument("--release", default=DATA_RELEASE)
    analyze.add_argument("--data-dir", type=Path)
    analyze.add_argument("--inventory-updates", type=Path)
    analyze.add_argument("--output", type=Path, required=True)
    analyze.add_argument("--force", action="store_true")
    analyze.add_argument("--skip-pdf", action="store_true")

    pipeline = commands.add_parser("pipeline", help="pełne odtworzenie V9–V13")
    pipeline_commands = pipeline.add_subparsers(dest="pipeline_command", required=True)
    full = pipeline_commands.add_parser("full", help="wykonaj wszystkie etapy")
    full.add_argument("--config", type=Path, required=True)
    full.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True)
    full.add_argument("--force", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "data":
            manifest = load_manifest(args.manifest)
            if str(manifest.get("release")) != args.release:
                raise ReleaseError(
                    f"Manifest opisuje wydanie {manifest.get('release')}, nie {args.release}"
                )
            result = fetch_release(manifest, args.data_dir, role=args.role, force=args.force)
            print(json.dumps({"release": args.release, "role": args.role, "data_root": str(result)}, ensure_ascii=False))
        elif args.command == "analyze":
            data_root = args.data_dir or Path("data/releases") / args.release
            result = generate_jst_report(
                data_root,
                args.teryt,
                args.output,
                inventory_updates=args.inventory_updates,
                force=args.force,
                skip_pdf=args.skip_pdf,
            )
            print(json.dumps({
                "teryt": args.teryt,
                "unit_name": result.unit_name,
                "output": str(args.output.resolve()),
                "metrics": result.metrics,
            }, ensure_ascii=False))
        else:
            config = load_pipeline_config(args.config)
            data_config = config.get("data")
            if data_config:
                manifest = load_manifest(Path(data_config["manifest"]))
                for role in data_config.get("roles", ["sources"]):
                    fetch_release(
                        manifest, Path(data_config.get("data_dir", "data")),
                        role=role, force=False,
                    )
            results = run_pipeline(config, resume=args.resume, force=args.force)
            print(json.dumps([
                {"stage": item.name, "status": item.status, "elapsed_seconds": item.elapsed_seconds}
                for item in results
            ], ensure_ascii=False))
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        print(f"soia: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
