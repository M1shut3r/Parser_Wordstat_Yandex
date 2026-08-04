from __future__ import annotations

from pathlib import Path
from typing import Iterable

from openpyxl import Workbook

from .models import ParseResult


def export_to_excel(
    results: Iterable[ParseResult],
    filepath: str | Path,
) -> None:
    results = list(results)

    if not results:
        raise ValueError(
            "Нет данных для сохранения."
        )

    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Результаты"

    worksheet.append(
        [
            "Запрос",
            "Показов (обычный)",
            "Показов (в кавычках)",
        ]
    )

    for result in results:
        worksheet.append(
            [
                result.query,
                result.normal_count,
                result.quoted_count,
            ]
        )

    worksheet.freeze_panes = "A2"
    worksheet.auto_filter.ref = worksheet.dimensions

    worksheet.column_dimensions["A"].width = 60
    worksheet.column_dimensions["B"].width = 22
    worksheet.column_dimensions["C"].width = 24

    workbook.save(filepath)