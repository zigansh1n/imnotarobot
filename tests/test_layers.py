import asyncio
import json
import random

import httpx
import pytest
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

from imnotarobot.layers import HumanLayer, LocalLayer, Unsolvable
from imnotarobot.layers.human import ACCOUNT_ERRORS, CAPTCHA_ERRORS, PERMANENT_ERRORS, Account
from helpers import png_b64, text_image


def run(coro):
    return asyncio.run(coro)


def test_local_reads_a_clean_text_captcha():
    r = run(LocalLayer().solve({"type": "ImageToTextTask", "body": text_image("ab3k7")}))
    assert r.solution == {"text": "ab3k7"}
    assert r.confidence > 0.9


def test_local_rejects_an_answer_that_breaks_constraints():
    with pytest.raises(Unsolvable):
        run(LocalLayer().solve({"type": "ImageToTextTask", "body": text_image("ab3k7"), "numeric": 1}))


def slider_pair(seed: int, w: int = 50):
    rnd = random.Random(seed)
    bg = Image.new("RGB", (320, 160))
    d = ImageDraw.Draw(bg)
    for _ in range(40):
        x, y = rnd.randint(0, 320), rnd.randint(0, 160)
        d.ellipse([x, y, x + rnd.randint(20, 80), y + rnd.randint(20, 80)], fill=tuple(rnd.randint(40, 255) for _ in range(3)))
    bg = bg.filter(ImageFilter.GaussianBlur(2))
    gx, gy = rnd.randint(120, 250), rnd.randint(20, 90)
    mask = Image.new("L", (w, w), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, w - 1], radius=10, fill=255)
    piece = Image.new("RGBA", (w, w), (0, 0, 0, 0))
    piece.paste(bg.crop((gx, gy, gx + w, gy + w)), (0, 0), mask)
    bg.paste(ImageEnhance.Brightness(bg.crop((gx, gy, gx + w, gy + w))).enhance(0.4), (gx, gy), mask)
    return png_b64(piece), png_b64(bg), gx, gy


@pytest.mark.parametrize("seed", range(10))
def test_local_finds_the_slider_gap_corner(seed):
    piece, bg, gx, gy = slider_pair(seed, w=[40, 50, 60][seed % 3])
    r = run(LocalLayer().solve({"type": "SlideMatchTask", "target": piece, "background": bg}))
    assert abs(r.solution["x"] - gx) <= 3 and abs(r.solution["y"] - gy) <= 3
    assert r.confidence > 0.9 and r.meta["method"] == "ncc"


def test_human_creates_polls_and_reports():
    calls: list[tuple[str, dict]] = []
    polls = iter(["processing", "ready"])

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        method = request.url.path.strip("/")
        calls.append((method, body))
        if method == "createTask":
            return httpx.Response(200, json={"errorId": 0, "taskId": 42})
        if method == "getTaskResult":
            status = next(polls)
            extra = {"solution": {"text": "w0rd"}, "cost": "0.00025"} if status == "ready" else {}
            return httpx.Response(200, json={"errorId": 0, "status": status, **extra})
        return httpx.Response(200, json={"errorId": 0, "status": "success"})

    layer = HumanLayer("https://api.2captcha.test", "key", poll_interval=0, transport=httpx.MockTransport(handler))
    task = {"type": "ImageToTextTask", "body": "eA=="}
    r = run(layer.solve(task))
    assert (r.solution, r.cost, r.confidence) == ({"text": "w0rd"}, 0.00025, 1.0)
    assert calls[0] == ("createTask", {"clientKey": "key", "task": task})
    run(layer.report_incorrect(r))
    assert calls[-1] == ("reportIncorrect", {"clientKey": "key", "taskId": 42})


def test_human_error_is_raised():
    handler = lambda r: httpx.Response(200, json={"errorId": 1, "errorCode": "ERROR_ZERO_BALANCE"})
    layer = HumanLayer("https://x.test", "key", poll_interval=0, transport=httpx.MockTransport(handler))
    with pytest.raises(Unsolvable, match="ERROR_ZERO_BALANCE"):
        run(layer.solve({"type": "ImageToTextTask", "body": "eA=="}))


