import asyncio

import httpx
import pytest

from imnotarobot.discovery import DetectError, detect


def fake_world(accepting: str, models: dict, calls: list):
    """Every provider rejects the key except `accepting`, which lists `models`."""
    hosts = {"api.openai.com": "openai", "api.deepseek.com": "deepseek", "dashscope.aliyuncs.com": "dashscope", "dashscope-intl.aliyuncs.com": "dashscope-intl",
             "api.mistral.ai": "mistral", "openrouter.ai": "openrouter", "api.x.ai": "xai", "api.groq.com": "groq",
             "api.anthropic.com": "anthropic", "generativelanguage.googleapis.com": "gemini", "reseller.example": "custom"}

    def handler(request: httpx.Request) -> httpx.Response:
        who = hosts[request.url.host]
        calls.append((who, request.url.path))
        if who == "openrouter" and request.url.path.endswith("/models"):
            return httpx.Response(200, json=models)
        if who != accepting:
            return httpx.Response(401, json={"error": "bad key"})
        if who == "custom" and not models:
            return httpx.Response(404 if request.url.path.endswith("/models") else 400, text="nope")
        if request.url.path.endswith("/key"):
            return httpx.Response(200, json={"data": {}})
        return httpx.Response(200, json=models)

    return httpx.MockTransport(handler)


def run(key, accepting, models, base_url=""):
    calls: list = []
    found = asyncio.run(detect(key, base_url, transport=fake_world(accepting, models, calls)))
    return found, calls


def test_prefix_goes_straight_to_the_provider():
    found, calls = run("sk-ant-abc", "anthropic", {"data": [{"id": "claude-sonnet-x"}, {"id": "claude-haiku-x"}]})
    assert found.provider == "anthropic" and calls == [("anthropic", "/v1/models")]
    assert [m.id for m in found.models if m.vision] == ["claude-haiku-x", "claude-sonnet-x"]


def test_unprefixed_key_is_probed_until_someone_accepts():
    found, calls = run("sk-plain", "dashscope-intl", {"data": [{"id": "qwen3-vl-plus"}, {"id": "qwen-max"}, {"id": "text-embedding-v3"}]})
    assert found.provider == "dashscope-intl"
    assert [c[0] for c in calls][:3] == ["openai", "deepseek", "dashscope"]
    assert [(m.id, m.vision) for m in found.models] == [("qwen3-vl-plus", True), ("qwen-max", False)]


def test_openrouter_uses_published_modalities_and_prices():
    models = {"data": [
        {"id": "google/gemini-x", "architecture": {"input_modalities": ["text", "image"]},
         "pricing": {"prompt": "0.0000003", "completion": "0.0000025"}},
        {"id": "deepseek/text-only", "architecture": {"input_modalities": ["text"]}, "pricing": {"prompt": "0", "completion": "0"}},
    ]}
    found, _ = run("sk-or-v1-x", "openrouter", models)
    gem = found.models[0]
    assert (gem.id, gem.vision, gem.sure) == ("google/gemini-x", True, True)
    assert gem.price_in == pytest.approx(0.3) and gem.price_out == pytest.approx(2.5)
    assert found.models[1].vision is False


def test_gemini_ids_lose_their_prefix():
    found, _ = run("AIzaXYZ", "gemini", {"data": [{"id": "models/gemini-2.5-flash"}, {"id": "models/text-embedding-004"}]})
    assert [m.id for m in found.models] == ["gemini-2.5-flash"]


def test_reseller_without_model_list_is_accepted_for_manual_entry():
    found, _ = run("sk-reseller", "custom", {}, base_url="https://reseller.example/v1")
    assert (found.provider, found.listable, found.base_url) == ("custom", False, "https://reseller.example/v1")


def test_reseller_rejecting_the_key_is_an_error():
    with pytest.raises(DetectError, match="не принял"):
        run("sk-bad", "nobody", {}, base_url="https://reseller.example/v1")


def test_nobody_accepts():
    with pytest.raises(DetectError, match="ни один"):
        run("sk-nothing", "nobody", {"data": []})


def test_reseller_from_local_codex_config_is_tried_after_providers():
    calls: list = []
    models = {"data": [{"id": "claude-opus-5-5"}, {"id": "gpt-6-luna"}, {"id": "glm-5.3"},
                       {"id": "gpt-4o-transcribe"}, {"id": "gpt-image-2"}]}
    found = asyncio.run(detect("sk-reseller-x", transport=fake_world("custom", models, calls),
                               resellers=["https://reseller.example/v1"]))
    assert (found.provider, found.base_url) == ("custom", "https://reseller.example/v1")
    assert [(m.id, m.vision) for m in found.models] == [("claude-opus-5-5", True), ("gpt-6-luna", True), ("glm-5.3", False)]
    assert calls[-1] == ("custom", "/v1/models")


def test_progress_names_every_provider_tried():
    seen: list = []
    asyncio.run(detect("sk-ant-x", transport=fake_world("anthropic", {"data": []}, []), progress=seen.append))
    assert seen == ["проверяю Anthropic"]


def test_newest_flash_and_its_thinking_variant_are_recommended():
    from imnotarobot.discovery import recommended_models

    assert recommended_models(["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-3-flash-preview", "gemini-2.5-pro"]) == ["gemini-3-flash-preview"]
    assert recommended_models(["gemini-3.8-flash-low", "gemini-3.8-flash-high", "gemini-3.5-flash", "gemini-3.8-flash-image"]) == \
        ["gemini-3.8-flash-low", "gemini-3.8-flash-high"]
    assert recommended_models(["gpt-4o", "qwen-vl-max"]) == ["qwen-vl-max"]
    assert recommended_models(["gpt-5", "gpt-5-mini", "gpt-4.1-mini", "gpt-5-nano"]) == ["gpt-5-mini"]
    assert recommended_models(["deepseek-flash", "deepseek-v4-pro"]) == ["deepseek-flash"]
    assert recommended_models(["claude-opus-5", "claude-haiku-5-5", "claude-3-5-haiku"]) == ["claude-haiku-5-5"]
    assert recommended_models(["openai/gpt-5-mini", "google/gemini-3-flash-preview"]) == ["google/gemini-3-flash-preview"]
    assert recommended_models(["llava", "whisper-1"]) == []


def test_no_reseller_is_tried_unless_the_user_set_one_up(tmp_path, monkeypatch):
    from pathlib import Path

    import importlib

    from imnotarobot import discovery

    real = importlib.reload(discovery).local_reseller_urls  # conftest replaces it for every other test
    monkeypatch.setattr(discovery, "local_reseller_urls", real)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert discovery.local_reseller_urls() == []
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".codex" / "config.toml").write_text('[model_providers.mine]\nbase_url = "https://mine.example/v1"\n')
    assert discovery.local_reseller_urls() == ["https://mine.example/v1"]


def test_fireworks_flags_decide_vision_and_drop_non_chat_models():
    from imnotarobot.discovery import _parse_models

    models = _parse_models("custom", {"data": [
        {"id": "accounts/fireworks/models/kimi-k3", "supports_chat": True, "supports_image_input": True},
        {"id": "accounts/fireworks/models/glm-5p3", "supports_chat": True, "supports_image_input": False},
        {"id": "accounts/fireworks/models/qwen3-embedding-8b", "kind": "EMBEDDING_MODEL", "supports_chat": True},
        {"id": "accounts/fireworks/models/rune", "supports_chat": False, "supports_image_input": True},
    ]})
    assert [(m.id.rsplit("/", 1)[-1], m.vision, m.sure) for m in models] == [("kimi-k3", True, True), ("glm-5p3", False, True)]
