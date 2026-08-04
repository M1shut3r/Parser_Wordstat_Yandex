import threading

from wordstat_parser.models import ParseResult


def test_parse_result() -> None:
    result = ParseResult(
        query="купить автомобиль",
        normal_count=1000,
        quoted_count=50,
    )

    assert result.query == "купить автомобиль"
    assert result.normal_count == 1000
    assert result.quoted_count == 50