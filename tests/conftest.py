import os

# tests check the Russian wording; the app itself follows the system language
os.environ["IMNOTAROBOT_LANG"] = "ru"

import pytest


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Keeps tests away from the real settings, state file and Keychain."""
    monkeypatch.setenv("CAPTCHA_STACK_HOME", str(tmp_path))
    import imnotarobot.config as config

    monkeypatch.setattr(config, "APP_DIR", tmp_path)
    monkeypatch.setattr(config, "get_secret", lambda account: "")


@pytest.fixture(autouse=True)
def no_local_resellers(monkeypatch):
    import imnotarobot.discovery as discovery

    monkeypatch.setattr(discovery, "local_reseller_urls", lambda: [])


@pytest.fixture(autouse=True)
def no_real_keychain(monkeypatch):
    import imnotarobot.config as config

    monkeypatch.setattr(config, "legacy_secret_exists", lambda account: False)


@pytest.fixture(autouse=True)
def no_real_permissions(monkeypatch):
    """The permission checks would ask this very Mac about its login item and notifications."""
    import imnotarobot.permissions as permissions

    monkeypatch.setattr(permissions, "background_allowed", lambda agent: None)
    monkeypatch.setattr(permissions, "ask_notifications", lambda store: store(None))
