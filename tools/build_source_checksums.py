from __future__ import annotations

import argparse
import json
from pathlib import Path

from soia.release import expand_package_specification, sha256_file


def main() -> int:
    parser = argparse.ArgumentParser(description="Zapisuje pierwotne sumy źródeł snapshotu.")
    parser.add_argument("--source-root", type=Path, default=Path(".."))
    parser.add_argument("--specification", type=Path, default=Path("configs/package-groups-2026.05.json"))
    parser.add_argument("--output", type=Path, default=Path("data/sources/original-checksums-2026.05.sha256"))
    args = parser.parse_args()
    root = args.source_root.resolve()
    spec = json.loads(args.specification.read_text(encoding="utf-8"))
    expanded = expand_package_specification(root, spec)
    own_path = args.output.resolve()
    items = []
    for item in expanded["files"]:
        if item["package"] != "sources":
            continue
        source = root / item.get("source_path", item["path"])
        if source.resolve() == own_path:
            continue
        items.append((item["path"], sha256_file(source)))
    own_path.parent.mkdir(parents=True, exist_ok=True)
    own_path.write_text("".join(f"{digest}  {path}\n" for path, digest in sorted(items)), encoding="utf-8")
    print(json.dumps({"source_files": len(items), "output": str(own_path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
