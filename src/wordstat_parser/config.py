from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from .models import AccountConfig, AccountState, AppSettings


class ConfigManager:
    def __init__(self, config_path: str | Path = "config.json") -> None:
        self.config_path = Path(config_path)
        self.accounts: list[AccountState] = []
        self.settings = AppSettings()

        self.load()

    def load(self) -> None:
        if not self.config_path.exists():
            self.save()
            return

        with self.config_path.open("r", encoding="utf-8") as file:
            data = json.load(file)

        settings_data = data.get("settings", {})

        allowed_settings = AppSettings.__annotations__.keys()

        self.settings = AppSettings(
            **{
                key: value
                for key, value in settings_data.items()
                if key in allowed_settings
            }
        )

        self.accounts.clear()

        for account in data.get("accounts", []):
            api_key = account.get("api_key")
            folder_id = account.get("folder_id")

            if not api_key or not folder_id:
                continue

            self.accounts.append(
                AccountState(
                    config=AccountConfig(
                        api_key=api_key,
                        folder_id=folder_id,
                    )
                )
            )

    def save(self) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "settings": asdict(self.settings),
            "accounts": [
                {
                    "api_key": account.config.api_key,
                    "folder_id": account.config.folder_id,
                }
                for account in self.accounts
            ],
        }

        with self.config_path.open("w", encoding="utf-8") as file:
            json.dump(
                data,
                file,
                ensure_ascii=False,
                indent=2,
            )

    def add_account(
        self,
        api_key: str,
        folder_id: str,
    ) -> None:
        self.accounts.append(
            AccountState(
                config=AccountConfig(
                    api_key=api_key,
                    folder_id=folder_id,
                )
            )
        )

        self.save()

    def remove_account(self, index: int) -> bool:
        if not 0 <= index < len(self.accounts):
            return False

        self.accounts.pop(index)
        self.save()

        return True