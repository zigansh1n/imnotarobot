from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .config import Config
from .layers import Layer
from .legacy import add_legacy_routes
from .service import Service
from . import __version__


def _error(code: str, description: str) -> JSONResponse:
    return JSONResponse({"errorId": 1, "errorCode": code, "errorDescription": description})


def create_app(cfg: Config | None = None, layers: list[Layer] | None = None,
               service: Service | None = None) -> FastAPI:
    svc = service or Service(cfg or Config.load(), layers)
    app = FastAPI(title="captcha-stack")
    app.state.service = svc

    async def body_of(request: Request) -> dict[str, Any] | JSONResponse:
        try:
            body = await request.json()
        except ValueError:
            return _error("ERROR_BAD_PARAMETERS", "body is not JSON")
        if svc.cfg.require_client_key and svc.cfg.client_key and body.get("clientKey") != svc.cfg.client_key:
            return _error("ERROR_KEY_DOES_NOT_EXIST", "wrong clientKey")
        return body

    @app.post("/createTask")
    async def create_task(request: Request):
        body = await body_of(request)
        if isinstance(body, JSONResponse):
            return body
        task = body.get("task") or {}
        if not task.get("type"):
            return _error("ERROR_BAD_PARAMETERS", "task.type is required")
        if not svc.supports(task["type"]):
            return _error("ERROR_TASK_NOT_SUPPORTED", f"no layer handles {task['type']}")
        return {"errorId": 0, "taskId": svc.submit(task)}

    @app.post("/getTaskResult")
    async def get_task_result(request: Request):
        body = await body_of(request)
        if isinstance(body, JSONResponse):
            return body
        job = svc.jobs.get(body.get("taskId"))
        if job is None:
            return _error("ERROR_NO_SUCH_CAPCHA_ID", "unknown or expired taskId")
        if not job.done.is_set():
            return {"errorId": 0, "status": "processing"}
        if job.result is None:
            return _error("ERROR_CAPTCHA_UNSOLVABLE", job.error)
        r = job.result
        return {"errorId": 0, "status": "ready", "solution": r.solution, "cost": f"{r.cost:.5f}",
                "layer": r.layer, "confidence": round(r.confidence, 3), "notes": r.meta.get("tried", []),
                "lowConfidence": bool(r.meta.get("low_confidence"))}

    @app.post("/reportIncorrect")
    async def report_incorrect(request: Request):
        body = await body_of(request)
        if isinstance(body, JSONResponse):
            return body
        try:
            new_id = await svc.report(body.get("taskId"), bool(body.get("retry")))
        except KeyError:
            return _error("ERROR_NO_SUCH_CAPCHA_ID", "unknown taskId or no answer to report")
        return {"errorId": 0, "status": "success", **({"taskId": new_id} if new_id else {})}

    @app.post("/reportCorrect")
    async def report_correct(request: Request):
        body = await body_of(request)
        return body if isinstance(body, JSONResponse) else {"errorId": 0, "status": "success"}

    @app.post("/getBalance")
    async def get_balance(request: Request):
        body = await body_of(request)
        if isinstance(body, JSONResponse):
            return body
        try:
            balance, _ = await svc.balance()
            return {"errorId": 0, "balance": balance}
        except Exception as e:
            return _error("ERROR_UPSTREAM", str(e))

    @app.get("/app-info")
    async def app_info():
        """Names this app with no key and no internals: the browser extension probes this to find
        the right port before it ever sends the access key anywhere."""
        return {"app": "imnotarobot", "version": __version__}

    @app.get("/health")
    async def health():
        return {"ok": True, "service": "captcha-stack", "app": "imnotarobot", "version": __version__, "paused": svc.paused,
                "layers": [x.name for x in svc.chain.layers if x.available()]}

    @app.get("/stats")
    async def stats():
        return {"layers": [{"name": x.name, "available": x.available()} for x in svc.chain.layers],
                "today": svc.today(), "stats": svc.chain.state.as_list(), "openTasks": len(svc.jobs)}

    add_legacy_routes(app, svc)
    return app
