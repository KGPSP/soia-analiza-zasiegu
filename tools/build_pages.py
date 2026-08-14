#!/usr/bin/env python3
"""Build the public GitHub Pages edition from the canonical Markdown report."""

from __future__ import annotations

import argparse
import html
import shutil
from pathlib import Path

from markdown import Markdown

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "publication" / "analiza-kierunkowa.md"
TEMPLATE = ROOT / "pages" / "index.template.html"
REPOSITORY = "https://github.com/KGPSP/soia-analiza-zasiegu"


def rewrite_links(source: str) -> str:
    replacements = {
        "../data/manifests/data-manifest.json": "data-manifest.json",
        "../docs/reference/interfejs-cli-i-dane.md": (
            f"{REPOSITORY}/blob/main/docs/reference/interfejs-cli-i-dane.md"
        ),
        "../docs/reference/warstwy-gis.md": (
            f"{REPOSITORY}/blob/main/docs/reference/warstwy-gis.md"
        ),
    }
    for old, new in replacements.items():
        source = source.replace(old, new)
    return source


def render_toc(tokens: list[dict[str, object]]) -> str:
    items: list[str] = []
    for token in tokens:
        name = str(token["name"])
        if name == "ANALIZA KIERUNKOWA":
            continue
        target = html.escape(str(token["id"]), quote=True)
        label = html.escape(name)
        items.append(f'<li><a href="#{target}">{label}</a></li>')
    return '<ul class="toc-list">' + "".join(items) + "</ul>"


def build(output: Path) -> Path:
    source = rewrite_links(REPORT.read_text(encoding="utf-8"))
    source = source.removeprefix("# ANALIZA KIERUNKOWA\n\n")
    converter = Markdown(extensions=["extra", "sane_lists", "toc"])
    report_html = converter.convert(source)
    toc_html = render_toc(converter.toc_tokens)

    template = TEMPLATE.read_text(encoding="utf-8")
    rendered = template.replace("{{TOC}}", toc_html).replace("{{REPORT}}", report_html)

    output.mkdir(parents=True, exist_ok=True)
    (output / "index.html").write_text(rendered, encoding="utf-8")
    (output / ".nojekyll").write_text("", encoding="utf-8")
    shutil.copy2(ROOT / "pages" / "site.css", output / "site.css")
    shutil.copy2(ROOT / "pages" / "site.js", output / "site.js")
    shutil.copy2(ROOT / "data" / "manifests" / "data-manifest.json", output / "data-manifest.json")

    for name in ("assets", "source", "tables"):
        shutil.copytree(ROOT / "publication" / name, output / name, dirs_exist_ok=True)
    shutil.copytree(ROOT / "pages" / "assets", output / "assets" / "branding", dirs_exist_ok=True)
    return output / "index.html"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "build" / "pages")
    args = parser.parse_args()
    index = build(args.output.resolve())
    print(f"Built GitHub Pages: {index}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
