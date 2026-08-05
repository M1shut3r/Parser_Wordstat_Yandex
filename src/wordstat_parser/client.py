from __future__ import annotations

import json
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
    """Сохраняет историю временной блокировки аккаунтов."""

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

        # Не даём файлу истории расти бесконечно.
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
            # Ошибка журнала не должна ломать парсер.
            pass


class WordstatClient:
    """
    Клиент Yandex Wordstat API.

    Отвечает только за:
    - HTTP-запросы;
    - управление аккаунтами;
    - лимиты;
    - обработку HTTP 429;
    - переключение аккаунтов;
    - остановку запросов.
    """

    BASE_URL = "https://searchapi.api.cloud.yandex.net/v2/wordstat/topRequests"

    REQUEST_TIMEOUT = 15
    ACCOUNT_BLOCK_SECONDS = 3600

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
        """Закрывает HTTP-сессию."""
        self.session.close()

    # ------------------------------------------------------------------
    # Account state
    # ------------------------------------------------------------------

    def _reset_if_expired(
        self,
        account: AccountState,
    ) -> None:
        """
        Снимает временную блокировку аккаунта,
        если прошёл час с момента последнего сброса.
        """

        now = time.time()

        if now - account.last_reset >= 3600:
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
        """Передаёт UI актуальное состояние текущего аккаунта."""

        if not self.accounts:
            self.stats_callback(
                0,
                0,
                0,
            )
            return

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

    # ------------------------------------------------------------------
    # Account switching
    # ------------------------------------------------------------------

    def _switch_account(self) -> bool:
        """
        Ищет следующий доступный аккаунт.

        Возвращает True, если переключение произошло.
        """

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
        """
        Проверяет все аккаунты и пытается выбрать доступный.
        """

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

    def _all_accounts_blocked(self) -> bool:
        if not self.accounts:
            return True

        for account in self.accounts:
            self._reset_if_expired(account)

            if account.is_available() and self._account_has_requests(account):
                return False

        return True

    # ------------------------------------------------------------------
    # Blocking
    # ------------------------------------------------------------------

    def _block_account(
        self,
        index: int,
        reason: str,
    ) -> None:
        """Временно блокирует аккаунт."""

        if not 0 <= index < len(self.accounts):
            return

        account = self.accounts[index]

        account.is_blocked = True

        account.blocked_until = datetime.now() + timedelta(
            seconds=self.ACCOUNT_BLOCK_SECONDS
        )

        self.block_logger.log_block(
            index,
            account.blocked_until,
        )

        self.log(f"Аккаунт {index + 1} временно заблокирован: {reason}")

    # ------------------------------------------------------------------
    # Waiting
    # ------------------------------------------------------------------

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
        """
        Ожидает разблокировки аккаунта.

        Проверка stop_event выполняется каждую секунду,
        поэтому приложение не зависает на полный час
        при остановке пользователем.
        """

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

            self.log(
                "Все аккаунты временно недоступны. "
                f"Ожидание примерно "
                f"{int(wait_seconds) + 1} сек."
            )

            deadline = time.monotonic() + wait_seconds + 1

            while time.monotonic() < deadline and not stop_event.is_set():
                stop_event.wait(1)

        return False

    # ------------------------------------------------------------------
    # Account preparation
    # ------------------------------------------------------------------

    def _prepare_account(
        self,
        stop_event: threading.Event,
    ) -> AccountState | None:
        """
        Возвращает аккаунт, который можно использовать
        для следующего запроса.
        """

        if not self.accounts:
            self.log("Нет настроенных аккаунтов.")
            return None

        while not stop_event.is_set():
            account = self.accounts[self.current_index]

            self._reset_if_expired(account)

            if account.is_available() and self._account_has_requests(account):
                self._update_stats()
                return account

            if self._switch_account():
                continue

            if self._all_accounts_blocked():
                if not self._wait_for_available_account(stop_event):
                    return None

                continue

            if self._find_available_account():
                continue

            return None

        return None

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------

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
    ) -> requests.Response | None:
        try:
            return self.session.post(
                self.BASE_URL,
                headers=self._build_headers(account),
                json=self._build_payload(
                    phrase,
                    account.config.folder_id,
                ),
                timeout=self.REQUEST_TIMEOUT,
            )

        except requests.RequestException as error:
            self.log(f'Сетевая ошибка при запросе "{phrase}": {error}')

            return None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_count(
        self,
        phrase: str,
        stop_event: threading.Event,
    ) -> int:
        """
        Получает количество показов для фразы.

        Метод не использует рекурсию:
        HTTP 429 обрабатывается циклом.

        Это важно, поскольку при большом количестве
        последовательных блокировок рекурсивная реализация
        могла бы привести к переполнению стека.
        """

        phrase = phrase.strip()

        if not phrase:
            return 0

        while not stop_event.is_set():
            account = self._prepare_account(stop_event)

            if account is None:
                return 0

            delay = self.config.settings.request_delay

            if delay > 0:
                if stop_event.wait(delay):
                    return 0

            response = self._post(
                account,
                phrase,
            )

            if response is None:
                return 0

            account.requests_used += 1

            self._update_stats()

            # ----------------------------------------------------------
            # Success
            # ----------------------------------------------------------

            if response.status_code == 200:
                try:
                    data = response.json()

                except ValueError:
                    self.log("Wordstat API вернул некорректный JSON.")
                    return 0

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
                ):
                    self.log("API вернул некорректное значение totalCount.")
                    return 0

            # ----------------------------------------------------------
            # Rate limit
            # ----------------------------------------------------------

            if response.status_code == 429:
                self._block_account(
                    self.current_index,
                    "получен HTTP 429",
                )

                if self._switch_account():
                    continue

                if self._wait_for_available_account(stop_event):
                    continue

                return 0

            # ----------------------------------------------------------
            # Unauthorized
            # ----------------------------------------------------------

            if response.status_code in (
                401,
                403,
            ):
                self._block_account(
                    self.current_index,
                    (f"HTTP {response.status_code} (ошибка авторизации)"),
                )

                if self._switch_account():
                    continue

                self.log("Нет доступных аккаунтов для продолжения работы.")

                return 0

            # ----------------------------------------------------------
            # Server errors
            # ----------------------------------------------------------

            if 500 <= response.status_code < 600:
                self.log(f"Ошибка сервера Wordstat API: HTTP {response.status_code}")

                return 0

            # ----------------------------------------------------------
            # Other errors
            # ----------------------------------------------------------

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
                f"Ошибка Wordstat API "
                f"HTTP {response.status_code}: "
                f"{error_message[:300]}"
            )

            return 0

        return 0

    def validate_account(
        self,
        api_key: str,
        folder_id: str,
    ) -> tuple[bool, str]:
        """
        Проверяет API Key и Folder ID.

        Возвращает:

            (True, "ok")
            (True, "rate_limited")
            (False, "error message")
        """

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
