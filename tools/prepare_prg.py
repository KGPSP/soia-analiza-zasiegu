from __future__ import annotations

import argparse
import zipfile
from pathlib import Path

from soia.release import UnsafeArchiveError


def main() -> int:
    parser = argparse.ArgumentParser(description="Bezpiecznie rozpakowuje granice PRG do pipeline.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.input) as archive:
        for member in archive.infolist():
            target = (root / member.filename).resolve()
            if root != target and root not in target.parents:
                raise UnsafeArchiveError(member.filename)
        archive.extractall(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
