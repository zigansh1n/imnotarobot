import time

import pytest
from fastapi.testclient import TestClient

import imnotarobot.config as config
import imnotarobot.control as control
from imnotarobot.config import Config
from imnotarobot.control import Controller
from imnotarobot.server import create_app
from imnotarobot.ui import add_ui_routes
from helpers import text_image, wait_health


@pytest.fixture
def ui(tmp_path, monkeypatch):
    secrets: dict[str, str] = {}
    monkeypatch.setattr(config, "get_secret", lambda a: secrets.get(a, ""))
    monkeypatch.setattr(config, "set_secret", lambda a, v: secrets.__setitem__(a, v) if v else secrets.pop(a, None))
    monkeypatch.setattr(control, "LAUNCH_AGENT", tmp_path / "agent.plist")
    c = Controller(Config.load())
    app = create_app(service=c.service)
    add_ui_routes(app, c)
    with TestClient(app) as client:
        client.headers["X-UI-Token"] = c.ui_token
        yield c, client


def fake_provider(monkeypatch, status=200, message='{"text": "abc"}'):
    """The background model check talks to this instead of the network."""
    import httpx

    real_run = control.bench.run

    async def run_with_fake_model(specs, cases, votes=1, **kw):
        def handler(request):
            body = {"choices": [{"message": {"content": message}}]} if status == 200 else {"error": {"message": message}}
            return httpx.Response(status, json=body)
        return await real_run(specs, cases, votes=votes, transport=httpx.MockTransport(handler), **kw)

    monkeypatch.setattr(control.bench, "run", run_with_fake_model)


def test_api_refuses_requests_without_the_window_token(ui):
    c, client = ui
    assert client.get("/api/ui/state", headers={"X-UI-Token": "wrong"}).status_code == 403
    assert client.post("/api/ui/pause", json={"paused": True}, headers={"X-UI-Token": ""}).status_code == 403
    assert not c.service.paused
    assert client.get("/ui").status_code == 200


def test_state_never_contains_secrets(ui, monkeypatch):
    c, client = ui
    from imnotarobot.discovery import Detected, ModelInfo

    fake_provider(monkeypatch)
    monkeypatch.setattr(control, "detect_sync", lambda k, b="", progress=None: Detected("custom", "https://reseller.example/v1",
                                                                          [ModelInfo("claude-x", True)]))
    r = client.post("/api/ui/add-key", json={"secret": "sk-reseller-supersecretvalue"}).json()
    assert r["ok"] and "посредник reseller.example" in r["message"] and "моделей с картинками: 1" in r["message"]
    assert "supersecretvalue" not in client.get("/api/ui/state").text
    r = client.post("/api/ui/toggle-model", json={"key": "reseller.example", "model": "claude-x"}).json()
    assert r["message"] == "claude-x: включена."
    wait_health(c, "reseller.example/claude-x")
    layer = next(x for x in r["state"]["layers"] if x["id"] == "vlm:reseller.example/claude-x")
    assert layer["enabled"] and layer["ready"]
    assert "supersecretvalue" not in client.get("/api/ui/state").text


def test_bad_key_explains_why(ui, monkeypatch):
    _, client = ui
    from imnotarobot.discovery import DetectError

    def nobody(key, base="", progress=None):
        raise DetectError("ни один известный поставщик не принял этот ключ")

    monkeypatch.setattr(control, "detect_sync", nobody)
    r = client.post("/api/ui/add-key", json={"secret": "sk-nothing-at-all"})
    assert r.status_code == 400 and "не принял" in r.json()["error"]


def test_saving_a_human_key_checks_it_and_reports_balance_in_roubles(ui, monkeypatch):
    c, client = ui
    from imnotarobot.layers.human import HumanLayer

    async def balance(self, index=0):
        return 200.0

    monkeypatch.setattr(HumanLayer, "balance", balance)
    r = client.post("/api/ui/human-key", json={"key": "abcd1234efgh"}).json()
    assert r["ok"] and r["message"] == "Ключ работает, баланс 200.00 ₽."
    h = r["state"]["human"]
    assert (h["currency"], h["hint"], h["hasKey"]) == ("₽", "abcd…efgh", True)
    assert h["status"] == {"ok": True, "balance": 200.0, "provider": "rucaptcha", "currency": "₽", "rate": 1.0}
    assert c.human_line() == "Люди (RuCaptcha): ✓ баланс 200.00 ₽"


