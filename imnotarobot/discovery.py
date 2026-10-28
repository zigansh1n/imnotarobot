"""From a bare API key to a provider and its vision-capable models."""

import asyncio
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from .providers import PRESETS
from .i18n import tr

# prefixes are a hint only; every guess is confirmed by an authenticated request
PREFIX_HINTS = [("sk-ant-", "anthropic"), ("AIza", "gemini"), ("sk-or-", "openrouter"), ("xai-", "xai"), ("gsk_", "groq")]
# tried in this order when the prefix says nothing
PROBE_ORDER = ["openai", "deepseek", "dashscope", "dashscope-intl", "mistral", "openrouter", "xai", "groq", "anthropic", "gemini"]

# names of models that take images, for providers whose model list does not say so
VISION_PATTERNS = {
    "openai": r"^(gpt-4o|gpt-4\.1|gpt-5|o1(?!-mini)|o3|o4|chatgpt-4o)",
    "dashscope": r"(vl|omni|qvq)",
    "dashscope-intl": r"(vl|omni|qvq)",
    "gemini": r"^gemini",
    "deepseek": r"^deepseek-flash",
    "anthropic": r"^claude",
    "xai": r"(vision|grok-4|grok-5)",
    "groq": r"(llama-4|vision)",
    "mistral": r"(pixtral|mistral-(medium|small|large)-)",
}
NOT_CHAT = r"(embed|tts|whisper|transcri|audio|realtime|dall-e|imagen|veo|sora|moderation|search|image-gen|-image$|gpt-image)"
# resellers serve models of many makers, so any maker's vision naming counts
VISION_ANY = r"(claude|gemini|deepseek-flash|gpt-4o|gpt-4\.1|gpt-5|gpt-6|o3|o4|vl|omni|qvq|pixtral|llama-4|vision|grok-4|grok-5)"


@dataclass
class ModelInfo:
    id: str
    vision: bool
    # USD per 1M tokens when the provider publishes it
    price_in: float = 0.0
    price_out: float = 0.0
    sure: bool = False


@dataclass
class Detected:
    provider: str
    base_url: str
    models: list[ModelInfo] = field(default_factory=list)
    # False when the provider has no model list (resellers): the model name must be typed in
    listable: bool = True


class DetectError(Exception):
    pass


def _headers(provider: str, key: str) -> dict[str, str]:
    if PRESETS[provider]["protocol"] == "anthropic":
        return {"x-api-key": key, "anthropic-version": "2023-06-01"}
    return {"Authorization": f"Bearer {key}"}


def _parse_models(provider: str, j: dict[str, Any]) -> list[ModelInfo]:
    out = []
    pattern = VISION_PATTERNS.get(provider, VISION_ANY)
    for m in j.get("data", j.get("models", [])):
        mid = str(m.get("id") or m.get("name") or "").removeprefix("models/")
        if not mid or re.search(NOT_CHAT, mid, re.I):
            continue
        arch = m.get("architecture") or {}
        caps = m.get("capabilities") or {}
        # Fireworks marks embedding and completion-only models instead of naming them
        if m.get("supports_chat") is False or m.get("kind") == "EMBEDDING_MODEL":
            continue
        if "supports_image_input" in m:
            info = ModelInfo(mid, bool(m["supports_image_input"]), sure=True)
        elif "input_modalities" in arch:
            info = ModelInfo(mid, "image" in arch["input_modalities"], sure=True)
        elif "vision" in caps:
            info = ModelInfo(mid, bool(caps["vision"]), sure=True)
        else:
            info = ModelInfo(mid, bool(pattern and re.search(pattern, mid, re.I)) if pattern else True)
        pricing = m.get("pricing") or {}
        try:
            info.price_in = float(pricing.get("prompt", 0)) * 1e6
            info.price_out = float(pricing.get("completion", 0)) * 1e6
        except (TypeError, ValueError):
            pass
        out.append(info)
    return sorted(out, key=lambda x: (not x.vision, x.id))


async def _try(client: httpx.AsyncClient, provider: str, key: str, base_url: str) -> Detected | None:
    preset = PRESETS[provider]
    base = (base_url or preset["base_url"]).rstrip("/")
    if provider == "openrouter":
        check = await client.get(f"{base}/key", headers=_headers(provider, key))
        if check.status_code != 200:
            return None
        r = await client.get(f"{base}/models")
    else:
        r = await client.get(f"{base}/models", headers=_headers(provider, key))
    if r.status_code == 200:
        try:
            return Detected(provider, base, _parse_models(provider, r.json()))
        except ValueError:
            return None
    return None


def local_reseller_urls() -> list[str]:
    """OpenAI-compatible resellers this user already set up for Codex.

    Only the user's own configuration counts: a key is never sent to a reseller the user did not choose.
    """
    urls: list[str] = []
    path = Path.home() / ".codex" / "config.toml"
    try:
        providers = tomllib.loads(path.read_text()).get("model_providers", {})
    except (OSError, tomllib.TOMLDecodeError):
        providers = {}
    for p in providers.values():
        url = str(p.get("base_url", "")).rstrip("/") if isinstance(p, dict) else ""
        if url.startswith("https://") and url not in urls:
            urls.append(url)
    return urls


