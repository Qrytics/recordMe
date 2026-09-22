"""Configuration, loaded from .env. See .env.example."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _req(key: str) -> str:
    val = os.getenv(key)
    if not val or val == "replace-me":
        raise RuntimeError(f"{key} is unset or still the placeholder — see pi/.env.example")
    return val


@dataclass(frozen=True)
class Config:
    upload_token: str
    db_path: Path
    audio_dir: Path

    receiver_host: str
    receiver_port: int
    search_host: str
    search_port: int
    search_password: str

    whisper_model: str
    whisper_compute: str
    whisper_threads: int
    vad_enabled: bool

    ollama_url: str
    ollama_model: str

    @classmethod
    def load(cls) -> "Config":
        return cls(
            upload_token=_req("RECORDME_UPLOAD_TOKEN"),
            db_path=Path(_req("RECORDME_DB_PATH")),
            audio_dir=Path(_req("RECORDME_AUDIO_DIR")),
            receiver_host=os.getenv("RECORDME_RECEIVER_HOST", "0.0.0.0"),
            receiver_port=int(os.getenv("RECORDME_RECEIVER_PORT", "8000")),
            search_host=os.getenv("RECORDME_SEARCH_HOST", "127.0.0.1"),
            search_port=int(os.getenv("RECORDME_SEARCH_PORT", "8001")),
            search_password=_req("RECORDME_SEARCH_PASSWORD"),
            whisper_model=os.getenv("RECORDME_WHISPER_MODEL", "small.en"),
            whisper_compute=os.getenv("RECORDME_WHISPER_COMPUTE", "int8"),
            whisper_threads=int(os.getenv("RECORDME_WHISPER_THREADS", "4")),
            vad_enabled=os.getenv("RECORDME_VAD_ENABLED", "1") == "1",
            ollama_url=os.getenv("RECORDME_OLLAMA_URL", "http://127.0.0.1:11434"),
            ollama_model=os.getenv("RECORDME_OLLAMA_MODEL", "llama3.1:8b"),
        )
