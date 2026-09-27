import io
import uuid
from datetime import date
from fractions import Fraction
from typing import Any

import pytest
from openpyxl import Workbook

from app.domain.bom_rows import (
    CatalogIndex,
    FileError,
    ItemInfo,
    read_table,
    template_csv,
    validate_rows,
)
from app.domain.units import Conversion

OPC = ItemInfo(uuid.uuid4(), "OPC53", "OPC 53 Grade Cement", "OPC 53", "bag",
               ("cement 53 grade", "opc 53"))  # fmt: skip
PPC = ItemInfo(uuid.uuid4(), "PPC", "PPC Cement", "PPC", "bag", ("ppc",))
TMT = ItemInfo(uuid.uuid4(), "TMT500D", "TMT Bar Fe 500D", "Fe 500D", "tonne", ("saria", "tmt"))
SAND = ItemInfo(uuid.uuid4(), "RIVERSAND", "River Sand", None, "cft", ("sand", "reti"))

INDEX = CatalogIndex(
    items=[OPC, PPC, TMT, SAND],
    conversions={
        None: [
            Conversion("tonne", "kg", Fraction(1000)),
            Conversion("brass", "cft", Fraction(100)),
        ],
        OPC.id: [Conversion("bag", "kg", Fraction(50))],
    },
    suggest=lambda text: [OPC, PPC] if "cem" in text.lower() else [],
)
TODAY = date(2026, 9, 25)
OK_DATE = "2026-10-05"


def row(**kw: Any) -> dict[str, Any]:
    base = {"item": "OPC 53 Grade Cement", "quantity": "30", "unit": "bag", "needed_by": OK_DATE}
    return {**base, **kw}


def check(*raws: dict[str, Any]) -> list[dict[str, Any]]:
    rows = validate_rows(list(raws), INDEX, today=TODAY, site_name="Noida Sector 62 Tower",
                         other_site_names={"gurugram sector 49"})  # fmt: skip
    return [r.as_dict() for r in rows]


def errors(r: dict[str, Any]) -> dict[str, str]:
    return {e["field"]: e["message"] for e in r["errors"]}


# --- reading files -------------------------------------------------------------------------


def test_template_round_trips() -> None:
    rows = read_table(template_csv().encode(), "template.csv")
    assert [r["item"] for r in rows] == ["OPC 53 Grade Cement", "TMT Bar Fe 500D"]


@pytest.mark.parametrize("data", [b"", b"\n\n", b" , , \n"])
def test_empty_file(data: bytes) -> None:
    with pytest.raises(FileError, match="empty"):
        read_table(data, "bom.csv")


def test_header_only() -> None:
    with pytest.raises(FileError, match="no items"):
        read_table(b"item,quantity,unit,needed_by\n", "bom.csv")


def test_wrong_columns() -> None:
    with pytest.raises(FileError, match="Missing column"):
        read_table(b"name,amount\ncement,30\n", "bom.csv")


def test_header_aliases_accepted() -> None:
    rows = read_table(b"Material,Qty,UOM,Required By\nsaria,2,tonne,05/10/2026\n", "bom.csv")
    assert rows[0] == {"item": "saria", "quantity": "2", "unit": "tonne", "needed_by": "05/10/2026"}


def test_more_than_500_rows_rejected() -> None:
    body = "item,quantity,unit,needed_by\n" + "sand,10,cft,2026-10-05\n" * 501
    with pytest.raises(FileError, match="Too many rows: 501"):
        read_table(body.encode(), "bom.csv")


def test_exactly_500_rows_ok() -> None:
    body = "item,quantity,unit,needed_by\n" + "sand,10,cft,2026-10-05\n" * 500
    assert len(read_table(body.encode(), "bom.csv")) == 500


def test_non_utf8_csv_read_as_cp1252() -> None:
    data = "item,quantity,unit,needed_by,notes\nsand,10,cft,2026-10-05,Délivery ₹ gate\n".encode(
        "cp1252", errors="replace"
    )
    rows = read_table(data, "bom.csv")
    assert rows[0]["notes"].startswith("D") and rows[0]["item"] == "sand"


def test_utf16_csv() -> None:
    data = "item,quantity,unit,needed_by\nsand,10,cft,2026-10-05\n".encode("utf-16")
    assert read_table(data, "bom.csv")[0]["item"] == "sand"


def test_wrong_file_type() -> None:
    with pytest.raises(FileError, match=r"\.csv or \.xlsx"):
        read_table(b"%PDF-1.4", "bom.pdf")


def test_corrupt_xlsx() -> None:
    with pytest.raises(FileError, match="not a readable"):
        read_table(b"not a zip", "bom.xlsx")


