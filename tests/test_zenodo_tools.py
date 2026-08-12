from __future__ import annotations

import json
from pathlib import Path

from tools.apply_reserved_doi import apply_reserved_doi
from tools.zenodo_draft import create_or_update_draft, metadata


class FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return self.payload


def test_draft_metadata_does_not_falsely_apply_one_license_to_all_files() -> None:
    payload = metadata()

    assert payload["access_right"] == "restricted"
    assert "license" not in payload
    assert [creator["name"] for creator in payload["creators"]] == [
        "Komenda Główna Państwowej Straży Pożarnej — Biuro Informatyki i Łączności"
    ]
    assert payload["contributors"] == [{
        "name": "Kłosiński, Michał",
        "affiliation": "Biuro Informatyki i Łączności KG PSP",
        "type": "ProjectLeader",
    }]


def test_zenodo_upload_can_update_existing_draft(monkeypatch, tmp_path: Path) -> None:
    calls: list[tuple[str, str]] = []
    draft = {
        "id": 123,
        "links": {
            "bucket": "https://zenodo.example/bucket",
            "html": "https://zenodo.example/deposit/123",
            "self": "https://zenodo.example/api/deposit/depositions/123",
        },
        "metadata": {"prereserve_doi": {"doi": "10.5281/zenodo.123"}},
        "state": "unsubmitted",
        "submitted": False,
    }

    def fake_put(url, **kwargs):
        calls.append(("PUT", url))
        return FakeResponse(draft if url.endswith("/123") else {})

    monkeypatch.setattr("tools.zenodo_draft.requests.put", fake_put)
    artifact = tmp_path / "artifact.zip"
    artifact.write_bytes(b"zip")

    result = create_or_update_draft(
        base_url="https://zenodo.example",
        token="secret-not-written",
        metadata_payload={"title": "test"},
        files=[artifact],
        upload=True,
        deposition_id=123,
    )

    assert calls == [
        ("PUT", "https://zenodo.example/api/deposit/depositions/123"),
        ("PUT", "https://zenodo.example/bucket/artifact.zip"),
    ]
    assert result["id"] == 123
    assert result["created"] is False
    assert result["files_uploaded"] is True


def test_apply_reserved_doi_updates_specification_and_archive_urls(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    specification = tmp_path / "groups.json"
    citation = tmp_path / "CITATION.cff"
    readme = tmp_path / "README.md"
    archives = [{"name": "soia-quickstart-2026.05.zip", "url": None}]
    manifest.write_text(json.dumps({"doi": None, "archives": archives}), encoding="utf-8")
    specification.write_text(
        json.dumps({"release": "2026.05", "doi": None, "archives": archives}),
        encoding="utf-8",
    )
    citation.write_text("cff-version: 1.2.0\n", encoding="utf-8")
    readme.write_text(
        "- DOI zostanie dopisany do `CITATION.cff` i manifestu po zatwierdzeniu draftu\n"
        "  Zenodo.\n",
        encoding="utf-8",
    )

    apply_reserved_doi(
        doi="10.5281/zenodo.123",
        record_id="123",
        manifest_path=manifest,
        specification_path=specification,
        citation_path=citation,
        readme_path=readme,
    )

    expected_url = (
        "https://zenodo.org/records/123/files/soia-quickstart-2026.05.zip?download=1"
    )
    assert json.loads(manifest.read_text())["archives"][0]["url"] == expected_url
    saved_specification = json.loads(specification.read_text())
    assert saved_specification["doi"] == "10.5281/zenodo.123"
    assert saved_specification["archives"][0]["url"] == expected_url
    assert "doi: 10.5281/zenodo.123" in citation.read_text()
    assert "https://doi.org/10.5281/zenodo.123" in readme.read_text()
