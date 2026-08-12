import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "publication" / "analiza-kierunkowa.md"


def test_publication_report_has_complete_docx_structure() -> None:
    markdown = REPORT.read_text(encoding="utf-8")
    expected = [
        "# ANALIZA KIERUNKOWA",
        "# Streszczenie dla decydentów",
        "# CZĘŚĆ I — Pokrycie ogólnokrajowe sygnałem syren",
        "# CZĘŚĆ II — Luki w pokryciu",
        "# CZĘŚĆ III — Obszary ryzyka powodziowego",
        "# CZĘŚĆ IV — Obszary do doposażenia w syreny",
        "# CZĘŚĆ V — Publiczny CAP, IoT Feed i kanały odpornościowe",
        "# CZĘŚĆ VI — Warianty budżetowe i decyzje",
        "# Załączniki tabelaryczne i materiały źródłowe",
    ]
    for heading in expected:
        assert heading in markdown
    assert markdown.count("\n| ---") == 19
    assert len(re.findall(r"!\[[^]]*]\(([^)]+)\)", markdown)) == 22
    assert "Spis treści" not in markdown


def test_publication_report_local_links_exist_and_are_relative() -> None:
    markdown = REPORT.read_text(encoding="utf-8")
    links = re.findall(r"!?\[[^]]*]\(([^)]+)\)", markdown)
    assert links
    for target in links:
        if target.startswith(("http://", "https://", "#")):
            continue
        assert not target.startswith("/")
        assert (REPORT.parent / target).resolve().exists(), target


def test_publication_report_preserves_key_values() -> None:
    markdown = REPORT.read_text(encoding="utf-8")
    for value in ["22 614", "29,35 mln", "77,2%", "13 050", "447 593", "15 909"]:
        assert value in markdown
    assert "CAP/PL-CAP" in markdown
    assert "PL-CAP-DIST-IOT" in markdown
