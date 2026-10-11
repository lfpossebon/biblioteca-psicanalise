"""Configuração central: variáveis de ambiente (.env) + arquivos YAML versionados em config/."""

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql://psi:psi@localhost:5433/psilib"
    corpus_dir: Path = PROJECT_ROOT.parent  # acervo local (montado read-only no Docker)
    data_dir: Path = PROJECT_ROOT / "data"  # raw baixado (Staferla, OpenAlex) e derivados (OCR)
    config_dir: Path = PROJECT_ROOT / "config"
    catalog_dir: Path = PROJECT_ROOT / "catalog"
    migrations_dir: Path = PROJECT_ROOT / "db" / "migrations"

    # Embeddings: modelo multilíngue para buscar em PT e achar trechos em FR/ES.
    embedding_provider: str = "local"  # local | voyage
    embedding_model: str = "BAAI/bge-m3"
    # Revisão fixa do modelo no Hugging Face: se o repositório for atualizado, vetores novos
    # deixariam de ser comparáveis aos antigos (mesmo nome em chunk_embedding.model).
    embedding_revision: str | None = "5617a9f61b028005a4858fdac845db406aefb181"
    embedding_dim: int = 1024
    embedding_batch: int = 32
    embedding_device: str = "auto"  # auto | cpu | mps (GPU Apple) | cuda
    voyage_api_key: str | None = None
    embedding_warmup: bool = True  # carrega o modelo ao subir a API (desligado nos testes)

    # Chunking (sem sobreposição: cada ocorrência de conceito conta uma vez — ver ADR 0002)
    chunk_words: int = 350

    # Extração
    min_chars_per_page: int = 200  # abaixo disso a página é tratada como imagem → OCR
    ocr_languages: str = "por+spa+fra+eng"

    # Coleta web
    http_user_agent: str = "psilib/0.1 (biblioteca de estudo pessoal; contato: veja README)"
    http_delay_seconds: float = 2.0  # cortesia com sites pequenos como o Staferla
    openalex_mailto: str | None = None

    # Implantação pública (POC): só o chat é servido; API REST, /docs e MCP ficam desligados.
    public_mode: bool = False

    # Chat da POC (a chave vem de ANTHROPIC_API_KEY, lida pelo próprio SDK)
    chat_model: str = "claude-opus-5-5"
    chat_effort: str = "medium"  # low | medium | high | xhigh | max
    chat_daily_limit: int = 30  # perguntas por conexão (hash do IP) por dia
    chat_ip_salt: str = "psilib-poc"  # sal do hash do IP; troque na nuvem (CHAT_IP_SALT)
    chat_daily_budget_usd: float = 15.0  # teto global de gasto por dia
    chat_max_turns: int = 12  # perguntas por conversa (o custo cresce com o histórico)
    # Preços em US$ por milhão de tokens (Claude Opus 5.5), para o controle de gasto
    chat_price_input: float = 4.0
    chat_price_output: float = 20.0
    chat_price_cache_read: float = 0.20
    chat_price_cache_write: float = 5.0


@lru_cache
def settings() -> Settings:
    return Settings()


@lru_cache
def load_yaml(name: str) -> dict:
    with open(settings().config_dir / name, encoding="utf-8") as f:
        return yaml.safe_load(f)
