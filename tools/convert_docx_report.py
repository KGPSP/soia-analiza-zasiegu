from __future__ import annotations

import argparse
import os
import re
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from docx import Document
from docx.document import Document as DocumentType
from docx.table import Table
from docx.text.paragraph import Paragraph

CAPTION_PREFIXES = ("Mapa ", "Wykres ", "Rysunek ", "Diagram ")
POLISH_TRANSLATION = str.maketrans("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ", "acelnoszzACELNOSZZ")
PUBLICATION_NOTE = """\
> **Edycja internetowa.** Dane odnoszą się do inwentaryzacji z 5 maja 2026 r.
> i wyników V9-V13. Rozszerzenie CAP/IoT wyeksportowano 6 lipca 2026 r.
> Wersja danych: `2026.05`. Dokument jest modelem planistycznym, a nie
> certyfikowanym pomiarem propagacji akustycznej.

[Pobierz kanoniczny PDF](source/SOIA_Analiza_Kierunkowa_v2_MSWiA_KGPSP_2_4_CAP_IOT_czytelny.pdf)
lub otwórz [manifest danych](../data/manifests/data-manifest.json).
"""
MACHINE_LINKS = """\
## Dane maszynowe powiązane z raportem

- [A01 - podsumowanie krajowe](tables/A01_kraj_podsumowanie.csv)
- [A02 - województwa](tables/A02_wojewodztwa.csv)
- [A03 - powiaty](tables/A03_powiaty.csv)
- [A04 - gminy](tables/A04_gminy.csv)
- [A05 - hierarchia RiskZone](tables/A05_riskzone_kraj_woj_pow_gmi.csv)
- [A06 - RiskZone poza zasięgiem](tables/A06_riskzone_poza_zasiegiem.csv)
- [A07 - luki priorytetowe](tables/A07_luki_priorytetowe.csv)
- [A08 - kandydaci i rekomendacje](tables/A08_kandydaci_syreny.csv)
- [A09 - redundancja](tables/A09_redundancja.csv)
- [A10 - jakość danych](tables/A10_jakosc_danych.csv)
- [A11 - integracja GSM](tables/A11_integracja_GSM.csv)
- [Opis warstw GIS i paczek](../docs/reference/interfejs-cli-i-dane.md)
"""


@dataclass(frozen=True)
class ConversionResult:
    table_count: int
    image_count: int
    heading_count: int


def iter_blocks(document: DocumentType) -> Iterator[Paragraph | Table]:
    for child in document.element.body.iterchildren():
        if child.tag.endswith("}p"):
            yield Paragraph(child, document)
        elif child.tag.endswith("}tbl"):
            yield Table(child, document)


def escape_cell(value: str) -> str:
    return "<br>".join(part.strip() for part in value.splitlines() if part.strip()).replace(
        "|", "\\|"
    )


def table_markdown(table: Table) -> str:
    rows = [[escape_cell(cell.text) for cell in row.cells] for row in table.rows]
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]
    lines = ["| " + " | ".join(rows[0]) + " |"]
    lines.append("| " + " | ".join(["---"] * width) + " |")
    lines.extend("| " + " | ".join(row) + " |" for row in rows[1:])
    return "\n".join(lines)


def formatted_text(paragraph: Paragraph) -> str:
    pieces: list[str] = []
    for run in paragraph.runs:
        value = run.text.replace("\t", " ").strip("\n")
        if not value:
            continue
        if run.bold and run.italic:
            value = f"***{value}***"
        elif run.bold:
            value = f"**{value}**"
        elif run.italic:
            value = f"*{value}*"
        pieces.append(value)
    text = "".join(pieces) if pieces else paragraph.text
    return re.sub(r"[ \t]+", " ", text).strip()


def plain_text(paragraph: Paragraph) -> str:
    return re.sub(r"[ \t]+", " ", paragraph.text.replace("\t", " ")).strip()


def image_parts(paragraph: Paragraph) -> list[object]:
    result: list[object] = []
    for blip in paragraph._p.xpath(".//a:blip"):
        relation_id = blip.get(
            "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed"
        )
        if relation_id:
            result.append(paragraph.part.related_parts[relation_id])
    return result


