from __future__ import annotations

import json
from pathlib import Path

from soia.pipeline import StageResult
from tools.run_release_test import run_release_test


def test_release_test_records_resources_stages_and_acceptance(monkeypatch, tmp_path: Path) -> None:
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"release": "test", "workdir": str(tmp_path), "stages": []}))
    result_root = tmp_path / "result"
    result_root.mkdir()
    (result_root / "artifact.bin").write_bytes(b"1234")
    output = tmp_path / "release-test.json"

    monkeypatch.setattr(
        "tools.run_release_test.run_pipeline",
        lambda *_args, **_kwargs: [StageResult("V9", "completed", tmp_path / "V9.json", 1.25)],
    )
    monkeypatch.setattr(
        "tools.run_release_test.validate_country_release",
        lambda _root: {"validation_passed": True, "checks": {"test": True}},
    )
    monkeypatch.setattr("tools.run_release_test._fetch_configured_data", lambda _config: [])

    report = run_release_test(config, result_root, output, force=False)

    assert report["validation_passed"] is True
    assert report["resource_usage"]["result_bytes"] == 4
    assert report["stages"] == [
        {"name": "V9", "status": "completed", "elapsed_seconds": 1.25}
    ]
    assert json.loads(output.read_text())["acceptance"]["checks"]["test"] is True
