"""Manual smoke test: does the bundled browser extension really solve through this app?

Launches a throwaway server (temp IMNOTAROBOT_HOME, no secrets, only the free local
OCR layer) and a visible Chromium with imnotarobot/extension loaded unpacked. Then:

1. the extension's options page logs in — the app is found through /app-info on
   127.0.0.1:8765-8774 before the key is ever sent anywhere;
2. on a local, CSP-free page: getConfig answers without secrets, a page-initiated
   solve is refused while auto-solve is off, and goes through once it is on;
3. on https://2captcha.com/demo/normal the extension sends the captcha to our
   in.php/res.php, fills the answer and the page accepts it.

Run it yourself (needs the Chromium that patchright bundles, and network access):

    .venv/bin/python tests/manual/smoke_extension.py
"""

import json
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
EXTENSION = ROOT / "imnotarobot" / "extension"
KEY = "cs-smoke-test-key"

HOME = Path(tempfile.mkdtemp(prefix="imnotarobot-smoke-"))
os.environ["IMNOTAROBOT_HOME"] = str(HOME)  # must land before imnotarobot.config loads
sys.path.insert(0, str(ROOT))  # run against this checkout, not an installed copy

import uvicorn  # noqa: E402
from fastapi.responses import HTMLResponse, Response  # noqa: E402

from imnotarobot.config import Config  # noqa: E402
from imnotarobot.layers.local import LocalLayer  # noqa: E402
from imnotarobot.server import create_app  # noqa: E402


def log(text: str) -> None:
    print(f"[smoke] {text}", flush=True)


def free_port() -> int:
    """The first free port of the app's own range, as Controller.free_port picks it."""
    for port in range(8765, 8775):
        with socket.socket() as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("ports 8765-8774 are all taken")


def imnotarobot_below(port: int) -> bool:
    """Another copy of the app answering /app-info on a lower port would win the discovery race;
    the smoke key would be checked against it. Only a keyless /app-info is ever asked."""
    import httpx

    for other in range(8765, port):
        try:
            r = httpx.get(f"http://127.0.0.1:{other}/app-info", timeout=0.5)
            if r.status_code == 200 and r.json().get("app") == "imnotarobot":
                return True
        except Exception:
            pass
    return False


def start_server(port: int):
    """The app's own server with only the free local layer; no keychain, no cloud keys."""
    cfg = Config(client_key=KEY, port=port, layers=["local"], ocr_min_confidence=0.1,
                 state_path=str(HOME / "state.json"))
    app = create_app(cfg, [LocalLayer(0.1)])

    # a page without Content-Security-Policy: the extension's page-side channel
    # (window.sendMsgToSolverCS) works here, while 2captcha.com's CSP silences it
    @app.get("/demo-page", response_class=HTMLResponse)
    def demo_page():
        return """<!doctype html><html><body style="font: 14px sans-serif">
            <form><img id="local-captcha-image" src="/captcha.png" alt="captcha"><br>
            <input id="local-captcha-field" type="text" name="captcha">
            <button type="submit">Check</button></form></body></html>"""

    @app.get("/captcha.png")
    def captcha_png():
        import io

        from PIL import Image, ImageDraw, ImageFont

        font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 40)
        img = Image.new("RGB", (160, 60), (240, 240, 240))
        ImageDraw.Draw(img).text((10, 8), "smoke", fill=(20, 20, 20), font=font)
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return Response(buf.getvalue(), media_type="image/png")

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            return server
        time.sleep(0.1)
    raise RuntimeError("the throwaway server never started")


def journal_entries() -> list[dict]:
    """What the server itself recorded: proves the captchas really came through in.php."""
    files = sorted((HOME / "journal").glob("*.json"), key=lambda p: p.stat().st_mtime)
    return [json.loads(f.read_text())["summary"] for f in files]


