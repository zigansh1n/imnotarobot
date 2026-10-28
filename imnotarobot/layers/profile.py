"""DataDome and Arkose FunCaptcha solved inside the client's own browser, attached over CDP.

Both protections judge the browser and its IP more than the answer, and the cookie or token they issue is
bound to that browser. So we do not open our own: the client passes `cdpEndpoint` of an anti-detect
profile that is already showing the captcha, we solve it in that tab and leave the browser running.
"""

import asyncio
import base64
import io
import logging
import re
import time
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit

import numpy as np
from PIL import Image

from .. import imaging
from ..i18n import tr
from .base import Unsolvable

log = logging.getLogger(__name__)

KINDS = {"DataDomeSliderTask": "datadome", "FunCaptchaTask": "funcaptcha", "FunCaptchaTaskProxyless": "funcaptcha"}

Solver = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
Drag = Callable[[Any, tuple[float, float], tuple[float, float]], Awaitable[None]]

DD_FRAME = "captcha-delivery.com/captcha"
DD_INTERSTITIAL = "captcha-delivery.com/interstitial"
ARKOSE_FRAME = re.compile(r"arkoselabs\.com|funcaptcha\.com|arkose", re.I)
ARKOSE_START = re.compile(r"^\s*(start|begin|verify|solve|continue|next|начать|пройти|продолжить)", re.I)
ARKOSE_NEXT = re.compile(r"next|right|forward|следующ|вправо", re.I)
ARKOSE_SUBMIT = re.compile(r"^\s*(submit|done|confirm|отправить|готово|подтвердить)", re.I)
# an Arkose session token looks like "<id>|r=eu-west-1|meta=3|..."
TOKEN_JS = """() => [...document.querySelectorAll('input')].map(e => e.value)
    .find(v => /\\|r=[a-z0-9-]+/i.test(v || '')) || ''"""


# CDP drives the user's own browser on this Mac, so only a loopback address is allowed
LOOPBACKS = ("127.0.0.1", "::1", "localhost")


def check_endpoint(endpoint: str) -> None:
    """Refuses a DevTools address that is not loopback http/ws: it would drive someone else's browser."""
    parts = urlsplit(endpoint)
    if parts.scheme not in ("http", "https", "ws", "wss"):
        raise Unsolvable(tr("cdpEndpoint должен начинаться с http:// или ws://"))
    if parts.hostname not in LOOPBACKS:
        raise Unsolvable(tr("cdpEndpoint должен указывать на этот Мак: 127.0.0.1, ::1 или localhost"))


def same_site(a: str, b: str) -> bool:
    ha, hb = urlsplit(a).hostname or "", urlsplit(b).hostname or ""
    tail = lambda h: ".".join(h.split(".")[-2:])
    return bool(ha and hb) and tail(ha) == tail(hb)


async def pick_page(browser, url: str, wanted: Callable[[Any], bool]):
    """The tab with the captcha: one on the task's site first, then any tab that shows it."""
    pages = [p for ctx in browser.contexts for p in ctx.pages]
    if not pages:
        raise Unsolvable(tr("в браузере нет открытых вкладок"))
    ordered = [p for p in pages if url and same_site(p.url, url)] + [p for p in pages if not (url and same_site(p.url, url))]
    for page in ordered:
        if wanted(page):
            return page
    if url and ordered and same_site(ordered[0].url, url):
        return ordered[0]
    raise Unsolvable(tr("ни на одной открытой вкладке нет этой капчи"))


async def solve_in_profile(task: dict[str, Any], models: Solver | None, drag: Drag, timeout: float) -> dict[str, Any]:
    endpoint = str(task.get("cdpEndpoint") or "").strip()
    if not endpoint:
        raise Unsolvable(tr("нужен cdpEndpoint браузера, где открыта капча"))
    check_endpoint(endpoint)
    try:
        from patchright.async_api import async_playwright
    except ImportError as e:
        raise Unsolvable(tr("в сборке нет браузерного модуля")) from e
    kind = KINDS[task["type"]]
    url = str(task.get("websiteURL") or "")
    # leaving the block only drops the connection: the client's browser and its tabs stay open
    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.connect_over_cdp(endpoint, timeout=15000)
        except Exception as e:
            raise Unsolvable(tr("не подключиться к браузеру по {endpoint}: {error}", endpoint=endpoint,
                                error=str(e).splitlines()[0][:160])) from e
        if kind == "datadome":
            page = await pick_page(browser, url, lambda p: any(DD_FRAME in f.url or DD_INTERSTITIAL in f.url for f in p.frames))
            return await asyncio.wait_for(DataDome(page, drag).solve(), timeout)
        page = await pick_page(browser, url, lambda p: any(ARKOSE_FRAME.search(f.url) for f in p.frames))
        return await asyncio.wait_for(Arkose(page, models, drag).solve(), timeout)


