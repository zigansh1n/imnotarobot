import asyncio
import socket
import sys

import pytest
from PIL import Image

from imnotarobot.layers import Unsolvable
from imnotarobot.layers.browser import CHROME_PATHS, BrowserLayer
from imnotarobot.layers.profile import contact_sheet, looks_same, same_site, slider_shift
from helpers import png_b64

SHOP = "https://shop.example/"

# the captcha page posts to its parent like the real one; the parent sets the cookie and drops the frame
SHOP_PAGE = """<!doctype html><html><body><h1>shop</h1>
<script>document.cookie = 'datadome=old; path=/';
addEventListener('message', e => { if (e.data === 'passed') { document.cookie = 'datadome=fresh; path=/';
document.querySelector('iframe').remove(); } });</script>
<iframe src="https://geo.captcha-delivery.com/captcha/?initialCid=x" width="400" height="300"></iframe></body></html>"""

DD_FRAME = """<!doctype html><html><body style="margin:0">
<div id="captcha__frame" class="simple"><div id="captcha__puzzle"><canvas width="280" height="0"></canvas>
<canvas width="280" height="0" class="block"></canvas></div>
<div class="sliderContainer" style="position:relative;width:280px;height:40px;background:#ddd">
<div class="sliderTarget" style="position:absolute;left:220px;width:60px;height:40px"></div>
<div class="slider" style="position:absolute;left:0;width:60px;height:40px;background:#09f"></div></div></div>
<script>let down = false;
addEventListener('mousedown', () => down = true);
addEventListener('mouseup', e => { if (down && e.clientX > 220) parent.postMessage('passed', '*'); });</script>
</body></html>"""

BLOCKED_FRAME = """<!doctype html><html><body><p class="captcha__human__title">Access is temporarily restricted</p>
<ul><li>Automated (bot) activity on your network (IP 203.0.113.7)</li></ul><button id="show-human-auth">Submit feedback.</button>
</body></html>"""

ARKOSE_PAGE = """<!doctype html><html><body><input name="fc-token" value="">
<script>addEventListener('message', e => document.querySelector('input').value = e.data);</script>
<iframe src="https://client-api.arkoselabs.com/fc/game" width="320" height="320"></iframe></body></html>"""

COLORS = ["red", "green", "blue", "orange"]
ARKOSE_GAME = """<!doctype html><html><body style="margin:0">
<p>Use the arrows to pick the blue square</p>
<div id="pic" style="width:200px;height:200px"></div>
<button aria-label="Navigate to next image" onclick="next()">&gt;</button><button onclick="submit()">Submit</button>
<script>const colors = %s; let i = 0;
const show = () => document.getElementById('pic').style.background = colors[i];
function next() { i = (i + 1) %% colors.length; show(); }
function submit() { parent.postMessage('tok|r=eu-west-1|meta=3|picked=' + colors[i], '*'); document.body.innerHTML = ''; }
show();</script></body></html>""" % COLORS


def test_same_site_compares_registrable_domains():
    assert same_site("https://www.etsy.com/listing/1", "https://etsy.com/")
    assert not same_site("https://www.etsy.com/", "https://www.seloger.com/")
    assert not same_site("", "https://etsy.com/")


def test_slider_shift_finds_the_gap_from_the_piece_canvas():
    import numpy as np

    bg = Image.fromarray(np.random.default_rng(1).integers(0, 255, (155, 280, 3), dtype=np.uint8)).resize((280, 155))
    piece_src = bg.crop((150, 50, 190, 90))
    # the gap is the same picture, darkened
    bg.paste(piece_src.point(lambda v: v // 2), (150, 50))
    piece = Image.new("RGBA", (280, 155), (0, 0, 0, 0))
    piece.paste(piece_src, (6, 50))
    assert abs(slider_shift(piece, bg) - 144) <= 2


def test_gallery_helpers():
    import base64

    red, red2, blue = (base64.b64decode(png_b64(Image.new("RGB", (50, 50), c))) for c in ("red", (250, 2, 0), "blue"))
    assert looks_same(red, red2) and not looks_same(red, blue)
    sheet = Image.open(__import__("io").BytesIO(contact_sheet([red, blue, red], 2)))
    assert sheet.size == (100, 100)


def test_without_cdp_endpoint_the_task_goes_on_to_the_next_layer():
    layer = BrowserLayer(None)
    assert layer.supports("DataDomeSliderTask") and layer.supports("FunCaptchaTaskProxyless")
    with pytest.raises(Unsolvable, match="cdpEndpoint"):
        asyncio.run(layer.solve({"type": "DataDomeSliderTask", "websiteURL": SHOP}))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def chrome_missing() -> bool:
    try:
        import patchright  # noqa: F401
    except ImportError:
        return True
    return sys.platform == "darwin" and not any(p.exists() for p in CHROME_PATHS)


async def in_client_browser(pages: dict[str, str], task: dict, models=None) -> tuple[dict, str]:
    """A Chrome with a DevTools port stands in for the client's anti-detect profile."""
    from patchright.async_api import async_playwright

    port = free_port()
    async with async_playwright() as pw:
        channel = "chrome" if any(p.exists() for p in CHROME_PATHS) else None
        browser = await pw.chromium.launch(channel=channel, headless=True, args=[f"--remote-debugging-port={port}"])
        try:
            ctx = await browser.new_context()
            def serve(body):
                return lambda route: route.fulfill(status=200, content_type="text/html", body=body)

            for prefix, body in pages.items():
                await ctx.route(lambda u, p=prefix: u.startswith(p), serve(body))
            page = await ctx.new_page()
            await page.goto(next(iter(pages)))
            await asyncio.sleep(1)
            layer = BrowserLayer(models, timeout=40)
            r = await layer.solve({**task, "cdpEndpoint": f"http://127.0.0.1:{port}", "websiteURL": next(iter(pages))})
            # the client's tab must still be there after we disconnect
            return r.solution, await page.evaluate("() => location.href")
        finally:
            await browser.close()


@pytest.mark.skipif(chrome_missing(), reason="needs Chrome or the patchright browser")
def test_datadome_slider_is_dragged_in_the_clients_tab():
    solution, href = asyncio.run(in_client_browser(
        {SHOP: SHOP_PAGE, "https://geo.captcha-delivery.com/captcha/": DD_FRAME}, {"type": "DataDomeSliderTask"}))
    assert solution["cookie"] == "datadome=fresh" and solution["userAgent"] and href == SHOP


@pytest.mark.skipif(chrome_missing(), reason="needs Chrome or the patchright browser")
def test_datadome_ip_block_is_reported_plainly():
    with pytest.raises(Unsolvable, match="203.0.113.7"):
        asyncio.run(in_client_browser(
            {SHOP: SHOP_PAGE, "https://geo.captcha-delivery.com/captcha/": BLOCKED_FRAME}, {"type": "DataDomeSliderTask"}))


@pytest.mark.skipif(chrome_missing(), reason="needs Chrome or the patchright browser")
def test_arkose_gallery_is_walked_round_and_the_models_pick_one():
    seen = []

    async def models(task):
        seen.append(task)
        return {"click": [3]}

    solution, _ = asyncio.run(in_client_browser(
        {SHOP: ARKOSE_PAGE, "https://client-api.arkoselabs.com/fc/game": ARKOSE_GAME}, {"type": "FunCaptchaTask"}, models))
    assert solution["token"].endswith("picked=blue") and solution["passed"]
    assert seen[0]["type"] == "GridTask" and seen[0]["columns"] == 4 and "blue square" in seen[0]["comment"]
