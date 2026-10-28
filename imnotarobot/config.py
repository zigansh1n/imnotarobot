"""Settings live in a JSON file; API keys live in the macOS Keychain, never in the file."""

import json
import os
import subprocess
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from .providers import ModelSpec
from .i18n import tr

# the folder keeps its first name so existing settings, journal and keys survive the rename
APP_DIR = Path(os.environ.get("IMNOTAROBOT_HOME") or os.environ.get("CAPTCHA_STACK_HOME")
               or Path.home() / "Library" / "Application Support" / "captcha-stack")
KEYCHAIN_SERVICE = "captcha-stack"
# all three speak the same createTask / getTaskResult API
HUMAN_PRESETS = {"2captcha": "https://api.2captcha.com", "rucaptcha": "https://api.rucaptcha.com",
                 "anticaptcha": "https://api.anti-captcha.com"}
# RuCaptcha bills and reports balance in roubles, the others in dollars
HUMAN_CURRENCY = {"2captcha": "$", "rucaptcha": "₽", "anticaptcha": "$"}
HUMAN_TOPUP = {"2captcha": "https://2captcha.com/enterpage", "rucaptcha": "https://rucaptcha.com/enterpage",
               "anticaptcha": "https://anti-captcha.com/clients/finance/refill"}


# Secrets go through Apple's own `security` tool. An item written by the app itself is tied to that build's code
# hash, so every rebuild lost access to it; an item written by /usr/bin/security stays readable by that tool.
# Trade-off: the value is briefly visible in the tool's argument list while it is being saved.
SECURITY = "/usr/bin/security"
SECRET_SERVICE = "Captcha Stack"
LEGACY_SERVICE = KEYCHAIN_SERVICE


def _secret_file() -> Path:
    return Path(APP_DIR) / "secrets.json"


def _file_secrets() -> dict[str, str]:
    try:
        return json.loads(_secret_file().read_text())
    except (OSError, ValueError):
        return {}


def keychain() -> bool:
    """Off a Mac (servers, Docker) secrets live in a file only the owner can read."""
    return os.path.exists(SECURITY)


def get_secret(account: str) -> str:
    if not keychain():
        return _file_secrets().get(account, "")
    r = subprocess.run([SECURITY, "find-generic-password", "-s", SECRET_SERVICE, "-a", account, "-w"],
                       capture_output=True, text=True, check=False)
    return r.stdout.strip() if r.returncode == 0 else ""


def set_secret(account: str, value: str) -> None:
    if not keychain():
        data = _file_secrets()
        if value:
            data[account] = value
        else:
            data.pop(account, None)
        path = _secret_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.touch(mode=0o600)
        tmp.write_text(json.dumps(data))
        tmp.replace(path)
        return
    if value:
        r = subprocess.run([SECURITY, "add-generic-password", "-U", "-s", SECRET_SERVICE, "-a", account,
                            "-l", f"Captcha Stack — {account}", "-w", value], capture_output=True, text=True, check=False)
        if r.returncode != 0:
            raise RuntimeError(tr("Связка ключей не сохранила ключ: {error}", error=r.stderr.strip()[:120]))
    else:
        subprocess.run([SECURITY, "delete-generic-password", "-s", SECRET_SERVICE, "-a", account],
                       capture_output=True, check=False)


def legacy_secret_exists(account: str) -> bool:
    """A key saved by an older build that this build cannot read: the user has to enter it once more."""
    if not keychain():
        return False
    r = subprocess.run([SECURITY, "find-generic-password", "-s", LEGACY_SERVICE, "-a", account],
                       capture_output=True, check=False)
    return r.returncode == 0


def model_key(spec: ModelSpec) -> str:
    if spec.key:
        return get_secret(f"key:{spec.key}")
    return get_secret(f"model:{spec.name}") or os.environ.get(f"{spec.name.upper().replace('-', '_')}_API_KEY", "")


@dataclass
class KeyEntry:
    """An API key the user added; the secret itself is in the Keychain under key:<name>."""

    name: str
    provider: str
    base_url: str = ""
    hint: str = ""
    # model ids that accept images, as last listed by the provider: [id, vision, price_in, price_out]
    models: list[list] = field(default_factory=list)
    listable: bool = True
    # the user's own label, e.g. which service or account the key is from
    note: str = ""


