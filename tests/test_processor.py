import threading
from unittest.mock import Mock

from wordstat_parser.models import ParseResult
from wordstat_parser.processor import WordstatProcessor


def test_parse_result() -> None:
    result = ParseResult(
        query="купить автомобиль",
        normal_count=1000,
        quoted_count=50,
    )

    assert result.query == "купить автомобиль"
    assert result.normal_count == 1000
    assert result.quoted_count == 50


def test_processor_does_not_skip_failed_query() -> None:
    processor = object.__new__(WordstatProcessor)
    processor.stop_event = threading.Event()
    processor.log = Mock()
    processor.progress_callback = Mock()
    processor.stats_callback = Mock()
    processor.finish_callback = Mock()
    processor.result_callback = None
    processor.config = Mock()
    processor.config.accounts = [object()]
    processor.config.settings.max_requests_per_hour = 100
    processor.config.settings.min_normal_count = 500
    processor.config.settings.min_quoted_count = 30
    processor.queries_file = None
    processor.progress_manager = Mock()
    processor.client = Mock()
    processor.client.get_count.return_value = None
    processor._load_queries = Mock(return_value=["запрос"])
    processor._load_progress = Mock(return_value=(0, []))

    processor.run()

    processor.progress_manager.save.assert_called()
    assert processor.progress_manager.save.call_args.kwargs["next_index"] == 0
