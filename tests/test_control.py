import plistlib

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


def test_notes_on_keys_survive_reload(ctl):
    ctl.set_human(note="основной, RuCaptcha")
    ctl.cfg.keys.append(config.KeyEntry(name="proxy", provider="custom"))
    ctl.set_key_note("proxy", "Antigravity Manager")
    cfg = Config.load()
    assert cfg.human_note == "основной, RuCaptcha" and cfg.keys[0].note == "Antigravity Manager"
