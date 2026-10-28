import plistlib
import threading

import pytest

import imnotarobot.config as config
import imnotarobot.control as control
from imnotarobot.config import Config
from imnotarobot.control import Controller


@pytest.fixture
def ctl(tmp_path, monkeypatch):
    secrets: dict[str, str] = {}
    monkeypatch.setattr(config, "get_secret", lambda a: secrets.get(a, ""))
    monkeypatch.setattr(config, "set_secret", lambda a, v: secrets.__setitem__(a, v) if v else secrets.pop(a, None))
    monkeypatch.setattr(control, "LAUNCH_AGENT", tmp_path / "LaunchAgents" / "app.captcha-stack.plist")
    c = Controller(Config.load())
    c.secrets = secrets
    return c


def test_adding_a_model_stores_the_key_in_keychain_not_in_the_file(ctl, tmp_path):
    ctl.add_model("gem", "gemini", "gemini-2.5-flash", "AIza-secret")
    saved = (tmp_path / "config.json").read_text()
    assert "AIza-secret" not in saved and '"gemini-2.5-flash"' in saved
    assert ctl.secrets["model:gem"] == "AIza-secret"
    assert [layer.name for layer in ctl.service.chain.layers] == ["local", "vlm:gem", "browser", "human"]
    assert ctl.service.chain.layer("vlm:gem").available()


def test_model_lines_show_missing_keys(ctl):
    ctl.add_model("oa", "openai", "gpt-x", "")
    assert ctl.model_lines() == [("oa", "⚠ нет ключа  oa: openai / gpt-x")]


def test_bad_input_is_refused(ctl):
    with pytest.raises(ValueError, match="неизвестный поставщик"):
        ctl.add_model("x", "nope", "m", "k")
    with pytest.raises(ValueError, match="название модели"):
        ctl.add_model("x", "openai", "", "k")
    with pytest.raises(ValueError, match="отрицательным"):
        ctl.set_human(daily_limit=-1)


def test_toggling_layers_keeps_order_and_survives_reload(ctl):
    ctl.add_model("q", "dashscope", "qwen3-vl-plus", "k")
    ctl.toggle_layer("local")
    assert ctl.cfg.layers == ["vlm:q", "browser", "human"]
    ctl.toggle_layer("local")
    assert ctl.cfg.layers == ["local", "vlm:q", "browser", "human"]
    assert Config.load().layers == ["local", "vlm:q", "browser", "human"]


def test_human_settings(ctl):
    ctl.set_human("2captcha", "hkey", 2.5)
    assert ctl.cfg.human_base_url == "https://api.2captcha.com" and ctl.secrets["human"] == "hkey"
    assert ctl.service.chain.daily_limits == {"human": 2.5, "cloud": 1.0}
    assert ctl.service.chain.layer("human").available()


def test_remove_model_deletes_its_key(ctl):
    ctl.add_model("q", "dashscope", "qwen3-vl-plus", "k")
    ctl.remove_model("q")
    assert "model:q" not in ctl.secrets and ctl.cfg.models == []


def test_start_at_login_writes_a_launch_agent(ctl):
    ctl.set_start_at_login(True)
    data = plistlib.loads(control.LAUNCH_AGENT.read_bytes())
    assert data["ProgramArguments"][1:] == ["-m", "imnotarobot.menubar"] and data["RunAtLoad"]
    ctl.set_start_at_login(False)
    assert not ctl.starts_at_login()


def test_today_line_sums_layers(ctl):
    ctl.service.chain.state.answered("k", "local", 0.0)
    ctl.service.chain.state.answered("k", "human", 0.0012)
    assert ctl.today_line() == "Сегодня: решено 2 · модели $0.000, люди 0.00 ₽"


def fake_detect(monkeypatch, provider, models, listable=True, base="https://api.example/v1"):
    from imnotarobot.discovery import Detected, ModelInfo

    found = Detected(provider, base, [ModelInfo(m, v, 0.3, 2.5) for m, v in models], listable)
    monkeypatch.setattr(control, "detect_sync", lambda key, base_url="", progress=None: found)


