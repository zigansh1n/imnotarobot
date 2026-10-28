import base64
import time

import httpx
import pytest

import imnotarobot.config as config
from imnotarobot import client as client_mod
from imnotarobot import mcp_server
from imnotarobot.config import Config
from imnotarobot.control import Controller
from helpers import text_image


@pytest.fixture
def running(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "set_secret", lambda a, v: None)
    cfg = Config.load()
    cfg.port = 18765
    cfg.layers = ["local"]
    c = Controller(cfg)
    c.start_server()
    for _ in range(100):
        try:
            if httpx.get(f"{c.address}/health", timeout=1).json()["ok"]:
                break
        except httpx.HTTPError:
            time.sleep(0.05)
    yield c
    c.stop_server()


def test_running_solver_is_found_and_solves_through_the_mcp_tool(running, tmp_path):
    address, key = client_mod.locate()
    assert address == running.address and key == running.cfg.client_key
    png = tmp_path / "c.png"
    png.write_bytes(base64.b64decode(text_image("k7m2p")))
    r = mcp_server.solve_image_captcha(str(png), kind="text")
    assert r["answer"] == {"text": "k7m2p"} and r["layer"] == "local" and r["task_id"]
    status = mcp_server.captcha_status()
    assert status["address"] == running.address and status["layers"][0]["name"] == "local"
    reported = mcp_server.report_wrong(r["task_id"], retry=False)
    assert reported == {"reported": True, "retry": None}


def test_not_running_is_explained(tmp_path):
    from mcp.server.mcpserver.exceptions import ToolError

    with pytest.raises(ToolError, match="не запущен"):
        mcp_server.captcha_status()


def test_wrong_access_key_is_refused(running):
    c = client_mod.Client(running.address, key="nope")
    with pytest.raises(RuntimeError, match="clientKey"):
        c.solve({"type": "ImageToTextTask", "body": text_image("ab")})


def test_a_slow_captcha_comes_back_as_solving_and_get_result_finishes_it(running, tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_server, "WAIT_PER_CALL", 0.0)
    png = tmp_path / "c.png"
    png.write_bytes(base64.b64decode(text_image("k7m2p")))
    first = mcp_server.solve_image_captcha(str(png), kind="text")
    assert first["status"] == "solving" and first["task_id"]
    monkeypatch.setattr(mcp_server, "WAIT_PER_CALL", 30.0)
    done = mcp_server.get_result(first["task_id"])
    assert done["answer"] == {"text": "k7m2p"} and done["task_id"] == first["task_id"]


def test_slider_goes_through_the_mcp_tool(running, tmp_path):
    import numpy as np
    from PIL import Image

    bg = Image.fromarray(np.random.default_rng(2).integers(0, 255, (120, 260, 3), dtype=np.uint8))
    piece = bg.crop((170, 40, 210, 80))
    bg.paste(piece.point(lambda v: v // 2), (170, 40))
    bg.save(tmp_path / "bg.png")
    piece.save(tmp_path / "piece.png")
    r = mcp_server.solve_image_captcha(str(tmp_path / "bg.png"), kind="slide", piece=str(tmp_path / "piece.png"))
    assert abs(r["answer"]["x"] - 170) <= 2 and r["layer"] == "local"
