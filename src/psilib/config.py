"""Configuração central: variáveis de ambiente (.env) + arquivos YAML versionados em config/."""

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql://psi:psi@localhost:5433/psilib"
    corpus_dir: Path = PROJECT_ROOT.parent          # acervo local (montado read-only no Docker)
    data_dir: Path = PROJECT_ROOT / "data"          # raw baixado (Staferla, OpenAlex) e derivados (OCR)
    config_dir: Path = PROJECT_ROOT / "config"
    catalog_dir: Path = PROJECT_ROOT / "catalog"
    migrations_dir: Path = PROJECT_ROOT / "db" / "migrations"

    # Embeddings: modelo multilíngue para buscar em PT e achar trechos em FR/ES.
    embedding_provider: str = "local"               # local | voyage
    embedding_model: str = "BAAI/bge-m3"
    embedding_dim: int = 1024
    embedding_batch: int = 32
    embedding_device: str = "auto"                  # auto | cpu | mps (GPU Apple) | cuda
    voyage_api_key: str | None = None

    # Chunking (sem sobreposição: cada ocorrência de conceito conta uma vez — ver ADR 0002)
    chunk_words: int = 350

    # Extração
    min_chars_per_page: int = 200                   # abaixo disso a página é tratada como imagem → OCR
    ocr_languages: str = "por+spa+fra+eng"

    # Coleta web
    http_user_agent: str = "psilib/0.1 (biblioteca de estudo pessoal; contato: veja README)"
    http_delay_seconds: float = 2.0                 # cortesia com sites pequenos como o Staferla
    openalex_mailto: str | None = None


@lru_cache
def settings() -> Settings:
    return Settings()


@lru_cache
def load_yaml(name: str) -> dict:
    with open(settings().config_dir / name, encoding="utf-8") as f:
        return yaml.safe_load(f)