def test_low_balance_is_flagged(ui, monkeypatch):
    c, client = ui
    from imnotarobot.layers.human import HumanLayer

    async def balance(self, index=0):
        return 12.0

    monkeypatch.setattr(HumanLayer, "balance", balance)
    client.post("/api/ui/human-key", json={"key": "abcd1234efgh"})
    assert c.human_line().endswith("пора пополнить")


def test_rejected_human_key_is_reported(ui, monkeypatch):
    c, client = ui
    from imnotarobot.layers.human import HumanLayer

    async def balance(self, index=0):
        raise RuntimeError("getBalance: ERROR_KEY_DOES_NOT_EXIST")

    monkeypatch.setattr(HumanLayer, "balance", balance)
    r = client.post("/api/ui/human-key", json={"key": "wrong-key-1"})
    assert r.status_code == 400 and "ERROR_KEY_DOES_NOT_EXIST" in r.json()["error"]
    assert c.human_line().startswith("Люди (RuCaptcha): ⚠")


def test_limits_save_without_touching_the_key(ui, monkeypatch):
    c, client = ui
    from imnotarobot.layers.human import HumanLayer
    called = []

    async def balance(self, index=0):
        called.append(1)
        return 1.0

    monkeypatch.setattr(HumanLayer, "balance", balance)
    r = client.post("/api/ui/limits", json={"human": "0", "low": "30", "cloud": "2,5"}).json()
    assert r["ok"] and r["message"] == "Лимиты сохранены: люди — без лимита, модели — $2.50 в день."
    assert (c.cfg.human_daily_limit, c.cfg.human_low_balance, c.cfg.cloud_daily_limit_usd) == (0.0, 30.0, 2.5)
    assert c.service.chain.daily_limits == {"human": 0.0, "cloud": 2.5} and not called
    assert client.post("/api/ui/limits", json={"human": "-1"}).status_code == 400


def test_switching_service_changes_currency(ui):
    c, client = ui
    r = client.post("/api/ui/human-provider", json={"provider": "2captcha"}).json()
    assert r["state"]["human"]["currency"] == "$" and c.cfg.human_base_url == "https://api.2captcha.com"


def test_solved_captchas_show_up_in_recent(ui):
    c, client = ui
    task_id = client.post("/createTask", json={"clientKey": c.cfg.client_key, "task": {"type": "ImageToTextTask", "body": text_image("ab3k7")}}).json()["taskId"]
    for _ in range(200):
        if client.post("/getTaskResult", json={"clientKey": c.cfg.client_key, "taskId": task_id}).json().get("status") == "ready":
            break
        time.sleep(0.05)
    recent = client.get("/api/ui/state").json()["recent"]
    assert recent[0]["layer"] == "local" and recent[0]["layerTitle"] == "Локальная модель" and recent[0]["type"] == "ImageToTextTask" and not recent[0]["error"]


def test_layers_move_and_toggle(ui):
    c, client = ui
    client.post("/api/ui/move-layer", json={"id": "human", "step": -2})
    assert c.cfg.layers == ["human", "local", "browser"]
    r = client.post("/api/ui/toggle-layer", json={"id": "local"}).json()
    assert [(x["id"], x["enabled"]) for x in r["state"]["layers"]] == [("human", True), ("browser", True), ("local", False)]
    client.post("/api/ui/toggle-layer", json={"id": "local"})
    assert c.cfg.layers == ["human", "local", "browser"]


def test_key_left_by_an_older_build_asks_to_be_entered_again(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "get_secret", lambda a: "")
    monkeypatch.setattr(config, "legacy_secret_exists", lambda a: a == "human")
    monkeypatch.setattr(control, "LAUNCH_AGENT", tmp_path / "agent.plist")
    c = Controller(Config.load())
    assert c.human_line() == "Люди (RuCaptcha): введите ключ ещё раз"
    monkeypatch.setattr(config, "set_secret", lambda a, v: None)
    c.set_human(key="new")
    assert not c.legacy_human_key


