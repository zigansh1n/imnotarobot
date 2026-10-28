import sys
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import imnotarobot.config as config
import imnotarobot.control as control
import imnotarobot.permissions as permissions
from imnotarobot.config import Config
from imnotarobot.control import Controller
from imnotarobot.server import create_app
from imnotarobot.ui import add_ui_routes


@pytest.fixture
def ctl(tmp_path, monkeypatch):
    secrets: dict[str, str] = {}
    monkeypatch.setattr(config, "get_secret", lambda a: secrets.get(a, ""))
    monkeypatch.setattr(config, "set_secret", lambda a, v: secrets.__setitem__(a, v) if v else secrets.pop(a, None))
    monkeypatch.setattr(control, "LAUNCH_AGENT", tmp_path / "LaunchAgents" / "app.captcha-stack.plist")
    return Controller(Config.load())


@pytest.fixture
def ui(tmp_path, monkeypatch):
    secrets: dict[str, str] = {}
    monkeypatch.setattr(config, "set_secret", lambda a, v: secrets.__setitem__(a, v) if v else secrets.pop(a, None))
    monkeypatch.setattr(control, "LAUNCH_AGENT", tmp_path / "LaunchAgents" / "app.captcha-stack.plist")
    c = Controller(Config.load())
    app = create_app(service=c.service)
    add_ui_routes(app, c)
    with TestClient(app) as client:
        client.headers["X-UI-Token"] = c.ui_token
        yield c, client


def land(ctl, **expect):
    """Waits until the worker thread has stored the given answers (the app itself never waits)."""
    for _ in range(300):
        if all(ctl.permissions.get(k) is v for k, v in expect.items()):
            return True
        time.sleep(0.01)
    return False


def test_detectors_say_nothing_off_macos(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    got = []
    permissions.ask_notifications(got.append)
    assert got == [None]
    assert permissions.background_allowed(Path("/no/such/agent.plist")) is None


def test_blocked_permissions_become_warnings(ctl, monkeypatch):
    monkeypatch.setattr(permissions, "background_allowed", lambda agent: False)
    monkeypatch.setattr(permissions, "ask_notifications", lambda store: store(False))
    ctl.set_start_at_login(True)
    ctl.refresh_permissions()
    assert land(ctl, background=False, notifications=False)
    assert ctl.permissions == {"background": False, "notifications": False}
    warnings = ctl.permission_warnings()
    assert [w["id"] for w in warnings] == ["background", "notifications"]
    assert "фоне" in warnings[0]["text"] and "Уведомления" in warnings[1]["text"]


def test_allowed_or_silent_permissions_warn_about_nothing(ctl, monkeypatch):
    monkeypatch.setattr(permissions, "background_allowed", lambda agent: True)
    monkeypatch.setattr(permissions, "ask_notifications", lambda store: store(None))
    ctl.set_start_at_login(True)
    ctl.refresh_permissions()
    assert land(ctl, background=True, notifications=None)
    assert ctl.permission_warnings() == []


def test_background_is_only_checked_with_the_login_item_on(ctl, monkeypatch):
    monkeypatch.setattr(permissions, "background_allowed", lambda agent: False)
    monkeypatch.setattr(permissions, "ask_notifications", lambda store: store(True))
    ctl.refresh_permissions()
    assert land(ctl, background=False)
    assert ctl.permission_warnings() == []  # no login item yet, so nothing to warn about
    ctl.set_start_at_login(True)
    assert [w["id"] for w in ctl.permission_warnings()] == ["background"]


def test_refreshing_never_blocks_the_caller(ctl, monkeypatch):
    monkeypatch.setattr(permissions, "background_allowed", lambda agent: None)

    def slow(store):
        time.sleep(0.3)
        store(False)

    monkeypatch.setattr(permissions, "ask_notifications", slow)
    started = time.monotonic()
    ctl.refresh_permissions()
    assert time.monotonic() - started < 0.25  # the ask happens in a worker thread, not here
    assert land(ctl, notifications=False) and ctl.permissions["notifications"] is False


def test_an_unanswered_ask_keeps_the_last_known_values(ctl, monkeypatch):
    monkeypatch.setattr(permissions, "background_allowed", lambda agent: False)
    monkeypatch.setattr(permissions, "ask_notifications", lambda store: store(False))
    ctl.refresh_permissions()
    assert land(ctl, background=False, notifications=False)
    # a later ask that gets no answer must not erase what was known
    monkeypatch.setattr(permissions, "background_allowed", lambda agent: None)
    monkeypatch.setattr(permissions, "ask_notifications", lambda store: None)
    ctl.refresh_permissions()
    assert land(ctl, background=None)
    assert ctl.permissions == {"background": None, "notifications": False}


def test_the_window_reads_only_what_has_landed(ui, monkeypatch):
    c, client = ui

    def refuses(name):
        def fail(*args):
            raise AssertionError(f"{name} was asked")

        return fail

    # state() must read the cache only: with the detectors made to refuse, it still answers
    monkeypatch.setattr(permissions, "background_allowed", refuses("background"))
    monkeypatch.setattr(permissions, "ask_notifications", refuses("notifications"))
    c.permissions = {}
    assert client.get("/api/ui/state").json()["permissions"] == []
    c.permissions = {"notifications": False}
    assert [w["id"] for w in client.get("/api/ui/state").json()["permissions"]] == ["notifications"]


def test_the_window_can_open_the_system_settings_panes(ui, monkeypatch):
    _, client = ui
    opened = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: opened.append(cmd))
    for pane in ("x-apple.systempreferences:com.apple.LoginItems-Settings.extension",
                 "x-apple.systempreferences:com.apple.Notifications-Settings.extension"):
        assert client.post("/api/ui/open-url", json={"url": pane}).json()["ok"]
    assert opened == [["open", "x-apple.systempreferences:com.apple.LoginItems-Settings.extension"],
                      ["open", "x-apple.systempreferences:com.apple.Notifications-Settings.extension"]]
    assert client.post("/api/ui/open-url", json={"url": "file:///etc/passwd"}).status_code == 400