def _xlsx(rows: list[list[Any]]) -> bytes:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_xlsx_values_and_dates() -> None:
    data = _xlsx([["Item", "Qty", "Unit", "Needed By"], ["saria", 2.5, "tonne", date(2026, 10, 5)]])
    r = check(read_table(data, "bom.xlsx")[0])[0]
    assert r["errors"] == [] and r["qty_canonical_milli"] == 2500 and r["needed_by"] == OK_DATE


def test_xlsx_formula_without_saved_value_is_flagged() -> None:
    # openpyxl writes formulas without computing them, like a file never opened in Excel.
    data = _xlsx([["item", "quantity", "unit", "needed_by"], ["sand", "=5*20", "cft", OK_DATE]])
    r = check(read_table(data, "bom.xlsx")[0])[0]
    assert "no saved value" in errors(r)["quantity"]


# --- row rules ---------------------------------------------------------------------------


def test_valid_row() -> None:
    r = check(row())[0]
    assert r["errors"] == [] and r["item"]["code"] == "OPC53" and r["qty_canonical_milli"] == 30_000


@pytest.mark.parametrize(("qty", "msg"), [("0", "more than zero"), ("-5", "more than zero"),
                                          ("abc", "not a number"), ("", "required")])  # fmt: skip
def test_bad_quantities(qty: str, msg: str) -> None:
    assert msg in errors(check(row(quantity=qty))[0])["quantity"]


def test_fractional_bags_rejected() -> None:
    assert "whole number" in errors(check(row(quantity="10.5"))[0])["quantity"]


def test_quantity_with_commas() -> None:
    assert check(row(quantity="1,000"))[0]["qty_canonical_milli"] == 1_000_000


def test_alias_and_spec_matching() -> None:
    assert check(row(item="cement", spec="53 grade"))[0]["item"]["code"] == "OPC53"
    assert (
        check(row(item="Saria", spec="Fe 500D", unit="MT", quantity="2"))[0]["item"]["code"]
        == "TMT500D"
    )


def test_unknown_item_suggests_and_requires_choice() -> None:
    r = check(row(item="cemnt"))[0]
    assert "Unknown item" in errors(r)["item"]
    assert [s["code"] for s in r["suggestions"]] == ["OPC53", "PPC"]


def test_chosen_item_overrides_text() -> None:
    r = check(row(item="cemnt", catalog_item_id=str(PPC.id)))[0]
    assert r["errors"] == [] and r["item"]["code"] == "PPC"


def test_unit_conversion_to_canonical() -> None:
    r = check(row(quantity="1.5", unit="tonne"))[0]  # 1.5 t of 50 kg bags = 30 bags
    assert r["qty_canonical_milli"] == 30_000
    assert check(row(item="sand", quantity="2", unit="brass"))[0]["qty_canonical_milli"] == 200_000


def test_truck_of_sand_asks_for_cft_or_brass() -> None:
    msg = errors(check(row(item="sand", quantity="2", unit="truck"))[0])["unit"]
    assert "not recognised" in msg and "brass, cft" in msg


def test_unit_known_but_not_convertible_for_item() -> None:
    msg = errors(check(row(item="sand", quantity="2", unit="bag"))[0])["unit"]
    assert "cannot be converted" in msg


@pytest.mark.parametrize(("d", "msg"), [("2026-09-20", "in the past"), ("2026-09-26", "Too soon"),
                                        ("31/31/2026", "not understood")])  # fmt: skip
def test_bad_dates(d: str, msg: str) -> None:
    assert msg in errors(check(row(needed_by=d))[0])["needed_by"]


def test_earliest_feasible_date_accepted() -> None:
    assert check(row(needed_by="2026-09-28"))[0]["errors"] == []


def test_indian_date_format() -> None:
    assert check(row(needed_by="05/10/2026"))[0]["needed_by"] == OK_DATE


def test_site_from_another_org_is_unknown() -> None:
    assert "Unknown site" in errors(check(row(site="Dwarka Sector 19"))[0])["site"]


def test_other_site_of_same_org_needs_separate_bom() -> None:
    assert "separate BOM" in errors(check(row(site="Gurugram Sector 49"))[0])["site"]


def test_partial_allowed_values() -> None:
    assert check(row(partial_allowed="Yes"))[0]["partial_allowed"] is True
    assert "yes or no" in errors(check(row(partial_allowed="maybe"))[0])["partial_allowed"]


def test_duplicates_merged() -> None:
    a, b, c = check(row(), row(quantity="0.5", unit="tonne"), row(needed_by="2026-10-06"))
    assert a["qty_canonical_milli"] == 40_000 and "merged" in a["warnings"][0]
    assert b["merged_into"] == 1
    assert c["merged_into"] is None


def test_no_rows() -> None:
    with pytest.raises(FileError):
        check()
