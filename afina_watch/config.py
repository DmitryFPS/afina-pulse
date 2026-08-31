from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class KafkaCfg(BaseModel):
    bootstrap: str = "127.0.0.1:9092"
    topic: str = "relay.events"
    group_id: str = "afina-watch"


class MongoCfg(BaseModel):
    uri: str = "mongodb://127.0.0.1:27017"
    database: str = "relay"
    events_collection: str = "events"


class TelegramCfg(BaseModel):
    enabled: bool = True
    mode: str = "both"
    kafka: KafkaCfg = Field(default_factory=KafkaCfg)
    mongo: MongoCfg = Field(default_factory=MongoCfg)
    relay_api: str = "http://127.0.0.1:8090"
    route_allowlist: list[str] = Field(default_factory=list)


class FbPageToken(BaseModel):
    page_id: str
    token: str


class FacebookGraphCfg(BaseModel):
    api_version: str = "v21.0"
    page_tokens: list[FbPageToken] = Field(default_factory=list)
    poll_seconds: int = 60


class FacebookBrowserCfg(BaseModel):
    enabled: bool = False
    user_data_dir: str = "./data/fb-profile"
    poll_seconds: int = 180


class FacebookImportCfg(BaseModel):
    enabled: bool = True
    inbox_dir: str = "./data/fb-inbox"
    poll_seconds: int = 20
    archive: bool = True


class FacebookCfg(BaseModel):
    enabled: bool = False
    graph: FacebookGraphCfg = Field(default_factory=FacebookGraphCfg)
    browser: FacebookBrowserCfg = Field(default_factory=FacebookBrowserCfg)
    import_box: FacebookImportCfg = Field(default_factory=FacebookImportCfg)


class LlmCfg(BaseModel):
    provider: str = "ollama"
    host: str = "http://127.0.0.1:11434"
    text_model: str = "qwen2.5:7b"
    vision_model: str = "qwen2.5vl:7b"
    embed_model: str = "bge-m3"
    embed_fallback: str = "intfloat/multilingual-e5-large"
    whisper_model: str = "medium"
    whisper_device: str = "auto"
    temperature: float = 0.1
    max_item_chars: int = 8000


class MatchingCfg(BaseModel):
    default_semantic_threshold: float = 0.72
    keyword_casefold: bool = True
    run_llm_if_semantic_above: float = 0.55
    run_vlm_on_media: bool = True
    video_max_frames: int = 8
    video_max_seconds: int = 180


class AlertTelegramCfg(BaseModel):
    enabled: bool = False
    bot_token: str = ""
    chat_id: str = ""


class AlertWebhookCfg(BaseModel):
    enabled: bool = False
    url: str = ""


class AlertsCfg(BaseModel):
    telegram: AlertTelegramCfg = Field(default_factory=AlertTelegramCfg)
    webhook: AlertWebhookCfg = Field(default_factory=AlertWebhookCfg)
    stdout: bool = True


class RuleCfg(BaseModel):
    id: str
    enabled: bool = True
    keywords: list[str] = Field(default_factory=list)
    phrases: list[str] = Field(default_factory=list)
    semantic_threshold: float | None = None
    always_llm: bool = False
    sources: dict[str, list[str]] = Field(default_factory=dict)
    actions: list[str] = Field(default_factory=lambda: ["store"])


class WindowCfg(BaseModel):
    """7 дней набора → архив.

    lookback_days — старше этого при ingest отбрасываем.
    hot_days — сколько держим в SQLite/media до упаковки.
    auto_archive — раз в сутки паковать просроченное, не дожидаясь CLI.
    """

    lookback_days: int = 7
    hot_days: int = 7
    archive_dir: str = "./data/archive"
    include_media: bool = True
    delete_hot_media_after_archive: bool = False
    auto_archive: bool = False
    auto_archive_every_hours: int = 24


class AppCfg(BaseModel):
    name: str = "afina-watch"
    log_level: str = "INFO"
    db_path: str = "./data/watch.sqlite"
    media_cache: str = "./data/media"
    workers: int = 2
    window: WindowCfg = Field(default_factory=WindowCfg)


class WatchConfig(BaseModel):
    app: AppCfg = Field(default_factory=AppCfg)
    telegram: TelegramCfg = Field(default_factory=TelegramCfg)
    facebook: FacebookCfg = Field(default_factory=FacebookCfg)
    llm: LlmCfg = Field(default_factory=LlmCfg)
    matching: MatchingCfg = Field(default_factory=MatchingCfg)
    alerts: AlertsCfg = Field(default_factory=AlertsCfg)
    rules: list[RuleCfg] = Field(default_factory=list)

    @property
    def window(self) -> WindowCfg:
        return self.app.window


class EnvSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WATCH_", extra="ignore")


def load_config(path: str | Path) -> WatchConfig:
    raw: dict[str, Any] = {}
    p = Path(path)
    if p.exists():
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return WatchConfig.model_validate(raw)
