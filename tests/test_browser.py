import asyncio
import os

import pytest

from imnotarobot.config import Config, ModelSpec
from imnotarobot.layers import Unavailable, Unsolvable
from imnotarobot.layers.browser import BrowserLayer, ask_models, proxy_of, widget_page
from imnotarobot.service import build_layers
from helpers import FakeLayer


def test_widget_page_escapes_the_key_and_picks_the_script():
    page = widget_page("recaptcha", {"websiteKey": 'k"><script>'})
    assert "&quot;&gt;&lt;script&gt;" in page and "recaptcha/api.js" in page
    assert "js.hcaptcha.com" in widget_page("hcaptcha", {"websiteKey": "k"})
    assert 'data-action="login"' in widget_page("turnstile", {"websiteKey": "k", "action": "login"})
    assert "render=k" in widget_page("recaptcha3", {"websiteKey": "k"})
    assert "grecaptcha.execute()" in widget_page("recaptcha", {"websiteKey": "k", "isInvisible": True})
    with pytest.raises(Unsolvable):
        widget_page("hcaptcha", {})


def test_proxy_is_passed_to_the_browser():
    assert proxy_of({}) is None
    assert proxy_of({"proxyType": "socks5", "proxyAddress": "1.2.3.4", "proxyPort": 1080, "proxyLogin": "u",
                     "proxyPassword": "p"}) == {"server": "socks5://1.2.3.4:1080", "username": "u", "password": "p"}


def test_models_are_asked_in_order_and_a_silent_service_is_skipped():
    class Grid(FakeLayer):
        async def solve(self, task):
            r = await super().solve(task)
            r.solution = {"click": [int(r.solution["text"])]}
            return r

    low = Grid("low", [Unavailable("All accounts failed")], types=("GridTask",))
    high = Grid("high", [("2", 1.0)], types=("GridTask",))
    other = Grid("other", [("3", 1.0)], types=("GridTask",))
    low.upstream = high.upstream = "antigravity"
    assert asyncio.run(ask_models([low, high, other], {"type": "GridTask"})) == {"click": [3]}
    assert high.calls == 0


def test_browser_layer_gets_every_enabled_model():
    cfg = Config(layers=["local", "browser", "vlm:g", "human"],
                 models=[ModelSpec(name="g", provider="gemini", api_key="k")])
    layers = build_layers(cfg)
    browser = next(x for x in layers if x.name == "browser")
    assert browser.models is not None and browser.supports("RecaptchaV3TaskProxyless")
    assert not browser.supports("ImageToTextTask")


@pytest.mark.skipif(not os.environ.get("CAPTCHA_LIVE"), reason="CAPTCHA_LIVE=1 opens Chrome against official test keys")
@pytest.mark.parametrize("task", [
    {"type": "TurnstileTaskProxyless", "websiteURL": "https://example.com/", "websiteKey": "1x00000000000000000000AA"},
    {"type": "HCaptchaTaskProxyless", "websiteURL": "https://example.com/", "websiteKey": "10000000-ffff-ffff-ffff-000000000001"},
])
def test_live_test_keys_give_a_token(task):
    r = asyncio.run(BrowserLayer(None).solve(task))
    assert r.solution["token"]


def test_tile_classifier_answers_only_when_sure(monkeypatch, tmp_path):
    import io

    import numpy as np
    from PIL import Image

    from imnotarobot.layers import tiles

    buf = io.BytesIO()
    Image.new("RGB", (300, 300), (120, 120, 120)).save(buf, "PNG")
    bus = tiles.CLASSES.index("bus")

    class Session:
        def __init__(self, p):
            self.p = p

        def get_inputs(self):
            return [type("I", (), {"name": "images"})]

        def run(self, _, feed):
            assert feed["images"].shape == (9, 3, 640, 640)
            out = np.zeros((9, len(tiles.CLASSES)), dtype=np.float32)
            out[:, bus] = self.p
            return [out]

    sure = [0.95, 0.01, 0.02, 0.9, 0.03, 0.0, 0.0, 0.99, 0.05]
    assert tiles.TileClassifier(Session(sure)).answer(buf.getvalue(), 3, 3, "Select all images with a bus") == [1, 4, 8]
    unsure = sure[:4] + [0.5] + sure[5:]
    assert tiles.TileClassifier(Session(unsure)).answer(buf.getvalue(), 3, 3, "Select all images with a bus") is None
    assert tiles.TileClassifier(Session(sure)).answer(buf.getvalue(), 3, 3, "Select all images with boats") is None


def test_hands_stop_when_the_model_declines(monkeypatch):
    from imnotarobot.layers import hands
    from imnotarobot.providers import ModelSpec

    replies = iter(['{"action": "click", "point": [500, 500]}', "I can't help with bypassing CAPTCHAs."])

    async def fake_ask(*a, **k):
        return next(replies), {}

    monkeypatch.setattr(hands, "ask", fake_ask)
    done = []

    async def shoot():
        return b"png", (10.0, 20.0, 200.0, 100.0)

    async def act(action, box):
        done.append(hands.to_page(action["point"], box))

    async def passed():
        return False

    async def instruction():
        return "Click the egg-laying animal"

    ok = asyncio.run(hands.Hands(ModelSpec(name="m", provider="gemini", api_key="k")).solve(shoot, act, passed, instruction))
    assert ok is False and done == [(110.0, 70.0)]


def test_tile_model_download_reports_progress_and_rejects_a_bad_file(monkeypatch, tmp_path):
    import httpx

    from imnotarobot import config
    from imnotarobot.layers import tiles

    monkeypatch.setattr(config, "APP_DIR", tmp_path)
    payload = b"x" * (3 << 20)
    real_stream = httpx.stream

    def fake_stream(method, url, **kw):
        transport = httpx.MockTransport(lambda r: httpx.Response(200, content=payload, headers={"content-length": str(len(payload))}))
        return httpx.Client(transport=transport).stream(method, url)

    monkeypatch.setattr(httpx, "stream", fake_stream)
    seen = []
    tiles.download(progress=lambda d, t: seen.append((d, t)), verify=lambda p: None)
    assert tiles.available() and seen[-1] == (len(payload), len(payload))
    tiles.remove()
    assert not tiles.available()

    def bad(_):
        raise ValueError("not a model")

    with pytest.raises(ValueError):
        tiles.download(verify=bad)
    assert not tiles.available() and not list((tmp_path / "models").glob("*.part"))
    monkeypatch.setattr(httpx, "stream", real_stream)


def test_browser_failures_are_short_and_in_plain_russian():
    from imnotarobot.layers.browser import short_error

    assert short_error(RuntimeError("Timeout 30000ms exceeded.")) == "не дождался ответа за 30 с"
    assert short_error(RuntimeError("Locator.click: Timeout 45000ms exceeded.\n=== logs ===")) == "не дождался ответа за 45 с"
    assert short_error(RuntimeError("net::ERR_CONNECTION_REFUSED at example.com")) == "net::ERR_CONNECTION_REFUSED at example.com"
