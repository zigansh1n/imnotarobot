import asyncio
import itertools
import json
import time
from collections import deque
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any

from .chain import Chain, State, today
from . import config as config_mod
from .config import HUMAN_CURRENCY, HUMAN_PRESETS, Config
from .layers import HumanLayer, Layer, LocalLayer, Result, VlmLayer
from .layers.human import Account
from .layers.browser import BrowserLayer, ask_models
from .i18n import tr


def build_layers(cfg: Config) -> list[Layer]:
    out: list[Layer] = []
    for name in cfg.layers:
        if name == "local":
            out.append(LocalLayer(cfg.ocr_min_confidence))
        elif name == "vlm":
            out += [VlmLayer(m) for m in cfg.models]
        elif name.startswith("vlm:"):
            out += [VlmLayer(m) for m in cfg.models if m.name == name[4:]]
        elif name == "browser":
            # filled below: the browser asks every enabled model, wherever it sits in the order
            out.append(BrowserLayer(None))
        elif name == "human":
            out.append(HumanLayer(cfg.human_base_url, cfg.human_api_key, cfg.human_timeout, cfg.human_poll_interval,
                                  spares=spare_accounts(cfg)))
    models = [x for x in out if isinstance(x, VlmLayer)]
    for x in out:
        if isinstance(x, BrowserLayer) and models:
            x.models = lambda t: ask_models(models, t)
            x.hands_spec = next((m.spec for m in models if m.available()), None)
    return out


def spare_accounts(cfg: Config) -> list[Account]:
    main = HUMAN_CURRENCY.get(cfg.human_provider, "$")
    # a rate edited to 0 by hand must not break every captcha
    usd = cfg.rub_per_usd if cfg.rub_per_usd > 0 else 80.0
    out = []
    for a in cfg.human_extra:
        cur = HUMAN_CURRENCY.get(a.provider, "$")
        rate = 1.0 if cur == main else usd if main == "₽" else 1 / usd
        out.append(Account(HUMAN_PRESETS.get(a.provider, ""), config_mod.get_secret(f"human:{a.id}"), a.note or a.provider, rate))
    return out


def answering_account(cfg: Config, human: HumanLayer) -> tuple[int, str, float]:
    """The first workers' account that has a key: (its index, provider, rate into the main currency).
    With no main key that account is the first spare, and its balance is what the checks must show."""
    index = next((i for i, a in enumerate(human.accounts) if a.api_key), 0)
    provider = cfg.human_provider if not index or index > len(cfg.human_extra) else cfg.human_extra[index - 1].provider
    return index, provider, human.accounts[index].rate


JOURNAL_KEEP = 300


def _short_answer(solution: dict[str, Any]) -> str:
    if "text" in solution:
        return str(solution["text"])[:40]
    if "click" in solution:
        return tr("клетки {tiles}", tiles=", ".join(map(str, solution["click"])))
    if "coordinates" in solution:
        return "; ".join(f"{p['x']},{p['y']}" for p in solution["coordinates"])[:60]
    if "x" in solution:
        return tr("сдвиг {x}", x=solution["x"])
    return tr("токен получен")


def limits(cfg: Config) -> dict[str, float]:
    return {"human": cfg.human_daily_limit, "cloud": cfg.cloud_daily_limit_usd}


@dataclass
class Job:
    task: dict[str, Any]
    created: float = field(default_factory=time.monotonic)
    result: Result | None = None
    error: str = ""
    # the chain's per-step notes when nothing solved the captcha
    notes: list[str] = field(default_factory=list)
    done: asyncio.Event = field(default_factory=asyncio.Event)


