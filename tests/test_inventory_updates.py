from pathlib import Path

import pytest

from soia.inventory import InventoryUpdateError, apply_updates, read_updates
from soia.teryt import InvalidTerytError, parse_teryt


def base_inventory() -> list[dict[str, str]]:
    return [
        {
            "nr_ref": "S-1", "teryt_gmi": "2815092", "lat": "53.7", "lon": "20.0",
            "rodzaj_syreny": "cyfrowa", "moc_w": "600", "wysokosc_nad_terenem_m": "10",
            "gsm_status": "NIE", "active": "1"
        },
        {
            "nr_ref": "S-2", "teryt_gmi": "2815092", "lat": "53.8", "lon": "20.1",
            "rodzaj_syreny": "analogowa", "moc_w": "900", "wysokosc_nad_terenem_m": "12",
            "gsm_status": "TAK", "active": "1"
        },
    ]


def write_updates(path: Path, rows: list[str]) -> Path:
    path.write_text(
        "operation,nr_ref,teryt_gmi,lat,lon,rodzaj_syreny,moc_w,"
        "wysokosc_nad_terenem_m,gsm_status,reason\n" + "\n".join(rows) + "\n",
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize(
    ("code", "level"), [("28", "wojewodztwo"), ("2815", "powiat"), ("2815092", "gmina")]
)
def test_parse_teryt_recognizes_administrative_level(code: str, level: str) -> None:
    assert parse_teryt(code).level == level


@pytest.mark.parametrize("code", ["", "281", "281509", "abcdefg", "28150922"])
def test_parse_teryt_rejects_unsupported_code(code: str) -> None:
    with pytest.raises(InvalidTerytError):
        parse_teryt(code)


def test_apply_updates_add_update_disable_and_audit(tmp_path: Path) -> None:
    updates = read_updates(
        write_updates(
            tmp_path / "updates.csv",
            [
                "update,S-1,,,,,900,,TAK,wymiana",
                "disable,S-2,,,,,,,,demontaz",
                "add,S-3,2815092,53.75,20.05,cyfrowa,300,8,NIE,nowa",
            ],
        )
    )

    result, audit = apply_updates(base_inventory(), updates, valid_gmina_codes={"2815092"})

    by_ref = {row["nr_ref"]: row for row in result}
    assert by_ref["S-1"]["moc_w"] == "900"
    assert by_ref["S-1"]["gsm_status"] == "TAK"
    assert by_ref["S-2"]["active"] == "0"
    assert by_ref["S-3"]["active"] == "1"
    assert [row["operation"] for row in audit] == ["update", "disable", "add"]
    assert all(row["status"] == "applied" for row in audit)


def test_updates_reject_duplicate_reference(tmp_path: Path) -> None:
    updates = read_updates(
        write_updates(tmp_path / "updates.csv", ["disable,S-1,,,,,,,,", "update,S-1,,,,,900,,,"])
    )
    with pytest.raises(InventoryUpdateError, match="więcej niż jedną operację"):
        apply_updates(base_inventory(), updates, valid_gmina_codes={"2815092"})


def test_add_rejects_invalid_coordinates_and_missing_fields(tmp_path: Path) -> None:
    updates = read_updates(
        write_updates(tmp_path / "updates.csv", ["add,S-3,2815092,10,200,,0,-2,,bad"])
    )
    with pytest.raises(InventoryUpdateError, match="S-3"):
        apply_updates(base_inventory(), updates, valid_gmina_codes={"2815092"})


def test_update_rejects_unknown_reference(tmp_path: Path) -> None:
    updates = read_updates(
        write_updates(tmp_path / "updates.csv", ["update,S-999,,,,,900,,,unknown"])
    )
    with pytest.raises(InventoryUpdateError, match="nie istnieje"):
        apply_updates(base_inventory(), updates, valid_gmina_codes={"2815092"})


def test_update_rejects_coordinates_outside_poland(tmp_path: Path) -> None:
    updates = read_updates(
        write_updates(tmp_path / "updates.csv", ["update,S-1,,10,200,,,,,bad"])
    )
    with pytest.raises(InventoryUpdateError, match="współrzędne"):
        apply_updates(base_inventory(), updates, valid_gmina_codes={"2815092"})


def test_disable_rejects_data_fields(tmp_path: Path) -> None:
    updates = read_updates(
        write_updates(tmp_path / "updates.csv", ["disable,S-1,,,,,900,,,bad"])
    )
    with pytest.raises(InventoryUpdateError, match="disable przyjmuje tylko"):
        apply_updates(base_inventory(), updates, valid_gmina_codes={"2815092"})