def test_add_key_lists_vision_models_and_hides_the_secret(ctl, monkeypatch, tmp_path):
    fake_detect(monkeypatch, "openrouter", [("google/gemini-x", True), ("deepseek/text", False)])
    entry = ctl.add_key("sk-or-v1-secretsecret")
    assert (entry.name, entry.hint) == ("openrouter", "sk-or…cret")
    assert ctl.secrets["key:openrouter"] == "sk-or-v1-secretsecret"
    assert "secretsecret" not in (tmp_path / "config.json").read_text()
    assert ctl.key_models("openrouter") == [("google/gemini-x", False)]


def test_toggle_model_from_key_adds_a_ready_layer_with_prices(ctl, monkeypatch):
    fake_detect(monkeypatch, "openrouter", [("google/gemini-x", True)])
    ctl.add_key("sk-or-v1-abc")
    ctl.toggle_key_model("openrouter", "google/gemini-x")
    spec = ctl.cfg.models[0]
    assert (spec.name, spec.key, spec.price_in, spec.ready()) == ("openrouter/google/gemini-x", "openrouter", 0.3, True)
    assert ctl.cfg.layers == ["local", "vlm:openrouter/google/gemini-x", "browser", "human"]
    ctl.toggle_key_model("openrouter", "google/gemini-x")
    assert ctl.cfg.models == [] and ctl.cfg.layers == ["local", "browser", "human"]


def test_second_key_of_same_provider_gets_its_own_name_and_removal_cleans_up(ctl, monkeypatch):
    fake_detect(monkeypatch, "openai", [("gpt-5", True)])
    ctl.add_key("sk-1111")
    ctl.add_key("sk-2222")
    assert [k.name for k in ctl.cfg.keys] == ["openai", "openai-2"]
    ctl.toggle_key_model("openai-2", "gpt-5")
    ctl.remove_key("openai-2")
    assert [k.name for k in ctl.cfg.keys] == ["openai"] and ctl.cfg.models == []
    assert "key:openai-2" not in ctl.secrets and ctl.cfg.layers == ["local", "browser", "human"]


def test_bench_specs_cover_every_vision_model_of_a_key(ctl, monkeypatch):
    fake_detect(monkeypatch, "openai", [("gpt-5", True), ("gpt-4o", True), ("tts-1", False)])
    ctl.add_key("sk-xyz")
    specs = ctl.bench_specs("openai")
    assert [s.model for s in specs] == ["gpt-5", "gpt-4o"] and all(s.api_key == "sk-xyz" for s in specs)
    runs, usd = ctl.bench_estimate(specs)
    assert runs == 2 * len(ctl.bench_cases()) and usd > 0


def test_use_best_enables_and_moves_it_first(ctl, monkeypatch):
    fake_detect(monkeypatch, "openai", [("gpt-5", True), ("gpt-4o", True)])
    ctl.add_key("sk-xyz")
    ctl.toggle_key_model("openai", "gpt-5")
    ctl.use_best("openai/gpt-4o")
    assert ctl.cfg.layers == ["local", "vlm:openai/gpt-4o", "vlm:openai/gpt-5", "browser", "human"]


def test_reseller_key_is_named_after_its_host(ctl, monkeypatch):
    fake_detect(monkeypatch, "custom", [("claude-opus-5-5", True)], base="https://reseller.example/v1")
    assert ctl.add_key("sk-reseller-abc").name == "reseller.example"


def test_model_check_runs_three_small_captchas(ctl, monkeypatch):
    import json as _json

    import httpx

    fake_detect(monkeypatch, "openai", [("gpt-x", True)])
    ctl.add_key("sk-xyz")
    real_run = control.bench.run

    async def run_with_fake_model(specs, cases, votes=1, **kw):
        def handler(request):
            return httpx.Response(200, json={"choices": [{"message": {"content": _json.dumps({"text": "nope"})}}]})
        return await real_run(specs, cases, votes=votes, transport=httpx.MockTransport(handler))

    monkeypatch.setattr(control.bench, "run", run_with_fake_model)
    result = ctl.test_model("openai", "gpt-x")
    assert (result["n"], result["ok"]) == (3, 0)
    assert [d["set"] for d in result["details"]] == ["text", "click", "grid"]
    assert ctl.model_tests["openai/gpt-x"] is result


