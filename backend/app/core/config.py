import logging.config
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CODERISK_", env_file=ROOT / ".env", extra="ignore"
    )
    app_name: str = "CodeRisk"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    mock_enabled: bool = True


def configure_logging(level: str) -> None:
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {"default": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"}},
            "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "default"}},
            "root": {"level": level, "handlers": ["console"]},
        }
    )
