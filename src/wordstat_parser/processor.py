from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

from .client import WordstatClient
from .config import ConfigManager
from .models import ParseResult
from .progress import ProgressManager

LogCallback = Callable[[str], None]
ProgressCallback = Callable[[int, int], None]
StatsCallback = Callable[[int, int, int], None]
ResultCallback = Callable[
    [ParseResult, int],
    None,
]
FinishCallback = Callable[
    [list[ParseResult], bool],
    None,
]


class WordstatProcessor:
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

        self.progress_manager = ProgressManager(self.queries_file)

        self.client = WordstatClient(
            config=config,
            log_callback=log_callback,
            stats_callback=stats_callback,
        )

    def stop(self) -> None:
        self.stop_event.set()

    def _load_queries(self) -> list[str]:
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
        if self.result_callback is None:
            return

        try:
            self.result_callback(
                result,
                found_count,
            )

        except Exception as error:
            self.log(f"Ошибка обновления интерфейса: {error}")

    def _save_progress(
        self,
        next_index: int,
        total: int,
        results: list[ParseResult],
    ) -> None:
        try:
            self.progress_manager.save(
                next_index=next_index,
                total=total,
                results=results,
            )

        except Exception as error:
            self.log(f"Не удалось сохранить прогресс: {error}")

    def _load_progress(
        self,
        total: int,
    ) -> tuple[
        int,
        list[ParseResult],
    ]:
        try:
            return self.progress_manager.load(total)

        except Exception as error:
            self.log(f"Не удалось загрузить прогресс: {error}")

            return 0, []

    def _notify_finish(
        self,
        results: list[ParseResult],
        completed: bool,
    ) -> None:
        try:
            self.finish_callback(
                results,
                completed,
            )

        except Exception as error:
            self.log(f"Ошибка callback завершения: {error}")

    def run(self) -> None:
        results: list[ParseResult] = []

        processed = 0
        total = 0
        completed = False

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

                completed = True
                return

            (
                start_index,
                saved_results,
            ) = self._load_progress(total)

            results = saved_results
            processed = start_index

            if start_index > 0:
                self.log("Найден сохранённый прогресс.")

                self.log(f"Продолжаем с запроса {start_index + 1}/{total}.")

                self.log(f"Восстановлено результатов: {len(results)}.")

                for (
                    result_index,
                    result,
                ) in enumerate(
                    results,
                    start=1,
                ):
                    self._emit_result(
                        result,
                        result_index,
                    )

            else:
                self.log(f"Всего уникальных запросов: {total}.")

                self.log("Начинаем обработку...")

            self.stats_callback(
                self.config.settings.max_requests_per_hour,
                1,
                len(self.config.accounts),
            )

            self.progress_callback(
                processed,
                total,
            )

            found = len(results)

            for index in range(
                start_index,
                total,
            ):
                if self.stop_event.is_set():
                    break

                query = queries[index]

                self.log(f"[{index + 1}/{total}] Обработка: {query}")

                normal_count = self.client.get_count(
                    query,
                    self.stop_event,
                )

                if normal_count is None:
                    if self.stop_event.is_set():
                        break

                    self.log(
                        f"Не удалось получить "
                        f'результат для "{query}". '
                        "Попытка будет "
                        "повторена автоматически."
                    )

                    continue

                quoted_count = 0

                if normal_count > self.config.settings.min_normal_count:
                    quoted_count = self.client.get_count(
                        f'"{query}"',
                        self.stop_event,
                    )

                    if quoted_count is None:
                        if self.stop_event.is_set():
                            break

                        self.log(
                            f"Не удалось получить "
                            f'результат для "{query}" '
                            "в кавычках. "
                            "Попытка будет "
                            "повторена автоматически."
                        )

                        continue

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

                    self._emit_result(
                        result,
                        found,
                    )

                else:
                    self.log(
                        "  -> Не подходит: "
                        f"обычный="
                        f"{normal_count}, "
                        f"кавычки="
                        f"{quoted_count}"
                    )

                processed = index + 1

                self.progress_callback(
                    processed,
                    total,
                )

                self._save_progress(
                    next_index=processed,
                    total=total,
                    results=results,
                )

            completed = processed == total and not self.stop_event.is_set()

        except Exception as error:
            self.log(f"Критическая ошибка обработки: {error}")

            self._save_progress(
                next_index=processed,
                total=total,
                results=results,
            )

        finally:
            try:
                if completed:
                    self.progress_manager.remove()

                    self.log("Обработка полностью завершена.")

                    self.log("Файл сохранённого прогресса удалён.")

                elif self.stop_event.is_set():
                    self._save_progress(
                        next_index=processed,
                        total=total,
                        results=results,
                    )

                    self.log(f"Обработка остановлена: {processed}/{total}.")

                else:
                    self._save_progress(
                        next_index=processed,
                        total=total,
                        results=results,
                    )

                    self.log(f"Обработка прервана: {processed}/{total}.")

            except Exception as error:
                self.log(f"Ошибка сохранения состояния: {error}")

            try:
                self.client.close()
            except Exception:
                pass

            self._notify_finish(
                results,
                completed,
            )