def test_effort_settings_and_level_comparison_specs(ctl, monkeypatch):
    fake_detect(monkeypatch, "openai", [("gpt-x", True)])
    ctl.add_key("sk-xyz")
    ctl.toggle_key_model("openai", "gpt-x")
    ctl.set_effort("openai", "gpt-x", "clicks", "high")
    assert ctl.cfg.models[0].effort_clicks == "high"
    assert Config.load().models[0].effort_clicks == "high"
    assert ctl.bench_specs("openai")[0].effort_clicks == "high"
    levels = [(s.effort_recognition, s.effort_clicks) for s in ctl.effort_specs("openai", "gpt-x")]
    assert levels == [("none", "none"), ("low", "low"), ("medium", "medium"), ("high", "high")]
    with pytest.raises(ValueError):
        ctl.set_effort("openai", "gpt-x", "clicks", "turbo")


def test_old_settings_switch_models_to_one_ask(tmp_path):
    import json

    from imnotarobot.config import Config

    path = tmp_path / "config.json"
    path.write_text(json.dumps({"models": [{"name": "g", "provider": "gemini", "votes": 3},
                                           {"name": "q", "provider": "dashscope", "votes": 2}]}))
    cfg = Config.load(path, with_secrets=False)
    assert [m.votes for m in cfg.models] == [1, 2] and cfg.schema == 3 and "browser" in cfg.layers
    cfg.models[0].votes = 3
    cfg.save(path)
    assert Config.load(path, with_secrets=False).models[0].votes == 3


def test_a_gemini_key_switches_its_best_models_on_at_once(ctl, monkeypatch):
    fake_detect(monkeypatch, "gemini", [("gemini-3-flash-preview", True), ("gemini-2.5-flash", True), ("gemini-2.5-pro", True)])
    ctl.add_key("AIza-free-key")
    assert [m.model for m in ctl.cfg.models] == ["gemini-3-flash-preview"]
    assert ctl.cfg.layers == ["local", "vlm:gemini/gemini-3-flash-preview", "browser", "human"]


def test_off_a_mac_secrets_live_in_a_private_file(tmp_path, monkeypatch):
    import importlib
    import os

    from imnotarobot import config as cfg_mod

    real = importlib.reload(cfg_mod)
    monkeypatch.setattr(real, "SECURITY", str(tmp_path / "no-security-tool"))
    monkeypatch.setattr(real, "APP_DIR", tmp_path)
    real.set_secret("key:gemini", "AIza-x")
    assert real.get_secret("key:gemini") == "AIza-x" and not real.legacy_secret_exists("human")
    assert oct(os.stat(tmp_path / "secrets.json").st_mode)[-3:] == "600"
    real.set_secret("key:gemini", "")
    assert real.get_secret("key:gemini") == ""


def test_server_takes_its_keys_from_the_environment(ctl, monkeypatch):
    fake_detect(monkeypatch, "gemini", [("gemini-3-flash-preview", True)])
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-env")
    lines = ctl.setup_from_env()
    assert lines == ["GEMINI_API_KEY: gemini, gemini-3-flash-preview"] and ctl.secrets["key:gemini"] == "AIza-env"
    assert ctl.setup_from_env() == []


def test_a_run_of_paid_captchas_raises_an_alarm_until_quieted(ctl):
    ctl.cfg.human_alarm_count = 3
    sent = ctl.service.chain.sent.setdefault("human", [])
    sent += [control.time.monotonic()] * 2
    assert ctl.human_alarm() == 0
    sent.append(control.time.monotonic())
    assert ctl.human_alarm() == 3
    assert "human-often" in dict(ctl.alerts())
    ctl.quiet_alarm()
    assert ctl.human_alarm() == 0


def test_only_companions_that_are_not_running_are_started(ctl, monkeypatch):
    calls = []

    def run(cmd, **_):
        calls.append(cmd)
        return type("R", (), {"returncode": 0 if "Busy" in cmd[-1] or cmd[0] == "open" else 1})()

    monkeypatch.setattr(control.subprocess, "run", run)
    ctl.cfg.companion_apps = ["Busy App", "Antigravity Manager"]
    ctl.cfg.companion_restart = True
    assert ctl.start_companions() == ["Antigravity Manager"]
    assert ["open", "-g", "-j", "-a", "Antigravity Manager"] in calls
    assert not any(c[0] == "open" and "Busy App" in c for c in calls)


def test_companions_are_never_started_unless_keep_running_is_on(ctl, monkeypatch):
    monkeypatch.setattr(control.subprocess, "run", lambda cmd, **_: type("R", (), {"returncode": 0 if cmd[0] == "open" else 1})())
    ctl.cfg.companion_apps = ["Antigravity Manager"]
    assert ctl.start_companions() == []
    ctl.set_companion_restart(True)
    assert ctl.cfg.companion_restart and Config.load().companion_restart
    assert ctl.start_companions() == ["Antigravity Manager"]


