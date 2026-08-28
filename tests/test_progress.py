from pathlib import Path

from wordstat_parser.models import ParseResult
from wordstat_parser.progress import ProgressManager


def test_progress_save_ignores_empty_total(tmp_path: Path) -> None:
    queries_file = tmp_path / "queries.txt"
    queries_file.write_text("один\nдва\n", encoding="utf-8")

    manager = ProgressManager(queries_file)
    manager.save(next_index=0, total=0, results=[])

    assert manager.exists() is False


def test_progress_roundtrip(tmp_path: Path) -> None:
    queries_file = tmp_path / "queries.txt"
    queries_file.write_text("один\nдва\nтри\n", encoding="utf-8")

    manager = ProgressManager(queries_file)
    result = ParseResult(
        query="один",
        normal_count=1000,
        quoted_count=40,
    )
    manager.save(
        next_index=2,
        total=3,
        results=[result],
    )

    next_index, results = manager.load(3)

    assert next_index == 2
    assert results == [result]
