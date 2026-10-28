"""The browser extension ships with the app: it must stay whole, local and quiet."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

EXT = Path(__file__).parent.parent / "imnotarobot" / "extension"


def read_manifest() -> dict:
    return json.loads((EXT / "manifest.json").read_text())


def test_attribution_and_upstream_files_are_kept():
    for path in ("LICENSE", "NOTICE", "README.md", "service_worker.js"):
        assert (EXT / path).is_file(), path
    assert "2captcha-solver" in (EXT / "NOTICE").read_text()


def test_manifest_files_all_exist():
    m = read_manifest()
    names = [m["background"]["service_worker"], *m["action"]["default_icon"].values(), *m["icons"].values()]
    for cs in m["content_scripts"]:
        names += cs["js"] + cs["css"]
    for path in names:
        assert (EXT / path).is_file(), path


def test_the_api_may_only_talk_to_localhost():
    """Solving goes through the app; anything else the extension could reach would be a leak."""
    m = read_manifest()
    assert m["host_permissions"] == ["http://127.0.0.1/*", "http://localhost/*"]
    # detection still needs to run on every page, which content_scripts matches grant on their own
    assert any("<all_urls>" in cs["matches"] for cs in m["content_scripts"])


def test_no_2captcha_addresses_or_referral_ids_remain_in_code():
    for path in EXT.rglob("*.js"):
        text = path.read_text()
        assert "2captcha.com" not in text, path
        assert "rucaptcha.com" not in text, path
        assert "2834" not in text, path  # the upstream soft id


def test_the_solver_address_is_a_setting_defaulting_to_the_app():
    config = (EXT / "common" / "config.js").read_text()
    assert 'apiService: "http://127.0.0.1:8765"' in config
    api = (EXT / "common" / "api.js").read_text()
    assert "http://127.0.0.1:8765" in api  # the fallback when no service is passed


def test_every_locale_names_the_new_fields():
    for lang in ("en", "ru", "es", "zh"):
        messages = json.loads((EXT / "_locales" / lang / "messages.json").read_text())
        assert messages["apiService"]["message"]
        assert messages["apiKeyIsAppKey"]["message"]
        assert "2captcha" not in messages["extDescription"]["message"].lower()


def test_page_facing_logic_in_node():
    """The extension's own tests: config whitelist for pages, page-solve gating, per-tab rate
    limit, and finding the app by /app-info before the key is ever sent. Needs node."""
    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    script = Path(__file__).parent / "extension_js" / "js_tests.js"
    r = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "all extension js tests passed" in r.stdout