def test_spare_human_accounts_keep_their_key_in_keychain_and_a_label(ctl):
    a = ctl.add_human_account("anticaptcha", " ac-secret-key-1234 ", "Anti-Captcha, рабочий")
    assert ctl.secrets[f"human:{a.id}"] == "ac-secret-key-1234" and a.hint == "ac-s…1234"
    saved = Config.load().human_extra
    assert [(x.provider, x.note) for x in saved] == [("anticaptcha", "Anti-Captcha, рабочий")]
    human = ctl.service.chain.layer("human")
    assert human.accounts[1].base_url == "https://api.anti-captcha.com" and human.accounts[1].api_key == "ac-secret-key-1234"
    # a dollar account under a rouble main account is counted in roubles
    assert human.accounts[1].rate == ctl.cfg.rub_per_usd
    ctl.set_human_account_note(a.id, "другая подпись")
    assert Config.load().human_extra[0].note == "другая подпись"
    with pytest.raises(ValueError):
        ctl.add_human_account("nosuch", "k")
    ctl.remove_human_account(a.id)
    assert not Config.load().human_extra and f"human:{a.id}" not in ctl.secrets


def test_human_line_without_a_main_key_names_the_spares(ctl):
    ctl.add_human_account("anticaptcha", "ac-secret-key-1234", "рабочий")
    assert not ctl.cfg.human_api_key
    assert ctl.human_line() == "Люди (RuCaptcha): нет основного ключа, капчи берут запасные"
    # the step itself keeps working through the spare account
    assert ctl.service.chain.layer("human").available()


def test_notes_on_keys_survive_reload(ctl):
    ctl.set_human(note="основной, RuCaptcha")
    ctl.cfg.keys.append(config.KeyEntry(name="proxy", provider="custom"))
    ctl.set_key_note("proxy", "Antigravity Manager")
    cfg = Config.load()
    assert cfg.human_note == "основной, RuCaptcha" and cfg.keys[0].note == "Antigravity Manager"


def test_model_alerts_explain_known_errors_and_are_shown_once_until_they_clear(ctl, monkeypatch):
    from imnotarobot.layers.vlm import LAST_ERRORS

    fake_detect(monkeypatch, "openai", [("gpt-x", True)])
    ctl.add_key("sk-xyz")
    ctl.toggle_key_model("openai", "gpt-x")
    now = control.time.time()
    try:
        for msg, plain in [
            ('сервис ответил HTTP 400: {"error": "User location is not supported for the API use"}',
             "регион не поддерживается — нужен прокси/VPN у посредника"),
            ("сервис ответил HTTP 401: Incorrect API key", "ключ не подходит"),
            ("сервис ответил HTTP 403: permission denied", "ключ не подходит"),
            ("сервис ответил HTTP 429: quota exceeded", "исчерпана квота запросов"),
            ("сервис ответил HTTP 500: boom", "сервис ответил HTTP 500: boom"),
        ]:
            LAST_ERRORS["vlm:openai/gpt-x"] = (now, msg)
            assert dict(ctl.alerts())["model-error:vlm:openai/gpt-x"] == f"Модель openai/gpt-x ошибается: {plain}"
        # the id is the same whatever the message, so the menu bar notifies once until the alert clears
        LAST_ERRORS["vlm:openai/gpt-x"] = (now, "сервис ответил HTTP 500: bang")
        assert list(dict(ctl.alerts())) == ["model-error:vlm:openai/gpt-x"]
        # a failure older than 10 minutes is not worth a notification, and a fresh one brings the alert back
        LAST_ERRORS["vlm:openai/gpt-x"] = (now - 601, "сервис ответил HTTP 500: boom")
        assert "model-error:vlm:openai/gpt-x" not in dict(ctl.alerts())
        LAST_ERRORS["vlm:openai/gpt-x"] = (now, "сервис ответил HTTP 500: boom")
        assert "model-error:vlm:openai/gpt-x" in dict(ctl.alerts())
        # a layer switched off in the path, or a model that has solved since (vlm pops the entry), clears it
        ctl.toggle_layer("vlm:openai/gpt-x")
        assert "model-error:vlm:openai/gpt-x" not in dict(ctl.alerts())
        ctl.toggle_layer("vlm:openai/gpt-x")
        LAST_ERRORS.pop("vlm:openai/gpt-x")
        assert "model-error:vlm:openai/gpt-x" not in dict(ctl.alerts())
    finally:
        LAST_ERRORS.pop("vlm:openai/gpt-x", None)


