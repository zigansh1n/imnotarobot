"""Manual smoke test: does the bundled browser extension really solve through this app?

Launches a throwaway server (temp IMNOTAROBOT_HOME, no secrets, only the free local
OCR layer), a visible Chrome with imnotarobot/extension loaded unpacked, then
https://2captcha.com/demo/normal. The extension must send the captcha to our
in.php/res.php and fill the answer into the page.

Run it yourself (needs Google Chrome and network access):

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

from imnotarobot.config import Config  # noqa: E402
from imnotarobot.layers.local import LocalLayer  # noqa: E402
from imnotarobot.server import create_app  # noqa: E402


def log(text: str) -> None:
    print(f"[smoke] {text}", flush=True)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_server(port: int):
    """The app's own server with only the free local layer; no keychain, no cloud keys."""
    cfg = Config(client_key=KEY, port=port, layers=["local"], ocr_min_confidence=0.1,
                 state_path=str(HOME / "state.json"))
    app = create_app(cfg, [LocalLayer(0.1)])
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            return server
        time.sleep(0.1)
    raise RuntimeError("the throwaway server never started")


def journal_entry() -> dict | None:
    """What the server itself recorded: proves the captcha really came through in.php."""
    files = sorted((HOME / "journal").glob("*.json"), key=lambda p: p.stat().st_mtime)
    return json.loads(files[-1].read_text())["summary"] if files else None


def wait_for_service_worker(ctx, timeout: float = 20.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        for worker in ctx.service_workers:
            if worker.url.startswith("chrome-extension://"):
                return worker
        time.sleep(0.25)
    return None


def launch_with_extension(pw):
    """Chrome sometimes skips --load-extension at startup (a known Chromium race): retry a few times."""
    for attempt in range(4):
        with tempfile.TemporaryDirectory(prefix="smoke-profile-") as profile:
            # the bundled Chromium for Testing: branded Google Chrome 137+ ignores --load-extension
            ctx = pw.chromium.launch_persistent_context(
                profile, headless=False,
                args=[f"--disable-extensions-except={EXTENSION}", f"--load-extension={EXTENSION}"],
                viewport={"width": 1400, "height": 1000},
            )
            if wait_for_service_worker(ctx) or attempt == 3:
                return ctx
            log(f"Chrome skipped the extension on attempt {attempt + 1}, relaunching")
            ctx.close()


def main() -> int:
    if not EXTENSION.is_dir():
        log(f"no extension folder at {EXTENSION}")
        return 1
    port = free_port()
    start_server(port)
    address = f"http://127.0.0.1:{port}"
    log(f"throwaway server on {address}, access key {KEY}")

    from patchright.sync_api import sync_playwright

    with sync_playwright() as pw:
        ctx = launch_with_extension(pw)
        try:
            return run(ctx, address)
        finally:
            ctx.close()


def run(ctx, address: str) -> int:
    worker = wait_for_service_worker(ctx)
    if worker is None:
        log("the extension never started its service worker")
        return 1
    ext_id = worker.url.split("/")[2]
    log(f"extension loaded unpacked, id {ext_id}")

    # 1. the options page: paste the key and the address, press Login — the res.php userinfo round trip.
    #    The tab stays open for the whole test: its port keeps the service worker from dozing off.
    options = ctx.new_page()
    dialogs = []
    options.on("dialog", lambda d: (dialogs.append(d.message), d.accept()))
    options.goto(f"chrome-extension://{ext_id}/options/options.html")
    options.fill("input[name=apiKey]", KEY)
    options.fill("input[name=apiService]", address)
    options.click("#connect")
    options.wait_for_timeout(1500)
    if not any("Connected" in m or "подключено" in m.lower() for m in dialogs):
        log(f"Login on the options page failed, dialogs seen: {dialogs}")
        return 1
    log(f"options page login ok: {dialogs[0]!r}")
    stored = worker.evaluate("async () => (await chrome.storage.local.get('config')).config")
    if stored.get("apiKey") != KEY or stored.get("apiService") != address:
        log(f"key/address not saved by the options page: {stored.get('apiKey')!r}, {stored.get('apiService')!r}")
        return 1

    # 2. everything the options page has no fields for: solve normal captchas on sight, submit the form
    worker.evaluate("""async patch => {
        const {config} = await chrome.storage.local.get('config');
        Object.assign(config, patch);
        await chrome.storage.local.set({config});
    }""", {"autoSolveNormal": True, "autoSubmitForms": True, "isPluginEnabled": True, "enabledForNormal": True,
           "normalSources": [{"host": "2captcha.com", "image": "//*[@id='smoke-captcha-image']",
                              "input": "//*[@id='simple-captcha-field']"}]})

    # 3. the demo page: a normal captcha the extension must catch, send to our server and fill
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
        log("the answer field never got filled; server journal: " + json.dumps(journal_entry(), ensure_ascii=False))
        shot = str(HOME / "demo-stuck.png")
        demo.screenshot(path=shot)
        log(f"screenshot: {shot}")
        return 1
    answer = demo.eval_on_selector("#simple-captcha-field", "el => el.value")
    log(f"the extension filled the answer: {answer!r}")

    entry = journal_entry()
    log("server journal: " + (json.dumps({k: entry[k] for k in ("type", "answer", "layer", "seconds", "confidence")},
                                         ensure_ascii=False) if entry else "nothing"))
    if entry is None or entry.get("type") != "ImageToTextTask":
        log("the server never saw the captcha — the extension did not use it")
        return 1

    # 4. the extension's default auto-submit rule presses Check on the demo page
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
