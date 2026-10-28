import time

from fastapi.testclient import TestClient

from imnotarobot.config import Config
from imnotarobot.server import create_app
from helpers import FakeLayer


def wait_result(client, task_id, key="", timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = client.post("/getTaskResult", json={"clientKey": key, "taskId": task_id}).json()
        if r.get("status") != "processing":
            return r
        time.sleep(0.02)
    raise AssertionError("task never finished")


def make(layers, **cfg):
    import imnotarobot.config as config
    cfg.setdefault("state_path", str(config.APP_DIR / "state.json"))
    return TestClient(create_app(Config(**cfg), layers))


def test_full_flow_report_and_retry_moves_to_the_next_layer():
    local, vlm = FakeLayer("local", [("abc", 0.95)]), FakeLayer("vlm", [("abd", 0.9)])
    with make([local, vlm]) as c:
        task = {"type": "ImageToTextTask", "body": "aGk="}
        task_id = c.post("/createTask", json={"clientKey": "", "task": task}).json()["taskId"]
        r = wait_result(c, task_id)
        assert (r["status"], r["solution"], r["layer"]) == ("ready", {"text": "abc"}, "local")
        rep = c.post("/reportIncorrect", json={"taskId": task_id, "retry": True}).json()
        assert rep["status"] == "success" and local.reports == 1
        r2 = wait_result(c, rep["taskId"])
        assert (r2["solution"], r2["layer"]) == ({"text": "abd"}, "vlm")
        stats = c.get("/stats").json()["stats"]
        assert {"kind": "ImageToTextTask|", "layer": "local", "answered": 1, "reported_bad": 1} in stats


def test_unsolvable_is_reported_in_2captcha_format():
    with make([FakeLayer("local", [("abc", 0.1)], min_confidence=0.9, types=("CoordinatesTask",)),
               FakeLayer("vlm", [RuntimeError("boom")], types=("CoordinatesTask",))]) as c:
        task_id = c.post("/createTask", json={"task": {"type": "CoordinatesTask", "body": "eA=="}}).json()["taskId"]
        r = wait_result(c, task_id)
        assert r["lowConfidence"] is True and r["layer"] == "local"
    with make([FakeLayer("local", [RuntimeError("boom")])]) as c:
        task_id = c.post("/createTask", json={"task": {"type": "ImageToTextTask", "body": "eA=="}}).json()["taskId"]
        r = wait_result(c, task_id)
        assert (r["errorId"], r["errorCode"]) == (1, "ERROR_CAPTCHA_UNSOLVABLE")
        assert "boom" in r["errorDescription"]


def test_unknown_task_type_and_wrong_key_are_rejected():
    with make([FakeLayer("local", [("a", 1.0)])], client_key="secret") as c:
        r = c.post("/createTask", json={"clientKey": "nope", "task": {"type": "ImageToTextTask"}}).json()
        assert r["errorCode"] == "ERROR_KEY_DOES_NOT_EXIST"
        r = c.post("/createTask", json={"clientKey": "secret", "task": {"type": "FooTask"}}).json()
        assert r["errorCode"] == "ERROR_TASK_NOT_SUPPORTED"
        r = c.post("/getTaskResult", json={"clientKey": "secret", "taskId": 1}).json()
        assert r["errorCode"] == "ERROR_NO_SUCH_CAPCHA_ID"


def test_request_shaped_like_2captcha_api_v2_is_accepted():
    """Software written for the 2Captcha API v2 should only need a different host."""
    with make([FakeLayer("local", [("w0rd", 0.99)])]) as c:
        created = c.post("/createTask", json={"clientKey": "any", "task": {"type": "ImageToTextTask", "body": "eA=="},
                                              "languagePool": "en"}).json()
        assert created["errorId"] == 0
        r = wait_result(c, created["taskId"], key="any")
        assert r["solution"]["text"] == "w0rd" and float(r["cost"]) == 0


def test_legacy_in_res_php_text_flow_and_reportbad():
    local, vlm = FakeLayer("local", [("abc", 0.95)]), FakeLayer("vlm", [("abd", 0.9)])
    with make([local, vlm]) as c:
        r = c.post("/in.php", data={"key": "k", "method": "base64", "body": "aGk=", "json": "1"}).json()
        assert r["status"] == 1
        task_id = r["request"]
        for _ in range(100):
            res = c.get("/res.php", params={"key": "k", "action": "get", "id": task_id}).text
            if res != "CAPCHA_NOT_READY":
                break
            time.sleep(0.02)
        assert res == "OK|abc"
        assert c.get("/res.php", params={"action": "reportbad", "id": task_id}).text == "OK_REPORT_RECORDED"
        assert local.reports == 1


def test_legacy_file_upload_and_grid_answer_format():
    grid = FakeLayer("vlm", [("x", 1.0)], types=("GridTask",))

    async def solve(task):
        from imnotarobot.layers import Result
        assert (task["rows"], task["columns"], task["comment"]) == (4, 4, "select buses")
        return Result({"click": [2, 5]}, 1.0, "vlm")

    grid.solve = solve
    with make([grid]) as c:
        r = c.post("/in.php", data={"method": "post", "recaptcha": "1", "recaptcharows": "4", "recaptchacols": "4",
                                    "textinstructions": "select buses"}, files={"file": ("c.png", b"\x89PNG")})
        task_id = r.text.split("|")[1]
        for _ in range(100):
            res = c.get("/res.php", params={"action": "get", "id": task_id, "json": "1"}).json()
            if res["request"] != "CAPCHA_NOT_READY":
                break
            time.sleep(0.02)
        assert res == {"status": 1, "request": "click:2/5"}


def test_legacy_rejects_unknown_method():
    with make([FakeLayer("local", [("a", 1.0)])]) as c:
        assert c.post("/in.php", data={"method": "geetest"}).text == "ERROR_BAD_PARAMETERS"


def test_legacy_getbalance_answers_in_both_formats():
    with make([FakeLayer("local", [("a", 1.0)])], client_key="secret") as c:
        async def balance():
            return 12.5

        c.app.state.service.balance = balance
        assert c.get("/res.php", params={"key": "secret", "action": "getbalance"}).text == "12.50000"
        assert c.get("/res.php", params={"key": "secret", "action": "getbalance", "json": "1"}).json() == \
            {"status": 1, "request": "12.50000"}
        assert c.get("/res.php", params={"key": "nope", "action": "getbalance"}).text == "ERROR_KEY_DOES_NOT_EXIST"


def test_legacy_userinfo_lets_the_extension_log_in():
    """The extension's Login button checks the key with res.php?action=userinfo."""
    with make([FakeLayer("local", [("a", 1.0)])], client_key="secret") as c:
        async def balance():
            return 200.0

        c.app.state.service.balance = balance
        r = c.get("/res.php", params={"key": "secret", "action": "userinfo", "json": "1"}).json()
        assert r["status"] == 1
        assert r["request"]["key_type"] == "customer"  # anything else the extension calls a worker key
        assert (r["request"]["balance"], r["request"]["valute"]) == ("200.00", "RUB")  # roubles: rucaptcha is the default
        with make([FakeLayer("local", [("a", 1.0)])], client_key="secret", human_provider="2captcha") as c2:
            c2.app.state.service.balance = balance
            assert c2.get("/res.php", params={"key": "secret", "action": "userinfo", "json": "1"}).json()["request"]["valute"] == "USD"
        assert c.get("/res.php", params={"key": "nope", "action": "userinfo"}).text == "ERROR_KEY_DOES_NOT_EXIST"


class SlowLayer(FakeLayer):
    """Keeps a task in the queue for a moment, so the polling answer can be checked."""

    async def solve(self, task):
        import asyncio

        await asyncio.sleep(0.3)
        return await super().solve(task)


def test_extension_shaped_requests_roundtrip():
    """The browser extension posts in.php as form data, always with json=1, and polls res.php the same way."""
    with make([SlowLayer("local", [("w0rd", 0.99)])], client_key="secret") as c:
        created = c.post("/in.php", data={"key": "secret", "method": "base64", "body": "aGk=",
                                          "json": "1", "header_acao": "1"}).json()
        assert created["status"] == 1 and created["request"].isdigit()
        r = c.get("/res.php", params={"key": "secret", "action": "get", "id": created["request"], "json": "1"}).json()
        assert r == {"status": 0, "request": "CAPCHA_NOT_READY"}  # what the extension keeps polling on
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            r = c.get("/res.php", params={"key": "secret", "action": "get", "id": created["request"], "json": "1"}).json()
            if r["status"] == 1:
                break
            time.sleep(0.02)
        assert r == {"status": 1, "request": "w0rd"}