async def _try_reseller(client: httpx.AsyncClient, key: str, base: str) -> Detected | None:
    r = await client.get(f"{base}/models", headers={"Authorization": f"Bearer {key}"})
    if r.status_code == 200:
        try:
            return Detected("custom", base, _parse_models("custom", r.json()))
        except ValueError:
            return None
    return None


# fast vision families of each maker, best first; the newest version in a family wins
FAMILIES = [r"gemini-([\d.]+)-flash", r"deepseek-flash", r"gpt-([\d.]+)-mini", r"claude-(?:[\d.-]+-)?haiku(?:-[\d-]+)?",
            r"qwen([\d.]*)-vl-(?:plus|max)", r"grok-([\d.]+)(?:-fast)?(?:-vision)?", r"pixtral-large"]


def _newest(ids: list[str], family: str) -> str | None:
    found = []
    for mid in ids:
        m = re.search(family, mid.split("/")[-1])
        if m and not re.search(r"lite|nano|image|tts|audio|embed|live|exp|realtime", mid):
            nums = tuple(int(x) for x in re.findall(r"\d+", m.group(0)) if len(x) < 6)
            found.append((nums, "preview" not in mid, -len(mid), mid))
    return max(found)[3] if found else None


def recommended_models(ids: list[str]) -> list[str]:
    """Models worth switching on for captchas when a key is added, whatever the maker.

    Gemini gets its fast Flash plus the thinking variant: on our captcha set the thinking one rescues a few
    clicks the fast one misses. Other makers get their newest fast vision model.
    """
    gemini = _gemini(ids)
    if gemini:
        return gemini
    for family in FAMILIES[1:]:
        best = _newest(ids, family)
        if best:
            return [best]
    return []


def _gemini(ids: list[str]) -> list[str]:
    def version(mid: str) -> tuple:
        m = re.match(r"gemini-([\d.]+)-flash", mid.split("/")[-1])
        return tuple(int(x) for x in m.group(1).split(".")) if m else ()

    flash = [m for m in ids if version(m) and not re.search(r"lite|image|tts|audio|embed|live|exp", m)]
    if not flash:
        return []
    newest = max(version(m) for m in flash)
    same = sorted((m for m in flash if version(m) == newest), key=lambda m: ("preview" in m, len(m)))
    fast = next((m for m in same if m.endswith("-low")), same[0])
    thinking = next((m for m in same if m.endswith("-high")), None)
    return [fast] + ([thinking] if thinking and thinking != fast else [])


PROVIDER_TITLES = {"openai": "OpenAI", "dashscope": tr("DashScope (Китай)"), "dashscope-intl": "DashScope", "mistral": "Mistral",
                   "openrouter": "OpenRouter", "xai": "xAI", "groq": "Groq", "anthropic": "Anthropic", "gemini": "Google Gemini"}


async def detect(key: str, base_url: str = "", transport: httpx.AsyncBaseTransport | None = None,
                 resellers: list[str] | None = None, progress=None) -> Detected:
    """Finds who issued the key. With base_url (a reseller or self-hosted server) only that address is checked."""
    key = key.strip()
    async with httpx.AsyncClient(timeout=20, transport=transport) as client:
        if base_url:
            base = base_url.rstrip("/")
            found = await _try_reseller(client, key, base)
            if found:
                return found
            r = await client.get(f"{base}/models", headers={"Authorization": f"Bearer {key}"})
            if r.status_code in (401, 403):
                raise DetectError(tr("{base} не принял ключ (HTTP {status})", base=base, status=r.status_code))
            probe = await client.post(f"{base}/chat/completions", headers={"Authorization": f"Bearer {key}"}, json={})
            if probe.status_code in (401, 403):
                raise DetectError(tr("{base} не принял ключ (HTTP {status})", base=base, status=probe.status_code))
            return Detected("custom", base, [], listable=False)
        hinted = [p for prefix, p in PREFIX_HINTS if key.startswith(prefix)]
        order = hinted + [p for p in PROBE_ORDER if p not in hinted]
        for provider in order:
            if progress:
                progress(tr("проверяю {name}", name=PROVIDER_TITLES.get(provider, provider)))
            try:
                found = await _try(client, provider, key, "")
            except httpx.HTTPError:
                continue
            if found:
                return found
        for base in (local_reseller_urls() if resellers is None else resellers):
            if progress:
                progress(tr("проверяю посредника {host}", host=base.split("/")[2]))
            try:
                found = await _try_reseller(client, key, base)
            except httpx.HTTPError:
                continue
            if found:
                return found
    raise DetectError(tr("ни один известный поставщик не принял этот ключ; если это посредник — укажите его адрес"))


def detect_sync(key: str, base_url: str = "", progress=None) -> Detected:
    return asyncio.run(detect(key, base_url, progress=progress))