def center(box: dict[str, float]) -> tuple[float, float]:
    return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2


class DataDome:
    """The captcha-delivery slider: either "slide right to the end" or a puzzle piece to fit into its gap."""

    def __init__(self, page, drag: Drag, attempts: int = 3):
        self.page = page
        self.drag = drag
        self.attempts = attempts

    def frame(self, part: str = DD_FRAME):
        return next((f for f in self.page.frames if part in f.url), None)

    async def solve(self) -> dict[str, Any]:
        before = await self.cookie()
        # the device check passes by itself in a clean browser, or turns into the slider
        for _ in range(40):
            if self.frame() or not self.frame(DD_INTERSTITIAL):
                break
            await asyncio.sleep(0.5)
        for attempt in range(self.attempts):
            frame = self.frame()
            if frame is None:
                return await self.result(before)
            await self.wait_ready(frame)
            await self.slide(frame)
            if await self.cleared(before):
                return await self.result(before)
            log.info("datadome: slider attempt %d did not pass", attempt + 1)
        raise Unsolvable(tr("DataDome не принял слайдер за {n} попытки", n=self.attempts))

    async def wait_ready(self, frame) -> None:
        for _ in range(30):
            if await frame.locator(".sliderContainer .slider").count():
                if await frame.locator("#captcha__puzzle canvas").count() and await self.puzzle_mode(frame) is None:
                    await asyncio.sleep(0.3)
                    continue
                return
            # the slider failed to load or expired: DataDome offers to try again
            retry = frame.locator("button.retryLink:visible")
            if await retry.count():
                await retry.first.click()
                await asyncio.sleep(1.5)
                continue
            text = await frame.evaluate("() => document.body ? document.body.innerText : ''")
            if await frame.locator("#show-human-auth").count() and not await frame.locator("#captcha__frame").count():
                ip = re.search(r"\bIP\s+([0-9a-fA-F:.]{7,})", text)
                raise Unsolvable(tr("DataDome заблокировал этот IP{ip} без капчи: смените прокси",
                                    ip=f" ({ip.group(1)})" if ip else ""))
            await asyncio.sleep(0.5)
        raise Unsolvable(tr("слайдер DataDome не появился"))

    @staticmethod
    async def puzzle_mode(frame) -> bool | None:
        """True for a picture puzzle, False for a plain slider, None while the pictures are still loading."""
        cls = await frame.evaluate("() => (document.querySelector('#captcha__frame') || {}).className || ''")
        heights = await frame.evaluate("() => [...document.querySelectorAll('#captcha__puzzle canvas')].map(c => c.height)")
        if "simple" in cls.split() or not heights:
            return False
        return True if all(h > 0 for h in heights) else None

    async def slide(self, frame) -> None:
        handle = await frame.locator(".sliderContainer .slider").bounding_box()
        target = await frame.locator(".sliderContainer .sliderTarget").bounding_box()
        start = center(handle)
        if await self.puzzle_mode(frame):
            shift = await self.puzzle_shift(frame)
            end = (start[0] + shift, start[1])
        elif target:
            end = center(target)
        else:
            track = await frame.locator(".sliderContainer").bounding_box()
            end = (track["x"] + track["width"] - handle["width"] / 2, start[1])
        log.info("datadome: drag %s -> %s", start, end)
        await self.drag(self.page, start, end)

    @staticmethod
    async def puzzle_shift(frame) -> float:
        """How far to move the handle, in page pixels, so the piece lands on its gap."""
        urls = await frame.evaluate("""() => {
            const cs = [...document.querySelectorAll('#captcha__puzzle canvas')];
            const piece = cs.find(c => c.classList.contains('block')) || cs[1], bg = cs.find(c => c !== piece);
            return [bg.toDataURL(), piece.toDataURL(), bg.getBoundingClientRect().width];
        }""")
        bg, piece = (imaging.load(base64.b64decode(u.split(",", 1)[1])) for u in urls[:2])
        return slider_shift(piece, bg) * urls[2] / bg.width

    async def cookie(self) -> str:
        cookies = await self.page.context.cookies([self.page.url])
        value = next((c["value"] for c in cookies if c["name"] == "datadome"), "")
        if value:
            return value
        # a context opened by another DevTools client may hide its cookie jar from us; the page still sees it
        jar = await self.page.evaluate("() => document.cookie")
        return next((v for k, _, v in (p.strip().partition("=") for p in jar.split(";")) if k == "datadome"), "")

    async def cleared(self, before: str) -> bool:
        for _ in range(30):
            await asyncio.sleep(0.5)
            if self.frame() is None and (await self.cookie()) != before:
                return True
            frame = self.frame()
            if frame is not None and await frame.locator(".sliderContainer.sliderContainer_fail, .toast.error").count():
                await asyncio.sleep(1.5)
                return False
        return self.frame() is None

    async def result(self, before: str) -> dict[str, Any]:
        value = await self.cookie()
        if not value:
            raise Unsolvable(tr("капча DataDome ушла, но cookie datadome нет"))
        if value == before:
            # the page moved on without the slider passing: the old cookie would not pass the site's check
            raise Unsolvable(tr("капча DataDome ушла, а cookie datadome не поменялся"))
        agent = await self.page.evaluate("() => navigator.userAgent")
        return {"cookie": f"datadome={value}", "userAgent": agent, "url": self.page.url}


