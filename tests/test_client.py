
import threading
from types import SimpleNamespace

import requests

from wordstat_parser.client import ValidationStatus, WordstatClient
from wordstat_parser.models import AccountConfig, AccountState


class FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


def make_client(accounts):
    config = SimpleNamespace(
        accounts=accounts,
        settings=SimpleNamespace(max_requests_per_hour=100, request_delay=0.0),
    )
    logs = []
    client = WordstatClient(config, logs.append, lambda *args: None)
    return client, logs


def test_http_200_total_count_zero_is_success(monkeypatch):
    account = AccountState(AccountConfig("key", "folder"))
    client, _ = make_client([account])
    responses = iter([FakeResponse(200, {"totalCount": "0"})])
    monkeypatch.setattr(client.session, "post", lambda *args, **kwargs: next(responses))

    assert client.get_count("ничего не найдено", threading.Event()) == 0
    assert account.requests_used == 1


def test_http_400_blocks_account_and_retries_same_request(monkeypatch):
    first = AccountState(AccountConfig("key1", "folder1"))
    second = AccountState(AccountConfig("key2", "folder2"))
    client, _ = make_client([first, second])
    responses = iter([
        FakeResponse(400, {"message": "rate limit"}),
        FakeResponse(200, {"totalCount": "123"}),
    ])
    calls = []

    def post(*args, **kwargs):
        calls.append(kwargs["json"]["phrase"])
        return next(responses)

    monkeypatch.setattr(client.session, "post", post)

    assert client.get_count("тест", threading.Event()) == 123
    assert calls == ["тест", "тест"]
    assert first.is_blocked is True
    assert first.requests_used == 0
    assert second.requests_used == 1


def test_validate_account_timeout_is_unreachable(monkeypatch):
    account = AccountState(AccountConfig("key", "folder"))
    client, _ = make_client([account])
    monkeypatch.setattr(client, "_reset_session", lambda: None)
    monkeypatch.setattr("wordstat_parser.client.time.sleep", lambda *_: None)

    def post(*args, **kwargs):
        raise requests.exceptions.ReadTimeout(
            "Read timed out. (read timeout=30)"
        )

    monkeypatch.setattr(client.session, "post", post)

    status, message = client.validate_account("key", "folder")

    assert status is ValidationStatus.UNREACHABLE
    assert "Сетевая ошибка" in message


def test_validate_account_retries_after_timeout(monkeypatch):
    account = AccountState(AccountConfig("key", "folder"))
    client, _ = make_client([account])
    monkeypatch.setattr(client, "_reset_session", lambda: None)
    monkeypatch.setattr("wordstat_parser.client.time.sleep", lambda *_: None)

    calls = {"n": 0}

    def post(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise requests.exceptions.ReadTimeout("timed out")
        return FakeResponse(200, {"totalCount": 1})

    monkeypatch.setattr(client.session, "post", post)

    status, message = client.validate_account("key", "folder")

    assert status is ValidationStatus.OK
    assert message == "ok"
    assert calls["n"] == 2
