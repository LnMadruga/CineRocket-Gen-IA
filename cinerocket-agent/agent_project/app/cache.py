"""Cache de respostas em disco (SQLite), por pergunta normalizada + modelo.

Existe por um motivo concreto, não só por "ser bônus": a conta gratuita do
OpenRouter permite só 50 requisições/dia, e um agente de tool-calling gasta
de 2 a 4 chamadas por pergunta. Repetir a mesma pergunta durante o
desenvolvimento (testando o prompt, por exemplo) queima a cota rapidamente
sem necessidade — se a pergunta já foi respondida com o mesmo modelo, a
resposta é reaproveitada do cache em vez de chamar a API de novo.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from app.config import settings


def _chave(pergunta: str, modelo: str) -> str:
    normalizada = " ".join(pergunta.strip().lower().split())
    return hashlib.sha256(f"{modelo}::{normalizada}".encode()).hexdigest()


class CacheRespostas:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or settings.cache_path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS respostas (
                chave TEXT PRIMARY KEY,
                pergunta TEXT NOT NULL,
                modelo TEXT NOT NULL,
                resposta_json TEXT NOT NULL,
                criado_em REAL NOT NULL
            )"""
        )
        self._conn.commit()

    def obter(self, pergunta: str, modelo: str) -> dict[str, Any] | None:
        if not settings.cache_enabled:
            return None
        chave = _chave(pergunta, modelo)
        linha = self._conn.execute("SELECT resposta_json FROM respostas WHERE chave = ?", (chave,)).fetchone()
        return json.loads(linha[0]) if linha else None

    def salvar(self, pergunta: str, modelo: str, resposta: Any) -> None:
        if not settings.cache_enabled:
            return
        dados = asdict(resposta) if is_dataclass(resposta) else resposta
        chave = _chave(pergunta, modelo)
        self._conn.execute(
            "INSERT OR REPLACE INTO respostas (chave, pergunta, modelo, resposta_json, criado_em) VALUES (?, ?, ?, ?, ?)",
            (chave, pergunta, modelo, json.dumps(dados, ensure_ascii=False, default=str), time.time()),
        )
        self._conn.commit()

    def limpar(self) -> None:
        self._conn.execute("DELETE FROM respostas")
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