def test_journal_survives_restart_opens_and_reports(ui):
    c, client = ui
    task_id = client.post("/createTask", json={"clientKey": c.cfg.client_key, "task": {"type": "ImageToTextTask", "body": text_image("k7m2p")}}).json()["taskId"]
    for _ in range(200):
        if client.post("/getTaskResult", json={"clientKey": c.cfg.client_key, "taskId": task_id}).json().get("status") == "ready":
            break
        time.sleep(0.05)
    entry = client.get(f"/api/ui/journal/{task_id}").json()
    assert entry["summary"]["typeTitle"] == "Текст на картинке" and entry["solution"] == {"text": "k7m2p"}
    assert "body" in entry["images"] and entry["summary"]["answer"] == "k7m2p"
    r = client.post("/api/ui/report-wrong", json={"id": task_id}).json()
    assert r["ok"] and r["state"]["recent"][0]["reported"]
    row = r["state"]["stats"]["rows"][0]
    assert (row["type"], row["layer"], row["answered"], row["bad"]) == ("Текст на картинке", "Локальная модель", 1, 1)
    from imnotarobot.service import Service
    reborn = Service(c.cfg)
    assert reborn.recent[0]["id"] == task_id and reborn.recent[0]["reported"]
    assert client.get("/api/ui/journal/123", headers={"X-UI-Token": "x"}).status_code == 403


def test_programs_need_the_access_key(ui):
    c, client = ui
    task = {"type": "ImageToTextTask", "body": text_image("ab3k7")}
    assert c.cfg.client_key.startswith("cs-")
    assert client.post("/createTask", json={"task": task}).json()["errorCode"] == "ERROR_KEY_DOES_NOT_EXIST"
    assert client.post("/in.php", data={"method": "base64", "body": task["body"]}).text == "ERROR_KEY_DOES_NOT_EXIST"
    assert client.post("/createTask", json={"clientKey": c.cfg.client_key, "task": task}).json()["errorId"] == 0
    old = c.cfg.client_key
    r = client.post("/api/ui/access", json={"regenerate": True}).json()
    assert r["state"]["access"]["key"] != old
    client.post("/api/ui/access", json={"required": False})
    assert client.post("/createTask", json={"task": task}).json()["errorId"] == 0


def test_alerts_for_low_balance_and_limits(ui, monkeypatch):
    c, client = ui
    from imnotarobot.layers.human import HumanLayer

    async def balance(self, index=0):
        return 10.0

    monkeypatch.setattr(HumanLayer, "balance", balance)
    client.post("/api/ui/human-key", json={"key": "abcd1234efgh"})
    c.cfg.cloud_daily_limit_usd = 0.001
    c.service.chain.state.answered("k", "vlm:x", 0.002)
    ids = [a for a, _ in c.alerts()]
    assert "low-balance" in ids and "cloud-limit" in ids


def test_panel_asks_the_app_to_open_the_window_or_quit(ui):
    c, client = ui
    assert client.post("/api/ui/open-window", json={"view": "journal"}).status_code == 400
    calls = []
    c.hooks["open-window"] = lambda view: calls.append(("open", view))
    c.hooks["quit"] = lambda _: calls.append(("quit", ""))
    assert client.post("/api/ui/open-window", json={"view": "journal"}).json()["ok"]
    client.post("/api/ui/quit", json={})
    assert calls == [("open", "journal"), ("quit", "")]


def test_every_phrase_has_an_english_translation():
    import re
    from pathlib import Path

    from imnotarobot.i18n import EN

    used = set()
    for path in Path("imnotarobot").rglob("*.py"):
        used |= set(re.findall(r'\btr\(\s*"((?:[^"\\]|\\.)*)"', path.read_text()))
    used |= {"Текст на картинке", "Локальная модель"}
    missing = sorted(u for u in used if re.search("[А-Яа-яЁё]", u) and u not in EN)
    assert not missing


def test_english_messages_fill_their_values(monkeypatch):
    from imnotarobot import i18n

    monkeypatch.setattr(i18n, "LANG", "en")
    assert i18n.tr("Сегодня: решено {solved} · {spend}", solved=3, spend="models $0.000") == "Today: solved 3 · models $0.000"
    assert i18n.tr("фраза без перевода") == "фраза без перевода"


def test_panel_reports_its_height_to_the_app(ui):
    c, client = ui
    assert client.post("/api/ui/panel-size", json={"height": 520}).json()["ok"]
    sizes = []
    c.hooks["panel-size"] = sizes.append
    client.post("/api/ui/panel-size", json={"height": 520})
    assert sizes == [520]