def heading_level(paragraph: Paragraph) -> int | None:
    style = paragraph.style.name if paragraph.style else ""
    match = re.fullmatch(r"Heading ([1-6])", style)
    return int(match.group(1)) if match else None


def slugify(value: str) -> str:
    ascii_value = (
        unicodedata.normalize("NFKD", value.translate(POLISH_TRANSLATION))
        .encode("ascii", "ignore")
        .decode("ascii")
        .lower()
    )
    return re.sub(r"[^a-z0-9]+", "-", ascii_value).strip("-")


def convert_docx(
    source: Path,
    output: Path,
    assets_dir: Path,
    *,
    image_prefix: str = "figure",
    semantic_names: bool = False,
    publication_metadata: bool = False,
) -> ConversionResult:
    document = Document(source)
    blocks = list(iter_blocks(document))
    output.parent.mkdir(parents=True, exist_ok=True)
    assets_dir.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    table_count = 0
    image_count = 0
    heading_count = 0
    skip_manual_toc = False
    index = 0

    while index < len(blocks):
        block = blocks[index]
        if isinstance(block, Table):
            table_count += 1
            lines.extend([table_markdown(block), ""])
            index += 1
            continue

        text = formatted_text(block)
        plain = plain_text(block)
        level = heading_level(block)
        if level == 1 and plain == "Spis treści":
            skip_manual_toc = True
            index += 1
            continue
        if skip_manual_toc:
            if level == 1 and plain != "Spis treści":
                skip_manual_toc = False
            else:
                index += 1
                continue

        parts = image_parts(block)
        if parts:
            caption = ""
            lookahead = index + 1
            while lookahead < len(blocks) and isinstance(blocks[lookahead], Paragraph):
                candidate = plain_text(blocks[lookahead])
                if not candidate:
                    lookahead += 1
                    continue
                if candidate.startswith(CAPTION_PREFIXES):
                    caption = candidate
                    index = lookahead
                break
            for part in parts:
                image_count += 1
                suffix = Path(str(part.partname)).suffix or ".png"
                if semantic_names and caption:
                    stem = slugify(caption)
                    image_name = f"{stem}{suffix}"
                    duplicate = 2
                    while (assets_dir / image_name).exists():
                        image_name = f"{stem}-{duplicate}{suffix}"
                        duplicate += 1
                else:
                    image_name = f"{image_prefix}-{image_count:03d}{suffix}"
                image_path = assets_dir / image_name
                image_path.write_bytes(part.blob)
                relative = Path(os.path.relpath(image_path, output.parent)).as_posix()
                alt = caption or f"Grafika {image_count} z raportu SOIA"
                lines.extend([f"![{alt}]({relative})", ""])
            index += 1
            continue

        if not text:
            index += 1
            continue
        if level:
            heading_count += 1
            lines.extend([f"{'#' * level} {plain}", ""])
        elif not lines and plain == "ANALIZA KIERUNKOWA":
            heading_count += 1
            lines.extend([f"# {plain}", ""])
            if publication_metadata:
                lines.extend([PUBLICATION_NOTE.rstrip(), ""])
        elif block.style and block.style.name == "List Paragraph":
            lines.append(f"- {text}")
        elif text.startswith("WNIOSEK CZĘŚCI"):
            lines.extend([f"> **{text}**", ""])
        else:
            lines.extend([text, ""])
        index += 1

    if publication_metadata:
        lines.extend([MACHINE_LINKS.rstrip(), ""])
    output.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return ConversionResult(table_count, image_count, heading_count)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Konwertuje raport DOCX SOIA do Markdown.")
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("assets", type=Path)
    parser.add_argument("--semantic-names", action="store_true")
    parser.add_argument("--publication-metadata", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = convert_docx(
        args.source,
        args.output,
        args.assets,
        semantic_names=args.semantic_names,
        publication_metadata=args.publication_metadata,
    )
    print(
        f"tables={result.table_count} images={result.image_count} headings={result.heading_count}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