def test_human_falls_back_to_a_spare_account_and_reports_to_it():
    from imnotarobot.layers.human import Account

    calls: list[tuple[str, str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        method = request.url.path.strip("/")
        calls.append((request.url.host, method, body))
        if request.url.host == "main.test":
            return httpx.Response(200, json={"errorId": 1, "errorCode": "ERROR_ZERO_BALANCE"})
        if method == "createTask":
            return httpx.Response(200, json={"errorId": 0, "taskId": 7})
        if method == "getTaskResult":
            return httpx.Response(200, json={"errorId": 0, "status": "ready", "solution": {"text": "ok"}, "cost": "0.002"})
        return httpx.Response(200, json={"errorId": 0})

    layer = HumanLayer("https://main.test", "main", poll_interval=0, transport=httpx.MockTransport(handler),
                       spares=[Account("https://spare.test", "spare", "Anti-Captcha", rate=80.0)])
    r = run(layer.solve({"type": "ImageToTextTask", "body": "eA=="}))
    assert r.solution == {"text": "ok"} and r.cost == 0.002 * 80 and r.meta["account"] == 1
    run(layer.report_incorrect(r))
    assert calls[-1] == ("spare.test", "reportIncorrect", {"clientKey": "spare", "taskId": 7})


def test_human_without_a_main_key_uses_the_spare():
    from imnotarobot.layers.human import Account

    layer = HumanLayer("https://main.test", "", spares=[Account("https://spare.test", "k")])
    assert layer.available()


def test_human_takes_token_captchas_but_not_local_only_ones():
    layer = HumanLayer("https://x", "k")
    assert layer.supports("HCaptchaTaskProxyless") and not layer.supports("SlideMatchTask")


def human_mock(main_answer, calls):
    """A transport where the main account answers with main_answer (a response, a callable or an
    error to raise) and the spare account always solves the captcha."""

    def handler(request: httpx.Request) -> httpx.Response:
        host, method = request.url.host, request.url.path.strip("/")
        calls.append((host, method))
        if host == "main.test":
            if isinstance(main_answer, Exception):
                raise main_answer
            return main_answer(request) if callable(main_answer) else main_answer
        if method == "createTask":
            return httpx.Response(200, json={"errorId": 0, "taskId": 7})
        return httpx.Response(200, json={"errorId": 0, "status": "ready", "solution": {"text": "ok"}, "cost": "0.002"})

    return httpx.MockTransport(handler)


def human_layer(main_answer, calls, **kw):
    kw.setdefault("poll_interval", 0)
    return HumanLayer("https://main.test", "main", transport=human_mock(main_answer, calls),
                      spares=[Account("https://spare.test", "spare", "Anti-Captcha")], **kw)


@pytest.mark.parametrize("code", sorted(ACCOUNT_ERRORS))
def test_human_account_errors_move_to_the_next_account(code):
    calls: list[tuple[str, str]] = []
    layer = human_layer(httpx.Response(200, json={"errorId": 1, "errorCode": code}), calls)
    assert run(layer.solve({"type": "ImageToTextTask", "body": "eA=="})).solution == {"text": "ok"}
    assert [h for h, _ in calls] == ["main.test", "spare.test", "spare.test"]


@pytest.mark.parametrize("answer", [
    httpx.ConnectError("no route to host"),
    httpx.Response(200, text="<html>proxy error</html>"),
    httpx.Response(500, text="Internal Server Error"),
    httpx.Response(500, json={"errorId": 0}),
], ids=["network", "non-json", "http-500", "http-500-json"])
def test_human_service_breakdowns_move_to_the_next_account(answer):
    calls: list[tuple[str, str]] = []
    layer = human_layer(answer, calls)
    assert run(layer.solve({"type": "ImageToTextTask", "body": "eA=="})).solution == {"text": "ok"}
    assert [h for h, _ in calls] == ["main.test", "spare.test", "spare.test"]


def test_human_unknown_error_at_create_task_moves_to_the_next_account():
    # a createTask that fails was never accepted, so the spare account risks no second payment
    calls: list[tuple[str, str]] = []
    answer = httpx.Response(200, json={"errorId": 1, "errorCode": "ERROR_SOMETHING_NEW", "errorDescription": "???"})
    layer = human_layer(answer, calls)
    assert run(layer.solve({"type": "ImageToTextTask", "body": "eA=="})).solution == {"text": "ok"}
    assert [h for h, _ in calls] == ["main.test", "spare.test", "spare.test"]


@pytest.mark.parametrize("code", sorted(CAPTCHA_ERRORS))
def test_human_captcha_errors_stop_at_the_first_account(code):
    # the captcha itself is the problem: a spare would only repeat the failure
    calls: list[tuple[str, str]] = []
    layer = human_layer(httpx.Response(200, json={"errorId": 1, "errorCode": code}), calls)
    with pytest.raises(Unsolvable, match=code):
        run(layer.solve({"type": "ImageToTextTask", "body": "eA=="}))
    assert [h for h, _ in calls] == ["main.test"]


@pytest.mark.parametrize("code", ["ERROR_CAPTCHA_UNSOLVABLE", "ERROR_SOMETHING_NEW"])
def test_human_errors_while_polling_stop_at_the_first_account(code):
    # the task is already with the workers and may still be solved and paid for: no second attempt
    calls: list[tuple[str, str]] = []

    def main_answer(request: httpx.Request) -> httpx.Response:
        if request.url.path.strip("/") == "createTask":
            return httpx.Response(200, json={"errorId": 0, "taskId": 7})
        return httpx.Response(200, json={"errorId": 1, "errorCode": code})

    layer = human_layer(main_answer, calls)
    with pytest.raises(Unsolvable, match=code):
        run(layer.solve({"type": "ImageToTextTask", "body": "eA=="}))
    assert [h for h, _ in calls] == ["main.test", "main.test"]


@pytest.mark.parametrize("code", sorted(PERMANENT_ERRORS))
def test_human_permanent_account_errors_while_polling_stop_at_the_first_account(code):
    # a bad key or an empty balance will not fix itself: stop with the code visible, no fallback
    calls: list[tuple[str, str]] = []

    def main_answer(request: httpx.Request) -> httpx.Response:
        if request.url.path.strip("/") == "createTask":
            return httpx.Response(200, json={"errorId": 0, "taskId": 7})
        return httpx.Response(200, json={"errorId": 1, "errorCode": code})

    layer = human_layer(main_answer, calls)
    with pytest.raises(Unsolvable, match=code):
        run(layer.solve({"type": "ImageToTextTask", "body": "eA=="}))
    assert [h for h, _ in calls] == ["main.test", "main.test"]


@pytest.mark.parametrize("answer", [
    httpx.ConnectError("no route to host"),
    httpx.Response(200, text="<html>proxy error</html>"),
    httpx.Response(502, text="Bad Gateway"),
    httpx.Response(200, json={"errorId": 1, "errorCode": "ERROR_NO_SLOT_AVAILABLE"}),
], ids=["network", "non-json", "http-502", "no-slots"])
def test_human_keeps_polling_through_transient_failures_after_acceptance(answer):
    # the workers may still solve and charge for an accepted task: a momentary inability of
    # the service to answer (a 5xx, no slots, a lost packet) keeps it polling to the deadline
    calls: list[tuple[str, str]] = []

    def main_answer(request: httpx.Request) -> httpx.Response:
        if request.url.path.strip("/") == "createTask":
            return httpx.Response(200, json={"errorId": 0, "taskId": 7})
        if isinstance(answer, Exception):
            raise answer
        return answer

    layer = human_layer(main_answer, calls, timeout=0.15, poll_interval=0.05)
    with pytest.raises(Unsolvable, match="нет ответа"):
        run(layer.solve({"type": "ImageToTextTask", "body": "eA=="}))
    assert {h for h, _ in calls} == {"main.test"}  # the spare was never asked


@pytest.mark.parametrize("failure", [
    httpx.ConnectError("no route to host"),
    httpx.Response(502, text="Bad Gateway"),
    httpx.Response(200, json={"errorId": 1, "errorCode": "ERROR_NO_SLOT_AVAILABLE"}),
], ids=["network", "http-502", "no-slots"])
def test_human_survives_a_transient_failure_while_polling(failure):
    # one momentary failure, then the answer arrives: the same account still solves it
    calls: list[tuple[str, str]] = []
    polls = iter([failure,
                  httpx.Response(200, json={"errorId": 0, "status": "ready", "solution": {"text": "ok"}, "cost": "0.002"})])

    def main_answer(request: httpx.Request) -> httpx.Response:
        if request.url.path.strip("/") == "createTask":
            return httpx.Response(200, json={"errorId": 0, "taskId": 7})
        answer = next(polls)
        if isinstance(answer, Exception):
            raise answer
        return answer

    layer = human_layer(main_answer, calls)
    r = run(layer.solve({"type": "ImageToTextTask", "body": "eA=="}))
    assert r.solution == {"text": "ok"}
    assert [h for h, _ in calls] == ["main.test"] * 3


def test_human_poll_timeout_after_acceptance_does_not_fall_back():
    # the workers may still be solving it: another account would mean a second payment
    calls: list[tuple[str, str]] = []
    answer = httpx.Response(200, json={"errorId": 0, "taskId": 7, "status": "processing"})
    layer = human_layer(answer, calls, timeout=0)
    with pytest.raises(Unsolvable, match="нет ответа"):
        run(layer.solve({"type": "ImageToTextTask", "body": "eA=="}))
    assert [h for h, _ in calls] == ["main.test"]


def test_human_lists_every_account_when_all_of_them_fail():
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.url.host, request.url.path.strip("/")))
        return httpx.Response(200, json={"errorId": 1, "errorCode": "ERROR_ZERO_BALANCE"})

    layer = HumanLayer("https://main.test", "main", poll_interval=0, transport=httpx.MockTransport(handler),
                       spares=[Account("https://spare.test", "spare", "Anti-Captcha")])
    with pytest.raises(Unsolvable) as exc:
        run(layer.solve({"type": "ImageToTextTask", "body": "eA=="}))
    assert str(exc.value) == ("https://main.test: на балансе нет денег (ERROR_ZERO_BALANCE); "
                              "Anti-Captcha: на балансе нет денег (ERROR_ZERO_BALANCE)")
    assert [h for h, _ in calls] == ["main.test", "spare.test"]


def test_human_single_account_keeps_its_messages():
    # without spares nothing is retried and the messages stay as they were
    layer = HumanLayer("https://main.test", "main", poll_interval=0,
                       transport=human_mock(httpx.ConnectError("no route to host"), []))
    with pytest.raises(Unsolvable, match="no route to host"):
        run(layer.solve({"type": "ImageToTextTask", "body": "eA=="}))
    layer = HumanLayer("https://main.test", "main", timeout=0, poll_interval=0,
                       transport=human_mock(httpx.Response(200, json={"errorId": 0, "taskId": 1, "status": "processing"}), []))
    with pytest.raises(Unsolvable, match=r"нет ответа от https://main\.test за 0 с"):
        run(layer.solve({"type": "ImageToTextTask", "body": "eA=="}))