def slider_shift(piece: Image.Image, background: Image.Image) -> int:
    """The piece canvas is as wide as the picture with the piece drawn at its start; returns the gap offset."""
    alpha = piece.convert("RGBA").getchannel("A")
    box = alpha.point(lambda a: 255 if a > 128 else 0).getbbox()
    if not box:
        raise Unsolvable(tr("на картинке DataDome нет кусочка пазла"))
    crop = piece.convert("RGBA").crop(box)
    band = background.convert("RGB").crop((0, box[1], background.width, box[3]))
    x, _, score = imaging.slide_ncc(crop, band)
    log.info("datadome puzzle: gap at %d (piece at %d), score %.2f", x, box[0], score)
    return x - box[0]


class Arkose:
    """Arkose games, round by round: a gallery behind arrows or a set of tiles, judged by the vision models.

    Not tested on a live challenge yet: the public keys we could reach answer DENIED ACCESS without a site session.
    """

    def __init__(self, page, models: Solver | None, drag: Drag, rounds: int = 12):
        self.page = page
        self.models = models
        self.drag = drag
        self.rounds = rounds

    def frames(self) -> list:
        return [f for f in self.page.frames if ARKOSE_FRAME.search(f.url)]

    async def token(self) -> str:
        for frame in [self.page.main_frame, *self.page.frames]:
            try:
                value = await frame.evaluate(TOKEN_JS)
            except Exception:
                continue
            if value:
                return value
        return ""

    async def game(self):
        """The innermost Arkose frame that has buttons: that is where the game is drawn."""
        for frame in reversed(self.frames()):
            try:
                if await frame.locator("button:visible").count():
                    return frame
            except Exception:
                continue
        return None

    async def button(self, frame, pattern: re.Pattern):
        for b in await frame.locator("button:visible, [role=button]:visible").all():
            label = " ".join(filter(None, [await b.get_attribute("aria-label"), await b.inner_text()]))
            if pattern.search(label):
                return b
        return None

    async def solve(self) -> dict[str, Any]:
        started, rounds = time.monotonic(), 0
        while True:
            token = await self.token()
            game = await self.game()
            if token:
                return {"token": token, "passed": True}
            if game is None and rounds and not self.frames():
                # the frames vanished without a token: the site navigated or the challenge was aborted
                raise Unsolvable(tr("капча Arkose ушла, а токен не появился"))
            if game is None:
                if time.monotonic() - started > 20:
                    raise Unsolvable(tr("игра Arkose не появилась"))
                await asyncio.sleep(1)
                continue
            if rounds >= self.rounds:
                raise Unsolvable(tr("не прошёл за {rounds} заданий с картинками", rounds=self.rounds))
            start = await self.button(game, ARKOSE_START)
            if start and not await self.button(game, ARKOSE_NEXT):
                await start.click()
                await asyncio.sleep(2)
                continue
            await self.play(game)
            rounds += 1
            await asyncio.sleep(2.5)

    async def instruction(self, frame) -> str:
        text = await frame.evaluate("() => document.body ? document.body.innerText : ''")
        lines = [" ".join(x.split()) for x in text.splitlines()]
        return next((x for x in lines if len(x.split()) >= 4), " ".join(lines)[:200])

    async def shot(self, frame) -> bytes:
        return await (await frame.frame_element()).screenshot()

    async def play(self, frame) -> None:
        if self.models is None:
            raise Unsolvable(tr("нет моделей для картинок"))
        comment = await self.instruction(frame)
        arrow, submit = await self.button(frame, ARKOSE_NEXT), await self.button(frame, ARKOSE_SUBMIT)
        if arrow and submit:
            await self.gallery(frame, comment, arrow, submit)
        else:
            await self.tiles(frame, comment)

    async def gallery(self, frame, comment: str, arrow, submit, limit: int = 12) -> None:
        """One option at a time behind arrows: walk the whole circle, show the models all of them at once."""
        shots: list[bytes] = []
        while True:
            png = await self.shot(frame)
            if shots and looks_same(png, shots[0]):
                break
            if len(shots) >= limit:
                raise Unsolvable(tr("варианты Arkose не пошли по кругу"))
            shots.append(png)
            await arrow.click()
            await asyncio.sleep(0.6)
        # the circle is closed: the first option is showing again
        columns = min(len(shots), 4)
        rows = -(-len(shots) // columns)
        answer = await self.models({"type": "GridTask", "body": base64.b64encode(contact_sheet(shots, columns)).decode(),
                                    "rows": rows, "columns": columns,
                                    "comment": f"{comment}. Pick the ONE tile that answers it."})
        picked = min(max((answer.get("click") or [1])[0], 1), len(shots)) - 1
        log.info("arkose gallery: %s -> option %d of %d", comment, picked + 1, len(shots))
        for _ in range(picked):
            await arrow.click()
            await asyncio.sleep(0.4)
        await submit.click()

    async def tiles(self, frame, comment: str) -> None:
        el = await frame.frame_element()
        box = await el.bounding_box()
        png = await el.screenshot()
        answer = await self.models({"type": "CoordinatesTask", "body": base64.b64encode(png).decode(),
                                    "comment": f"{comment}. Point to the ONE image that answers it."})
        points = answer.get("coordinates") or []
        if not points:
            raise Unsolvable(tr("модель не выбрала картинку"))
        k = box["width"] / Image.open(io.BytesIO(png)).width
        x, y = box["x"] + points[0]["x"] * k, box["y"] + points[0]["y"] * k
        log.info("arkose tiles: %s -> %s", comment, points[0])
        await self.page.mouse.click(x, y)


def looks_same(a: bytes, b: bytes) -> bool:
    """Screenshots of one option differ in a few pixels (hover, animation); a thumbnail comparison ignores that."""
    ta, tb = (np.asarray(imaging.load(x).convert("RGB").resize((24, 24)), dtype=np.float64) for x in (a, b))
    return float(np.abs(ta - tb).mean()) < 4


def contact_sheet(shots: list[bytes], columns: int) -> bytes:
    images = [imaging.load(s) for s in shots]
    w, h = max(i.width for i in images), max(i.height for i in images)
    rows = -(-len(images) // columns)
    sheet = Image.new("RGB", (w * columns, h * rows), (255, 255, 255))
    for n, im in enumerate(images):
        sheet.paste(im.convert("RGB"), ((n % columns) * w, (n // columns) * h))
    return imaging.to_png(sheet)
