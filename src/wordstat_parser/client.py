from __future__ import annotations

import json
import random
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path

import requests

from .config import ConfigManager
from .models import AccountState

LogCallback = Callable[[str], None]
StatsCallback = Callable[[int, int, int], None]


class ValidationStatus(StrEnum):
    OK = "ok"
    RATE_LIMITED = "rate_limited"
    UNREACHABLE = "unreachable"
    INVALID = "invalid"


class PhraseRejectedError(Exception):
    """Фраза стабильно отклоняется API всеми аккаунтами (не троттлинг)."""


class ErrorLogger:
    """Сохраняет полный текст ответов API с ошибками для диагностики."""

    def __init__(self, log_file: str | Path = "errors_log.jsonl") -> None:
        self.log_file = Path(log_file)

    def log(
        self,
        *,
        phrase: str,
        account_index: int,
        status_code: int,
        message: str,
    ) -> None:
        entry = {
            "at": datetime.now().isoformat(),
            "phrase": phrase,
            "account_index": account_index,
            "status_code": status_code,
            "message": message,
        }

        try:
            with self.log_file.open("a", encoding="utf-8") as file:
                file.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass


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
                data = json.loads(self.log_file.read_text(encoding="utf-8"))
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

    CONNECT_TIMEOUT = 10.0
    READ_TIMEOUT = 30.0
    REQUEST_TIMEOUT = (CONNECT_TIMEOUT, READ_TIMEOUT)
    VALIDATION_TIMEOUT = (CONNECT_TIMEOUT, READ_TIMEOUT)
    VALIDATION_ATTEMPTS = 2

    # Настоящая часовая блокировка — только для авторизационных ошибок (401/403),
    # это реальный признак нерабочего ключа/квоты, а не кратковременного троттлинга.
    ACCOUNT_BLOCK_SECONDS = 3600

    # 400/429 чаще всего — кратковременный троттлинг по IP/скорости, а не почасовая
    # квота, поэтому блокируем аккаунт ненадолго и пробуем снова.
    THROTTLE_BLOCK_SECONDS = 90

    # Если фраза получает 400 от ВСЕХ аккаунтов столько кругов подряд — считаем,
    # что дело не в троттлинге, а в самой фразе, и пропускаем именно её.
    MAX_PHRASE_400_ROUNDS = 2

    # Официальный лимит Wordstat API для поля phrase.
    MAX_PHRASE_LENGTH = 400

    RETRY_BASE_DELAY = 2.0
    RETRY_MAX_DELAY = 60.0

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
        self.error_logger = ErrorLogger()

        self.session = requests.Session()
        self._lock = threading.Lock()
        self._last_request_at = 0.0

    @property
    def accounts(self) -> list[AccountState]:
        return self.config.accounts

    def close(self) -> None:
        self.session.close()

    def _reset_session(self) -> None:
        old_session = self.session
        self.session = requests.Session()

        try:
            old_session.close()
        except Exception:
            pass

    def _reset_if_expired(
        self,
        account: AccountState,
    ) -> None:
        if time.time() - account.last_reset >= 3600:
            account.reset()

    def _account_has_requests(
        self,
        account: AccountState,
    ) -> bool:
        self._reset_if_expired(account)
        return account.requests_used < self.config.settings.max_requests_per_hour

    def _is_account_available(
        self,
        account: AccountState,
    ) -> bool:
        self._reset_if_expired(account)

        if not account.is_available():
            return False

        return self._account_has_requests(account)

    def _update_stats(self) -> None:
        if not self.accounts:
            self.stats_callback(
                0,
                0,
                0,
            )
            return

        self.current_index = max(
            0,
            min(
                self.current_index,
                len(self.accounts) - 1,
            ),
        )

        account = self.accounts[self.current_index]
        self._reset_if_expired(account)

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

            self.current_index = index
            self._update_stats()
            return True

        return False

    def _block_account(
        self,
        index: int,
        reason: str,
        block_seconds: int | None = None,
    ) -> None:
        if not 0 <= index < len(self.accounts):
            return

        account = self.accounts[index]
        account.is_blocked = True

        seconds = (
            self.ACCOUNT_BLOCK_SECONDS
            if block_seconds is None
            else block_seconds
        )

        account.blocked_until = datetime.now() + timedelta(seconds=seconds)

        self.block_logger.log_block(
            index,
            account.blocked_until,
        )

        self.log(f"Аккаунт {index + 1} временно заблокирован: {reason}")
        self._update_stats()

    def _get_account_available_time(
        self,
        account: AccountState,
    ) -> datetime | None:
        self._reset_if_expired(account)

        if self._is_account_available(account):
            return datetime.now()

        if account.blocked_until is not None:
            return account.blocked_until

        if account.requests_used >= self.config.settings.max_requests_per_hour:
            return datetime.fromtimestamp(account.last_reset + 3600)

        return None

    def _get_earliest_available_time(
        self,
    ) -> datetime | None:
        available_times: list[datetime] = []

        for account in self.accounts:
            available_time = self._get_account_available_time(account)
            if available_time is not None:
                available_times.append(available_time)

        if not available_times:
            return None

        return min(available_times)

    def _sleep_until(
        self,
        target_time: datetime,
        stop_event: threading.Event,
    ) -> bool:
        while not stop_event.is_set():
            remaining = (target_time - datetime.now()).total_seconds()

            if remaining <= 0:
                return True

            if stop_event.wait(min(1.0, remaining)):
                return False

        return False

    def _wait_for_available_account(
        self,
        stop_event: threading.Event,
    ) -> bool:
        while not stop_event.is_set():
            if self._find_available_account():
                return True

            available_time = self._get_earliest_available_time()

            if available_time is None:
                self.log("Нет доступного времени сброса лимита.")
                return False

            wait_seconds = max(
                0,
                (available_time - datetime.now()).total_seconds(),
            )

            if wait_seconds <= 0:
                for account in self.accounts:
                    self._reset_if_expired(account)
                continue

            minutes = int(wait_seconds // 60)
            seconds = int(wait_seconds % 60)

            self.log(
                "Все аккаунты исчерпали "
                "часовой лимит. "
                f"Ожидание сброса: "
                f"{minutes} мин. "
                f"{seconds:02d} сек."
            )

            self.log("API-запросы во время ожидания НЕ выполняются.")

            if not self._sleep_until(
                available_time,
                stop_event,
            ):
                return False

            for account in self.accounts:
                self._reset_if_expired(account)

            self.log("Часовой лимит сброшен. Продолжаем обработку.")

        return False

    def _prepare_account(
        self,
        stop_event: threading.Event,
    ) -> AccountState | None:
        if not self.accounts:
            self.log("Нет настроенных аккаунтов.")
            return None

        while not stop_event.is_set():
            for index, account in enumerate(self.accounts):
                self._reset_if_expired(account)

                if not self._is_account_available(account):
                    continue

                self.current_index = index
                self._update_stats()

                return account

            if not self._wait_for_available_account(stop_event):
                return None

        return None

    @staticmethod
    def _build_auth_header(api_key: str) -> str:
        api_key = api_key.strip()

        if not api_key:
            raise ValueError("API Key не может быть пустым.")

        return f"Api-Key {api_key}"

    def _build_headers(
        self,
        account: AccountState,
    ) -> dict[str, str]:
        return {
            "Authorization": self._build_auth_header(account.config.api_key),
            "Content-Type": "application/json",
        }

    @staticmethod
    def _build_payload(
        phrase: str,
        folder_id: str,
        num_phrases: str = "10",
    ) -> dict:
        return {
            "folderId": folder_id,
            "phrase": phrase,
            "numPhrases": num_phrases,
            "regions": ["225"],
            "devices": ["DEVICE_ALL"],
        }

    def _wait_request_interval(
        self,
        stop_event: threading.Event,
    ) -> bool:
        interval = max(0.1, float(self.config.settings.request_delay))

        with self._lock:
            now = time.monotonic()
            wait_for = interval - (now - self._last_request_at)

        if wait_for > 0 and stop_event.wait(wait_for):
            return False

        with self._lock:
            self._last_request_at = time.monotonic()

        return not stop_event.is_set()

    def _post(
        self,
        account: AccountState,
        phrase: str,
        stop_event: threading.Event,
    ) -> tuple[
        requests.Response | None,
        Exception | None,
    ]:
        if not self._wait_request_interval(stop_event):
            return None, None

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

            return response, None

        except requests.RequestException as error:
            self._reset_session()
            return None, error

    @classmethod
    def _calculate_backoff(
        cls,
        attempt: int,
    ) -> float:
        delay = min(
            cls.RETRY_BASE_DELAY
            * (
                2
                ** max(
                    0,
                    attempt - 1,
                )
            ),
            cls.RETRY_MAX_DELAY,
        )

        jitter = random.uniform(
            0,
            min(
                1.0,
                delay * 0.25,
            ),
        )

        return min(
            cls.RETRY_MAX_DELAY,
            delay + jitter,
        )

    @staticmethod
    def _get_error_message(
        response: requests.Response,
    ) -> str:
        try:
            data = response.json()

            if isinstance(data, dict):
                message = data.get(
                    "message",
                    response.text,
                )

                return str(message)

        except ValueError:
            pass

        return response.text

    def get_count(
        self,
        phrase: str,
        stop_event: threading.Event,
    ) -> int | None:
        phrase = " ".join(phrase.strip().split())

        if not phrase:
            return 0

        if len(phrase) > self.MAX_PHRASE_LENGTH:
            raise PhraseRejectedError(phrase)

        network_retry = 0
        server_retry = 0
        api_retry = 0

        phrase_400_accounts: set[int] = set()
        phrase_400_rounds = 0

        auth_error_accounts: set[int] = set()

        while not stop_event.is_set():
            account = self._prepare_account(stop_event)

            if account is None:
                return None

            response, network_error = self._post(
                account,
                phrase,
                stop_event,
            )

            if network_error is not None:
                network_retry += 1
                retry_delay = self._calculate_backoff(network_retry)

                self.log(
                    "Сетевая ошибка при "
                    f'запросе "{phrase}": '
                    f"{network_error}. "
                    f"Повтор через "
                    f"{retry_delay:.1f} сек. "
                    f"(попытка "
                    f"{network_retry})"
                )

                if stop_event.wait(retry_delay):
                    return None

                continue

            if response is None:
                network_retry += 1
                retry_delay = self._calculate_backoff(network_retry)

                self.log(
                    f"Неизвестная ошибка "
                    f'при запросе "{phrase}". '
                    f"Повтор через "
                    f"{retry_delay:.1f} сек."
                )

                if stop_event.wait(retry_delay):
                    return None

                continue

            network_retry = 0

            if response.status_code == 200:
                try:
                    data = response.json()
                except ValueError as error:
                    api_retry += 1
                    retry_delay = self._calculate_backoff(api_retry)

                    self.log(
                        "Wordstat API вернул "
                        "некорректный JSON: "
                        f"{error}. "
                        f"Повтор через "
                        f"{retry_delay:.1f} сек. "
                        f"(попытка "
                        f"{api_retry})"
                    )

                    if stop_event.wait(retry_delay):
                        return None

                    continue

                try:
                    count = int(
                        data.get(
                            "totalCount",
                            0,
                        )
                    )
                except (
                    TypeError,
                    ValueError,
                ) as error:
                    api_retry += 1
                    retry_delay = self._calculate_backoff(api_retry)

                    self.log(
                        "API вернул "
                        "некорректное "
                        f"totalCount: {error}. "
                        f"Повтор через "
                        f"{retry_delay:.1f} сек. "
                        f"(попытка "
                        f"{api_retry})"
                    )

                    if stop_event.wait(retry_delay):
                        return None

                    continue

                account.requests_used += 1
                self._update_stats()

                return count

            if response.status_code == 400:
                error_message = self._get_error_message(response).strip()
                safe_error_message = error_message[:300] or "пустой ответ"

                self.error_logger.log(
                    phrase=phrase,
                    account_index=self.current_index,
                    status_code=400,
                    message=error_message or "пустой ответ",
                )

                phrase_400_accounts.add(self.current_index)

                self._block_account(
                    self.current_index,
                    f"HTTP 400: {safe_error_message}",
                    block_seconds=self.THROTTLE_BLOCK_SECONDS,
                )

                self.log(
                    f"Аккаунт "
                    f"{self.current_index + 1} "
                    "получил HTTP 400. "
                    f"Ответ API: {safe_error_message}. "
                    f"Короткая пауза ({self.THROTTLE_BLOCK_SECONDS} сек), "
                    "полный текст сохранён в errors_log.jsonl. "
                    "Повторный API-запрос "
                    "НЕ выполняется."
                )

                if self.accounts and phrase_400_accounts >= {
                    i for i in range(len(self.accounts))
                }:
                    phrase_400_rounds += 1
                    phrase_400_accounts.clear()

                    if phrase_400_rounds >= self.MAX_PHRASE_400_ROUNDS:
                        self.log(
                            f'Фраза "{phrase}" получила HTTP 400 от ВСЕХ аккаунтов '
                            f"{phrase_400_rounds} раза подряд. Похоже, дело не в "
                            "троттлинге, а в самой фразе (см. errors_log.jsonl). "
                            "Пропускаем эту фразу и продолжаем со следующей."
                        )

                        raise PhraseRejectedError(phrase)

                if self._switch_account():
                    network_retry = 0
                    server_retry = 0
                    api_retry = 0
                    continue

                self.log("Все аккаунты временно на паузе. Ожидание.")

                if self._wait_for_available_account(stop_event):
                    network_retry = 0
                    server_retry = 0
                    api_retry = 0
                    continue

                return None

            if response.status_code == 429:
                error_message = self._get_error_message(response).strip()

                self.error_logger.log(
                    phrase=phrase,
                    account_index=self.current_index,
                    status_code=429,
                    message=error_message or "пустой ответ",
                )

                self._block_account(
                    self.current_index,
                    "HTTP 429: слишком много запросов",
                    block_seconds=self.THROTTLE_BLOCK_SECONDS,
                )

                self.log(
                    f"Аккаунт "
                    f"{self.current_index + 1} "
                    "получил HTTP 429. "
                    f"Короткая пауза ({self.THROTTLE_BLOCK_SECONDS} сек). "
                    "Повторный запрос "
                    "на этом аккаунте "
                    "не выполняется."
                )

                if self._switch_account():
                    network_retry = 0
                    server_retry = 0
                    api_retry = 0
                    continue

                self.log("Все аккаунты временно недоступны. Ожидание сброса.")

                if self._wait_for_available_account(stop_event):
                    network_retry = 0
                    server_retry = 0
                    api_retry = 0
                    continue

                return None

            if response.status_code in (
                401,
                403,
            ):
                error_message = self._get_error_message(response).strip()
                safe_error_message = error_message[:300] or "пустой ответ"

                self.error_logger.log(
                    phrase=phrase,
                    account_index=self.current_index,
                    status_code=response.status_code,
                    message=error_message or "пустой ответ",
                )

                self._block_account(
                    self.current_index,
                    f"HTTP {response.status_code}: ошибка авторизации: {safe_error_message}",
                )

                self.log(
                    f"Аккаунт "
                    f"{self.current_index + 1} "
                    f"получил HTTP "
                    f"{response.status_code}. "
                    "Переключение "
                    "аккаунта."
                )

                auth_error_accounts.add(self.current_index)

                if self.accounts and auth_error_accounts >= {
                    i for i in range(len(self.accounts))
                }:
                    self.log(
                        "Все аккаунты получили ошибку авторизации. "
                        "Проверьте API-Key, срок действия ключа, "
                        "Folder ID и права сервисного аккаунта."
                    )
                    return None

                if self._switch_account():
                    network_retry = 0
                    server_retry = 0
                    api_retry = 0
                    continue

                if self._wait_for_available_account(stop_event):
                    network_retry = 0
                    server_retry = 0
                    api_retry = 0
                    continue

                return None

            if 500 <= response.status_code < 600:
                server_retry += 1
                retry_delay = self._calculate_backoff(server_retry)

                self.log(
                    "Ошибка сервера "
                    "Wordstat API: "
                    f"HTTP "
                    f"{response.status_code}. "
                    f"Повтор через "
                    f"{retry_delay:.1f} сек. "
                    f"(попытка "
                    f"{server_retry})"
                )

                if stop_event.wait(retry_delay):
                    return None

                continue

            error_message = self._get_error_message(response)

            api_retry += 1
            retry_delay = self._calculate_backoff(api_retry)

            self.log(
                "Ошибка Wordstat API: "
                f"HTTP "
                f"{response.status_code}: "
                f"{error_message[:300]}. "
                f"Повтор через "
                f"{retry_delay:.1f} сек. "
                f"(попытка "
                f"{api_retry})"
            )

            if stop_event.wait(retry_delay):
                return None

        return None

    def validate_account(
        self,
        api_key: str,
        folder_id: str,
        stop_event: threading.Event | None = None,
        attempts: int | None = None,
    ) -> tuple[ValidationStatus, str]:
        api_key = api_key.strip()
        folder_id = folder_id.strip()

        if not api_key:
            return (
                ValidationStatus.INVALID,
                "API Key не указан.",
            )

        if not folder_id:
            return (
                ValidationStatus.INVALID,
                "Folder ID не указан.",
            )

        try:
            headers = {
                "Authorization": self._build_auth_header(api_key),
                "Content-Type": "application/json",
            }
        except ValueError as error:
            return (
                ValidationStatus.INVALID,
                str(error),
            )

        payload = self._build_payload(
            phrase="тест",
            folder_id=folder_id,
            num_phrases="1",
        )

        last_error: Exception | None = None
        response: requests.Response | None = None
        max_attempts = (
            self.VALIDATION_ATTEMPTS if attempts is None else max(1, attempts)
        )

        for attempt in range(1, max_attempts + 1):
            if stop_event is not None and stop_event.is_set():
                return (
                    ValidationStatus.INVALID,
                    "Проверка прервана.",
                )

            try:
                response = self.session.post(
                    self.BASE_URL,
                    headers=headers,
                    json=payload,
                    timeout=self.VALIDATION_TIMEOUT,
                )
                break
            except requests.RequestException as error:
                last_error = error
                self._reset_session()

                if attempt >= max_attempts:
                    return (
                        ValidationStatus.UNREACHABLE,
                        f"Сетевая ошибка: {error}",
                    )

                delay = min(2.0 * attempt, 5.0)

                if stop_event is not None:
                    if stop_event.wait(delay):
                        return (
                            ValidationStatus.INVALID,
                            "Проверка прервана.",
                        )
                else:
                    time.sleep(delay)

        if response is None:
            return (
                ValidationStatus.UNREACHABLE,
                f"Сетевая ошибка: {last_error}",
            )

        if response.status_code == 200:
            return ValidationStatus.OK, "ok"

        if response.status_code == 429:
            return ValidationStatus.RATE_LIMITED, "rate_limited"

        message = self._get_error_message(response).strip()

        if not message:
            message = "Неизвестная ошибка API."

        if 500 <= response.status_code < 600:
            return (
                ValidationStatus.UNREACHABLE,
                f"HTTP {response.status_code}: {message[:300]}",
            )

        return (
            ValidationStatus.INVALID,
            f"HTTP {response.status_code}: {message[:300]}",
        )
