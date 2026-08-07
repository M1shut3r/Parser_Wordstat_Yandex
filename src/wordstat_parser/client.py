from __future__ import annotations

import json
import random
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

import requests

from .config import ConfigManager
from .models import AccountState

LogCallback = Callable[[str], None]
StatsCallback = Callable[[int, int, int], None]


class BlockLogger:
    def __init__(
        self,
        log_file: str | Path = "block_history.json",
    ) -> None:
        self.log_file = Path(log_file)

    def log_block(
        self,
        account_index: int,
        blocked_until: datetime,
    ) -> None:
        history: list[dict] = []

        try:
            if self.log_file.exists():
                data = json.loads(
                    self.log_file.read_text(
                        encoding="utf-8",
                    )
                )

                if isinstance(data, list):
                    history = data

        except (
            OSError,
            json.JSONDecodeError,
            TypeError,
        ):
            history = []

        history.append(
            {
                "account_index": account_index,
                "blocked_at": datetime.now().isoformat(),
                "unblocks_at": blocked_until.isoformat(),
            }
        )

        history = history[-100:]

        try:
            self.log_file.write_text(
                json.dumps(
                    history,
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except OSError:
            pass


class WordstatClient:
    BASE_URL = "https://searchapi.api.cloud.yandex.net/v2/wordstat/topRequests"

    REQUEST_TIMEOUT = 15
    ACCOUNT_BLOCK_SECONDS = 3600

    MAX_NETWORK_RETRIES = 5
    MAX_SERVER_RETRIES = 5

    RETRY_BASE_DELAY = 1.0
    RETRY_MAX_DELAY = 30.0

    def __init__(
        self,
        config: ConfigManager,
        log_callback: LogCallback,
        stats_callback: StatsCallback,
    ) -> None:
        self.config = config
        self.log = log_callback
        self.stats_callback = stats_callback

        self.current_index = 0
        self.block_logger = BlockLogger()
        self.session = requests.Session()
        self._lock = threading.Lock()

    @property
    def accounts(self) -> list[AccountState]:
        return self.config.accounts

    def close(self) -> None:
        self.session.close()

    def _reset_if_expired(
        self,
        account: AccountState,
    ) -> None:
        if time.time() - account.last_reset >= 3600:
            account.reset()

    def _is_account_available(
        self,
        account: AccountState,
    ) -> bool:
        self._reset_if_expired(account)
        return account.is_available()

    def _account_has_requests(
        self,
        account: AccountState,
    ) -> bool:
        return account.requests_used < self.config.settings.max_requests_per_hour

    def _update_stats(self) -> None:
        if not self.accounts:
            self.stats_callback(0, 0, 0)
            return

        self.current_index = min(
            self.current_index,
            len(self.accounts) - 1,
        )

        account = self.accounts[self.current_index]

        remaining = max(
            0,
            self.config.settings.max_requests_per_hour - account.requests_used,
        )

        self.stats_callback(
            remaining,
            self.current_index + 1,
            len(self.accounts),
        )

    def _switch_account(self) -> bool:
        if not self.accounts:
            return False

        previous_index = self.current_index

        for offset in range(
            1,
            len(self.accounts) + 1,
        ):
            index = (previous_index + offset) % len(self.accounts)

            account = self.accounts[index]

            if not self._is_account_available(account):
                continue

            if not self._account_has_requests(account):
                continue

            self.current_index = index

            self.log(
                f"Переключение с аккаунта {previous_index + 1} на аккаунт {index + 1}."
            )

            self._update_stats()

            return True

        return False

    def _find_available_account(self) -> bool:
        if not self.accounts:
            return False

        for index, account in enumerate(self.accounts):
            if not self._is_account_available(account):
                continue

            if not self._account_has_requests(account):
                continue

            self.current_index = index
            self._update_stats()

            return True

        return False

    def _all_accounts_exhausted(self) -> bool:
        if not self.accounts:
            return True

        for account in self.accounts:
            self._reset_if_expired(account)

            if account.is_available() and self._account_has_requests(account):
                return False

        return True

    def _block_account(
        self,
        index: int,
        reason: str,
    ) -> None:
        if not 0 <= index < len(self.accounts):
            return

        account = self.accounts[index]

        account.is_blocked = True
        account.blocked_until = datetime.now() + timedelta(
            seconds=self.ACCOUNT_BLOCK_SECONDS,
        )

        self.block_logger.log_block(
            index,
            account.blocked_until,
        )

        self.log(f"Аккаунт {index + 1} временно заблокирован: {reason}")

    def _get_earliest_unblock_time(
        self,
    ) -> datetime | None:
        unblock_times = [
            account.blocked_until
            for account in self.accounts
            if account.blocked_until is not None
        ]

        if not unblock_times:
            return None

        return min(unblock_times)

    def _wait_for_available_account(
        self,
        stop_event: threading.Event,
    ) -> bool:
        notification_sent = False

        while not stop_event.is_set():
            if self._find_available_account():
                return True

            unblock_time = self._get_earliest_unblock_time()

            if unblock_time is None:
                return False

            wait_seconds = max(
                0,
                (unblock_time - datetime.now()).total_seconds(),
            )

            if not notification_sent:
                self.log(
                    "Все доступные аккаунты временно "
                    "заблокированы. "
                    f"Ожидание примерно "
                    f"{int(wait_seconds) + 1} сек."
                )
                notification_sent = True

            stop_event.wait(
                min(
                    1.0,
                    max(0.1, wait_seconds),
                )
            )

        return False

    def _prepare_account(
        self,
        stop_event: threading.Event,
    ) -> AccountState | None:
        if not self.accounts:
            self.log("Нет настроенных аккаунтов.")
            return None

        while not stop_event.is_set():
            if self.current_index >= len(self.accounts):
                self.current_index = 0

            account = self.accounts[self.current_index]

            self._reset_if_expired(account)

            if account.is_available() and self._account_has_requests(account):
                self._update_stats()
                return account

            if self._switch_account():
                continue

            if self._all_accounts_exhausted():
                return None

            if self._wait_for_available_account(
                stop_event,
            ):
                continue

            return None

        return None

    def _build_headers(
        self,
        account: AccountState,
    ) -> dict[str, str]:
        return {
            "Authorization": (f"Api-Key {account.config.api_key}"),
            "Content-Type": "application/json",
        }

    @staticmethod
    def _build_payload(
        phrase: str,
        folder_id: str,
    ) -> dict:
        return {
            "folderId": folder_id,
            "phrase": phrase,
            "numPhrases": "10",
            "regions": ["225"],
            "devices": ["DEVICE_ALL"],
        }

    def _post(
        self,
        account: AccountState,
        phrase: str,
    ) -> tuple[
        requests.Response | None,
        Exception | None,
    ]:
        try:
            response = self.session.post(
                self.BASE_URL,
                headers=self._build_headers(account),
                json=self._build_payload(
                    phrase,
                    account.config.folder_id,
                ),
                timeout=self.REQUEST_TIMEOUT,
            )

            account.requests_used += 1
            self._update_stats()

            return response, None

        except requests.RequestException as error:
            return None, error

    @classmethod
    def _calculate_backoff(
        cls,
        attempt: int,
    ) -> float:
        base_delay = min(
            cls.RETRY_BASE_DELAY * (2 ** (attempt - 1)),
            cls.RETRY_MAX_DELAY,
        )

        jitter = random.uniform(
            0.0,
            min(1.0, base_delay * 0.25),
        )

        return min(
            cls.RETRY_MAX_DELAY,
            base_delay + jitter,
        )

    @staticmethod
    def _get_retry_after(
        response: requests.Response,
    ) -> float | None:
        value = response.headers.get("Retry-After")

        if not value:
            return None

        try:
            seconds = float(value)
        except ValueError:
            return None

        if seconds < 0:
            return None

        return min(seconds, 60.0)

    def get_count(
        self,
        phrase: str,
        stop_event: threading.Event,
    ) -> int | None:
        phrase = phrase.strip()

        if not phrase:
            return 0

        network_retry = 0
        server_retry = 0

        while not stop_event.is_set():
            account = self._prepare_account(
                stop_event,
            )

            if account is None:
                if stop_event.is_set():
                    return None

                self.log(
                    f'Не удалось получить доступный аккаунт для запроса "{phrase}".'
                )

                return None

            delay = self.config.settings.request_delay

            if delay > 0:
                if stop_event.wait(delay):
                    return None

            response, network_error = self._post(
                account,
                phrase,
            )

            if network_error is not None:
                network_retry += 1

                if network_retry > self.MAX_NETWORK_RETRIES:
                    self.log(
                        f"Сетевая ошибка при запросе "
                        f'"{phrase}" после '
                        f"{self.MAX_NETWORK_RETRIES} повторов: "
                        f"{network_error}"
                    )

                    return None

                retry_delay = self._calculate_backoff(
                    network_retry,
                )

                self.log(
                    f"Сетевая ошибка при запросе "
                    f'"{phrase}": {network_error}. '
                    f"Повтор {network_retry}/"
                    f"{self.MAX_NETWORK_RETRIES} "
                    f"через {retry_delay:.1f} сек."
                )

                if stop_event.wait(retry_delay):
                    return None

                continue

            if response is None:
                self.log(f'Неизвестная ошибка сети при запросе "{phrase}".')
                return None

            network_retry = 0

            if response.status_code == 200:
                try:
                    data = response.json()
                except ValueError as error:
                    self.log(f"Wordstat API вернул некорректный JSON: {error}")
                    return None

                try:
                    return int(
                        data.get(
                            "totalCount",
                            0,
                        )
                    )
                except (
                    TypeError,
                    ValueError,
                ) as error:
                    self.log(f"API вернул некорректное значение totalCount: {error}")
                    return None

            if response.status_code == 429:
                self._block_account(
                    self.current_index,
                    "получен HTTP 429",
                )

                if self._switch_account():
                    server_retry = 0
                    continue

                if self._wait_for_available_account(
                    stop_event,
                ):
                    server_retry = 0
                    continue

                return None

            if response.status_code in (401, 403):
                self._block_account(
                    self.current_index,
                    (f"HTTP {response.status_code} (ошибка авторизации)"),
                )

                if self._switch_account():
                    continue

                self.log("Нет доступных аккаунтов для продолжения работы.")

                return None

            if 500 <= response.status_code < 600:
                server_retry += 1

                if server_retry > self.MAX_SERVER_RETRIES:
                    self.log(
                        f"Ошибка сервера Wordstat API "
                        f'при запросе "{phrase}": '
                        f"HTTP {response.status_code}. "
                        f"Исчерпаны повторные попытки."
                    )

                    return None

                retry_delay = self._calculate_backoff(
                    server_retry,
                )

                self.log(
                    f"Ошибка сервера Wordstat API: "
                    f"HTTP {response.status_code}. "
                    f"Повтор {server_retry}/"
                    f"{self.MAX_SERVER_RETRIES} "
                    f"через {retry_delay:.1f} сек."
                )

                if stop_event.wait(retry_delay):
                    return None

                continue

            try:
                error_data = response.json()

                error_message = str(
                    error_data.get(
                        "message",
                        response.text,
                    )
                )
            except ValueError:
                error_message = response.text

            self.log(
                f"Ошибка Wordstat API при запросе "
                f'"{phrase}": '
                f"HTTP {response.status_code}: "
                f"{error_message[:300]}"
            )

            return None

        return None

    def validate_account(
        self,
        api_key: str,
        folder_id: str,
    ) -> tuple[bool, str]:
        api_key = api_key.strip()
        folder_id = folder_id.strip()

        if not api_key:
            return False, "API Key не указан."

        if not folder_id:
            return False, "Folder ID не указан."

        headers = {
            "Authorization": f"Api-Key {api_key}",
            "Content-Type": "application/json",
        }

        payload = self._build_payload(
            phrase="тест",
            folder_id=folder_id,
        )

        try:
            response = self.session.post(
                self.BASE_URL,
                headers=headers,
                json=payload,
                timeout=10,
            )
        except requests.RequestException as error:
            return False, f"Сетевая ошибка: {error}"

        if response.status_code == 200:
            return True, "ok"

        if response.status_code == 429:
            return True, "rate_limited"

        try:
            data = response.json()

            message = str(
                data.get(
                    "message",
                    response.text,
                )
            )
        except ValueError:
            message = response.text

        message = message.strip()

        if not message:
            message = "Неизвестная ошибка API."

        return (
            False,
            (f"HTTP {response.status_code}: {message[:300]}"),
        )
