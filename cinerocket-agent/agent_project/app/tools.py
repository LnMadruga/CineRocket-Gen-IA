"""Definição das ferramentas (function/tool calling) que o modelo pode chamar.

Só duas ferramentas são expostas de propósito: manter a superfície pequena
reduz a chance do modelo "se perder" decidindo qual ferramenta usar (e gasta
menos tokens/chamadas). O schema completo já vai no prompt (app/schema.py),
então não há uma ferramenta "listar tabelas" — ela seria só mais uma chamada
desnecessária consumindo a cota diária do free tier.
"""

from __future__ import annotations

from typing import Any

from app.db import ConsultaRecusada, executar_consulta
from app.semantic_search import BuscaSinopses

TOOLS_SCHEMA: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "executar_sql",
            "description": (
                "Executa uma consulta SQL de LEITURA (SELECT) no banco SQLite do catálogo de "
                "filmes e devolve as linhas do resultado. Use sempre esta ferramenta antes de "
                "responder qualquer pergunta que dependa de dados do catálogo."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sql": {
                        "type": "string",
                        "description": "Consulta SQL (SELECT ou WITH...SELECT). Nunca INSERT/UPDATE/DELETE/DDL.",
                    }
                },
                "required": ["sql"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "buscar_sinopses_similares",
            "description": (
                "Busca filmes cuja SINOPSE é textualmente mais parecida com a frase de consulta "
                "(similaridade léxica TF-IDF, não é uma busca exata de palavra-chave). Útil para "
                "perguntas como 'filmes parecidos com uma história de viagem no tempo', quando não "
                "há uma coluna estruturada para filtrar. As sinopses no banco estão majoritariamente "
                "em INGLÊS — para melhores resultados, formule o parâmetro `consulta` em inglês, "
                "mesmo que a pergunta original do usuário esteja em português. Não serve para "
                "perguntas numéricas/agregadas — para isso use executar_sql."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "consulta": {"type": "string", "description": "Descrição em linguagem natural do que buscar na sinopse."},
                    "top_k": {"type": "integer", "description": "Quantos filmes retornar (padrão 5, máximo 15)."},
                },
                "required": ["consulta"],
            },
        },
    },
]

_busca_sinopses: BuscaSinopses | None = None


def _get_busca_sinopses() -> BuscaSinopses:
    global _busca_sinopses
    if _busca_sinopses is None:
        _busca_sinopses = BuscaSinopses()
        _busca_sinopses.carregar()
    return _busca_sinopses


def executar_tool_call(nome: str, argumentos: dict[str, Any]) -> dict[str, Any]:
    """Despacha a chamada de ferramenta decidida pelo modelo e devolve um dict serializável."""
    if nome == "executar_sql":
        sql = argumentos.get("sql", "")
        try:
            resultado = executar_consulta(sql)
        except ConsultaRecusada as exc:
            return {"erro": str(exc), "sql_recebido": sql}
        return {
            "colunas": resultado.columns,
            "linhas": resultado.rows,
            "quantidade_linhas": len(resultado.rows),
            "truncado": resultado.truncated,
            "tempo_ms": round(resultado.elapsed_ms, 1),
        }

    if nome == "buscar_sinopses_similares":
        consulta = argumentos.get("consulta", "")
        top_k = min(int(argumentos.get("top_k", 5) or 5), 15)
        try:
            resultados = _get_busca_sinopses().buscar(consulta, top_k=top_k)
        except Exception as exc:  # defensivo: nunca deixar a busca derrubar o loop do agente
            return {"erro": f"Falha na busca por sinopse: {exc}"}
        return {"resultados": resultados}

    return {"erro": f"Ferramenta desconhecida: {nome}"}
