import re
from pathlib import Path
from urllib.parse import urlparse

from tools.build_pages import build

ROOT = Path(__file__).resolve().parents[1]


def test_pages_build_contains_canonical_report_and_public_links(tmp_path: Path) -> None:
    index = build(tmp_path / "site")
    html = index.read_text(encoding="utf-8")
    assert "Analiza kierunkowa SOIA" in html
    assert 'id="wnioski-z-realizacji"' in html
    assert "22 614" in html
    assert "77,2%" in html
    assert "≥ 65 dB(A)" in html
    assert "https://github.com/KGPSP/soia-analiza-zasiegu" in html
    assert "https://doi.org/10.5281/zenodo.21921103" in html
    assert "full_recalculation_executed: false" in html
    assert html.count("<table>") == 19
    assert 'src="assets/branding/herb-psp.png"' in html
    assert "Znak graficzny Państwowej Straży Pożarnej" in html
    assert 'src="assets/branding/label-ai-modified.svg"' in html
    assert "Treść częściowo zmodyfikowana przez AI" in html


def test_pages_build_copies_report_assets_and_downloads(tmp_path: Path) -> None:
    output = tmp_path / "site"
    index = build(output)
    html = index.read_text(encoding="utf-8")
    assert len(list((output / "assets" / "report").iterdir())) == 22
    assert len(list((output / "tables").iterdir())) == 11
    assert len(list((output / "source").iterdir())) == 2
    assert (output / "data-manifest.json").is_file()
    assert (output / "assets" / "branding" / "herb-psp.png").is_file()
    assert (output / "assets" / "branding" / "label-ai-modified.svg").is_file()

    targets = re.findall(r'(?:href|src)="([^"]+)"', html)
    for target in targets:
        parsed = urlparse(target)
        if parsed.scheme or target.startswith(("#", "mailto:")):
            continue
        path = target.split("#", 1)[0]
        assert not path.startswith(("/", "../")), target
        assert (output / path).exists(), target


def test_pages_uses_real_report_thresholds_not_concept_placeholders(tmp_path: Path) -> None:
    html = build(tmp_path / "site").read_text(encoding="utf-8")
    assert "≥ 85 dB" not in html
    assert "30 kwietnia 2025" not in html
    assert "inwentaryzacji z 5 maja 2026 r." in html
