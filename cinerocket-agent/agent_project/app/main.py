"""Módulo de backend FastAPI — uma das três formas de entregável aceitas pelo enunciado.

Não é uma interface de chat (o enunciado deixa isso explicitamente opcional);
é uma API simples com um endpoint de pergunta/resposta, pensada para ser
testada pelo Swagger UI (`/docs`) ou por qualquer cliente HTTP.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.agent import CinerocketAgent, RespostaAgente
from app.config import settings

_agent: CinerocketAgent | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _agent
    _agent = CinerocketAgent()
    yield
    if _agent is not None:
        _agent.close()


app = FastAPI(
    title="CineData Analytics — Agente Text-to-SQL",
    description="Perguntas em linguagem natural sobre o catálogo de filmes, via tool-calling + SQLite.",
    version="1.0.0",
    lifespan=lifespan,
)


class PerguntaRequest(BaseModel):
    pergunta: str = Field(min_length=3, max_length=500, examples=["Quais são os 5 filmes mais populares?"])


class PerguntaResponse(BaseModel):
    pergunta: str
    resposta: str
    modelo_usado: str | None
    consultas_sql: list[str]
    chamadas_api: int
    do_cache: bool


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "database": str(settings.database_path), "models": settings.models}


@app.post("/ask", response_model=PerguntaResponse)
def ask(payload: PerguntaRequest) -> PerguntaResponse:
    if _agent is None:
        raise HTTPException(503, "Agente ainda não inicializado.")
    resultado: RespostaAgente = _agent.perguntar(payload.pergunta)
    if resultado.erro:
        raise HTTPException(422, resultado.resposta)
    return PerguntaResponse(
        pergunta=resultado.pergunta,
        resposta=resultado.resposta,
        modelo_usado=resultado.modelo_usado,
        consultas_sql=resultado.consultas_sql,
        chamadas_api=resultado.chamadas_api,
        do_cache=resultado.do_cache,
    )
