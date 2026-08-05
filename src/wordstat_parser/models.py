from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime


@dataclass(slots=True)
class AccountConfig:
    api_key: str
    folder_id: str


@dataclass(slots=True)
class AccountState:
    config: AccountConfig
    requests_used: int = 0
    last_reset: float = field(default_factory=time.time)
    is_blocked: bool = False
    blocked_until: datetime | None = None

    def reset(self) -> None:
        self.requests_used = 0
        self.last_reset = time.time()
        self.is_blocked = False
        self.blocked_until = None

    def is_available(self) -> bool:
        if not self.is_blocked:
            return True

        if self.blocked_until is None:
            return False

        return datetime.now() >= self.blocked_until


@dataclass(slots=True)
class AppSettings:
    max_requests_per_hour: int = 100
    request_delay: float = 0.1
    min_normal_count: int = 500
    min_quoted_count: int = 30


@dataclass(slots=True)
class ParseResult:
    query: str
    normal_count: int
    quoted_count: int

    @property
    def is_suitable(self) -> bool:
        return self.normal_count > 0 and self.quoted_count > 0