class Service:
    """Task bookkeeping shared by the HTTP APIs and the menu bar app."""

    def __init__(self, cfg: Config, layers: list[Layer] | None = None):
        self.cfg = cfg
        self.chain = Chain(layers if layers is not None else build_layers(cfg),
                           State(cfg.state_path, cfg.skip_after_reports), limits(cfg))
        self.jobs: dict[int, Job] = {}
        self._ids = itertools.count(int(time.time()))
        self._running: set[asyncio.Task] = set()
        self.paused = False
        self.recent: deque[dict[str, Any]] = deque(maxlen=100)
        self.journal_dir = Path(cfg.state_path).parent / "journal"
        self._load_journal()

    def _load_journal(self) -> None:
        """Brings back the summaries of the latest solved captchas after a restart."""
        files = sorted(self.journal_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        for path in files[:JOURNAL_KEEP]:
            try:
                entry = json.loads(path.read_text())
            except (ValueError, OSError):
                continue
            self.recent.append(entry["summary"])
        for path in files[JOURNAL_KEEP:]:
            path.unlink(missing_ok=True)
        # a slow captcha is written later than a newer one, so mtime lies about the order
        order = sorted(self.recent, key=lambda s: (s.get("at", 0), s.get("id", 0)), reverse=True)
        self.recent = deque(order, maxlen=self.recent.maxlen)

    def journal_entry(self, entry_id: int) -> dict[str, Any] | None:
        path = self.journal_dir / f"{int(entry_id)}.json"
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            return None

    def _write_journal(self, entry_id: int, job: "Job", summary: dict[str, Any]) -> None:
        r = job.result
        record = {"summary": summary, "task": job.task, "solution": r.solution if r else None,
                  "meta": {k: v for k, v in (r.meta if r else {}).items() if k in ("tried", "answers", "remote_task_id", "method", "agreed_with")}}
        try:
            self.journal_dir.mkdir(parents=True, exist_ok=True)
            (self.journal_dir / f"{entry_id}.json").write_text(json.dumps(record, ensure_ascii=False, default=str))
        except OSError:
            pass

    async def report_entry(self, entry_id: int) -> dict[str, Any]:
        """Marks a journal answer as wrong: the layer is skipped for this image and the workers' service is told."""
        entry = self.journal_entry(entry_id)
        if not entry or entry.get("solution") is None:
            raise KeyError(entry_id)
        s = entry["summary"]
        result = Result(entry["solution"], s.get("confidence", 0), s["layer"], meta=entry.get("meta", {}))
        await self.chain.report_incorrect(entry["task"], result)
        s["reported"] = True
        for item in self.recent:
            if item.get("id") == entry_id:
                item["reported"] = True
        (self.journal_dir / f"{entry_id}.json").write_text(json.dumps(entry, ensure_ascii=False, default=str))
        return s

    def reconfigure(self, cfg: Config) -> None:
        """Swaps layers and limits; statistics and open tasks are kept."""
        self.cfg = cfg
        self.chain.layers = build_layers(cfg)
        self.chain.daily_limits = limits(cfg)
        self.chain.state.skip_after_reports = cfg.skip_after_reports

    def supports(self, task_type: str) -> bool:
        return any(layer.supports(task_type) for layer in self.chain.layers)

    def submit(self, task: dict[str, Any]) -> int:
        now = time.monotonic()
        for k in [k for k, j in self.jobs.items() if now - j.created > self.cfg.task_ttl]:
            del self.jobs[k]
        task_id = next(self._ids)
        job = Job(task)
        self.jobs[task_id] = job
        t = asyncio.create_task(self._run(job, task_id))
        self._running.add(t)
        t.add_done_callback(self._running.discard)
        return task_id

    async def _run(self, job: Job, task_id: int = 0) -> None:
        started = time.time()
        try:
            if self.paused:
                raise RuntimeError(tr("решатель на паузе"))
            job.result = await self.chain.solve(job.task)
        except Exception as e:
            job.error = str(e)
            job.notes = list(getattr(e, "notes", []) or [])
        finally:
            job.done.set()
            r = job.result
            summary = {
                "id": task_id, "at": started, "seconds": round(time.time() - started, 2), "type": job.task.get("type"),
                "layer": r.layer if r else "",
                "layerTitle": next((x.title for x in self.chain.layers if r and x.name == r.layer), r.layer if r else ""), "confidence": round(r.confidence, 2) if r else 0,
                "cost": r.cost if r else 0.0, "lowConfidence": bool(r and r.meta.get("low_confidence")),
                "error": job.error, "notes": (r.meta.get("tried", []) if r else job.notes),
                "answer": _short_answer(r.solution) if r else "", "reported": False,
            }
            self.recent.appendleft(summary)
            if task_id:
                self._write_journal(task_id, job, summary)

    async def report(self, task_id: int, retry: bool = False) -> int | None:
        job = self.jobs.get(task_id)
        if job is None or job.result is None:
            raise KeyError(task_id)
        await self.chain.report_incorrect(job.task, job.result)
        return self.submit(job.task) if retry else None

    async def balance(self) -> tuple[float, str]:
        """The balance of the first workers' account that has a key, with that account's currency:
        with no main key a spare account is the one that answers."""
        human = self.chain.layer("human")
        if not isinstance(human, HumanLayer) or not human.available():
            return 0.0, HUMAN_CURRENCY.get(self.cfg.human_provider, "$")
        index, provider, _ = answering_account(self.cfg, human)
        return await human.balance(index), HUMAN_CURRENCY.get(provider, "$")

    def today(self) -> dict[str, dict[str, float]]:
        return self.chain.state.daily.get(today(), {})
