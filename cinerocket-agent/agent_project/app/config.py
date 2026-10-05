"""Configuração do agente: lida do ambiente (.env), com valores padrão sensatos.

Mantido em um único lugar, sem framework extra (nem pydantic-settings), porque
a lista de parâmetros é pequena e um módulo simples é mais fácil de ler e de
defender do que uma dependência adicional só para isso.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # lê o arquivo .env na raiz do projeto, se existir

BASE_DIR = Path(__file__).resolve().parent.parent


def _env_list(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name)
    if not raw:
        return default
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    # --- OpenRouter ---
    openrouter_api_key: str = field(default_factory=lambda: os.getenv("OPENROUTER_API_KEY", ""))
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    # Modelo principal + cadeia de fallback (todos gratuitos, com suporte a tool calling).
    # A disponibilidade de modelos :free muda com frequência — confira
    # https://openrouter.ai/models?q=:free antes de travar numa lista definitiva.
    models: list[str] = field(
        default_factory=lambda: _env_list(
            "OPENROUTER_MODELS",
            [
                "nvidia/nemotron-3.5-lightning:free",
                "z-ai/glm-5.2:free",
                "google/gemma-4-26b-a4b-it:free",
                "openrouter/free",  # roteador automático: último recurso
            ],
        )
    )

    # --- Banco de dados ---
    database_path: Path = field(default_factory=lambda: Path(os.getenv("DATABASE_PATH", BASE_DIR / "data" / "cinerocket.db")))

    # --- Guardrails / cota ---
    max_tool_call_rounds: int = int(os.getenv("MAX_TOOL_CALL_ROUNDS", "4"))  # limite de idas e voltas com o modelo por pergunta
    max_rows_returned: int = int(os.getenv("MAX_ROWS_RETURNED", "200"))      # linhas no máximo devolvidas por consulta
    query_timeout_seconds: float = float(os.getenv("QUERY_TIMEOUT_SECONDS", "10"))

    # --- Cache de respostas (bônus) ---
    cache_enabled: bool = os.getenv("CACHE_ENABLED", "true").lower() in ("1", "true", "yes")
    cache_path: Path = field(default_factory=lambda: Path(os.getenv("CACHE_PATH", BASE_DIR / "data" / "cache.sqlite")))

    def validate(self) -> None:
        if not self.openrouter_api_key:
            raise RuntimeError(
                "OPENROUTER_API_KEY não definido. Copie .env.example para .env e preencha sua chave "
                "(gerada em https://openrouter.ai/keys)."
            )
        if not self.database_path.exists():
            raise RuntimeError(
                f"Banco de dados não encontrado em {self.database_path}. Copie o arquivo cinerocket.db "
                "fornecido pelo professor para a pasta data/."
            )


settings = Settings()
