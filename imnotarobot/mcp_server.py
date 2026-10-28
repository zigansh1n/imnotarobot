"""MCP server: lets agents solve captchas through the «Я не робот» (Ya Ne Robot) app running on this Mac."""

from typing import Literal

import functools

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from .client import Client, NotRunning, StillSolving, image_b64
from .i18n import tr

server = MCPServer(
    name="captcha-stack",
    instructions=(
        "Solves captchas through the user's own «Я не робот» app (local model, then vision models, then RuCaptcha workers). "
        "Use solve_image_captcha when you have the captcha picture (a screenshot or a file); use solve_token_captcha when "
        "the page has a reCAPTCHA, hCaptcha or Turnstile widget and you know its site key; use solve_in_browser for DataDome "
        "or Arkose FunCaptcha showing in a browser you can reach over CDP (an anti-detect profile). If the site rejects an answer, "
        "call report_wrong with the task_id so that layer is not trusted for that picture again. A slow captcha comes back "
        "as status \"solving\" with a task_id: call get_result with it until the answer arrives."
    ),
)

KINDS = {"text": "ImageToTextTask", "click": "CoordinatesTask", "grid": "GridTask", "slide": "SlideMatchTask"}
TOKEN_KINDS = {"recaptcha_v2": "RecaptchaV2TaskProxyless", "recaptcha_v3": "RecaptchaV3TaskProxyless",
               "hcaptcha": "HCaptchaTaskProxyless", "turnstile": "TurnstileTaskProxyless"}
BROWSER_KINDS = {"datadome": "DataDomeSliderTask", "funcaptcha": "FunCaptchaTask"}


def _client() -> Client:
    try:
        return Client()
    except NotRunning as e:
        raise ToolError(str(e)) from e


def explained(fn):
    """Failures reach the agent as readable text (the SDK hides the message of anything but ToolError)."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ToolError:
            raise
        except Exception as e:
            raise ToolError(tr("не получилось: {error}", error=e)) from e
    return wrapper


# MCP clients drop a call after about a minute (Claude Code by default), while workers can take several
WAIT_PER_CALL = 45.0


def _answer(c: Client, task_id: int) -> dict:
    try:
        return _result(c.wait(task_id, WAIT_PER_CALL))
    except StillSolving:
        return {"status": "solving", "task_id": task_id,
                "next": tr("капча ещё решается — вызовите get_result с этим task_id")}


def _result(r: dict) -> dict:
    return {"answer": r.get("solution"), "layer": r.get("layer"), "confidence": r.get("confidence"),
            "low_confidence": r.get("lowConfidence"), "task_id": r.get("taskId"), "cost": r.get("cost")}


@server.tool()
@explained
def solve_image_captcha(image: str, kind: Literal["text", "click", "grid", "slide"] = "text", instruction: str = "",
                        rows: int = 3, columns: int = 3, piece: str = "") -> dict:
    """Solve a captcha picture. `image` is a file path, a data URL or base64. kind: text = type the characters;
    click = points to click (answer.coordinates in image pixels, in order); grid = tile numbers from 1, left to right,
    top to bottom (answer.click); slide = `image` is the background with a gap and `piece` the puzzle piece, answer.x
    is how far to move the piece. `instruction` is the captcha's task text, e.g. "select all images with buses"."""
    if kind == "slide":
        if not piece:
            raise ToolError(tr("для слайдера нужен piece — картинка кусочка"))
        task = {"type": KINDS[kind], "background": image_b64(image), "target": image_b64(piece)}
    else:
        task = {"type": KINDS[kind], "body": image_b64(image)}
    if instruction:
        task["comment"] = instruction
    if kind == "grid":
        task.update(rows=rows, columns=columns)
    c = _client()
    return _answer(c, c.create(task))


@server.tool()
@explained
def solve_token_captcha(kind: Literal["recaptcha_v2", "recaptcha_v3", "hcaptcha", "turnstile"], page_url: str,
                        site_key: str, action: str = "") -> dict:
    """Get a pass token for a checkbox captcha widget on page_url (the app's own browser first, RuCaptcha workers if it
    fails). Put the returned answer.gRecaptchaResponse or answer.token into the page's response field and call the
    widget's callback if it has one."""
    task = {"type": TOKEN_KINDS[kind], "websiteURL": page_url, "websiteKey": site_key}
    if action:
        task["pageAction"] = action
    c = _client()
    return _answer(c, c.create(task))


@server.tool()
@explained
def solve_in_browser(kind: Literal["datadome", "funcaptcha"], cdp_endpoint: str, page_url: str = "") -> dict:
    """Solve DataDome or Arkose FunCaptcha right in your own browser (an anti-detect profile) that is showing it.
    cdp_endpoint is the profile's DevTools address, e.g. http://127.0.0.1:9222 or ws://...; page_url picks the tab.
    The page passes by itself; answer.cookie (DataDome) or answer.token (Arkose) is returned in case you need it."""
    task = {"type": BROWSER_KINDS[kind], "cdpEndpoint": cdp_endpoint}
    if page_url:
        task["websiteURL"] = page_url
    c = _client()
    return _answer(c, c.create(task))


@server.tool()
@explained
def get_result(task_id: int) -> dict:
    """Answer of a captcha that came back as status "solving"; may say "solving" again — then call it again."""
    return _answer(_client(), task_id)


@server.tool()
@explained
def report_wrong(task_id: int, retry: bool = True) -> dict:
    """Tell the solver that the site rejected this answer; with retry the next layer solves it again."""
    c = _client()
    r = c.report_wrong(task_id, retry)
    if r.get("errorId"):
        raise ToolError(r.get("errorDescription") or r.get("errorCode"))
    return {"reported": True, "retry": _answer(c, r["taskId"]) if r.get("taskId") else None}


@server.tool()
@explained
def captcha_status() -> dict:
    """Which layers are ready, today's solved count and spend."""
    c = _client()
    s = c.stats()
    return {"address": c.address, "layers": s.get("layers"), "today": s.get("today")}


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