def test_other_apps_open_the_window_with_the_access_key(ui):
    c, client = ui
    opened = []
    c.hooks["open-window"] = opened.append
    assert client.post("/app/open", json={"clientKey": "wrong"}).status_code == 403
    assert client.post("/app/open", json={"clientKey": c.cfg.client_key, "view": "journal"}).json()["ok"]
    assert opened == ["journal"]
    assert client.get("/health").json()["app"] == "imnotarobot"


def test_update_button_starts_the_install(ui, monkeypatch):
    import imnotarobot.update as update

    c, client = ui
    assert client.get("/api/ui/state").json()["update"] is None
    assert client.post("/api/ui/update").status_code == 400
    monkeypatch.setattr(update, "running_app", lambda: control.Path("/Applications/ImNotARobot.app"))
    monkeypatch.setattr(update, "latest", lambda: {"version": "9.0.0", "url": "u", "page": "p", "sha256": ""})
    calls = []
    monkeypatch.setattr(update, "install", lambda info, app, progress=None: calls.append((info["version"], str(app))))
    monkeypatch.setattr(update, "relaunch_later", lambda app: calls.append("relaunch"))
    assert c.check_update()["version"] == "9.0.0"
    assert any(i.startswith("update:9.0.0") for i, _ in c.alerts())
    assert client.get("/api/ui/state").json()["update"]["version"] == "9.0.0"
    assert client.post("/api/ui/update").json()["ok"]
    for _ in range(50):
        if c.update_job.get("done"):
            break
        time.sleep(0.02)
    assert calls == [("9.0.0", "/Applications/ImNotARobot.app"), "relaunch"]


def test_a_spare_key_is_checked_labelled_and_never_shown(ui, monkeypatch):
    c, client = ui
    from imnotarobot.layers.human import HumanLayer

    async def balance(self, index=0):
        assert self.accounts[index].api_key == "ac-very-secret-9999"
        return 12.5

    monkeypatch.setattr(HumanLayer, "balance", balance)
    r = client.post("/api/ui/spare-add", json={"provider": "anticaptcha", "key": "ac-very-secret-9999", "note": "от Васи"}).json()
    assert r["ok"] and r["message"] == "Запасной ключ Anti-Captcha работает, баланс $12.50."
    spare = r["state"]["human"]["spares"][0]
    assert (spare["title"], spare["note"], spare["status"]["balance"]) == ("Anti-Captcha", "от Васи", 12.5)
    assert "very-secret" not in client.get("/api/ui/state").text
    assert client.post("/api/ui/spare-note", json={"id": spare["id"], "note": "рабочий"}).json()["state"]["human"]["spares"][0]["note"] == "рабочий"
    assert client.post("/api/ui/spare-remove", json={"id": spare["id"]}).json()["state"]["human"]["spares"] == []
    assert client.post("/api/ui/spare-remove", json={"id": "nope"}).status_code == 400


def test_the_humans_step_stays_usable_with_only_spare_keys(ui, monkeypatch):
    c, client = ui
    from imnotarobot.layers.human import HumanLayer

    async def balance(self, index=0):
        return 5.0

    monkeypatch.setattr(HumanLayer, "balance", balance)
    client.post("/api/ui/spare-add", json={"provider": "anticaptcha", "key": "ac-key-12345678"})
    s = client.get("/api/ui/state").json()
    layer = next(x for x in s["layers"] if x["id"] == "human")
    assert layer["enabled"] and layer["active"] and layer["ready"] and layer["why"] == ""
    assert s["human"]["hasKey"] is False and s["human"]["spares"][0]["currency"] == "$"


def test_limits_save_the_dollar_rate_for_mixed_currencies(ui, monkeypatch):
    c, client = ui
    from imnotarobot.layers.human import HumanLayer

    async def balance(self, index=0):
        return 5.0

    monkeypatch.setattr(HumanLayer, "balance", balance)
    client.post("/api/ui/spare-add", json={"provider": "anticaptcha", "key": "ac-key-12345678"})
    r = client.post("/api/ui/limits", json={"rate": "90"}).json()
    assert r["ok"] and "курс — 90 ₽/$" in r["message"]
    assert c.cfg.rub_per_usd == 90.0
    # the spare's spending is counted at the new rate from now on
    assert c.service.chain.layer("human").accounts[1].rate == 90.0
    assert client.get("/api/ui/state").json()["human"]["rubPerUsd"] == 90.0
    bad = client.post("/api/ui/limits", json={"rate": "0"})
    assert bad.status_code == 400 and bad.json()["error"] == "курс должен быть больше нуля"
    assert client.post("/api/ui/limits", json={"rate": "-1"}).status_code == 400