@pytest.mark.parametrize("status,mention,off", [
    (400, "Invalid image or video input", True),
    (400, "this model does not support vision input", True),
    (400, "not a multimodal model", True),
    (400, "User location is not supported for the API use", False),  # a region block is not a picture refusal
    (401, "Invalid image or video input", False),
    (500, "Invalid image or video input", False),
])
def test_only_picture_refusals_switch_the_model_off(ctl, monkeypatch, status, mention, off):
    from helpers import wait_health

    fake_detect(monkeypatch, "openai", [("gpt-x", True)])
    ctl.add_key("sk-xyz")
    ctl.toggle_key_model("openai", "gpt-x")

    async def refusing(specs, cases, votes=1, **kw):
        error = f"сервис ответил HTTP {status}: {mention}"
        return {s.name: {"rows": [{"ok": False, "error": error, "set": c["set"], "seconds": 0.1} for c in cases]}
                for s in specs}

    monkeypatch.setattr(control.bench, "run", refusing)
    ctl.check_model_health("openai", "gpt-x")
    wait_health(ctl, "openai/gpt-x")
    assert (len(ctl.cfg.models) == 0) == off
    assert ("no-vision:openai/gpt-x" in ctl.toasts) == off
    assert ctl.model_tests["openai/gpt-x"]["ok"] == 0  # the test result is kept either way


def test_a_picture_refusal_switches_the_model_off_end_to_end(ctl, monkeypatch):
    import httpx
    from helpers import wait_health
    from imnotarobot.layers.vlm import LAST_ERRORS

    fake_detect(monkeypatch, "openai", [("gpt-x", True)])
    ctl.add_key("sk-xyz")
    ctl.toggle_key_model("openai", "gpt-x")
    real_run = control.bench.run

    async def run_with_refusing_provider(specs, cases, votes=1, **kw):
        def handler(request):
            return httpx.Response(400, json={"error": {"message": "Invalid image or video input"}})
        return await real_run(specs, cases, votes=votes, transport=httpx.MockTransport(handler), **kw)

    monkeypatch.setattr(control.bench, "run", run_with_refusing_provider)
    try:
        ctl.check_model_health("openai", "gpt-x")
        wait_health(ctl, "openai/gpt-x")
        assert ctl.cfg.models == [] and ctl.cfg.layers == ["local", "browser", "human"]
        assert ctl.key("openai").models[0][1] is False  # no longer listed as a model with pictures
        assert Config.load().keys[0].models[0][1] is False
        assert ctl.toasts["no-vision:openai/gpt-x"][1] == "gpt-x не принимает картинки — выключена"
    finally:
        LAST_ERRORS.pop("vlm:openai/gpt-x", None)


def test_other_test_failures_leave_the_model_on_and_only_show_the_result(ctl, monkeypatch):
    import httpx
    from helpers import wait_health
    from imnotarobot.layers.vlm import LAST_ERRORS

    fake_detect(monkeypatch, "openai", [("gpt-x", True)])
    ctl.add_key("sk-xyz")
    ctl.toggle_key_model("openai", "gpt-x")
    real_run = control.bench.run

    async def run_with_broken_service(specs, cases, votes=1, **kw):
        def handler(request):
            return httpx.Response(500, json={"error": "boom"})
        return await real_run(specs, cases, votes=votes, transport=httpx.MockTransport(handler), **kw)

    monkeypatch.setattr(control.bench, "run", run_with_broken_service)
    try:
        ctl.check_model_health("openai", "gpt-x")
        wait_health(ctl, "openai/gpt-x")
        assert [m.name for m in ctl.cfg.models] == ["openai/gpt-x"] and "vlm:openai/gpt-x" in ctl.cfg.layers
        assert ctl.key("openai").models[0][1] is True
        assert not ctl.toasts
        assert ctl.model_tests["openai/gpt-x"]["ok"] == 0
    finally:
        LAST_ERRORS.pop("vlm:openai/gpt-x", None)


