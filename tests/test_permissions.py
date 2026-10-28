import sys
import subprocess
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


def test_detectors_say_nothing_off_macos(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert permissions.background_allowed(Path("/no/such/agent.plist")) is None
    assert permissions.notifications_allowed() is None


def test_blocked_permissions_become_warnings(ctl, monkeypatch):
    monkeypatch.setattr(permissions, "background_allowed", lambda agent: False)
    monkeypatch.setattr(permissions, "notifications_allowed", lambda: False)
    ctl.set_start_at_login(True)
    assert ctl.refresh_permissions() == {"background": False, "notifications": False}
    warnings = ctl.permission_warnings()
    assert [w["id"] for w in warnings] == ["background", "notifications"]
    assert "фоне" in warnings[0]["text"] and "Уведомления" in warnings[1]["text"]


def test_allowed_or_silent_permissions_warn_about_nothing(ctl, monkeypatch):
    monkeypatch.setattr(permissions, "background_allowed", lambda agent: True)
    monkeypatch.setattr(permissions, "notifications_allowed", lambda: None)
    ctl.set_start_at_login(True)
    assert ctl.permission_warnings() == []


def test_background_is_only_checked_with_the_login_item_on(ctl, monkeypatch):
    monkeypatch.setattr(permissions, "background_allowed", lambda agent: False)
    monkeypatch.setattr(permissions, "notifications_allowed", lambda: True)
    assert ctl.permission_warnings() == []
    ctl.set_start_at_login(True)
    assert [w["id"] for w in ctl.permission_warnings()] == ["background"]


def test_permission_warnings_reach_the_window(ui, monkeypatch):
    c, client = ui
    monkeypatch.setattr(permissions, "notifications_allowed", lambda: False)
    c.permissions = {}
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
