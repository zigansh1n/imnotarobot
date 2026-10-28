"""JSON API and page for the settings window.

Every /api/ui call must carry the X-UI-Token header. A web page in a browser cannot add a custom header to a
cross-origin request without a CORS preflight, which this server never approves, so other sites cannot change
settings or read state through the local port. Secrets are never returned, only their hints.
"""

import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from .config import HUMAN_CURRENCY, HUMAN_TOPUP
from .control import HUMAN_TITLES, LAYER_TITLES, money
from .layers import tiles
from .layers.browser import BrowserLayer
from .layers.vlm import LAST_ERRORS
from .providers import NO_EFFORT
from . import i18n
from .i18n import tr

if TYPE_CHECKING:
    from .control import Controller

PAGE = Path(__file__).with_name("assets") / "ui.html"
# ships with the app: the browser extension whose folder the window reveals in Finder
EXTENSION_DIR = Path(__file__).with_name("extension")

TYPE_TITLES = {
    "ImageToTextTask": tr("Текст на картинке"), "GridTask": tr("Сетка картинок"), "CoordinatesTask": tr("Клик по картинке"),
    "SlideMatchTask": tr("Слайдер"), "RotateTask": tr("Поворот"), "RecaptchaV2TaskProxyless": "reCAPTCHA v2",
    "RecaptchaV2Task": "reCAPTCHA v2", "RecaptchaV3TaskProxyless": "reCAPTCHA v3", "HCaptchaTaskProxyless": "hCaptcha",
    "HCaptchaTask": "hCaptcha", "TurnstileTaskProxyless": "Cloudflare Turnstile", "TurnstileTask": "Cloudflare Turnstile",
    "GeeTestTaskProxyless": "GeeTest", "FunCaptchaTaskProxyless": "FunCaptcha", "FunCaptchaTask": "FunCaptcha",
    "DataDomeSliderTask": "DataDome",
}


LAYER_CAN = {"local": tr("текст на картинке, слайдеры · бесплатно, доли секунды"),
             "human": tr("всё, включая reCAPTCHA, hCaptcha, Turnstile · платно, 10–60 секунд"),
             "vlm": tr("текст, сетки картинок, клики · центы, 5–30 секунд"),
             "browser": tr("жмёт галочку, картинки отдаёт моделям выше · бесплатно, 10–60 секунд")}


EFFORT_TITLES = {k: tr(v) for k, v in {"default": "как у поставщика", "none": "нет", "low": "мало", "medium": "средне", "high": "много"}.items()}


def type_title(task_type: str) -> str:
    return TYPE_TITLES.get(task_type, task_type)


def stats(c: "Controller") -> dict[str, Any]:
    titles = {x.name: x.title or x.name for x in c.service.chain.layers}
    rows = []
    for s in c.service.chain.state.as_list():
        task_type, _, comment = s["kind"].partition("|")
        rows.append({"type": type_title(task_type), "comment": comment, "layer": titles.get(s["layer"], s["layer"]),
                     "answered": s["answered"], "bad": s["reported_bad"]})
    days = sorted(c.service.chain.state.daily.items())[-7:]
    history = [{"day": d, "solved": int(sum(v.get("solved", 0) for v in layers.values())),
                "cloud": sum(v.get("spent", v.get("usd", 0.0)) for k, v in layers.items() if k.startswith("vlm:")),
                "human": layers.get("human", {}).get("spent", layers.get("human", {}).get("usd", 0.0))}
               for d, layers in days]
    return {"rows": sorted(rows, key=lambda r: -r["answered"])[:40], "days": history}


