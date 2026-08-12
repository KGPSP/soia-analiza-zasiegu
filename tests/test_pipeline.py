import json
import sys
from pathlib import Path

import pytest

from soia.pipeline import PipelineOutputError, load_pipeline_config, run_pipeline


def test_pipeline_records_checksums_and_resumes_valid_stage(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("source", encoding="utf-8")
    script = tmp_path / "stage.py"
    script.write_text(
        "from pathlib import Path\n"
        "p=Path('runs.txt'); p.write_text(p.read_text()+'x' if p.exists() else 'x')\n"
        "Path('result.txt').write_text(Path('source.txt').read_text()+' result')\n",
        encoding="utf-8",
    )
    config = {
        "release": "test",
        "workdir": str(tmp_path),
        "state_dir": ".pipeline-state",
        "stages": [{
            "name": "V9",
            "command": [sys.executable, str(script)],
            "inputs": ["source.txt"],
            "outputs": ["result.txt"],
        }],
    }

    first = run_pipeline(config, resume=True)
    second = run_pipeline(config, resume=True)

    assert [item.status for item in first] == ["completed"]
    assert [item.status for item in second] == ["skipped"]
    assert (tmp_path / "runs.txt").read_text() == "x"
    manifest = json.loads((tmp_path / ".pipeline-state/V9.json").read_text())
    assert manifest["validation_passed"] is True
    assert len(manifest["inputs"][0]["sha256"]) == 64
    assert len(manifest["outputs"][0]["sha256"]) == 64


def test_pipeline_reruns_when_input_checksum_changes(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("one", encoding="utf-8")
    script = tmp_path / "stage.py"
    script.write_text(
        "from pathlib import Path\n"
        "p=Path('runs.txt'); p.write_text(p.read_text()+'x' if p.exists() else 'x')\n"
        "Path('result.txt').write_text(Path('source.txt').read_text())\n",
        encoding="utf-8",
    )
    config = {
        "workdir": str(tmp_path), "state_dir": ".state",
        "stages": [{"name": "V10", "command": [sys.executable, str(script)], "inputs": ["source.txt"], "outputs": ["result.txt"]}],
    }
    run_pipeline(config, resume=True)
    source.write_text("two", encoding="utf-8")

    result = run_pipeline(config, resume=True, force=True)

    assert result[0].status == "completed"
    assert (tmp_path / "runs.txt").read_text() == "xx"


def test_pipeline_refuses_untracked_output_without_force(tmp_path: Path) -> None:
    (tmp_path / "result.txt").write_text("existing", encoding="utf-8")
    config = {
        "workdir": str(tmp_path), "state_dir": ".state",
        "stages": [{"name": "V11", "command": [sys.executable, "-c", "pass"], "inputs": [], "outputs": ["result.txt"]}],
    }

    with pytest.raises(PipelineOutputError):
        run_pipeline(config, resume=True)


def test_country_config_connects_replayed_v9_to_v13() -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_pipeline_config(root / "configs/poland-2026.05.yaml")
    v9 = next(stage for stage in config["stages"] if stage["name"] == "V9")
    v13 = next(stage for stage in config["stages"] if stage["name"] == "V13")
    analysis_dir = v13["command"][v13["command"].index("--analysis-dir") + 1]
    expected_inventory = (
        f"{analysis_dir}/inwentaryzacja-syren-2026-05-05.normalized.analysis.final.V9.csv"
    )
    expected_validation = expected_inventory.replace(".csv", ".validation.json")

    assert v9["command"][v9["command"].index("--output") + 1] == expected_inventory
    assert expected_inventory in v9["outputs"]
    assert expected_validation in v9["outputs"]
