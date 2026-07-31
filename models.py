import json
import os
import time
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional
from datetime import datetime


@dataclass
class AccountConfig:
    api_key: str
    folder_id: str


@dataclass
class AccountState:
    config: AccountConfig
    requests_used: int = 0
    last_reset: float = field(default_factory=time.time)
    is_blocked: bool = False
    blocked_until: Optional[datetime] = None


@dataclass
class AppSettings:
    max_requests_per_hour: int = 100
    request_delay: float = 0.1
    min_normal_count: int = 500
    min_quoted_count: int = 30


class ConfigManager:
    def __init__(self, config_path: str = "config.json"):
        self.config_path = config_path
        self.accounts: List[AccountState] = []
        self.settings = AppSettings()
        self._load()

    def _load(self):
        if not os.path.exists(self.config_path):
            self._save_default()
            return

        with open(self.config_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        settings_data = data.get('settings', {})
        self.settings = AppSettings(**{k: v for k, v in settings_data.items() if k in AppSettings.__annotations__})

        self.accounts = []
        for acc_data in data.get('accounts', []):
            if 'api_key' in acc_data and 'folder_id' in acc_data:
                config = AccountConfig(api_key=acc_data['api_key'], folder_id=acc_data['folder_id'])
                self.accounts.append(AccountState(config=config))

    def _save_default(self):
        data = {"settings": asdict(self.settings), "accounts": []}
        with open(self.config_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def save(self):
        data = {
            "settings": asdict(self.settings),
            "accounts": [{"api_key": acc.config.api_key, "folder_id": acc.config.folder_id} for acc in self.accounts]
        }
        with open(self.config_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def add_account(self, api_key: str, folder_id: str):
        self.accounts.append(AccountState(config=AccountConfig(api_key=api_key, folder_id=folder_id)))
        self.save()

    def remove_account(self, index: int):
        if 0 <= index < len(self.accounts):
            self.accounts.pop(index)
            self.save()


class BlockLogger:
    def __init__(self, log_file: str = "block_history.json"):
        self.log_file = log_file

    def log_block(self, account_index: int, blocked_until: datetime):
        history = self._load()
        history.append({
            "account_index": account_index,
            "blocked_at": datetime.now().isoformat(),
            "unblocks_at": blocked_until.isoformat()
        })
        history = history[-100:]
        with open(self.log_file, 'w', encoding='utf-8') as f:
            json.dump(history, f, ensure_ascii=False, indent=2)

    def _load(self) -> list:
        if not os.path.exists(self.log_file):
            return []
        try:
            with open(self.log_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return []