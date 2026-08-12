from __future__ import annotations

import argparse
import re
from pathlib import Path
from urllib.parse import unquote

LINK = re.compile(r"!?\[[^]]*]\(([^)]+)\)")


def check(root: Path) -> list[str]:
    failures: list[str] = []
    for document in sorted(root.rglob("*.md")):
        if any(part in {".git", ".venv", "outputs", "release-artifacts"} for part in document.parts):
            continue
        text = document.read_text(encoding="utf-8")
        headings = {
            re.sub(r"[^a-z0-9ąćęłńóśźż -]", "", title.casefold()).strip().replace(" ", "-")
            for title in re.findall(r"^#{1,6}\s+(.+)$", text, flags=re.MULTILINE)
        }
        for raw in LINK.findall(text):
            target = raw.strip().strip("<>")
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            path_part, _, anchor = target.partition("#")
            linked = (document.parent / unquote(path_part)).resolve() if path_part else document.resolve()
            if path_part and not linked.exists():
                failures.append(f"{document}: brak {target}")
            elif anchor and linked == document.resolve() and unquote(anchor).casefold() not in headings:
                failures.append(f"{document}: brak kotwicy #{anchor}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Sprawdza lokalne linki i obrazy Markdown.")
    parser.add_argument("root", type=Path, nargs="?", default=Path("."))
    args = parser.parse_args()
    failures = check(args.root.resolve())
    if failures:
        print("\n".join(failures))
        return 1
    print("Wszystkie lokalne linki Markdown są poprawne.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
