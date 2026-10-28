"""Everything the menu bar does, without the GUI, so it can be tested."""

import asyncio
import json
import os
import plistlib
import re
import secrets
import subprocess
import sys
import threading
import time
from urllib.parse import urlparse
from pathlib import Path
from typing import Any

import uvicorn

from . import config as config_mod
from . import bench, testset
from .config import HUMAN_CURRENCY, HUMAN_PRESETS, Config, HumanAccount, KeyEntry
from .discovery import detect_sync, recommended_models
from .providers import PRESETS, ModelSpec
from .server import create_app
from .layers import HumanLayer
from .service import Service, answering_account
from .i18n import tr

LAUNCH_AGENT = Path.home() / "Library" / "LaunchAgents" / "app.captcha-stack.plist"
# a model layer's last failure stops being worth a notification after this many seconds
MODEL_ERROR_WINDOW = 600
LAYER_TITLES = {"local": tr("Локальная модель"), "browser": tr("Браузер (галочки: reCAPTCHA, hCaptcha, Turnstile)"), "human": tr("Люди")}
HUMAN_TITLES = {"rucaptcha": "RuCaptcha", "2captcha": "2Captcha", "anticaptcha": "Anti-Captcha"}


def money(value: float, currency: str) -> str:
    return f"{value:.2f} ₽" if currency == "₽" else f"${value:.2f}"


def plain_error(msg: str) -> str:
    """Known provider refusals in plain Russian, so a notification says what to do; the rest are quoted as answered."""
    low = msg.lower()
    if "location is not supported" in low:
        return tr("регион не поддерживается — нужен прокси/VPN у посредника")
    if "HTTP 401" in msg or "HTTP 403" in msg:
        return tr("ключ не подходит")
    if "HTTP 429" in msg:
        return tr("исчерпана квота запросов")
    return msg[:120]


def rejects_images(error: str) -> bool:
    """HTTP 400 that complains about image/vision/multimodal input: the provider lists a model that cannot see."""
    return "HTTP 400" in error and any(w in error.lower() for w in ("image", "vision", "multimodal"))


