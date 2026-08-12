from __future__ import annotations

import argparse
import json
from pathlib import Path

from soia.validation import validate_country_release


def main() -> int:
    parser = argparse.ArgumentParser(description="Sprawdza kryteria akceptacji pełnego wydania Polski.")
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("release-artifacts/release-validation.json"))
    args = parser.parse_args()
    result = validate_country_release(args.data_root.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["validation_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
