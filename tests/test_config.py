from pathlib import Path

from wordstat_parser.config import ConfigManager


def test_config_creation(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"

    config = ConfigManager(config_path)

    assert config.accounts == []
    assert config.settings.max_requests_per_hour == 100
    assert config_path.exists()


def test_add_and_remove_account(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"

    config = ConfigManager(config_path)

    config.add_account(
        "test-api-key",
        "test-folder-id",
    )

    assert len(config.accounts) == 1
    assert (
        config.accounts[0].config.api_key
        == "test-api-key"
    )

    assert config.remove_account(0) is True
    assert config.accounts == []