import json
from pathlib import Path

from soia import cli


def test_cli_analyze_forwards_public_arguments(monkeypatch, tmp_path: Path, capsys) -> None:
    captured = {}

    def fake_generate(data_root, teryt, output, **kwargs):
        captured.update(data_root=Path(data_root), teryt=teryt, output=Path(output), **kwargs)
        return type("Result", (), {"metrics": {"pct_ge65": 60.98}, "unit_name": "Ostróda"})()

    monkeypatch.setattr(cli, "generate_jst_report", fake_generate)
    result = cli.main([
        "analyze", "--teryt", "2815092", "--data-dir", str(tmp_path / "data"),
        "--inventory-updates", str(tmp_path / "changes.csv"), "--output", str(tmp_path / "out"),
        "--force", "--skip-pdf",
    ])

    assert result == 0
    assert captured["teryt"] == "2815092"
    assert captured["force"] is True
    assert captured["skip_pdf"] is True
    assert json.loads(capsys.readouterr().out)["unit_name"] == "Ostróda"


def test_cli_data_fetch_rejects_wrong_release(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"release": "2026.05", "archives": []}))

    result = cli.main([
        "data", "fetch", "--release", "2027.01", "--manifest", str(manifest),
        "--data-dir", str(tmp_path / "data"),
    ])

    assert result == 2
