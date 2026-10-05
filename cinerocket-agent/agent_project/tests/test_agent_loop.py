"""Testes do loop de tool-calling do agente, usando um cliente OpenAI FALSO.

Por que mockar em vez de chamar a API de verdade: (1) a conta OpenRouter tem
cota de 50 req/dia — testes automatizados não devem gastá-la; (2) este
sandbox de desenvolvimento não tem saída de rede liberada para openrouter.ai;
(3) mockar permite testar deliberadamente os cenários de erro (429, estourar
o limite de rodadas) que seriam difíceis de reproduzir de propósito contra a
API real.

O teste de ponta a ponta com a API real deve ser feito manualmente, com uma
chave válida — ver README, seção "Testando com a API real".
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from types import SimpleNamespace

import pytest
from openai import APIStatusError

from app.agent import CinerocketAgent


# --------------------------------------------------------------------------- dublês (fakes) do SDK da OpenAI
@dataclass
class FakeFunction:
    name: str
    arguments: str


@dataclass
class FakeToolCall:
    id: str
    function: FakeFunction


class FakeMessage:
    def __init__(self, content: str | None = None, tool_calls: list[FakeToolCall] | None = None):
        self.content = content
        self.tool_calls = tool_calls

    def model_dump(self, exclude_none: bool = False) -> dict:
        d = {"role": "assistant", "content": self.content, "tool_calls": self.tool_calls}
        return {k: v for k, v in d.items() if not exclude_none or v is not None}


@dataclass
class FakeChoice:
    message: FakeMessage
    finish_reason: str = "stop"


@dataclass
class FakeCompletion:
    choices: list[FakeChoice]


def resposta_final(texto: str, finish_reason: str = "stop") -> FakeCompletion:
    return FakeCompletion(choices=[FakeChoice(message=FakeMessage(content=texto), finish_reason=finish_reason)])


def resposta_com_tool_call(nome: str, argumentos: dict, call_id: str = "call_1") -> FakeCompletion:
    tc = FakeToolCall(id=call_id, function=FakeFunction(name=nome, arguments=json.dumps(argumentos)))
    return FakeCompletion(choices=[FakeChoice(message=FakeMessage(tool_calls=[tc]))])


def erro_429() -> APIStatusError:
    resp = SimpleNamespace(status_code=429, headers={}, request=SimpleNamespace())
    return APIStatusError("rate limited", response=resp, body=None)


class FakeClient:
    """Consome uma fila de respostas/erros programados, um por chamada a .create()."""

    def __init__(self, fila: list):
        self._fila = list(fila)
        self.chamadas: list[str] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, *, model, messages, tools, tool_choice, temperature):
        self.chamadas.append(model)
        item = self._fila.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


# Observação: Settings é um dataclass frozen (imutável) de propósito — os testes abaixo não
# fazem monkeypatch nele; em vez disso, usam os parâmetros `use_cache=False` e `max_rounds=`
# que CinerocketAgent aceita exatamente para permitir esse tipo de teste isolado.


# --------------------------------------------------------------------------- cenários
def test_fluxo_basico_uma_tool_call_e_resposta_final():
    fila = [
        resposta_com_tool_call("executar_sql", {"sql": "SELECT COUNT(*) FROM dim_movies"}),
        resposta_final("Há 95.645 filmes no catálogo."),
    ]
    client = FakeClient(fila)
    agente = CinerocketAgent(models=["modelo-a"], use_cache=False, client=client)

    resultado = agente.perguntar("Quantos filmes existem no catálogo?")

    assert resultado.erro is None
    assert "95.645" in resultado.resposta or "filmes" in resultado.resposta
    assert resultado.consultas_sql == ["SELECT COUNT(*) FROM dim_movies"]
    assert resultado.chamadas_api == 2
    assert resultado.modelo_usado == "modelo-a"


def test_fallback_para_o_proximo_modelo_quando_o_primeiro_recebe_429():
    fila = [
        erro_429(),  # modelo-a falha
        resposta_final("Resposta do modelo-b."),  # modelo-b responde
    ]
    client = FakeClient(fila)
    agente = CinerocketAgent(models=["modelo-a", "modelo-b"], use_cache=False, client=client)

    resultado = agente.perguntar("Pergunta qualquer, sem necessidade de SQL.")

    assert resultado.modelo_usado == "modelo-b"
    assert client.chamadas == ["modelo-a", "modelo-b"]


def test_guardrail_recusa_sql_de_escrita_e_modelo_recebe_o_erro_para_corrigir():
    fila = [
        resposta_com_tool_call("executar_sql", {"sql": "DROP TABLE dim_movies"}),
        resposta_com_tool_call("executar_sql", {"sql": "SELECT COUNT(*) FROM dim_movies"}),
        resposta_final("Consulta corrigida: 95.645 filmes."),
    ]
    client = FakeClient(fila)
    agente = CinerocketAgent(models=["modelo-a"], use_cache=False, client=client)

    resultado = agente.perguntar("Quantos filmes existem?")

    assert resultado.erro is None
    assert resultado.consultas_sql == ["DROP TABLE dim_movies", "SELECT COUNT(*) FROM dim_movies"]
    assert resultado.chamadas_api == 3


def test_excede_limite_sem_nenhuma_consulta_valida_mostra_erro_generico():
    # as duas tool calls pedem SQL inválido (bloqueado pelo guardrail) — nunca há um
    # resultado real para usar como fallback, então o erro genérico é o esperado
    fila = [
        resposta_com_tool_call("executar_sql", {"sql": "DROP TABLE dim_movies"}),
        resposta_com_tool_call("executar_sql", {"sql": "DROP TABLE dim_movies"}),
    ]
    client = FakeClient(fila)
    agente = CinerocketAgent(models=["modelo-a"], use_cache=False, client=client, max_rounds=2)

    resultado = agente.perguntar("Pergunta que nunca termina.")

    assert resultado.erro == "limite_de_rodadas_excedido"
    assert resultado.chamadas_api == 2


def test_excede_limite_mas_com_consulta_valida_anterior_usa_fallback_de_dados():
    # cenário real observado com a API: o modelo roda uma consulta válida, mas não
    # consegue "fechar" a resposta em texto antes do limite de rodadas. O agente deve
    # devolver os dados da última consulta válida, em vez de só um aviso genérico.
    fila = [
        resposta_com_tool_call("executar_sql", {"sql": "SELECT titulo FROM dim_movies LIMIT 1"}),
        resposta_com_tool_call("executar_sql", {"sql": "SELECT titulo FROM dim_movies LIMIT 1"}),
    ]
    client = FakeClient(fila)
    agente = CinerocketAgent(models=["modelo-a"], use_cache=False, client=client, max_rounds=2)

    resultado = agente.perguntar("Pergunta que nunca termina.")

    assert resultado.erro == "resposta_final_nao_formulada"
    assert "titulo" in resultado.resposta  # os dados reais aparecem na resposta de fallback
    assert resultado.chamadas_api == 2


def test_resposta_cortada_por_falta_de_espaco_nao_e_aceita_como_final():
    # observado na prática: a API corta a resposta no meio da frase (finish_reason="length").
    # Isso não deve ser aceito como resposta válida nem salvo no cache — o agente deve pedir
    # uma resposta curta e completa na rodada seguinte.
    fila = [
        resposta_final("The query worked with INNER JOIN and COUNT(bg.sk_movie_id) without", finish_reason="length"),
        resposta_final("Existem 19 gêneros no catálogo, Drama é o mais comum."),
    ]
    client = FakeClient(fila)
    agente = CinerocketAgent(models=["modelo-a"], use_cache=False, client=client, max_rounds=3)

    resultado = agente.perguntar("Quantos filmes existem em cada gênero?")

    assert resultado.erro is None
    assert resultado.resposta == "Existem 19 gêneros no catálogo, Drama é o mais comum."
    assert resultado.chamadas_api == 2


def test_resposta_cortada_nao_e_salva_no_cache():
    fila = [
        resposta_final("Resposta incompleta cortada no meio...", finish_reason="length"),
        resposta_final("Resposta completa e correta."),
    ]
    client = FakeClient(fila)
    agente = CinerocketAgent(models=["modelo-a"], use_cache=True, client=client, max_rounds=3)

    resultado = agente.perguntar("Pergunta de teste de cache.")
    assert resultado.resposta == "Resposta completa e correta."

    cacheado = agente._cache.obter("Pergunta de teste de cache.", "modelo-a")
    assert cacheado is not None
    assert cacheado["resposta"] == "Resposta completa e correta."  # não ficou com a versão cortada
    agente.close()


def test_conteudo_com_sintaxe_de_tool_call_vazada_nao_e_aceito_como_resposta_final():
    # observado na prática com um modelo :free: em vez de usar o campo estruturado de
    # tool_calls, o modelo às vezes escreve a sintaxe interna como texto comum. Isso não
    # deve ser aceito como resposta final — o agente deve pedir correção e seguir tentando.
    fila = [
        resposta_final("<tool_call>\n<function=executar_sql>\n<parameter=sql>\nSELECT 1\n</parameter>\n</function>\n</tool_call>"),
        resposta_final("Agora sim: a resposta é 1."),
    ]
    client = FakeClient(fila)
    agente = CinerocketAgent(models=["modelo-a"], use_cache=False, client=client, max_rounds=3)

    resultado = agente.perguntar("Pergunta qualquer.")

    assert resultado.erro is None
    assert resultado.resposta == "Agora sim: a resposta é 1."
    assert resultado.chamadas_api == 2


def test_todos_os_modelos_falham_levanta_erro():
    fila = [erro_429(), erro_429()]
    client = FakeClient(fila)
    agente = CinerocketAgent(models=["modelo-a", "modelo-b"], use_cache=False, client=client)

    with pytest.raises(RuntimeError, match="Todos os modelos"):
        agente.perguntar("Qualquer pergunta.")


def test_ferramenta_de_sinopse_e_despachada_corretamente():
    fila = [
        resposta_com_tool_call("buscar_sinopses_similares", {"consulta": "time travel", "top_k": 3}),
        resposta_final("Encontrei alguns filmes sobre viagem no tempo."),
    ]
    client = FakeClient(fila)
    agente = CinerocketAgent(models=["modelo-a"], use_cache=False, client=client)

    resultado = agente.perguntar("Quais filmes falam sobre viagem no tempo?")

    assert resultado.erro is None
    assert resultado.consultas_sql == []  # essa ferramenta não é SQL