def test_many_threads_changing_settings_all_survive_a_reload(ctl, monkeypatch):
    fake_detect(monkeypatch, "openai", [(f"gpt-{i}", True) for i in range(4)])
    ctl.add_key("sk-xyz")
    for i in range(4):
        ctl.toggle_key_model("openai", f"gpt-{i}")

    # what a real run is like: every save reads the Keychain, and one save is slow to land
    monkeypatch.setattr(config, "get_secret", lambda a: (control.time.sleep(0.003) or ""))
    stalled = {"used": False}
    real_save = Config.save

    def stalling_save(self_cfg, path=None):
        if not stalled["used"]:
            stalled["used"] = True
            control.time.sleep(0.3)  # without one lock the other writers race past and get overwritten
        real_save(self_cfg, path)

    monkeypatch.setattr(Config, "save", stalling_save)
    errors: list[Exception] = []
    changes = [
        lambda: ctl.toggle_key_model("openai", "gpt-0"),
        lambda: ctl.toggle_key_model("openai", "gpt-1"),
        lambda: ctl.set_effort("openai", "gpt-2", "clicks", "high"),
        lambda: ctl.set_effort("openai", "gpt-3", "recognition", "low"),
        lambda: ctl.set_key_note("openai", "написано параллельно"),
        lambda: ctl.toggle_layer("local"),
        lambda: ctl.set_companion_restart(True),
        lambda: ctl.set_access(required=False),
        lambda: ctl.set_human(daily_limit=7),
        lambda: ctl.add_model("extra", "openai", "gpt-extra", ""),
        lambda: ctl.add_human_account("2captcha", "spare-key-123456", "запасной"),
        lambda: ctl.move_layer("human", -3),
    ]
    gate = threading.Barrier(len(changes))

    def job(fn):
        gate.wait()
        try:
            fn()
        except Exception as e:  # without one lock, a save could drop another change or fail outright
            errors.append(e)

    # a reader keeps parsing the file mid-storm: a torn save would surface here
    stop = threading.Event()

    def read():
        while not stop.is_set():
            try:
                Config.load()
            except Exception as e:
                errors.append(e)
            control.time.sleep(0.001)

    reader = threading.Thread(target=read)
    reader.start()
    writers = [threading.Thread(target=job, args=(fn,)) for fn in changes]
    for t in writers:
        t.start()
    for t in writers:
        t.join()
    stop.set()
    reader.join()
    cfg = Config.load()
    models = {m.model: m for m in cfg.models}
    assert not errors
    assert "gpt-0" not in models and "gpt-1" not in models and "gpt-extra" in models
    assert models["gpt-2"].effort_clicks == "high" and models["gpt-3"].effort_recognition == "low"
    assert cfg.keys[0].note == "написано параллельно"
    assert "local" not in cfg.layers and "vlm:openai/gpt-2" in cfg.layers and "vlm:extra" in cfg.layers
    assert "human" in cfg.layers
    assert cfg.companion_restart and not cfg.require_client_key and cfg.human_daily_limit == 7
    assert [a.note for a in cfg.human_extra] == ["запасной"]


def test_a_health_check_never_drops_a_change_made_while_it_runs(ctl, monkeypatch):
    from helpers import wait_health

    fake_detect(monkeypatch, "openai", [("gpt-x", True), ("gpt-y", True)])
    ctl.add_key("sk-xyz")
    ctl.toggle_key_model("openai", "gpt-x")
    release = threading.Event()

    def slow_test(key_name, model_id):
        release.wait()  # the window changes settings while the check is running
        return {"ok": 0, "n": 3, "seconds": 0.1, "error": "",
                "details": [{"set": "text", "ok": False, "error": "сервис ответил HTTP 400: Invalid image or video input"}]}

    monkeypatch.setattr(ctl, "test_model", slow_test)
    ctl.check_model_health("openai", "gpt-x")
    control.time.sleep(0.05)  # let the check reach its wait
    ctl.toggle_key_model("openai", "gpt-y")  # a settings change during the check
    release.set()
    wait_health(ctl, "openai/gpt-x")
    cfg = Config.load()
    assert [m.model for m in cfg.models] == ["gpt-y"]  # both the switch-off and the later tick survive
    assert "vlm:openai/gpt-y" in cfg.layers and "vlm:openai/gpt-x" not in cfg.layers
    assert ctl.key("openai").models[0][1] is False
