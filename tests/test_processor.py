import threading
from unittest.mock import Mock

from wordstat_parser.client import ValidationStatus
from wordstat_parser.models import ParseResult
from wordstat_parser.processor import WordstatProcessor


def _make_processor() -> WordstatProcessor:
    processor = object.__new__(WordstatProcessor)
    processor.stop_event = threading.Event()
    processor.log = Mock()
    processor.progress_callback = Mock()
    processor.stats_callback = Mock()
    processor.finish_callback = Mock()
    processor.result_callback = None
    processor.config = Mock()
    processor.config.accounts = [Mock()]
    processor.config.accounts[0].config.api_key = "key"
    processor.config.accounts[0].config.folder_id = "folder"
    processor.config.settings.max_requests_per_hour = 100
    processor.config.settings.min_normal_count = 500
    processor.config.settings.min_quoted_count = 30
    processor.queries_file = None
    processor.progress_manager = Mock()
    processor.client = Mock()
    return processor


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
    processor = _make_processor()
    processor.client.validate_account.return_value = (
        ValidationStatus.OK,
        "ok",
    )
    processor.client.get_count.return_value = None
    processor._load_queries = Mock(return_value=["запрос"])
    processor._load_progress = Mock(return_value=(0, []))

    processor.run()

    processor.progress_manager.save.assert_called()
    assert processor.progress_manager.save.call_args.kwargs["next_index"] == 0
    assert processor.progress_manager.save.call_args.kwargs["total"] == 1


def test_processor_validation_failure_does_not_overwrite_progress() -> None:
    processor = _make_processor()
    saved = [
        ParseResult(
            query="отель москва",
            normal_count=2000,
            quoted_count=100,
        )
    ]
    processor._load_queries = Mock(
        return_value=[f"запрос {index}" for index in range(300)]
    )
    processor._load_progress = Mock(return_value=(215, saved))
    processor._validate_accounts = Mock(return_value=False)

    processor.run()

    processor.progress_manager.save.assert_not_called()
    processor.progress_callback.assert_called_with(215, 300)
    processor.finish_callback.assert_called_once()

    results, completed = processor.finish_callback.call_args.args
    assert completed is False
    assert results == saved


def test_processor_unreachable_accounts_still_start_parsing() -> None:
    processor = _make_processor()
    processor.client.validate_account.return_value = (
        ValidationStatus.UNREACHABLE,
        "Сетевая ошибка: Read timed out",
    )
    processor.client.get_count.return_value = 10
    processor._load_queries = Mock(return_value=["запрос"])
    processor._load_progress = Mock(return_value=(0, []))

    processor.run()

    processor.client.get_count.assert_called()
    processor.progress_manager.save.assert_called()
    assert processor.progress_manager.save.call_args.kwargs["next_index"] == 1
    assert processor.progress_manager.save.call_args.kwargs["total"] == 1
