from __future__ import annotations

import importlib.metadata
import json
import os
import platform
import shutil
import subprocess
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .release import sha256_file


class PipelineError(RuntimeError):
    """Pełny pipeline nie może zostać bezpiecznie wykonany."""


class PipelineOutputError(PipelineError):
    """Etap nadpisałby istniejący artefakt bez jawnego --force."""


@dataclass(frozen=True)
class StageResult:
    name: str
    status: str
    manifest: Path
    elapsed_seconds: float


def load_pipeline_config(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        return json.loads(text)
    try:
        import yaml
    except ImportError as error:
        raise PipelineError("Konfiguracja YAML wymaga pakietu PyYAML") from error
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise PipelineError("Konfiguracja pipeline musi być obiektem")
    return data


def _paths(workdir: Path, patterns: Iterable[str], *, require: bool) -> list[Path]:
    found: list[Path] = []
    for pattern in patterns:
        matches = sorted(workdir.glob(pattern))
        if not matches and require:
            raise PipelineError(f"Brak wymaganego wejścia/wyniku: {pattern}")
        for match in matches:
            if match.is_file():
                found.append(match)
            elif match.is_dir():
                found.extend(path for path in sorted(match.rglob("*")) if path.is_file())
    return list(dict.fromkeys(found))


def _snapshot(workdir: Path, patterns: Iterable[str], *, require: bool) -> list[dict[str, Any]]:
    return [
        {
            "path": path.relative_to(workdir).as_posix(),
            "size_bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in _paths(workdir, patterns, require=require)
    ]


def _manifest_valid(
    manifest_path: Path,
    workdir: Path,
    inputs: list[str],
    outputs: list[str],
) -> bool:
    if not manifest_path.is_file():
        return False
    try:
        previous = json.loads(manifest_path.read_text(encoding="utf-8"))
        return (
            previous.get("validation_passed") is True
            and previous.get("inputs") == _snapshot(workdir, inputs, require=True)
            and previous.get("outputs") == _snapshot(workdir, outputs, require=True)
        )
    except (OSError, ValueError, PipelineError):
        return False


def _remove_declared_outputs(workdir: Path, outputs: Iterable[str]) -> None:
    for pattern in outputs:
        for path in sorted(workdir.glob(pattern), reverse=True):
            resolved = path.resolve()
            if resolved == workdir or workdir not in resolved.parents:
                raise PipelineOutputError(f"Niebezpieczny cel --force: {path}")
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()


def _versions() -> dict[str, str]:
    result = {"python": platform.python_version()}
    for package in ("geopandas", "numpy", "pandas", "pyproj", "rasterio", "scipy", "shapely"):
        try:
            result[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            continue
    return result


def run_pipeline(
    config: dict[str, Any],
    *,
    resume: bool = True,
    force: bool = False,
) -> list[StageResult]:
    workdir = Path(config.get("workdir", ".")).resolve()
    state_dir = (workdir / config.get("state_dir", ".soia-pipeline")).resolve()
    if workdir not in state_dir.parents and state_dir != workdir:
        raise PipelineError("Katalog stanu musi znajdować się wewnątrz workdir")
    state_dir.mkdir(parents=True, exist_ok=True)
    stages = config.get("stages")
    if not isinstance(stages, list) or not stages:
        raise PipelineError("Konfiguracja nie zawiera etapów")

    results: list[StageResult] = []
    for stage in stages:
        name = str(stage.get("name", "")).strip()
        command = stage.get("command")
        inputs = list(stage.get("inputs", []))
        outputs = list(stage.get("outputs", []))
        if not name or not isinstance(command, list) or not command or not outputs:
            raise PipelineError("Każdy etap wymaga name, command i outputs")
        manifest_path = state_dir / f"{name}.json"
        if resume and not force and _manifest_valid(manifest_path, workdir, inputs, outputs):
            results.append(StageResult(name, "skipped", manifest_path, 0.0))
            continue

        existing = _paths(workdir, outputs, require=False)
        if existing and not force:
            raise PipelineOutputError(
                f"Etap {name} nadpisałby istniejący wynik; użyj --force albo zmień katalog wynikowy"
            )
        if force:
            _remove_declared_outputs(workdir, outputs)
        input_snapshot = _snapshot(workdir, inputs, require=True)
        started = datetime.now(UTC)
        timer = time.monotonic()
        environment = os.environ.copy()
        environment["SOIA_DATA_RELEASE"] = str(config.get("release", "unknown"))
        try:
            completed = subprocess.run(
                [str(item) for item in command], cwd=workdir, env=environment, check=True
            )
        except subprocess.CalledProcessError as error:
            raise PipelineError(f"Etap {name} zakończył się kodem {error.returncode}") from error
        elapsed = time.monotonic() - timer
        output_snapshot = _snapshot(workdir, outputs, require=True)
        manifest = {
            "schema_version": "1.0.0",
            "stage": name,
            "release": config.get("release"),
            "command": [str(item) for item in command],
            "parameters": stage.get("parameters", {}),
            "started_at": started.isoformat(),
            "completed_at": datetime.now(UTC).isoformat(),
            "elapsed_seconds": round(elapsed, 6),
            "exit_code": completed.returncode,
            "runtime": _versions(),
            "inputs": input_snapshot,
            "outputs": output_snapshot,
            "validation_passed": True,
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        results.append(StageResult(name, "completed", manifest_path, elapsed))
    return results