def wait_for_service_worker(ctx, timeout: float = 20.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        for worker in ctx.service_workers:
            if worker.url.startswith("chrome-extension://"):
                return worker
        time.sleep(0.25)
    return None


def launch_with_extension(pw):
    """Chrome sometimes skips --load-extension at startup (a known Chromium race; it either loads
    within a couple of seconds or never does): retry with fresh profiles."""
    for attempt in range(8):
        # Chrome needs a moment to let go of the profile after close() before it can be cleaned up
        with tempfile.TemporaryDirectory(prefix="smoke-profile-", ignore_cleanup_errors=True) as profile:
            # the bundled Chromium for Testing: branded Google Chrome 137+ ignores --load-extension
            ctx = pw.chromium.launch_persistent_context(
                profile, headless=False,
                args=[f"--disable-extensions-except={EXTENSION}", f"--load-extension={EXTENSION}"],
                viewport={"width": 1400, "height": 1000},
            )
            if wait_for_service_worker(ctx, 8) or attempt == 7:
                return ctx
            log(f"Chrome skipped the extension on attempt {attempt + 1}, relaunching")
            ctx.close()


def set_config(worker, patch: dict) -> None:
    worker.evaluate("""async patch => {
        const {config} = await chrome.storage.local.get('config');
        Object.assign(config, patch);
        await chrome.storage.local.set({config});
    }""", patch)


# exactly what a hostile page does: raw DOM messages on the channel the content script polls.
# (patchright evaluates in an isolated world, so window.sendMsgToSolverCS is not even visible here)
DOM_RELAY = """async ([action, data]) => {
    let messages = document.querySelector("body > solver-ext-messages");
    if (!messages) {
        messages = document.createElement("solver-ext-messages");
        messages.style.display = "none";
        document.body.appendChild(messages);
    }
    const msg = document.createElement("solver-ext-message");
    msg.dataset.action = action;
    msg.dataset.messageId = String(Date.now());
    if (data) msg.dataset.data = encodeURIComponent(JSON.stringify(data));
    messages.appendChild(msg);
    const deadline = Date.now() + 15000;
    while (!msg.dataset.response && Date.now() < deadline) await new Promise(r => setTimeout(r, 100));
    return msg.dataset.response ? JSON.parse(decodeURIComponent(msg.dataset.response)) : null;
}"""


def main() -> int:
    if not EXTENSION.is_dir():
        log(f"no extension folder at {EXTENSION}")
        return 1
    port = free_port()
    start_server(port)
    address = f"http://127.0.0.1:{port}"
    # with the app on the first free port of the range, the extension's own discovery (no address
    # typed in) must find it; only another copy of the app on a lower port would win that race
    explicit = imnotarobot_below(port)
    log(f"throwaway server on {address}, access key {KEY}"
        + (", address typed into the options page (another copy of the app holds a lower port)" if explicit
           else ", the extension must find it by itself on 8765-8774"))

    from patchright.sync_api import sync_playwright

    with sync_playwright() as pw:
        ctx = launch_with_extension(pw)
        try:
            return run(ctx, address, explicit)
        finally:
            ctx.close()


def run(ctx, address: str, explicit: bool) -> int:
    worker = wait_for_service_worker(ctx)
    if worker is None:
        log("the extension never started its service worker")
        return 1
    ext_id = worker.url.split("/")[2]
    log(f"extension loaded unpacked, id {ext_id}")

    # 1. the options page: paste the key, press Login — the app is found through /app-info and the
    #    key is checked with res.php?action=userinfo. The tab stays open for the whole test:
    #    its port keeps the service worker from dozing off.
    options = ctx.new_page()
    dialogs = []
    options.on("dialog", lambda d: (dialogs.append(d.message), d.accept()))
    options.goto(f"chrome-extension://{ext_id}/options/options.html")
    options.fill("input[name=apiKey]", KEY)
    if explicit:
        options.fill("input[name=apiService]", address)
    options.click("#connect")
    options.wait_for_timeout(1500)
    if not any("Connected" in m or "подключено" in m.lower() for m in dialogs):
        log(f"Login on the options page failed, dialogs seen: {dialogs}")
        return 1
    log(f"options page login ok: {dialogs[0]!r}")
    stored = worker.evaluate("async () => (await chrome.storage.local.get('config')).config")
    if stored.get("apiKey") != KEY or stored.get("apiService") != address:
        log(f"the extension did not settle on this app: {stored.get('apiKey')!r}, {stored.get('apiService')!r}")
        return 1

    host = f"127.0.0.1:{address.rsplit(':', 1)[1]}"
    set_config(worker, {"isPluginEnabled": True, "enabledForNormal": True,
                        "normalSources": [{"host": host, "image": "//*[@id='local-captcha-image']",
                                           "input": "//*[@id='local-captcha-field']"}]})

    # 2. the local page, first with solving switched off: the captcha must be detected (the button
    #    appears) yet a page-initiated solve must be refused — and getConfig must stay secret-free
    local = ctx.new_page()
    local.goto(f"{address}/demo-page", wait_until="domcontentloaded")
    try:
        local.wait_for_selector(".captcha-solver", timeout=30000)
    except Exception:
        log("the extension never noticed the local captcha: no solve button appeared")
        return 1
    log("the extension detected the local captcha and placed its button (solving still off)")

    page_config = local.evaluate(DOM_RELAY, ["getConfig"])
    if page_config is None or {"apiKey", "apiService", "proxy", "email"} & set(page_config):
        log(f"the page learned secrets through getConfig: {page_config}")
        return 1
    log(f"page-side getConfig is clean: {sorted(page_config)}")
    refusal = local.evaluate(DOM_RELAY, ["solve", {"captchaType": "normal", "widgetId": 0, "params": {"body": "aGk="}}])
    if not (refusal or {}).get("error") or journal_entries():
        log(f"a page-initiated solve was not refused cleanly: {refusal!r}, journal: {journal_entries()}")
        return 1
    log(f"page-initiated solve refused ({refusal['error']}), nothing reached the server")

    # 3. switch on auto-solve and reload as a fresh visit: the extension must solve by itself,
    #    and the page-side channel may then ask for the same captcha and get the code back
    set_config(worker, {"autoSolveNormal": True})
    local.reload(wait_until="domcontentloaded")
    try:
        local.wait_for_function(
            "() => { const i = document.querySelector('#local-captcha-field'); return i && i.value.trim().length > 0; }",
            timeout=120000)
    except Exception:
        log("the local answer field never got filled; journal: " + json.dumps(journal_entries(), ensure_ascii=False))
        return 1
    log("the extension auto-solved the local captcha and filled: "
        f"{local.eval_on_selector('#local-captcha-field', 'el => el.value')!r}")
    relayed = local.evaluate(DOM_RELAY, ["solve", {"captchaType": "normal", "widgetId": 0, "params": {}}])
    if not (relayed or {}).get("response"):
        log(f"a page-initiated solve with auto-solve on did not come back: {relayed!r}")
        return 1
    log(f"page-initiated solve with auto-solve on returned the code: {relayed['response']!r}")
    if len(journal_entries()) != 2 or any(e.get("type") != "ImageToTextTask" for e in journal_entries()):
        log(f"the server journal does not show exactly the two local solves: {journal_entries()}")
        return 1

    # 4. the real thing: 2captcha's normal-captcha demo, solved through our server
    set_config(worker, {"autoSubmitForms": True,
                        "normalSources": [{"host": "2captcha.com", "image": "//*[@id='smoke-captcha-image']",
                                           "input": "//*[@id='simple-captcha-field']"}]})
    demo = ctx.new_page()
    demo.goto("https://2captcha.com/demo/normal", wait_until="domcontentloaded", timeout=60000)
    try:
        demo.wait_for_selector("form img", timeout=60000)
    except Exception:
        log(f"the demo page never showed a captcha; title: {demo.title()!r}")
        return 1
    demo.evaluate("""() => {
        const img = document.querySelector('form img[class*=captchaImage]') || document.querySelector('form img');
        if (!img.id) img.id = 'smoke-captcha-image';
    }""")
    log("demo page is up, waiting for the extension to solve (it polls every 2 s)…")

    try:
        demo.wait_for_function(
            "() => { const i = document.querySelector('#simple-captcha-field'); return i && i.value.trim().length > 0; }",
            timeout=240000)
    except Exception:
        log("the answer field never got filled; server journal: " + json.dumps(journal_entries(), ensure_ascii=False))
        shot = str(HOME / "demo-stuck.png")
        demo.screenshot(path=shot)
        log(f"screenshot: {shot}")
        return 1
    answer = demo.eval_on_selector("#simple-captcha-field", "el => el.value")
    log(f"the extension filled the answer: {answer!r}")

    entry = journal_entries()[-1]
    log("server journal: " + json.dumps({k: entry[k] for k in ("type", "answer", "layer", "seconds", "confidence")},
                                        ensure_ascii=False))
    if entry.get("type") != "ImageToTextTask":
        log("the server never saw the demo captcha — the extension did not use it")
        return 1

    # 5. the extension's default auto-submit rule presses Check on the demo page
    try:
        demo.wait_for_function("() => /Captcha is passed|Incorrect captcha/.test(document.body.innerText)", timeout=30000)
        verdict = demo.evaluate("() => (document.body.innerText.match(/Captcha is passed[^\\n]*|Incorrect captcha[^\\n]*/) || [''])[0]")
    except Exception:
        verdict = "(the demo page gave no verdict in 30 s)"
    log(f"demo page verdict: {verdict}")
    shot = str(HOME / "demo-result.png")
    demo.screenshot(path=shot)
    log(f"screenshot: {shot}")

    ok = "Captcha is passed" in verdict
    log("SMOKE TEST " + ("PASSED" if ok else "DONE (pipeline works, the answer itself was not accepted)"))
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
