from pathlib import Path

from openpyxl import load_workbook

from wordstat_parser.exporter import export_to_excel
from wordstat_parser.models import ParseResult


def test_export_to_excel(tmp_path: Path) -> None:
    output = tmp_path / "results.xlsx"

    results = [
        ParseResult(
            query="купить машину",
            normal_count=1000,
            quoted_count=100,
        ),
    ]

    export_to_excel(results, output)

    assert output.exists()

    workbook = load_workbook(output)
    worksheet = workbook.active

    assert worksheet["A1"].value == "Запрос"
    assert worksheet["B1"].value == "Показов (обычный)"
    assert worksheet["C1"].value == "Показов (в кавычках)"

    assert worksheet["A2"].value == "купить машину"
    assert worksheet["B2"].value == 1000
    assert worksheet["C2"].value == 100
