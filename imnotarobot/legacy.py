"""2Captcha API v1 (in.php / res.php), which much older software still speaks."""

import base64
import json
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from .service import Service


def task_from_v1(p: dict[str, Any], file_bytes: bytes | None) -> dict[str, Any]:
    method = p.get("method", "")
    if method == "userrecaptcha":
        t = {"type": "RecaptchaV3TaskProxyless" if p.get("version") == "v3" else "RecaptchaV2TaskProxyless",
             "websiteURL": p.get("pageurl"), "websiteKey": p.get("googlekey")}
        if p.get("action"):
            t["pageAction"] = p["action"]
        if p.get("min_score"):
            t["minScore"] = float(p["min_score"])
        return t
    if method == "hcaptcha":
        return {"type": "HCaptchaTaskProxyless", "websiteURL": p.get("pageurl"), "websiteKey": p.get("sitekey")}
    if method == "turnstile":
        return {"type": "TurnstileTaskProxyless", "websiteURL": p.get("pageurl"), "websiteKey": p.get("sitekey")}
    if method not in ("base64", "post"):
        raise ValueError(f"method {method!r} is not supported")
    body = p.get("body", "") if method == "base64" else base64.b64encode(file_bytes or b"").decode()
    if not body:
        raise ValueError("no image")
    comment = p.get("textinstructions") or p.get("instructions") or ""
    if p.get("coordinatescaptcha") == "1":
        return {"type": "CoordinatesTask", "body": body, "comment": comment}
    if p.get("recaptcha") == "1":
        return {"type": "GridTask", "body": body, "comment": comment,
                "rows": int(p.get("recaptcharows") or 3), "columns": int(p.get("recaptchacols") or 3)}
    t: dict[str, Any] = {"type": "ImageToTextTask", "body": body, "comment": comment,
                         "phrase": p.get("phrase") == "1", "case": p.get("regsense") == "1",
                         "numeric": int(p.get("numeric") or 0), "math": p.get("calc") == "1"}
    if p.get("min_len"):
        t["minLength"] = int(p["min_len"])
    if p.get("max_len"):
        t["maxLength"] = int(p["max_len"])
    return t


def answer_v1(solution: dict[str, Any]) -> Any:
    if "text" in solution:
        return solution["text"]
    if "click" in solution:
        return "click:" + "/".join(str(i) for i in solution["click"])
    if "coordinates" in solution:
        return solution["coordinates"]
    return solution.get("gRecaptchaResponse") or solution.get("token") or json.dumps(solution)


def add_legacy_routes(app: FastAPI, svc: Service) -> None:
    async def params(request: Request) -> tuple[dict[str, Any], bytes | None]:
        p: dict[str, Any] = dict(request.query_params)
        file_bytes = None
        if request.method == "POST":
            form = await request.form()
            for k, v in form.multi_items():
                if hasattr(v, "read"):
                    file_bytes = await v.read()
                else:
                    p[k] = v
        return p, file_bytes

    def reply(p: dict[str, Any], status: int, request_value: Any) -> Any:
        if p.get("json") == "1":
            return JSONResponse({"status": status, "request": request_value})
        if isinstance(request_value, (list, dict)):
            request_value = json.dumps(request_value)
        return PlainTextResponse(f"OK|{request_value}" if status == 1 else str(request_value))

    def key_ok(p: dict[str, Any]) -> bool:
        return not (svc.cfg.require_client_key and svc.cfg.client_key) or p.get("key") == svc.cfg.client_key

    @app.api_route("/in.php", methods=["GET", "POST"])
    async def in_php(request: Request):
        p, file_bytes = await params(request)
        if not key_ok(p):
            return reply(p, 0, "ERROR_KEY_DOES_NOT_EXIST")
        try:
            task = task_from_v1(p, file_bytes)
        except ValueError:
            return reply(p, 0, "ERROR_BAD_PARAMETERS")
        if not svc.supports(task["type"]):
            return reply(p, 0, "ERROR_BAD_PARAMETERS")
        return reply(p, 1, str(svc.submit(task)))

    @app.api_route("/res.php", methods=["GET", "POST"])
    async def res_php(request: Request):
        p, _ = await params(request)
        if not key_ok(p):
            return reply(p, 0, "ERROR_KEY_DOES_NOT_EXIST")
        action = p.get("action", "get")
        if action == "getbalance":
            try:
                balance, _ = await svc.balance()
                return PlainTextResponse(f"{balance:.5f}") if p.get("json") != "1" else \
                    JSONResponse({"status": 1, "request": f"{balance:.5f}"})
            except Exception:
                return reply(p, 0, "ERROR_UPSTREAM")
        if action == "userinfo":
            # the browser extension's "login": proves the key works and shows the balance of the workers' step
            try:
                balance, cur = await svc.balance()
            except Exception:
                return reply(p, 0, "ERROR_UPSTREAM")
            valute = "RUB" if cur == "₽" else "USD"
            return reply(p, 1, {"key_type": "customer", "email": "ImNotARobot",
                                "balance": f"{balance:.2f}", "valute": valute})
        try:
            task_id = int(p.get("id", ""))
        except ValueError:
            return reply(p, 0, "ERROR_WRONG_CAPTCHA_ID")
        if action == "reportbad":
            try:
                await svc.report(task_id)
            except KeyError:
                return reply(p, 0, "ERROR_WRONG_CAPTCHA_ID")
            return reply(p, 1, "OK_REPORT_RECORDED") if p.get("json") == "1" else PlainTextResponse("OK_REPORT_RECORDED")
        if action == "reportgood":
            return reply(p, 1, "OK_REPORT_RECORDED") if p.get("json") == "1" else PlainTextResponse("OK_REPORT_RECORDED")
        job = svc.jobs.get(task_id)
        if job is None:
            return reply(p, 0, "ERROR_WRONG_CAPTCHA_ID")
        if not job.done.is_set():
            return reply(p, 0, "CAPCHA_NOT_READY")
        if job.result is None:
            return reply(p, 0, "ERROR_CAPTCHA_UNSOLVABLE")
        return reply(p, 1, answer_v1(job.result.solution))
