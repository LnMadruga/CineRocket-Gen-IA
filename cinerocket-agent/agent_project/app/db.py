"""Acesso ao banco e guardrails de segurança para consultas geradas pelo modelo.

O agente só deve LER dados (Text-to-SQL de análise, nunca de escrita). Como o
SQL final é escrito por um LLM, defendemos essa regra em **três camadas
independentes**, para que uma falha em uma camada não comprometa a proteção:

1. A conexão com o SQLite é aberta em modo **read-only** no nível do sistema
   de arquivos (`?mode=ro` na URI) — mesmo um comando de escrita que escape
   da validação falha ao tentar executar, porque o SO/SQLite recusa.
2. `PRAGMA query_only = ON` reforça a mesma garantia na camada do driver.
3. `validar_sql()` inspeciona o texto antes de executar: exige uma única
   instrução, que comece com SELECT/WITH, e rejeita palavras-chave de
   escrita/DDL mesmo dentro de um CTE.

Também aplicamos um teto de linhas devolvidas (evita estourar o contexto do
modelo com milhares de linhas) e um timeout por consulta (evita uma consulta
acidentalmente pesada — ex.: cruzar bridge_movie_person sem filtro — travar o
agente).
"""

from __future__ import annotations

import re
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass

from app.config import settings

# Palavras que não podem aparecer em nenhuma consulta do agente, em nenhuma posição
# (defesa em profundidade: mesmo que apareçam dentro de um CTE ou subquery).
PALAVRAS_PROIBIDAS = [
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "REPLACE",
    "TRUNCATE", "ATTACH", "DETACH", "VACUUM", "PRAGMA", "REINDEX",
]


class ConsultaRecusada(Exception):
    """Levantada quando o SQL proposto pelo modelo não passa pelos guardrails."""


def validar_sql(sql: str) -> str:
    """Garante que `sql` é uma única consulta de LEITURA. Levanta ConsultaRecusada caso contrário."""
    texto = sql.strip().rstrip(";")

    if not texto:
        raise ConsultaRecusada("Consulta vazia.")

    # uma única instrução: não pode haver ';' no meio (encadeamento de comandos)
    if ";" in texto:
        raise ConsultaRecusada("Apenas uma instrução SQL por chamada (nenhum ';' no meio da consulta).")

    primeira_palavra = texto.split(None, 1)[0].upper()
    if primeira_palavra not in ("SELECT", "WITH"):
        raise ConsultaRecusada("Só são permitidas consultas SELECT (ou WITH ... SELECT).")

    # varre por palavra inteira (word boundary) para não bloquear colunas como "created_at"
    maiusculo = texto.upper()
    for palavra in PALAVRAS_PROIBIDAS:
        if re.search(rf"\b{palavra}\b", maiusculo):
            raise ConsultaRecusada(f"Palavra-chave não permitida nesta consulta: {palavra}.")

    return texto


@dataclass
class ResultadoConsulta:
    columns: list[str]
    rows: list[tuple]
    truncated: bool
    elapsed_ms: float


@contextmanager
def _conexao_leitura():
    """Conexão SQLite aberta estritamente em modo leitura (nível de arquivo)."""
    uri = f"file:{settings.database_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        conn.execute("PRAGMA query_only = ON;")  # segunda camada de defesa
        yield conn
    finally:
        conn.close()


def executar_consulta(sql: str, *, max_rows: int | None = None) -> ResultadoConsulta:
    """Valida e executa uma consulta de leitura, com teto de linhas e timeout."""
    sql_validado = validar_sql(sql)
    limite = max_rows or settings.max_rows_returned

    with _conexao_leitura() as conn:
        inicio_limite = time.monotonic()

        def verificador_tempo(*_args):
            # chamado periodicamente pelo SQLite durante a execução; retornar
            # != 0 interrompe a consulta (proteção contra consultas muito pesadas)
            if time.monotonic() - inicio_limite > settings.query_timeout_seconds:
                return 1
            return 0

        conn.set_progress_handler(verificador_tempo, 1000)
        inicio = time.monotonic()
        try:
            cursor = conn.execute(sql_validado)
            colunas = [c[0] for c in cursor.description] if cursor.description else []
            linhas = cursor.fetchmany(limite + 1)
        except sqlite3.OperationalError as exc:
            if "interrupted" in str(exc).lower():
                raise ConsultaRecusada(
                    f"Consulta interrompida: excedeu o limite de {settings.query_timeout_seconds}s. "
                    "Adicione filtros (WHERE) ou um LIMIT para reduzir o custo."
                ) from exc
            raise ConsultaRecusada(f"Erro ao executar a consulta: {exc}") from exc
        elapsed_ms = (time.monotonic() - inicio) * 1000

    truncado = len(linhas) > limite
    if truncado:
        linhas = linhas[:limite]
    return ResultadoConsulta(columns=colunas, rows=linhas, truncated=truncado, elapsed_ms=elapsed_ms)