class Controller:
    def __init__(self, cfg: Config | None = None):
        # one writer at a time: the window, the HTTP API and the background jobs all save settings through this
        self._lock = threading.RLock()
        self.cfg = cfg or Config.load()
        self._explicit_layers()
        if self.cfg.require_client_key and not self.cfg.client_key:
            self.cfg.client_key = "cs-" + secrets.token_urlsafe(18)
            self.cfg.save()
        self.service = Service(self.cfg)
        self._server: uvicorn.Server | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self.bench: dict = {}
        self.detecting = ""
        self.model_tests: dict[str, dict] = {}
        # checks of freshly enabled models running in the background, by model name
        self.health_jobs: dict[str, dict] = {}
        # messages for the window from jobs that cannot answer an action: id -> (unix time, text)
        self.toasts: dict[str, tuple[float, str]] = {}
        self.dataset_jobs: dict[str, dict] = {}
        self.tiles_job: dict = {}
        self.port: int | None = None
        self.port_note = ""
        self.human_status: dict = {}
        # balance checks of the spare accounts, by account id
        self.spare_status: dict[str, dict] = {}
        self.legacy_human_key = not self.cfg.human_api_key and config_mod.legacy_secret_exists("human")
        self.ui_token = secrets.token_urlsafe(24)
        # set by the menu bar app: the page asks for native things (open the window, quit) through these
        self.hooks: dict[str, Any] = {}
        self.alarm_quiet_since = 0.0
        # what macOS allows the app to do (background login item, notifications); None when unknown
        self.permissions: dict[str, bool | None] = {}
        # newer release found on GitHub; job tracks a download started from the window
        self.update: dict | None = None
        self.update_job: dict = {}

    # --- server ---------------------------------------------------------------
    def free_port(self) -> int:
        """The configured port, or the next free one if another program holds it."""
        import socket

        for port in range(self.cfg.port, self.cfg.port + 20):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                # as uvicorn does: connections of a previous run still closing must not push us to the next port
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    s.bind(("127.0.0.1", port))
                    return port
                except OSError:
                    continue
        raise RuntimeError(tr("порты {first}–{last} заняты другими программами", first=self.cfg.port, last=self.cfg.port + 19))

    def start_server(self, host: str = "127.0.0.1") -> None:
        port = self.free_port()
        if port != self.cfg.port:
            self.port_note = tr("Порт {port} занят другой программой — работаю на {actual}. Направьте программы сюда.", port=self.cfg.port, actual=port)
        self.port = port
        app = create_app(service=self.service)
        from .ui import add_ui_routes

        add_ui_routes(app, self)
        self._server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="warning"))

        def run():
            self._loop = asyncio.new_event_loop()
            self._loop.run_until_complete(self._server.serve())

        threading.Thread(target=run, daemon=True, name="captcha-stack-server").start()
        self._write_runtime()
        # the first permission answer lands in the background before the window asks for it;
        # later ones come every minute from the menu bar, which never blocks on them either
        self.refresh_permissions()

    def _write_runtime(self) -> None:
        """Lets other programs on this Mac find the running solver without being told its port."""
        path = Path(config_mod.APP_DIR) / "runtime.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"address": self.address, "pid": os.getpid(), "started": time.time()}))

    def setup_from_env(self) -> list[str]:
        """Servers are configured by environment: GEMINI_API_KEY (or any vision key) and HUMAN_API_KEY."""
        done = []
        for var in ("GEMINI_API_KEY", "VISION_API_KEY"):
            secret = os.environ.get(var, "").strip()
            if secret and not any(config_mod.get_secret(f"key:{k.name}") == secret for k in self.cfg.keys):
                entry = self.add_key(secret, os.environ.get("VISION_BASE_URL", ""))
                done.append(f"{var}: {entry.name}, {', '.join(m.model for m in self.cfg.models if m.key == entry.name) or '—'}")
        human = os.environ.get("HUMAN_API_KEY", "").strip()
        if human and config_mod.get_secret("human") != human:
            self.set_human(provider=os.environ.get("HUMAN_PROVIDER") or None, key=human)
            done.append("HUMAN_API_KEY")
        return done

    def stop_server(self) -> None:
        if self._server:
            self._server.should_exit = True
        (Path(config_mod.APP_DIR) / "runtime.json").unlink(missing_ok=True)

    @property
    def address(self) -> str:
        return f"http://127.0.0.1:{self.port or self.cfg.port}"

    def _explicit_layers(self) -> None:
        """The menu switches models one by one, so the catch-all "vlm" layer is spelled out per model."""
        if "vlm" in self.cfg.layers:
            i = self.cfg.layers.index("vlm")
            self.cfg.layers[i:i + 1] = [f"vlm:{m.name}" for m in self.cfg.models]

    def _add_model_layer(self, name: str) -> None:
        if f"vlm:{name}" in self.cfg.layers:
            return
        at = next((i for i, x in enumerate(self.cfg.layers) if x in ("browser", "human")), len(self.cfg.layers))
        self.cfg.layers.insert(at, f"vlm:{name}")

    def _apply(self) -> None:
        with self._lock:
            self.cfg.save()
            fresh = Config.load()
            fresh.state_path = self.cfg.state_path
            self.cfg = fresh
            self._explicit_layers()
            self.service.reconfigure(fresh)

    # --- what the menu shows --------------------------------------------------
    def today_line(self) -> str:
        day = self.service.today()
        solved = int(sum(v.get("solved", 0) for v in day.values()))
        return tr("Сегодня: решено {solved} · {spend}", solved=solved, spend=self.spend_text())

    def spend_today(self) -> dict[str, float]:
        """Today's spend split by currency: cloud models in dollars, workers in their service's currency."""
        state = self.service.chain.state
        return {"cloud_usd": state.spent_today_prefix("vlm:"), "human": state.spent_today("human")}

    def spend_text(self) -> str:
        s = self.spend_today()
        cur = HUMAN_CURRENCY.get(self.cfg.human_provider, "$")
        parts = [tr("модели ${usd}", usd=f"{s['cloud_usd']:.3f}")]
        if self.cfg.human_api_key or s["human"]:
            parts.append(tr("люди {money}", money=money(s["human"], cur)))
        return ", ".join(parts)

    def layer_items(self) -> list[tuple[str, str, bool]]:
        """(layer id, title, enabled) for every layer that can be switched."""
        items = [("local", LAYER_TITLES["local"], "local" in self.cfg.layers)]
        for m in self.cfg.models:
            items.append((f"vlm:{m.name}", tr("Модель {name}", name=m.name), f"vlm:{m.name}" in self.cfg.layers))
        items.append(("browser", LAYER_TITLES["browser"], "browser" in self.cfg.layers))
        items.append(("human", f"{LAYER_TITLES['human']} ({self.cfg.human_provider})", "human" in self.cfg.layers))
        return items

    def model_lines(self) -> list[tuple[str, str]]:
        return [(m.name, f"{'✓' if m.ready() else tr('⚠ нет ключа')}  {m.name}: {m.provider} / {m.model or '?'}")
                for m in self.cfg.models]

    # --- actions ----------------------------------------------------------------
    def toggle_layer(self, layer_id: str) -> None:
        with self._lock:
            if layer_id in self.cfg.layers:
                self.cfg.layers.remove(layer_id)
            else:
                # keeps the user's order: the layer goes before the first enabled layer that follows it by default
                order = [i for i, _, _ in self.layer_items()]
                after = order[order.index(layer_id) + 1:] if layer_id in order else []
                at = next((self.cfg.layers.index(i) for i in after if i in self.cfg.layers), len(self.cfg.layers))
                self.cfg.layers.insert(at, layer_id)
            self._apply()

    def add_model(self, name: str, provider: str, model: str, key: str, base_url: str = "") -> ModelSpec:
        with self._lock:
            name = name.strip() or provider
            if provider not in PRESETS:
                raise ValueError(tr("неизвестный поставщик {provider}; есть: {known}", provider=repr(provider), known=", ".join(PRESETS)))
            if any(m.name == name for m in self.cfg.models):
                raise ValueError(tr("модель с именем {name} уже есть", name=repr(name)))
            spec = ModelSpec(name=name, provider=provider, model=model.strip(), base_url=base_url.strip())
            if not spec.model:
                raise ValueError(tr("укажите название модели"))
            if key.strip():
                config_mod.set_secret(f"model:{name}", key.strip())
            self.cfg.models.append(spec)
            self._add_model_layer(name)
            self._apply()
            return spec

    def remove_model(self, name: str) -> None:
        with self._lock:
            self.cfg.models = [m for m in self.cfg.models if m.name != name]
            self.cfg.layers = [x for x in self.cfg.layers if x != f"vlm:{name}"]
            config_mod.set_secret(f"model:{name}", "")
            self._apply()

    def set_model_key(self, name: str, key: str) -> None:
        with self._lock:
            config_mod.set_secret(f"model:{name}", key.strip())
            self._apply()

    def set_human(self, provider: str | None = None, key: str | None = None, daily_limit: float | None = None,
                  note: str | None = None) -> None:
        with self._lock:
            if provider is not None:
                if provider not in HUMAN_PRESETS:
                    raise ValueError(tr("сервис {provider} не знаю; есть: {known}", provider=repr(provider), known=", ".join(HUMAN_PRESETS)))
                self.cfg.human_provider = provider
                self.cfg.human_base_url = HUMAN_PRESETS[provider]
            if key is not None:
                key = key.strip()
                config_mod.set_secret("human", key)
                self.cfg.human_key_hint = f"{key[:4]}…{key[-4:]}" if key else ""
                self.legacy_human_key = False
                self.human_status = {}
            if daily_limit is not None:
                if daily_limit < 0:
                    raise ValueError(tr("лимит не может быть отрицательным"))
                self.cfg.human_daily_limit = daily_limit
            if note is not None:
                self.cfg.human_note = note.strip()[:80]
            self._apply()

    def add_human_account(self, provider: str, key: str, note: str = "") -> HumanAccount:
        """A spare workers' account, used when the main one cannot take a captcha."""
        with self._lock:
            if provider not in HUMAN_PRESETS:
                raise ValueError(tr("сервис {provider} не знаю; есть: {known}", provider=repr(provider), known=", ".join(HUMAN_PRESETS)))
            key = key.strip()
            if not key:
                raise ValueError(tr("вставьте ключ"))
            account = HumanAccount(id=secrets.token_hex(4), provider=provider, note=note.strip()[:80], hint=f"{key[:4]}…{key[-4:]}")
            config_mod.set_secret(f"human:{account.id}", key)
            self.cfg.human_extra.append(account)
            self._apply()
            return account

    def human_account(self, account_id: str) -> HumanAccount:
        found = next((a for a in self.cfg.human_extra if a.id == account_id), None)
        if found is None:
            raise ValueError(tr("такого ключа нет"))
        return found

    def remove_human_account(self, account_id: str) -> None:
        with self._lock:
            self.human_account(account_id)
            self.cfg.human_extra = [a for a in self.cfg.human_extra if a.id != account_id]
            config_mod.set_secret(f"human:{account_id}", "")
            self.spare_status.pop(account_id, None)
            self._apply()

    def set_human_account_note(self, account_id: str, note: str) -> None:
        with self._lock:
            self.human_account(account_id).note = note.strip()[:80]
            self._apply()

    def check_human_account(self, account_id: str) -> dict:
        index = 1 + self.cfg.human_extra.index(self.human_account(account_id))
        human = self.service.chain.layer("human")
        if not isinstance(human, HumanLayer) or index >= len(human.accounts):
            status = {"ok": False, "error": tr("слой людей выключен")}
        else:
            try:
                status = {"ok": True, "balance": asyncio.run(human.balance(index))}
            except Exception as e:
                status = {"ok": False, "error": str(e)[:120]}
        self.spare_status[account_id] = status
        return status

    def set_key_note(self, name: str, note: str) -> None:
        with self._lock:
            entry = next((k for k in self.cfg.keys if k.name == name), None)
            if entry is None:
                raise ValueError(tr("такого ключа нет"))
            entry.note = note.strip()[:80]
            self._apply()

    def set_companion_restart(self, on: bool) -> None:
        with self._lock:
            self.cfg.companion_restart = on
            self._apply()

    def set_paused(self, paused: bool) -> None:
        self.service.paused = paused

    def check_human(self) -> dict:
        """Asks the workers' service for the balance: proves the key works. Cached for the menu."""
        human = self.service.chain.layer("human")
        if not isinstance(human, HumanLayer) or not human.available():
            self.human_status = {"ok": False, "error": tr("нет ключа")}
            return self.human_status
        # with no main key the first spare account is the one that answers
        index, provider, rate = answering_account(self.cfg, human)
        try:
            self.human_status = {"ok": True, "balance": asyncio.run(human.balance(index)),
                                 "provider": provider, "currency": HUMAN_CURRENCY.get(provider, "$"), "rate": rate}
        except Exception as e:
            self.human_status = {"ok": False, "error": str(e)[:120]}
        return self.human_status

    def human_line(self) -> str:
        st = getattr(self, "human_status", None) or {}
        name = HUMAN_TITLES.get(self.cfg.human_provider, self.cfg.human_provider)
        if not self.cfg.human_api_key:
            if self.cfg.human_extra:
                return tr("Люди ({name}): нет основного ключа, капчи берут запасные", name=name)
            return tr("Люди ({name}): введите ключ ещё раз", name=name) if self.legacy_human_key else tr("Люди ({name}): нет ключа", name=name)
        if st.get("ok"):
            # the balance may be a spare's, in its own currency; the threshold is in the main one
            cur = st.get("currency") or HUMAN_CURRENCY.get(self.cfg.human_provider, "$")
            low = tr(" — пора пополнить") if st["balance"] * st.get("rate", 1.0) < self.cfg.human_low_balance else ""
            return tr("Люди ({name}): ✓ баланс {money}{low}", name=name, money=money(st["balance"], cur), low=low)
        if st:
            return tr("Люди ({name}): ⚠ {error}", name=name, error=st.get("error", "")[:40])
        return tr("Люди ({name}): ключ есть", name=name)

    def move_layer(self, layer_id: str, step: int) -> None:
        with self._lock:
            layers = self.cfg.layers
            if layer_id not in layers:
                return
            i = layers.index(layer_id)
            j = max(0, min(len(layers) - 1, i + step))
            layers.insert(j, layers.pop(i))
            self._apply()

    # --- keys --------------------------------------------------------------------
    def add_key(self, secret: str, base_url: str = "") -> KeyEntry:
        """Detects the provider, stores the secret in the Keychain and remembers the provider's model list."""
        secret = secret.strip()
        if not secret:
            raise ValueError(tr("пустой ключ"))
        self.detecting = tr("начинаю поиск поставщика")
        try:
            found = detect_sync(secret, base_url.strip(), progress=lambda text: setattr(self, "detecting", text))
        finally:
            self.detecting = ""
        base_name = urlparse(found.base_url).hostname or "custom" if found.provider == "custom" else found.provider
        with self._lock:  # the provider was found outside the lock: the slow part must not block other changes
            taken = {k.name for k in self.cfg.keys}
            name = base_name if base_name not in taken else next(
                f"{base_name}-{i}" for i in range(2, 99) if f"{base_name}-{i}" not in taken)
            entry = KeyEntry(name=name, provider=found.provider, base_url=found.base_url,
                             hint=f"{secret[:5]}…{secret[-4:]}", listable=found.listable,
                             models=[[m.id, m.vision, m.price_in, m.price_out] for m in found.models])
            config_mod.set_secret(f"key:{name}", secret)
            self.cfg.keys.append(entry)
            self._apply()
            # a fresh Gemini key works at once: its best captcha models are switched on
            for model_id in recommended_models([mid for mid, vision, *_ in entry.models if vision]):
                self.toggle_key_model(name, model_id)
            return entry

    def key(self, name: str) -> KeyEntry:
        entry = next((k for k in self.cfg.keys if k.name == name), None)
        if entry is None:
            raise ValueError(tr("ключа {name} нет", name=repr(name)))
        return entry

    def refresh_key(self, name: str) -> KeyEntry:
        entry = self.key(name)
        found = detect_sync(config_mod.get_secret(f"key:{name}"), entry.base_url if entry.provider == "custom" else "")
        with self._lock:
            entry = self.key(name)  # re-read: another thread may have reloaded the config while we were detecting
            entry.models = [[m.id, m.vision, m.price_in, m.price_out] for m in found.models]
            self._apply()
            return entry

    def remove_key(self, name: str) -> None:
        with self._lock:
            self.cfg.layers = [x for x in self.cfg.layers
                               if not any(x == f"vlm:{m.name}" for m in self.cfg.models if m.key == name)]
            self.cfg.models = [m for m in self.cfg.models if m.key != name]
            self.cfg.keys = [k for k in self.cfg.keys if k.name != name]
            config_mod.set_secret(f"key:{name}", "")
            self._apply()

    def key_models(self, name: str, vision_only: bool = True) -> list[tuple[str, bool]]:
        """(model id, enabled) for the menu."""
        enabled = {m.model for m in self.cfg.models if m.key == name}
        return [(mid, mid in enabled) for mid, vision, *_ in self.key(name).models if vision or not vision_only]

    def toggle_key_model(self, key_name: str, model_id: str) -> None:
        with self._lock:
            entry = self.key(key_name)
            existing = next((m for m in self.cfg.models if m.key == key_name and m.model == model_id), None)
            if existing:
                self.cfg.models.remove(existing)
                self.cfg.layers = [x for x in self.cfg.layers if x != f"vlm:{existing.name}"]
            else:
                price = next(((p_in, p_out) for mid, _, p_in, p_out in entry.models if mid == model_id), (0.0, 0.0))
                spec = ModelSpec(name=f"{key_name}/{model_id}", provider=entry.provider, base_url=entry.base_url,
                                 model=model_id, key=key_name, price_in=price[0], price_out=price[1])
                self.cfg.models.append(spec)
                self._add_model_layer(spec.name)
            self._apply()

    # --- comparing models ----------------------------------------------------------
    def bench_cases(self, per_kind: int = 5) -> list[dict]:
        return testset.generated(per_kind) + testset.downloaded()

    def bench_specs(self, key_name: str | None = None) -> list[ModelSpec]:
        """Every vision model of one key (enabled or not), or every enabled model."""
        if key_name is None:
            return [m for m in self.cfg.models if m.ready()]
        entry = self.key(key_name)
        secret = config_mod.get_secret(f"key:{key_name}")
        chosen = {m.model: m for m in self.cfg.models if m.key == key_name}
        return [ModelSpec(name=f"{key_name}/{mid}", provider=entry.provider, base_url=entry.base_url, model=mid,
                          key=key_name, api_key=secret, price_in=p_in, price_out=p_out,
                          effort_recognition=chosen[mid].effort_recognition if mid in chosen else "none",
                          effort_clicks=chosen[mid].effort_clicks if mid in chosen else "medium")
                for mid, vision, p_in, p_out in entry.models if vision]

    def bench_estimate(self, specs: list[ModelSpec], votes: int = 1) -> tuple[int, float | None]:
        n = len(self.bench_cases())
        return n * len(specs), bench.estimate_usd(specs, n, votes)

    def run_bench(self, specs: list[ModelSpec], votes: int = 1, progress=None, stop=None) -> Path:
        cases = self.bench_cases()
        results = asyncio.run(bench.run(specs, cases, votes=votes, progress=progress, stop=stop))
        if not results:
            raise RuntimeError(tr("сравнение остановлено до первого ответа"))
        stamp = time.strftime("%Y%m%d-%H%M%S")
        out = Path(config_mod.APP_DIR) / "reports" / f"compare-{stamp}.html"
        (out.with_suffix(".json")).parent.mkdir(parents=True, exist_ok=True)
        out.with_suffix(".json").write_text(json.dumps(results, ensure_ascii=False, default=str))
        return bench.report_html(results, out, votes)

    def effort_specs(self, key_name: str, model_id: str) -> list[ModelSpec]:
        """The same model at every thinking level, for comparing them against each other."""
        from dataclasses import replace

        from .providers import EFFORTS

        base = next((s for s in self.bench_specs(key_name) if s.model == model_id), None)
        if base is None:
            raise ValueError(tr("модели {model} у ключа {key} нет", model=model_id, key=key_name))
        return [replace(base, name=f"{base.name} · раздумья {level}", effort_recognition=level, effort_clicks=level)
                for level in EFFORTS if level != "default"]

    def set_effort(self, key_name: str, model_id: str, kind: str, level: str) -> None:
        from .providers import EFFORTS

        with self._lock:
            if level not in EFFORTS or kind not in ("recognition", "clicks"):
                raise ValueError(tr("неизвестный уровень раздумий"))
            spec = next((m for m in self.cfg.models if m.key == key_name and m.model == model_id), None)
            if spec is None:
                raise ValueError(tr("сначала включите модель"))
            setattr(spec, f"effort_{kind}", level)
            self._apply()

    def start_bench(self, key_name: str | None, specs: list[ModelSpec] | None = None, label: str = "") -> dict:
        """Runs the comparison in a background thread; progress and results land in self.bench."""
        if self.bench.get("running"):
            raise ValueError(tr("сравнение уже идёт"))
        specs = specs or self.bench_specs(key_name)
        if not specs:
            raise ValueError(tr("нет моделей с картинками для сравнения"))
        runs, _ = self.bench_estimate(specs)
        state = {"running": True, "done": 0, "total": runs, "key": label or key_name, "started": time.time(), "stopping": False}
        self.bench = state

        def work():
            try:
                report = self.run_bench(specs, progress=lambda d, t: state.update(done=d, total=t),
                                        stop=lambda: state["stopping"])
                results = json.loads(report.with_suffix(".json").read_text())
                state["report"] = str(report)
                state["rows"] = [{"name": n, "model": results[n]["model"], "accuracy": results[n]["accuracy"],
                                  "seconds": results[n]["seconds"], "usd_per_1000": results[n]["usd_per_1000"],
                                  "errors": results[n]["errors"], "by_set": results[n]["by_set"]}
                                 for n in bench.ranking(results)]
            except Exception as e:
                state["error"] = str(e)[:300]
            state["running"] = False
            state["finished"] = time.time()

        threading.Thread(target=work, daemon=True, name="captcha-stack-bench").start()
        return state

    def test_model(self, key_name: str, model_id: str) -> dict:
        """One text, one click and one grid captcha with a single ask each: does this model work at all, how fast."""
        spec = next((s for s in self.bench_specs(key_name) if s.model == model_id), None)
        if spec is None:
            entry = self.key(key_name)
            spec = ModelSpec(name=f"{key_name}/{model_id}", provider=entry.provider, base_url=entry.base_url,
                             model=model_id, key=key_name, api_key=config_mod.get_secret(f"key:{key_name}"))
        cases = [c for c in testset.generated(1, seed=11) if not c["id"].startswith("gen-math")]
        rows = asyncio.run(bench.run([spec], cases, votes=1))[spec.name]["rows"]
        result = {"at": time.time(), "ok": sum(r["ok"] for r in rows), "n": len(rows),
                  "seconds": round(sum(r["seconds"] for r in rows) / len(rows), 1),
                  "error": next((r["error"] for r in rows if r["error"]), ""),
                  "details": [{"set": r["set"].replace("сгенерированные: ", ""), "ok": r["ok"], "error": r["error"]} for r in rows]}
        self.model_tests[spec.name] = result
        return result

    def toast(self, id: str, text: str) -> None:
        """The window shows each id once; old entries are dropped so a restart does not replay them."""
        self.toasts = {i: v for i, v in self.toasts.items() if time.time() - v[0] < MODEL_ERROR_WINDOW}
        self.toasts[id] = (time.time(), text)

    def check_model_health(self, key_name: str, model_id: str) -> None:
        """Tests a freshly enabled model in the background; one that rejects pictures is switched off at once."""
        name = f"{key_name}/{model_id}"
        if self.health_jobs.get(name, {}).get("running"):
            return
        job = self.health_jobs[name] = {"running": True}

        def work():
            try:
                result = self.test_model(key_name, model_id)
                if not result["ok"] and any(rejects_images(d["error"]) for d in result["details"]):
                    with self._lock:
                        # re-read the current config: the window may have reloaded it while the test was running
                        for row in self.key(key_name).models:
                            if row[0] == model_id:
                                row[1] = False
                        existing = next((m for m in self.cfg.models if m.key == key_name and m.model == model_id), None)
                        if existing:  # switched off by hand while the test ran: only the vision flag is left to save
                            self.cfg.models.remove(existing)
                            self.cfg.layers = [x for x in self.cfg.layers if x != f"vlm:{existing.name}"]
                        self._apply()
                    self.toast(f"no-vision:{name}", tr("{model} не принимает картинки — выключена", model=model_id))
            except Exception:
                pass  # nothing to switch off; the empty test result says the check got no answer
            finally:
                job["running"] = False

        threading.Thread(target=work, daemon=True, name="captcha-stack-health").start()

    def set_access(self, required: bool | None = None, regenerate: bool = False) -> None:
        with self._lock:
            if required is not None:
                self.cfg.require_client_key = required
            if regenerate or (self.cfg.require_client_key and not self.cfg.client_key):
                self.cfg.client_key = "cs-" + secrets.token_urlsafe(18)
            self._apply()

    def check_connection(self) -> dict:
        """Sends a generated text captcha through the app's own 2Captcha-style API, the way a program would."""
        import httpx

        case = next(c for c in testset.generated(1, seed=5) if c["task"]["type"] == "ImageToTextTask")
        started = time.time()
        with httpx.Client(base_url=self.address, timeout=10) as client:
            created = client.post("/createTask", json={"clientKey": self.cfg.client_key, "task": case["task"]}).json()
            if created.get("errorId"):
                return {"ok": False, "error": created.get("errorDescription") or created.get("errorCode")}
            while time.time() - started < 120:
                r = client.post("/getTaskResult", json={"clientKey": self.cfg.client_key, "taskId": created["taskId"]}).json()
                if r.get("status") != "processing":
                    break
                time.sleep(0.3)
        if r.get("errorId"):
            return {"ok": False, "error": r.get("errorDescription") or r.get("errorCode")}
        titles = {x.name: x.title or x.name for x in self.service.chain.layers}
        return {"ok": True, "seconds": round(time.time() - started, 2), "layer": titles.get(r.get("layer"), r.get("layer")),
                "correct": r.get("solution", {}).get("text") == case["expected"]["text"]}

    def alerts(self) -> list[tuple[str, str]]:
        """(id, text) of things worth a notification right now; the menu shows each id once until it clears."""
        out = []
        cur = HUMAN_CURRENCY.get(self.cfg.human_provider, "$")
        st = self.human_status or {}
        if st.get("ok") and st["balance"] * st.get("rate", 1.0) < self.cfg.human_low_balance:
            # the answering account may be a spare: name it and show its own currency
            provider = st.get("provider") or self.cfg.human_provider
            out.append(("low-balance", tr("На {service} осталось {money} — пора пополнить.", service=HUMAN_TITLES.get(provider, provider),
                                           money=money(st["balance"], HUMAN_CURRENCY.get(provider, "$")))))
        limit = self.cfg.human_daily_limit
        if limit and self.service.chain.state.spent_today("human") >= limit:
            out.append(("human-limit", tr("Люди: исчерпан дневной лимит {money}. До завтра капчи им не отправляются.", money=money(limit, cur))))
        if sent := self.human_alarm():
            out.append(("human-often", tr("За {minutes} мин людям ушло {count} капч — похоже, модели перестали отвечать.", minutes=f"{self.cfg.human_alarm_minutes:g}", count=sent)))
        cloud = self.cfg.cloud_daily_limit_usd
        if cloud and self.spend_today()["cloud_usd"] >= cloud:
            out.append(("cloud-limit", tr("Облачные модели: исчерпан дневной лимит ${limit}.", limit=f"{cloud:g}")))
        from .layers.vlm import LAST_ERRORS

        for name, (at, msg) in LAST_ERRORS.items():
            # only an enabled layer is worth a notification; a success pops the entry, so a fresh entry
            # means the model failed and nothing has been solved by it since
            if name not in self.cfg.layers or time.time() - at > MODEL_ERROR_WINDOW:
                continue
            out.append((f"model-error:{name}", tr("Модель {name} ошибается: {error}", name=name[4:], error=plain_error(msg))))
        if self.update and not self.update_job.get("running"):
            out.append((f"update:{self.update['version']}", tr("Вышла версия {version}. Обновить можно в окне программы.", version=self.update["version"])))
        if self.bench.get("finished") and not self.bench.get("running"):
            best = (self.bench.get("rows") or [{}])[0].get("model")
            text = tr("Сравнение моделей готово. Лучшая: {best}.", best=best) if best else tr("Сравнение моделей закончилось.")
            out.append((f"bench:{self.bench['finished']}", self.bench.get("error") or text))
        return out

    def human_alarm(self) -> int:
        """How many captchas reached the workers lately when that is too many, else 0.

        The cheaper layers normally answer almost everything, so a run of paid captchas means one of them broke.
        Acknowledging silences it until the next run.
        """
        window = self.cfg.human_alarm_minutes * 60
        since = max(time.monotonic() - window, self.alarm_quiet_since)
        sent = self.service.chain.sent_since("human", since)
        return sent if self.cfg.human_alarm_count and sent >= self.cfg.human_alarm_count else 0

    def quiet_alarm(self) -> None:
        self.alarm_quiet_since = time.monotonic()

    def start_companions(self) -> list[str]:
        """Starts, hidden, every companion program that is not running; returns the ones it started.

        Only with companion_restart on: otherwise the user decides when they run.
        """
        if not self.cfg.companion_restart:
            return []
        started = []
        for name in self.cfg.companion_apps:
            if subprocess.run(["pgrep", "-qf", f"/{re.escape(name)}\\.app/Contents/MacOS/"]).returncode == 0:
                continue
            if subprocess.run(["open", "-g", "-j", "-a", name]).returncode == 0:
                started.append(name)
        return started

    def check_update(self) -> dict | None:
        from . import update

        if update.running_app() is None:
            return None
        try:
            self.update = update.latest()
        except Exception:  # offline or GitHub refused: the next daily check tries again
            pass
        return self.update

    def install_update(self) -> None:
        """Downloads and installs the found release in the background, then restarts the app."""
        from . import update

        app = update.running_app()
        if app is None:
            raise ValueError(tr("это доступно только в приложении"))
        if not self.update:
            raise ValueError(tr("обновлений нет"))
        if self.update_job.get("running"):
            return
        info = self.update
        self.update_job = {"running": True, "progress": 0.0, "error": ""}

        def work():
            try:
                update.install(info, app, progress=lambda f: self.update_job.update(progress=round(f, 3)))
            except Exception as e:
                self.update_job = {"running": False, "error": str(e)}
                return
            self.update_job = {"running": False, "done": True}
            update.relaunch_later(app)
            if hook := self.hooks.get("quit"):
                hook(None)

        threading.Thread(target=work, daemon=True).start()

    def stop_bench(self) -> None:
        if self.bench.get("running"):
            self.bench["stopping"] = True

    # --- test sets ------------------------------------------------------------------
    def datasets(self) -> list[dict]:
        root = Path(config_mod.APP_DIR) / "testsets"
        out = []
        for name, info in testset.DOWNLOADABLE.items():
            have = testset.count_cases(root / name)
            out.append({"name": name, **{k: v for k, v in info.items() if k != "files"}, "installed": have,
                        "job": self.dataset_jobs.get(name, {})})
        return out

    def download_dataset(self, name: str) -> None:
        if name not in testset.DOWNLOADABLE:
            raise ValueError(tr("набора {name} нет", name=name))
        if self.dataset_jobs.get(name, {}).get("running"):
            raise ValueError(tr("уже скачивается"))
        job = {"running": True, "done": 0, "total": 0}
        self.dataset_jobs[name] = job

        def work():
            try:
                testset.download(name, Path(config_mod.APP_DIR) / "testsets" / name,
                                 progress=lambda d, t: job.update(done=d, total=t))
            except Exception as e:
                job["error"] = str(e)[:300]
            job["running"] = False

        threading.Thread(target=work, daemon=True, name=f"download-{name}").start()

    def download_tiles(self) -> None:
        from .layers import tiles

        if self.tiles_job.get("running"):
            raise ValueError(tr("уже скачивается"))
        job = self.tiles_job = {"running": True, "done": 0, "total": 0}

        def work():
            try:
                tiles.download(progress=lambda d, t: job.update(done=d, total=t))
            except Exception as e:
                job["error"] = str(e)[:300]
            job["running"] = False

        threading.Thread(target=work, daemon=True, name="download-tiles").start()

    def remove_dataset(self, name: str) -> None:
        import shutil

        target = Path(config_mod.APP_DIR) / "testsets" / name
        if name in testset.DOWNLOADABLE and target.exists():
            shutil.rmtree(target)
        self.dataset_jobs.pop(name, None)

    def promote(self, model_names: list[str]) -> None:
        """Puts the given models first among the vision layers, in this order."""
        with self._lock:
            vlm = [f"vlm:{n}" for n in model_names if any(m.name == n for m in self.cfg.models)]
            others = [x for x in self.cfg.layers if x not in vlm]
            insert_at = next((i for i, x in enumerate(others) if x.startswith("vlm") or x == "human"), len(others))
            self.cfg.layers = others[:insert_at] + vlm + others[insert_at:]
            self._apply()

    def use_best(self, model_name: str) -> None:
        """Enables a compared model if needed and puts it first among the vision layers."""
        with self._lock:
            if not any(m.name == model_name for m in self.cfg.models):
                key_name, model_id = model_name.split("/", 1)
                self.toggle_key_model(key_name, model_id)
            self.promote([model_name])

    # --- start at login ---------------------------------------------------------
    def refresh_permissions(self) -> None:
        """Asks macOS in a worker thread what it allows; answers merge into self.permissions as they arrive.

        The ask itself never blocks this thread, whichever thread calls this, and the window reads
        only what has landed, so a slow or missing answer never stalls the menu bar or the server.
        """
        from . import permissions

        def store(values: dict[str, bool | None]) -> None:
            # a system queue calls this: whole values are merged, so a reader never sees halves
            self.permissions.update(values)

        def work():
            store({"background": permissions.background_allowed(LAUNCH_AGENT)})
            permissions.ask_notifications(lambda allowed: store({"notifications": allowed}))

        threading.Thread(target=work, daemon=True, name="captcha-stack-permissions").start()

    def permission_warnings(self) -> list[dict[str, str]]:
        """What macOS blocks, for the window to warn about; reads the last known values only."""
        out = []
        if self.starts_at_login() and self.permissions.get("background") is False:
            out.append({"id": "background", "text": tr("macOS не разрешает приложению работать в фоне — после включения Мака оно не запустится.")})
        if self.permissions.get("notifications") is False:
            out.append({"id": "notifications", "text": tr("Уведомления выключены — приложение не сможет предупредить о неполадках.")})
        return out

    @staticmethod
    def launch_agent_plist() -> dict:
        # inside the bundled app sys.executable is the app itself
        args = [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, "-m", "imnotarobot.menubar"]
        return {"Label": "app.captcha-stack", "ProgramArguments": args, "RunAtLoad": True, "ProcessType": "Interactive"}

    @staticmethod
    def starts_at_login() -> bool:
        return LAUNCH_AGENT.exists()

    def set_start_at_login(self, on: bool) -> None:
        if on:
            LAUNCH_AGENT.parent.mkdir(parents=True, exist_ok=True)
            LAUNCH_AGENT.write_bytes(plistlib.dumps(self.launch_agent_plist()))
        elif LAUNCH_AGENT.exists():
            LAUNCH_AGENT.unlink()
