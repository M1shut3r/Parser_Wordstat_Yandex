from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

from .client import WordstatClient
from .config import ConfigManager
from .models import ParseResult

LogCallback = Callable[[str], None]
ProgressCallback = Callable[[int, int], None]
StatsCallback = Callable[[int, int, int], None]
ResultCallback = Callable[[ParseResult, int], None]
FinishCallback = Callable[[list[ParseResult]], None]


class WordstatProcessor:
    """
    Основной обработчик запросов Wordstat.

    Processor не занимается отображением UI напрямую.
    Он сообщает интерфейсу о событиях через callbacks:

    - log_callback      — новое сообщение в лог;
    - progress_callback — изменение прогресса;
    - stats_callback    — статистика API;
    - result_callback   — найден новый подходящий результат;
    - finish_callback   — обработка полностью завершена.
    """

    def __init__(
        self,
        config: ConfigManager,
        queries_file: str,
        log_callback: LogCallback,
        progress_callback: ProgressCallback,
        stats_callback: StatsCallback,
        finish_callback: FinishCallback,
        result_callback: ResultCallback | None = None,
    ) -> None:
        self.config = config
        self.queries_file = Path(queries_file)

        self.log = log_callback
        self.progress_callback = progress_callback
        self.stats_callback = stats_callback
        self.finish_callback = finish_callback
        self.result_callback = result_callback

        self.stop_event = threading.Event()

        self.client = WordstatClient(
            config=config,
            log_callback=log_callback,
            stats_callback=stats_callback,
        )

    def stop(self) -> None:
        """
        Запрашивает остановку обработки.

        Текущий HTTP-запрос будет завершён,
        после чего processor прекратит дальнейшую обработку.
        """
        self.stop_event.set()

    def _load_queries(self) -> list[str]:
        """
        Загружает запросы из TXT-файла.

        Пустые строки удаляются.
        Дубликаты удаляются с сохранением исходного порядка.
        """
        with self.queries_file.open(
            "r",
            encoding="utf-8-sig",
        ) as file:
            queries = [line.strip() for line in file if line.strip()]

        return list(dict.fromkeys(queries))

    def _emit_result(
        self,
        result: ParseResult,
        found_count: int,
    ) -> None:
        """
        Немедленно передаёт найденный результат в UI.

        Важно:
        callback вызывается непосредственно в момент,
        когда результат добавлен в results, а не после
        завершения всей обработки.
        """
        if self.result_callback is None:
            return

        try:
            self.result_callback(
                result,
                found_count,
            )
        except Exception as error:
            # Ошибка UI callback не должна останавливать
            # основной процесс парсинга.
            self.log(f"Ошибка обновления интерфейса: {error}")

    def run(self) -> None:
        """
        Основной цикл обработки запросов.

        Метод предполагается запускать в отдельном потоке,
        чтобы не блокировать GUI.
        """
        results: list[ParseResult] = []

        try:
            try:
                queries = self._load_queries()
            except OSError as error:
                self.log(f"Ошибка чтения файла запросов: {error}")
                return

            if not self.config.accounts:
                self.log("Невозможно начать обработку: не добавлен ни один аккаунт.")
                return

            total = len(queries)

            if total == 0:
                self.log("Файл запросов пуст.")
                return

            self.log(f"Всего уникальных запросов: {total}.")
            self.log("Начинаем обработку...")

            self.stats_callback(
                self.config.settings.max_requests_per_hour,
                1,
                len(self.config.accounts),
            )

            processed = 0
            found = 0

            # Показываем начальное состояние прогресса.
            self.progress_callback(
                0,
                total,
            )

            for index, query in enumerate(
                queries,
                start=1,
            ):
                if self.stop_event.is_set():
                    self.log("Обработка остановлена пользователем.")
                    break

                self.log(f"[{index}/{total}] Обработка: {query}")

                normal_count = self.client.get_count(
                    query,
                    self.stop_event,
                )

                if self.stop_event.is_set():
                    break

                quoted_count = 0

                if normal_count > self.config.settings.min_normal_count:
                    quoted_count = self.client.get_count(
                        f'"{query}"',
                        self.stop_event,
                    )

                if self.stop_event.is_set():
                    break

                result = ParseResult(
                    query=query,
                    normal_count=normal_count,
                    quoted_count=quoted_count,
                )

                is_valid = (
                    normal_count > self.config.settings.min_normal_count
                    and quoted_count > self.config.settings.min_quoted_count
                )

                if is_valid:
                    results.append(result)
                    found += 1

                    self.log(
                        f"  -> Подходит: обычный={normal_count}, кавычки={quoted_count}"
                    )

                    # КЛЮЧЕВОЕ ИЗМЕНЕНИЕ:
                    # UI получает результат сразу,
                    # не дожидаясь окончания всего файла.
                    self._emit_result(
                        result,
                        found,
                    )
                else:
                    self.log(
                        "  -> Не подходит: "
                        f"обычный={normal_count}, "
                        f"кавычки={quoted_count}"
                    )

                processed = index

                # Обновляем прогресс ПОСЛЕ обработки запроса.
                self.progress_callback(
                    processed,
                    total,
                )

        except Exception as error:
            self.log(f"Критическая ошибка обработки: {error}")

        finally:
            self.client.close()

            self.finish_callback(results)
