import time
import threading
import requests
import pandas as pd
from datetime import datetime, timedelta
from typing import List, Dict, Callable, Optional

from models import ConfigManager, AccountState, BlockLogger


class WordstatAPI:
    BASE_URL = "https://searchapi.api.cloud.yandex.net/v2/wordstat/topRequests"

    def __init__(self, config: ConfigManager, log_callback: Callable, stats_callback: Callable):
        self.config = config
        self.block_logger = BlockLogger()
        self.current_index = 0
        self.log = log_callback
        self.stats_callback = stats_callback

    def _switch_account(self):
        old_idx = self.current_index
        self.current_index = (self.current_index + 1) % len(self.config.accounts)
        self.log(f"Переключение с аккаунта {old_idx + 1} на {self.current_index + 1}")
        self._update_stats()

    def _check_all_blocked(self) -> bool:
        return all(acc.is_blocked for acc in self.config.accounts)

    def _wait_for_reset(self):
        unblock_times = [acc.blocked_until for acc in self.config.accounts if acc.is_blocked and acc.blocked_until]
        if not unblock_times: return

        earliest = min(unblock_times)
        wait_seconds = max(0, (earliest - datetime.now()).total_seconds()) + 2

        self.log(f"Все аккаунты заблокированы. Ожидание {int(wait_seconds)} сек...")

        start_wait = time.time()
        while time.time() - start_wait < wait_seconds:
            time.sleep(1)

        for acc in self.config.accounts:
            if acc.is_blocked and acc.blocked_until and datetime.now() >= acc.blocked_until:
                acc.requests_used = 0
                acc.last_reset = time.time()
                acc.is_blocked = False
                acc.blocked_until = None
                self.log(f"Аккаунт {self.config.accounts.index(acc) + 1} разблокирован.")
        self._update_stats()

    def _reset_account_if_needed(self, acc: AccountState):
        if time.time() - acc.last_reset >= 3600:
            acc.requests_used = 0
            acc.last_reset = time.time()
            acc.is_blocked = False
            acc.blocked_until = None

    def _update_stats(self):
        acc = self.config.accounts[self.current_index]
        remaining = max(0, self.config.settings.max_requests_per_hour - acc.requests_used)
        self.stats_callback(remaining, self.current_index + 1, len(self.config.accounts))

    def get_count(self, phrase: str, stop_event: threading.Event) -> int:
        if stop_event.is_set(): return 0

        acc = self.config.accounts[self.current_index]

        if acc.is_blocked:
            if acc.blocked_until and datetime.now() >= acc.blocked_until:
                acc.is_blocked = False
                acc.requests_used = 0
            else:
                if self._check_all_blocked():
                    self._wait_for_reset()
                else:
                    self._switch_account()
                return self.get_count(phrase, stop_event)

        self._reset_account_if_needed(acc)

        if acc.requests_used >= self.config.settings.max_requests_per_hour:
            acc.is_blocked = True
            acc.blocked_until = datetime.now() + timedelta(hours=1)
            self.block_logger.log_block(self.current_index, acc.blocked_until)
            self.log(f"Аккаунт {self.current_index + 1} достиг лимита.")
            if self._check_all_blocked():
                self._wait_for_reset()
            else:
                self._switch_account()
            return self.get_count(phrase, stop_event)

        headers = {"Authorization": f"Api-Key {acc.config.api_key}", "Content-Type": "application/json"}
        data = {"folderId": acc.config.folder_id, "phrase": phrase, "numPhrases": "10", "regions": ["225"],
                "devices": ["DEVICE_ALL"]}

        time.sleep(self.config.settings.request_delay)

        try:
            response = requests.post(self.BASE_URL, headers=headers, json=data, timeout=15)
            acc.requests_used += 1
            self._update_stats()

            if response.status_code == 200:
                return int(response.json().get("totalCount", 0))
            elif response.status_code == 429:
                acc.is_blocked = True
                acc.blocked_until = datetime.now() + timedelta(hours=1)
                self.block_logger.log_block(self.current_index, acc.blocked_until)
                self.log(f"Ошибка 429. Аккаунт {self.current_index + 1} заблокирован.")
                if self._check_all_blocked():
                    self._wait_for_reset()
                else:
                    self._switch_account()
                return self.get_count(phrase, stop_event)
            else:
                self.log(f"Ошибка API {response.status_code}: {response.text[:100]}")
                return 0
        except requests.RequestException as e:
            self.log(f"Сетевая ошибка: {str(e)}")
            return 0


class WordstatProcessor:
    def __init__(self, config: ConfigManager, queries_file: str,
                 log_callback: Callable, progress_callback: Callable,
                 stats_callback: Callable, finish_callback: Callable):
        self.config = config
        self.queries_file = queries_file
        self.log = log_callback
        self.progress_callback = progress_callback
        self.stats_callback = stats_callback
        self.finish_callback = finish_callback

        self.api = WordstatAPI(config, self.log, self.stats_callback)
        self.stop_event = threading.Event()

    def stop(self):
        self.stop_event.set()

    @staticmethod
    def export_results(results: List[Dict], filepath: str):
        """Статический метод для экспорта результатов в Excel. UI не должен знать о pandas."""
        if not results:
            raise ValueError("Нет данных для сохранения")
        df = pd.DataFrame(results)
        df.to_excel(filepath, index=False, engine='openpyxl')

    def run(self):
        try:
            with open(self.queries_file, 'r', encoding='utf-8') as f:
                queries = [line.strip() for line in f if line.strip()]
        except Exception as e:
            self.log(f"Ошибка чтения файла запросов: {e}")
            self.finish_callback([])
            return

        results = []
        total = len(queries)
        self.log(f"Всего запросов: {total}. Начинаем обработку...")
        self.stats_callback(self.config.settings.max_requests_per_hour, 1, len(self.config.accounts))

        for i, query in enumerate(queries):
            if self.stop_event.is_set():
                self.log("Обработка прервана пользователем.")
                break

            self.progress_callback(i + 1, total)
            self.log(f"[{i + 1}/{total}] Обработка: {query}")

            count_normal = self.api.get_count(query, self.stop_event)
            if self.stop_event.is_set(): break

            count_quoted = 0
            if count_normal > self.config.settings.min_normal_count:
                count_quoted = self.api.get_count(f'"{query}"', self.stop_event)
                if self.stop_event.is_set(): break

            if count_normal > self.config.settings.min_normal_count and count_quoted > self.config.settings.min_quoted_count:
                results.append({
                    'Запрос': query,
                    'Показов (обычный)': count_normal,
                    'Показов (в кавычках)': count_quoted
                })
                self.log(f"  -> Подходит (Обычный: {count_normal}, Кавычки: {count_quoted})")
            else:
                self.log(f"  -> Не подходит (Обычный: {count_normal}, Кавычки: {count_quoted})")

        self.finish_callback(results)