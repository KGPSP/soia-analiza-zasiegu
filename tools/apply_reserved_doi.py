from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


def _write_json(path: Path, data: dict[str, object]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _archive_url(record_id: str, name: str) -> str:
    return f"https://zenodo.org/records/{record_id}/files/{name}?download=1"


def apply_reserved_doi(
    *,
    doi: str,
    record_id: str | None,
    manifest_path: Path,
    specification_path: Path,
    citation_path: Path,
    readme_path: Path,
) -> None:
    if not doi.startswith("10.") or "/" not in doi:
        raise ValueError("Niepoprawny DOI")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    specification = json.loads(specification_path.read_text(encoding="utf-8"))
    for data in (manifest, specification):
        data["doi"] = doi
        if record_id:
            for archive in data.get("archives", []):
                archive["url"] = _archive_url(record_id, archive["name"])
    _write_json(manifest_path, manifest)
    _write_json(specification_path, specification)

    citation = citation_path.read_text(encoding="utf-8")
    if re.search(r"(?m)^doi:", citation):
        citation = re.sub(r"(?m)^doi:.*$", f"doi: {doi}", citation)
    else:
        citation = citation.rstrip() + f"\ndoi: {doi}\n"
    citation_path.write_text(citation, encoding="utf-8")

    readme = readme_path.read_text(encoding="utf-8")
    marker = (
        "- DOI zostanie dopisany do `CITATION.cff` i manifestu po zatwierdzeniu draftu\n"
        "  Zenodo."
    )
    replacement = f"- DOI Zenodo: [{doi}](https://doi.org/{doi})."
    if marker in readme:
        readme = readme.replace(marker, replacement)
    readme_path.write_text(readme, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Wpisuje zarezerwowany DOI Zenodo do artefaktów publikacji.")
    parser.add_argument("--doi", required=True)
    parser.add_argument(
        "--record-id",
        help="identyfikator rekordu używany do publicznych URL archiwów po publikacji",
    )
    parser.add_argument("--manifest", type=Path, default=Path("data/manifests/data-manifest.json"))
    parser.add_argument(
        "--specification",
        type=Path,
        default=Path("configs/package-groups-2026.05.json"),
    )
    parser.add_argument("--citation", type=Path, default=Path("CITATION.cff"))
    parser.add_argument("--readme", type=Path, default=Path("README.md"))
    args = parser.parse_args()
    try:
        apply_reserved_doi(
            doi=args.doi,
            record_id=args.record_id,
            manifest_path=args.manifest,
            specification_path=args.specification,
            citation_path=args.citation,
            readme_path=args.readme,
        )
    except ValueError as error:
        raise SystemExit(str(error)) from error
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
