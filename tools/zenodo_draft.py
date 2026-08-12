from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from urllib.parse import quote

import requests

MAX_FILES = 100
MAX_BYTES = 50_000_000_000


def metadata() -> dict[str, object]:
    return {
        "title": "SOIA — analiza zasięgu syren alarmowych, snapshot 2026.05",
        "upload_type": "dataset",
        "description": (
            "Dane źródłowe i pochodne V9–V13 oraz zoptymalizowany quickstart do "
            "reprodukowalnych analiz zasięgu syren SOIA dla jednostek TERYT. "
            "Właściciel: KG PSP; jednostka odpowiedzialna: BIŁ KG PSP; opracowanie: "
            "zespół pod kierownictwem st. bryg. Michała Kłosińskiego. Inwentaryzacja: "
            "maj 2026; rozszerzenie CAP/IoT: eksport lipiec 2026."
        ),
        "creators": [
            {
                "name": (
                    "Komenda Główna Państwowej Straży Pożarnej — "
                    "Biuro Informatyki i Łączności"
                ),
            },
        ],
        "contributors": [
            {
                "name": "Kłosiński, Michał",
                "affiliation": "Biuro Informatyki i Łączności KG PSP",
                "type": "ProjectLeader",
            }
        ],
        "version": "2026.05",
        "language": "pol",
        "access_right": "restricted",
        "access_conditions": (
            "Draft przedpublikacyjny. Dostęp zostanie otwarty po zgodzie właściciela "
            "danych, kontroli informacji i zatwierdzeniu macierzy wielu licencji."
        ),
        "keywords": [
            "SOIA", "syreny alarmowe", "GIS", "TERYT", "ochrona ludności",
            "zasięg akustyczny", "reprodukowalność",
        ],
        "notes": (
            "Pliki mają różne warunki: kod MIT, własna dokumentacja CC BY 4.0, "
            "OpenStreetMap ODbL oraz prawa pozostałych źródeł wskazane w "
            "data-manifest.json. Przed publikacją należy dodać wszystkie prawa "
            "w interfejsie Zenodo; jednej licencji nie wolno stosować do całej paczki."
        ),
    }


def artifact_paths(artifacts_dir: Path, manifest: Path) -> list[Path]:
    names = [
        "soia-sources-2026.05.zip",
        "soia-derived-v9-v13-2026.05.zip",
        "soia-quickstart-2026.05.zip",
    ]
    paths = [artifacts_dir / name for name in names] + [manifest]
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise FileNotFoundError("Brak artefaktów: " + ", ".join(missing))
    total = sum(path.stat().st_size for path in paths)
    if len(paths) > MAX_FILES or total > MAX_BYTES:
        raise ValueError(f"Pakiet przekracza limit Zenodo: {len(paths)} plików, {total} B")
    return paths


def create_or_update_draft(
    *,
    base_url: str,
    token: str,
    metadata_payload: dict[str, object],
    files: list[Path],
    upload: bool,
    deposition_id: int | None = None,
) -> dict[str, object]:
    headers = {"Authorization": f"Bearer {token}"}
    endpoint = f"{base_url}/api/deposit/depositions"
    request = requests.post
    if deposition_id is not None:
        endpoint = f"{endpoint}/{deposition_id}"
        request = requests.put
    response = request(
        endpoint,
        headers={**headers, "Content-Type": "application/json"},
        json={"metadata": metadata_payload},
        timeout=60,
    )
    response.raise_for_status()
    draft = response.json()
    if upload:
        bucket = draft["links"]["bucket"]
        for path in files:
            with path.open("rb") as handle:
                uploaded = requests.put(
                    f"{bucket}/{quote(path.name)}", headers=headers, data=handle,
                    timeout=(60, 24 * 60 * 60),
                )
            uploaded.raise_for_status()
    return {
        "id": draft["id"],
        "html": draft["links"].get("html"),
        "api": draft["links"].get("self"),
        "doi": draft.get("metadata", {}).get("prereserve_doi", {}).get("doi"),
        "state": draft.get("state"),
        "submitted": draft.get("submitted", False),
        "created": deposition_id is None,
        "files_uploaded": upload,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Przygotowuje albo tworzy NIEOPUBLIKOWANY draft Zenodo. Nie zawiera operacji publish."
    )
    parser.add_argument("--artifacts-dir", type=Path, default=Path("release-artifacts"))
    parser.add_argument("--manifest", type=Path, default=Path("data/manifests/data-manifest.json"))
    parser.add_argument("--metadata-output", type=Path, default=Path("publication/zenodo-metadata.json"))
    parser.add_argument("--create-draft", action="store_true")
    parser.add_argument("--upload-files", action="store_true")
    parser.add_argument(
        "--deposition-id",
        type=int,
        help="identyfikator istniejącego, nieopublikowanego draftu do uzupełnienia",
    )
    parser.add_argument("--sandbox", action="store_true")
    args = parser.parse_args()
    if args.deposition_id is not None and not args.create_draft:
        parser.error("--deposition-id wymaga --create-draft")
    if args.upload_files and not args.create_draft:
        parser.error("--upload-files wymaga --create-draft")
    payload = metadata()
    args.metadata_output.parent.mkdir(parents=True, exist_ok=True)
    args.metadata_output.write_text(json.dumps({"metadata": payload}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    files = artifact_paths(args.artifacts_dir, args.manifest)
    plan = {
        "metadata": str(args.metadata_output),
        "files": [{"name": path.name, "size_bytes": path.stat().st_size} for path in files],
        "total_bytes": sum(path.stat().st_size for path in files),
        "within_zenodo_limits": True,
        "publication_action_available": False,
    }
    if not args.create_draft:
        print(json.dumps(plan, ensure_ascii=False))
        return 0
    token = os.environ.get("ZENODO_TOKEN")
    if not token:
        raise SystemExit("Brak ZENODO_TOKEN. Tokenu nie zapisuj w repozytorium ani w argumentach polecenia.")
    base = "https://sandbox.zenodo.org" if args.sandbox else "https://zenodo.org"
    result = create_or_update_draft(
        base_url=base,
        token=token,
        metadata_payload=payload,
        files=files,
        upload=args.upload_files,
        deposition_id=args.deposition_id,
    )
    output = args.artifacts_dir / "zenodo-draft.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