def state(c: "Controller") -> dict[str, Any]:
    # one consistent snapshot: background jobs save settings behind this same lock
    with c._lock:
        cfg = c.cfg
        layers = []
        known = {f"vlm:{m.name}": m for m in cfg.models}
        order = list(cfg.layers) + [i for i in ["local", *known, "browser", "human"] if i not in cfg.layers]
        built = {x.name: x for x in c.service.chain.layers}
        for layer_id in order:
            if layer_id == "local":
                title, ready, why = LAYER_TITLES["local"], True, ""
            elif layer_id == "browser":
                title = LAYER_TITLES["browser"]
                ready = BrowserLayer(None).available()
                why = "" if ready else tr("нужен Google Chrome")
            elif layer_id == "human":
                title = tr("Люди ({name})", name=HUMAN_TITLES.get(cfg.human_provider, cfg.human_provider))
                ready = bool(cfg.human_api_key or cfg.human_extra)
                why = "" if ready else tr("нет ключа")
            elif layer_id in known:
                m = known[layer_id]
                title, ready, why = f"{m.model} · {m.key or m.provider}", m.ready(), "" if m.ready() else tr("нет ключа")
            else:
                continue
            can = LAYER_CAN["vlm" if layer_id.startswith("vlm:") else layer_id]
            layers.append({"id": layer_id, "title": title, "enabled": layer_id in cfg.layers, "ready": ready,
                           "why": why, "active": layer_id in built, "can": can})
        day = c.service.today()
        return {
            "paused": c.service.paused, "address": c.address, "startAtLogin": c.starts_at_login(),
            "permissions": c.permission_warnings(),
            "today": {"solved": int(sum(v.get("solved", 0) for v in day.values())),
                      "usd": sum(v.get("usd", 0.0) for v in day.values()), "byLayer": day},
            "layers": layers,
            "keys": [{"name": k.name, "provider": k.provider, "hint": k.hint, "baseUrl": k.base_url, "listable": k.listable, "note": k.note,
                      "models": [{"id": mid, "enabled": on, "inPath": f"vlm:{k.name}/{mid}" in cfg.layers}
                                 for mid, on in c.key_models(k.name)]} for k in cfg.keys],
            "efforts": {m.name: {"recognition": m.effort_recognition, "clicks": m.effort_clicks,
                                 "unsupported": m.name in NO_EFFORT} for m in cfg.models},
            "human": {"provider": cfg.human_provider, "currency": HUMAN_CURRENCY.get(cfg.human_provider, "$"),
                      "hasKey": bool(cfg.human_api_key), "hint": cfg.human_key_hint, "legacyKey": c.legacy_human_key,
                      "limit": cfg.human_daily_limit, "lowBalance": cfg.human_low_balance, "rubPerUsd": cfg.rub_per_usd,
                      "status": c.human_status, "spentToday": c.service.chain.state.spent_today("human"), "topup": HUMAN_TOPUP.get(cfg.human_provider),
                      "note": cfg.human_note,
                      "spares": [{"id": a.id, "provider": a.provider, "title": HUMAN_TITLES.get(a.provider, a.provider), "note": a.note,
                                  "hint": a.hint, "currency": HUMAN_CURRENCY.get(a.provider, "$"), "status": c.spare_status.get(a.id, {}),
                                  "topup": HUMAN_TOPUP.get(a.provider)} for a in cfg.human_extra]},
            "companions": {"apps": cfg.companion_apps, "restart": cfg.companion_restart},
            "cloud": {"limit": cfg.cloud_daily_limit_usd, "spentToday": c.spend_today()["cloud_usd"],
                      "unpriced": [m.name for m in cfg.models if not (m.price_in or m.price_out)]},
            "recent": [{**r, "typeTitle": type_title(r.get("type", ""))} for r in list(c.service.recent)[:50]],
            "stats": stats(c),
            "bench": {k: v for k, v in c.bench.items() if k != "report"},
            "datasets": c.datasets(),
            "access": {"required": cfg.require_client_key, "key": cfg.client_key, "portNote": c.port_note},
            "detecting": c.detecting,
            "modelErrors": {name[4:]: {"at": at, "error": msg} for name, (at, msg) in LAST_ERRORS.items()},
            "modelTests": c.model_tests,
            "toasts": [{"id": i, "text": text, "at": at} for i, (at, text) in c.toasts.items()],
            "benchCases": len(c.bench_cases()),
            "tiles": {"installed": tiles.available(), "sizeMb": tiles.SIZE_MB, "job": c.tiles_job},
            "update": c.update and {**c.update, "job": c.update_job},
            "now": time.time(),
            "lang": i18n.LANG,
        }