@dataclass
class HumanAccount:
    """A spare workers' service account, tried after the main one; the key is in the Keychain under human:<id>."""

    id: str
    provider: str
    note: str = ""
    hint: str = ""


@dataclass
class Config:
    client_key: str = ""
    # programs must send client_key as their API key; without it any program on this Mac could spend the money
    require_client_key: bool = True
    port: int = 8765
    layers: list[str] = field(default_factory=lambda: ["local", "vlm", "browser", "human"])
    ocr_min_confidence: float = 0.9
    keys: list[KeyEntry] = field(default_factory=list)
    models: list[ModelSpec] = field(default_factory=list)
    human_provider: str = "rucaptcha"
    human_base_url: str = ""
    human_api_key: str = ""
    human_key_hint: str = ""
    human_note: str = ""
    # tried in order when the main account cannot take a captcha (no money, wrong kind, service down)
    human_extra: list[HumanAccount] = field(default_factory=list)
    # spending of a dollar account is added to a rouble limit (and back) at this rate
    rub_per_usd: float = 80.0
    human_timeout: float = 180.0
    human_poll_interval: float = 5.0
    # in the workers' service currency (see HUMAN_CURRENCY); 0 = no limit
    human_daily_limit: float = 100.0
    # warn in the menu when the workers' balance drops below this, same currency
    human_low_balance: float = 50.0
    # this many captchas sent to the workers within human_alarm_minutes means the cheaper layers are failing
    human_alarm_count: int = 5
    human_alarm_minutes: float = 30.0
    # programs the model layers depend on (local model servers); started hidden when they are not running
    companion_apps: list[str] = field(default_factory=list)
    # on: started with the app and again whenever they are not running; off: never started by the app
    companion_restart: bool = False
    # all cloud models together, USD per day; 0 = no limit. Models with unknown prices are not counted
    cloud_daily_limit_usd: float = 1.0
    # a layer is skipped for a captcha kind once it has this many reports and fails at least half of them
    skip_after_reports: int = 5
    task_ttl: float = 600.0
    state_path: str = ""
    # bumped when saved settings need migrating
    schema: int = 3

    def __post_init__(self):
        self.human_base_url = (self.human_base_url or HUMAN_PRESETS.get(self.human_provider, "")).rstrip("/")
        self.state_path = self.state_path or str(APP_DIR / "state.json")

    @classmethod
    def load(cls, path: Path | None = None, with_secrets: bool = True) -> "Config":
        path = path or APP_DIR / "config.json"
        raw: dict[str, Any] = json.loads(path.read_text()) if path.exists() else {}
        names = {f.name for f in fields(cls)} - {"models", "keys", "human_api_key", "human_extra"}
        if "human_daily_limit_usd" in raw and "human_daily_limit" not in raw:
            raw["human_daily_limit"] = raw["human_daily_limit_usd"]
        cfg = cls(**{k: v for k, v in raw.items() if k in names})
        cfg.keys = [KeyEntry(**k) for k in raw.get("keys", [])]
        cfg.models = [ModelSpec(**m) for m in raw.get("models", [])]
        cfg.human_extra = [HumanAccount(**a) for a in raw.get("human_extra", [])]
        if raw.get("schema", 1) < 2:
            # schema 1 always asked models 3 times; free quotas last longer with one ask
            for m in cfg.models:
                if m.votes == 3:
                    m.votes = 1
        if raw.get("schema", 1) < 3 and "browser" not in cfg.layers:
            at = cfg.layers.index("human") if "human" in cfg.layers else len(cfg.layers)
            cfg.layers.insert(at, "browser")
        cfg.schema = 3
        if with_secrets:
            for m in cfg.models:
                m.api_key = m.api_key or model_key(m)
            cfg.human_api_key = get_secret("human") or os.environ.get("HUMAN_API_KEY", "")
        if os.environ.get("LAYERS"):
            cfg.layers = [x.strip() for x in os.environ["LAYERS"].split(",") if x.strip()]
        cfg.client_key = os.environ.get("CLIENT_KEY", cfg.client_key)
        return cfg

    def save(self, path: Path | None = None) -> None:
        path = path or APP_DIR / "config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        data = asdict(self)
        data.pop("human_api_key")
        for m in data["models"]:
            m.pop("api_key", None)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        tmp.replace(path)