def test_limits_leave_the_rate_out_without_spare_accounts(ui):
    c, client = ui
    r = client.post("/api/ui/limits", json={"rate": "70"}).json()
    assert r["ok"] and "курс" not in r["message"] and c.cfg.rub_per_usd == 70.0


def test_restart_switch_shows_only_with_companion_apps(ui):
    c, client = ui
    assert client.get("/api/ui/state").json()["companions"] == {"apps": [], "restart": False}
    r = client.post("/api/ui/companion-restart", json={"on": True}).json()
    assert r["state"]["companions"]["restart"] and c.cfg.companion_restart


def test_a_model_ticked_but_off_in_the_path_is_marked_in_the_key_card(ui, monkeypatch):
    c, client = ui
    from imnotarobot.discovery import Detected, ModelInfo

    fake_provider(monkeypatch)
    monkeypatch.setattr(control, "detect_sync", lambda k, b="", progress=None: Detected(
        "openai", "https://api.openai.com/v1", [ModelInfo("gpt-x", True)]))
    client.post("/api/ui/add-key", json={"secret": "sk-xyz"})
    client.post("/api/ui/toggle-model", json={"key": "openai", "model": "gpt-x"})
    wait_health(c, "openai/gpt-x")
    client.post("/api/ui/toggle-layer", json={"id": "vlm:openai/gpt-x"})
    models = client.get("/api/ui/state").json()["keys"][0]["models"]
    assert models == [{"id": "gpt-x", "enabled": True, "inPath": False}]
    client.post("/api/ui/toggle-layer", json={"id": "vlm:openai/gpt-x"})
    assert client.get("/api/ui/state").json()["keys"][0]["models"][0]["inPath"]


def test_enabling_a_model_checks_it_in_the_background_and_switches_off_picture_rejectors(ui, monkeypatch):
    c, client = ui
    from imnotarobot.discovery import Detected, ModelInfo
    from imnotarobot.layers.vlm import LAST_ERRORS

    fake_provider(monkeypatch, status=400, message="Invalid image or video input")
    monkeypatch.setattr(control, "detect_sync", lambda k, b="", progress=None: Detected(
        "openai", "https://api.openai.com/v1", [ModelInfo("gpt-x", True)]))
    client.post("/api/ui/add-key", json={"secret": "sk-xyz"})
    try:
        r = client.post("/api/ui/toggle-model", json={"key": "openai", "model": "gpt-x"}).json()
        assert r["message"] == "gpt-x: включена."
        wait_health(c, "openai/gpt-x")
        state = client.get("/api/ui/state").json()
        assert state["keys"][0]["models"] == []  # it no longer counts as a model with pictures
        assert not any(x["id"] == "vlm:openai/gpt-x" for x in state["layers"])
        assert [tn["text"] for tn in state["toasts"]] == ["gpt-x не принимает картинки — выключена"]
        # a model entered by hand gets the same background check
        r = client.post("/api/ui/manual-model", json={"key": "openai", "model": "gpt-x"}).json()
        assert r["message"] == "gpt-x добавлена и включена."
        wait_health(c, "openai/gpt-x")
        assert c.cfg.models == [] and c.cfg.layers == ["local", "browser", "human"]
        assert list(c.toasts) == ["no-vision:openai/gpt-x"]  # one message, not one per check
    finally:
        LAST_ERRORS.pop("vlm:openai/gpt-x", None)


def test_extension_folder_is_revealed_in_finder(ui, monkeypatch, tmp_path):
    c, client = ui
    import imnotarobot.ui as ui_mod

    monkeypatch.setattr(ui_mod, "EXTENSION_DIR", tmp_path / "extension")
    (tmp_path / "extension").mkdir()
    calls = []
    monkeypatch.setattr("subprocess.run", lambda cmd, **k: calls.append(cmd))
    r = client.post("/api/ui/open-extension-folder", json={}).json()
    assert r["ok"] and r["message"] == "Открыл папку расширения в Finder."
    assert calls == [["open", "-R", str(tmp_path / "extension")]]
    monkeypatch.setattr(ui_mod, "EXTENSION_DIR", tmp_path / "nowhere")
    assert client.post("/api/ui/open-extension-folder", json={}).status_code == 400