def add_ui_routes(app: FastAPI, c: "Controller") -> None:
    from starlette.concurrency import run_in_threadpool

    def guard(request: Request) -> JSONResponse | None:
        if request.headers.get("x-ui-token") != c.ui_token:
            return JSONResponse({"ok": False, "error": tr("нет доступа")}, status_code=403)
        return None

    async def body(request: Request) -> dict[str, Any]:
        try:
            return await request.json()
        except ValueError:
            return {}

    def done(message: str, **extra) -> dict[str, Any]:
        # without the state: act() adds it off the event loop, so a slow save never stalls captcha traffic
        return {"ok": True, "message": message, **extra}

    async def failed(e: Exception) -> JSONResponse:
        return JSONResponse({"ok": False, "error": str(e), "state": await run_in_threadpool(state, c)}, status_code=400)

    @app.post("/app/open")
    async def open_from_another_app(request: Request):
        """Other programs on this Mac (spros and the like) bring the window up; they send the access key."""
        b = await body(request)
        if c.cfg.require_client_key and b.get("clientKey") != c.cfg.client_key:
            return JSONResponse({"ok": False, "error": tr("нет доступа")}, status_code=403)
        hook = c.hooks.get("open-window")
        if hook is None:
            return JSONResponse({"ok": False, "error": tr("это доступно только в приложении")}, status_code=409)
        hook(str(b.get("view") or "today"))
        return {"ok": True}

    @app.get("/ui", response_class=HTMLResponse)
    def page():
        return FileResponse(PAGE)

    @app.get("/api/ui/state")
    def get_state(request: Request):
        return guard(request) or state(c)

    @app.get("/api/ui/journal/{entry_id}")
    def journal(entry_id: int, request: Request):
        denied = guard(request)
        if denied:
            return denied
        entry = c.service.journal_entry(entry_id)
        if entry is None:
            return JSONResponse({"ok": False, "error": tr("запись не найдена")}, status_code=404)
        task = entry["task"]
        images = {k: task[k] for k in ("body", "target", "background", "imgInstructions") if task.get(k)}
        details = {k: v for k, v in task.items() if k not in images}
        return {"ok": True, "summary": {**entry["summary"], "typeTitle": type_title(task.get("type", ""))},
                "images": images, "task": details, "solution": entry.get("solution"), "meta": entry.get("meta", {})}

    @app.post("/api/ui/{action}")
    async def act(action: str, request: Request):
        denied = guard(request)
        if denied:
            return denied
        b = await body(request)
        try:
            r = await run_action(action, b)
        except Exception as e:
            return await failed(e)
        if isinstance(r, dict) and "state" not in r:
            r["state"] = await run_in_threadpool(state, c)
        return r

    async def run_action(action: str, b: dict[str, Any]) -> Any:
        if action == "share-results":
            from . import share

            if not c.bench.get("rows"):
                raise ValueError(tr("сначала сравните модели"))
            providers = {k.name: k.provider for k in c.cfg.keys}
            await run_in_threadpool(subprocess.run, ["open", share.issue_url(share.report(c.bench, providers))], check=False)
            return done(tr("Открыл заготовку на GitHub: проверьте и нажмите «Submit new issue»."))
        if action == "update":
            c.install_update()
            return done(tr("Скачиваю версию {version}. Программа перезапустится сама.", version=c.update["version"]))
        if action == "open-url":
            url = str(b.get("url", ""))
            if not url.startswith(("https://", "http://", "x-apple.systempreferences:")):
                raise ValueError(tr("нужна ссылка"))
            await run_in_threadpool(subprocess.run, ["open", url], check=False)
            return done("")
        if action == "panel-size":
            hook = c.hooks.get("panel-size")
            if hook:
                hook(int(b.get("height") or 0))
            return done("")
        if action in ("open-window", "quit"):
            hook = c.hooks.get(action)
            if hook is None:
                raise ValueError(tr("это доступно только в приложении"))
            hook(b.get("view") or "")
            return done("")
        if action == "add-key":
            entry = await run_in_threadpool(c.add_key, b.get("secret", ""), b.get("baseUrl", ""))
            n = len(c.key_models(entry.name))
            where = tr("посредник") if entry.provider == "custom" else tr("поставщик")
            hint = tr("моделей с картинками: {n}", n=n) if entry.listable else tr("список моделей недоступен — впишите модель вручную")
            on = [m.model for m in c.cfg.models if m.key == entry.name]
            if on:
                hint = tr("включены {models} — можно решать капчи", models=", ".join(on))
            return done(tr("Ключ добавлен: {where} {name}, {hint}.", where=where, name=entry.name, hint=hint), key=entry.name)
        if action == "refresh-key":
            entry = await run_in_threadpool(c.refresh_key, b["name"])
            return done(tr("Список моделей {name} обновлён: {n} с картинками.", name=entry.name, n=len(c.key_models(entry.name))))
        if action == "remove-key":
            await run_in_threadpool(c.remove_key, b["name"])
            return done(tr("Ключ {name} удалён вместе с его моделями.", name=b["name"]))
        if action == "toggle-model":
            await run_in_threadpool(c.toggle_key_model, b["key"], b["model"])
            on = any(m.key == b["key"] and m.model == b["model"] for m in c.cfg.models)
            if on:
                c.check_model_health(b["key"], b["model"])
            return done(tr("{model}: включена." if on else "{model}: выключена.", model=b["model"]))
        if action == "manual-model":
            model = b.get("model", "").strip()
            if not model:
                raise ValueError(tr("впишите название модели"))

            def add_manual():
                with c._lock:
                    entry = c.key(b["key"])
                    if not any(mid == model for mid, *_ in entry.models):
                        entry.models.append([model, True, 0.0, 0.0])
                    c.toggle_key_model(b["key"], model)

            await run_in_threadpool(add_manual)
            c.check_model_health(b["key"], model)
            return done(tr("{model} добавлена и включена.", model=model))
        if action == "toggle-layer":
            await run_in_threadpool(c.toggle_layer, b["id"])
            return done(tr("Слои обновлены."))
        if action == "move-layer":
            await run_in_threadpool(c.move_layer, b["id"], int(b.get("step", 0)))
            return done(tr("Порядок обновлён."))
        cur = HUMAN_CURRENCY.get(c.cfg.human_provider, "$")
        if action == "human-key":
            key = str(b.get("key") or "").strip()
            if not key:
                raise ValueError(tr("вставьте ключ"))
            await run_in_threadpool(c.set_human, key=key)
            st = await run_in_threadpool(c.check_human)
            if not st["ok"]:
                raise ValueError(tr("ключ сохранён, но {service} его не принял: {error}", service=c.cfg.human_provider, error=st["error"]))
            return done(tr("Ключ работает, баланс {money}.", money=money(st["balance"], st.get("currency") or HUMAN_CURRENCY.get(c.cfg.human_provider, "$"))))
        if action == "human-note":
            await run_in_threadpool(c.set_human, note=str(b.get("note") or ""))
            return done(tr("Подпись сохранена."))
        if action == "spare-add":
            account = await run_in_threadpool(c.add_human_account, str(b.get("provider") or ""), str(b.get("key") or ""), str(b.get("note") or ""))
            st = await run_in_threadpool(c.check_human_account, account.id)
            title = HUMAN_TITLES.get(account.provider, account.provider)
            if not st["ok"]:
                raise ValueError(tr("ключ сохранён, но {service} его не принял: {error}", service=title, error=st["error"]))
            return done(tr("Запасной ключ {service} работает, баланс {money}.", service=title,
                           money=money(st["balance"], HUMAN_CURRENCY.get(account.provider, "$"))))
        if action == "spare-check":
            account = c.human_account(str(b.get("id") or ""))
            st = await run_in_threadpool(c.check_human_account, account.id)
            if not st["ok"]:
                raise ValueError(st["error"])
            return done(tr("Баланс {money}.", money=money(st["balance"], HUMAN_CURRENCY.get(account.provider, "$"))))
        if action == "spare-note":
            await run_in_threadpool(c.set_human_account_note, str(b.get("id") or ""), str(b.get("note") or ""))
            return done(tr("Подпись сохранена."))
        if action == "spare-remove":
            await run_in_threadpool(c.remove_human_account, str(b.get("id") or ""))
            return done(tr("Запасной ключ удалён."))
        if action == "key-note":
            await run_in_threadpool(c.set_key_note, str(b.get("name") or ""), str(b.get("note") or ""))
            return done(tr("Подпись сохранена."))
        if action == "companion-restart":
            await run_in_threadpool(c.set_companion_restart, bool(b.get("on")))
            return done(tr("{apps}: приложение держит запущенным.", apps=", ".join(c.cfg.companion_apps)) if c.cfg.companion_restart
                        else tr("{apps}: приложение больше не запускает само.", apps=", ".join(c.cfg.companion_apps)))
        if action == "human-remove-key":
            await run_in_threadpool(c.set_human, key="")
            return done(tr("Ключ людей удалён, слой людей выключен."))
        if action == "human-provider":
            await run_in_threadpool(c.set_human, provider=b["provider"])
            msg = tr("Сервис: {service}. Валюта: {currency}.", service=HUMAN_TITLES.get(c.cfg.human_provider), currency=HUMAN_CURRENCY.get(c.cfg.human_provider))
            if c.cfg.human_api_key:
                st = await run_in_threadpool(c.check_human)
                msg += tr(" Ключ подходит.") if st["ok"] else tr(" Этот ключ к нему не подходит — вставьте ключ этого сервиса.")
            return done(msg)
        if action == "limits":
            def number(name: str) -> float | None:
                v = b.get(name)
                if v in (None, ""):
                    return None
                try:
                    v = float(str(v).replace(",", "."))
                except ValueError:
                    raise ValueError(tr("нужно число"))
                if v < 0:
                    raise ValueError(tr("число не может быть отрицательным"))
                return v

            human, low, cloud, rate = number("human"), number("low"), number("cloud"), number("rate")
            if rate is not None and rate <= 0:
                raise ValueError(tr("курс должен быть больше нуля"))

            def apply_limits():
                with c._lock:
                    if human is not None:
                        c.cfg.human_daily_limit = human
                    if low is not None:
                        c.cfg.human_low_balance = low
                    if cloud is not None:
                        c.cfg.cloud_daily_limit_usd = cloud
                    if rate is not None:
                        c.cfg.rub_per_usd = rate
                    c._apply()

            await run_in_threadpool(apply_limits)
            h = c.cfg.human_daily_limit
            def per_day(v, cur):
                return tr("без лимита") if not v else tr("{money} в день", money=money(v, cur))

            parts = [tr("люди — {value}", value=per_day(h, HUMAN_CURRENCY.get(c.cfg.human_provider, "$"))),
                     tr("модели — {value}", value=per_day(c.cfg.cloud_daily_limit_usd, "$"))]
            # the rate only matters when a spare account bills in another currency
            if any(HUMAN_CURRENCY.get(a.provider, "$") != cur for a in c.cfg.human_extra):
                parts.append(tr("курс — {value}", value=f"{c.cfg.rub_per_usd:g} ₽/$"))
            return done(tr("Лимиты сохранены: {parts}.", parts=", ".join(parts)))
        if action == "check-human":
            st = await run_in_threadpool(c.check_human)
            if not st["ok"]:
                raise ValueError(st["error"])
            return done(tr("Баланс {money}.", money=money(st["balance"], st.get("currency") or cur)))
        if action == "pause":
            c.set_paused(bool(b.get("paused")))
            return done(tr("Решатель на паузе.") if c.service.paused else tr("Решатель работает."))
        if action == "login":
            c.set_start_at_login(bool(b.get("on")))
            return done(tr("Будет запускаться при входе.") if c.starts_at_login() else tr("Автозапуск выключен."))
        if action == "bench-estimate":
            specs = await run_in_threadpool(c.bench_specs, b.get("key"))
            runs, usd = await run_in_threadpool(c.bench_estimate, specs)
            return done("", models=len(specs), runs=runs, usd=usd)
        if action == "bench-start":
            await run_in_threadpool(c.start_bench, b.get("key"))
            return done(tr("Сравнение запущено."))
        if action == "report-wrong":
            try:
                await c.service.report_entry(int(b["id"]))
            except KeyError:
                raise ValueError(tr("у этой капчи нет ответа, сообщать нечего"))
            return done(tr("Записал: этот слой на такой картинке больше не используется. Если решали люди — сервису отправлена жалоба."))
        if action == "copy-key":
            await run_in_threadpool(subprocess.run, ["pbcopy"], input=c.cfg.client_key.encode(), check=False)
            return done(tr("Ключ доступа скопирован."))
        if action == "open-extension-folder":
            if not EXTENSION_DIR.is_dir():
                raise ValueError(tr("в этой сборке нет папки расширения"))
            await run_in_threadpool(subprocess.run, ["open", "-R", str(EXTENSION_DIR)], check=False)
            return done(tr("Открыл папку расширения в Finder."))
        if action == "copy-address":
            await run_in_threadpool(subprocess.run, ["pbcopy"], input=c.address.encode(), check=False)
            return done(tr("Адрес {address} скопирован.", address=c.address))
        if action == "access":
            await run_in_threadpool(c.set_access, b.get("required"), bool(b.get("regenerate")))
            if b.get("regenerate"):
                return done(tr("Новый ключ доступа создан. Старый больше не работает — обновите его в своих программах."))
            return done(tr("Программы должны присылать ключ доступа.") if c.cfg.require_client_key
                        else tr("Ключ доступа выключен: решатель примет запрос от любой программы на этом Маке."))
        if action == "check-connection":
            r = await run_in_threadpool(c.check_connection)
            if not r["ok"]:
                raise ValueError(tr("программа получила бы ошибку: {error}", error=r["error"]))
            verdict = tr("ответ верный") if r["correct"] else tr("ответ неверный")
            return done(tr("Подключение работает: тестовую капчу решил слой «{layer}» за {seconds} с, {verdict}.", layer=r["layer"], seconds=r["seconds"], verdict=verdict))
        if action == "test-model":
            r = await run_in_threadpool(c.test_model, b["key"], b["model"])
            if r["error"] and not r["ok"]:
                raise ValueError(f"{b['model']}: {r['error']}")
            return done(tr("{model}: решено {ok} из {n}, в среднем {seconds} с на капчу.", model=b["model"], ok=r["ok"], n=r["n"], seconds=r["seconds"]))
        if action == "set-effort":
            await run_in_threadpool(c.set_effort, b["key"], b["model"], b["kind"], b["level"])
            what = tr("распознавания") if b["kind"] == "recognition" else tr("кликов")
            return done(tr("{model}: раздумья для {what} — {level}.", model=b["model"], what=what, level=EFFORT_TITLES[b["level"]]))
        if action == "bench-efforts-estimate":
            specs = await run_in_threadpool(c.effort_specs, b["key"], b["model"])
            runs, usd = await run_in_threadpool(c.bench_estimate, specs)
            return done("", models=len(specs), runs=runs, usd=usd)
        if action == "bench-efforts":
            specs = await run_in_threadpool(c.effort_specs, b["key"], b["model"])
            await run_in_threadpool(c.start_bench, None, specs, label=tr("{model} на разных уровнях раздумий", model=b["model"]))
            return done(tr("Сравнение уровней раздумий запущено."))
        if action == "bench-stop":
            c.stop_bench()
            return done(tr("Останавливаю: дорешаю начатые капчи и покажу то, что успели."))
        if action == "tiles-download":
            c.download_tiles()
            return done(tr("Скачиваю модель клеток — ход виден в разделе «Путь капчи»."))
        if action == "tiles-remove":
            tiles.remove()
            c.tiles_job = {}
            return done(tr("Модель клеток удалена: сетки reCAPTCHA снова решает облачная модель."))
        if action == "dataset-download":
            c.download_dataset(b["name"])
            return done(tr("Скачиваю набор — прогресс виден в разделе сравнения."))
        if action == "dataset-remove":
            c.remove_dataset(b["name"])
            return done(tr("Набор удалён."))
        if action == "use-best":
            await run_in_threadpool(c.use_best, b["name"])
            return done(tr("{name} теперь первая среди моделей.", name=b["name"]))
        raise ValueError(tr("неизвестное действие {action}", action=action))
