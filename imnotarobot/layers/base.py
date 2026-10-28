import base64
import io
import re
from dataclasses import dataclass, field
from typing import Any

from PIL import Image


class Unsolvable(Exception):
    # the chain's per-step notes, attached by Chain.solve when every layer failed
    notes: list[str] = []


class Unavailable(Unsolvable):
    """The service behind the layer gave no answer at all; other layers on the same service are skipped."""


@dataclass
class Result:
    solution: dict[str, Any]
    confidence: float
    layer: str
    cost: float = 0.0
    meta: dict[str, Any] = field(default_factory=dict)


class Layer:
    name = ""
    # shown to people in notes and the window; `name` stays the stable id programs see
    title = ""
    task_types: frozenset[str] = frozenset()
    # 1.0 means the answer is accepted without a confidence check
    min_confidence = 1.0
    # layers with the same non-empty upstream share one service and its outages
    upstream = ""

    def available(self) -> bool:
        return True

    def supports(self, task_type: str) -> bool:
        return task_type in self.task_types

    async def solve(self, task: dict[str, Any]) -> Result:
        raise NotImplementedError

    async def report_incorrect(self, result: Result) -> None:
        return None


def decode_image(body: str) -> bytes:
    if body.startswith("data:"):
        body = body.split(",", 1)[1]
    return base64.b64decode(re.sub(r"\s", "", body))


def image_size(data: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(data)) as im:
        return im.size


def text_fits(text: str, task: dict[str, Any]) -> bool:
    """Checks a text answer against the ImageToTextTask constraints the client sent."""
    if not text:
        return False
    if len(text) < int(task.get("minLength") or 0):
        return False
    if task.get("maxLength") and len(text) > int(task["maxLength"]):
        return False
    numeric = int(task.get("numeric") or 0)
    if numeric == 1 and not text.isdigit():
        return False
    if numeric == 2 and any(ch.isdigit() for ch in text):
        return False
    if numeric == 4 and not (any(ch.isdigit() for ch in text) and any(ch.isalpha() for ch in text)):
        return False
    if not task.get("phrase") and " " in text.strip():
        return False
    return True
