"""Núcleo do agente Text-to-SQL: loop de tool-calling sobre a API do OpenRouter.

Framework de agentes escolhido: um loop ReAct-like **escrito à mão**, sobre o
SDK `openai` (compatível com OpenRouter trocando só a `base_url`), em vez de
um framework pronto (LangChain/CrewAI/etc.). Decisão deliberada: para um
agente de 1-2 ferramentas como este, um framework pesado adicionaria camadas
de abstração sem necessidade, dificultando controlar exatamente quantas
chamadas de API são feitas — o que importa muito aqui, dado o limite de 50
requisições/dia do free tier do OpenRouter. O loop abaixo é ~80 linhas e dá
controle total sobre cada chamada.

Nota sobre robustez com modelos gratuitos: na prática (testando com a API
real), alguns modelos :free às vezes "vazam" a sintaxe interna de tool
calling como texto comum (ex.: `<tool_call><function=executar_sql>...`) em
vez de usar o campo estruturado da API — principalmente quando instruídos a
não chamar mais ferramentas. Por isso o agente NÃO depende de um único
mecanismo para terminar bem: ele guarda o resultado da última consulta SQL
que funcionou e, se o modelo não conseguir formular a resposta final em
texto dentro do limite de rodadas, devolve os dados brutos encontrados em
vez de travar ou devolver um texto não-confiável.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from openai import APIStatusError, OpenAI

from app.cache import CacheRespostas
from app.config import settings
from app.schema import montar_system_prompt
from app.tools import TOOLS_SCHEMA, executar_tool_call

logger = logging.getLogger("cinerocket_agent")


@dataclass
class RespostaAgente:
    pergunta: str
    resposta: str
    modelo_usado: str | None
    consultas_sql: list[str] = field(default_factory=list)
    chamadas_api: int = 0
    rodadas_tool_calling: int = 0
    do_cache: bool = False
    erro: str | None = None


def _parece_tool_call_vazado(texto: str | None) -> bool:
    """Detecta quando o modelo escreveu a sintaxe de tool-calling como texto comum,
    em vez de usar o campo estruturado da API (observado em alguns modelos :free)."""
    if not texto:
        return False
    inicio = texto.strip()[:20].lower()
    return inicio.startswith(("<tool_call", "<function")) or "<function=" in texto[:200].lower()


def _formatar_tabela(colunas: list[str], linhas: list[tuple], max_linhas: int = 15) -> str:
    if not linhas:
        return "(a consulta não retornou nenhuma linha)"
    texto = [" | ".join(colunas)]
    for linha in linhas[:max_linhas]:
        texto.append(" | ".join(str(v) for v in linha))
    if len(linhas) > max_linhas:
        texto.append(f"... (+{len(linhas) - max_linhas} linhas)")
    return "\n".join(texto)


class CinerocketAgent:
    def __init__(
        self,
        *,
        models: list[str] | None = None,
        use_cache: bool = True,
        client: object | None = None,
        max_rounds: int | None = None,
    ) -> None:
        """`client` e `max_rounds` são pontos de injeção de dependência: os testes passam um
        cliente falso e um limite de rodadas menor (ver tests/test_agent_loop.py) para validar
        o loop de forma rápida e sem gastar cota real da API."""
        if client is None:
            settings.validate()
        self._client = client or OpenAI(api_key=settings.openrouter_api_key, base_url=settings.openrouter_base_url)
        self._models = models or settings.models
        self._max_rounds = max_rounds or settings.max_tool_call_rounds
        self._cache = CacheRespostas() if use_cache else None
        self._system_prompt = montar_system_prompt()

    # ------------------------------------------------------------------ chamada com fallback
    def _chamar_modelo(self, messages: list[dict]) -> tuple[object, str]:
        """Tenta cada modelo da lista de fallback, na ordem, até um responder com sucesso."""
        ultimo_erro: Exception | None = None
        for modelo in self._models:
            try:
                resposta = self._client.chat.completions.create(
                    model=modelo,
                    messages=messages,
                    tools=TOOLS_SCHEMA,
                    tool_choice="auto",
                    temperature=0,
                )
                return resposta, modelo
            except APIStatusError as exc:
                # 429 (cota/pool lotado) ou outro erro do provider: registra e tenta o próximo modelo
                logger.warning("Modelo %s falhou (status %s): %s — tentando o próximo da lista.", modelo, exc.status_code, exc)
                ultimo_erro = exc
                continue
            except Exception as exc:  # erro de rede, timeout, etc.
                logger.warning("Modelo %s falhou (%s) — tentando o próximo da lista.", modelo, exc)
                ultimo_erro = exc
                continue
        raise RuntimeError(
            f"Todos os modelos da lista de fallback falharam. Último erro: {ultimo_erro}"
        ) from ultimo_erro

    # ------------------------------------------------------------------ loop principal
    def perguntar(self, pergunta: str) -> RespostaAgente:
        if self._cache is not None:
            cache_key_model = self._models[0]
            cacheado = self._cache.obter(pergunta, cache_key_model)
            if cacheado is not None:
                return RespostaAgente(**{**cacheado, "do_cache": True})

        messages: list[dict] = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": pergunta},
        ]
        consultas_sql: list[str] = []
        chamadas_api = 0
        modelo_usado: str | None = None
        ultimo_resultado_sql: dict | None = None  # rede de segurança: dados da última consulta que funcionou

        for rodada in range(1, self._max_rounds + 1):
            resposta, modelo_usado = self._chamar_modelo(messages)
            chamadas_api += 1
            escolha = resposta.choices[0]
            mensagem = escolha.message
            finish_reason = getattr(escolha, "finish_reason", "stop")

            # Só aceita como resposta final de verdade quando o PRÓPRIO modelo decidiu parar
            # (finish_reason == "stop"). Se a API cortou a resposta por falta de espaço
            # (finish_reason == "length" — observado na prática com um modelo :free, cortando
            # a frase no meio) ou por qualquer outro motivo que não seja uma parada natural,
            # NÃO tratamos como resposta válida — evita salvar lixo truncado no cache.
            resposta_final_valida = (
                not mensagem.tool_calls
                and finish_reason == "stop"
                and not _parece_tool_call_vazado(mensagem.content)
            )

            if resposta_final_valida:
                # modelo decidiu que já pode responder, com texto de verdade: fim do loop
                texto_final = mensagem.content or "(o modelo não produziu texto de resposta)"
                resultado = RespostaAgente(
                    pergunta=pergunta,
                    resposta=texto_final,
                    modelo_usado=modelo_usado,
                    consultas_sql=consultas_sql,
                    chamadas_api=chamadas_api,
                    rodadas_tool_calling=rodada,
                )
                if self._cache is not None:
                    self._cache.salvar(pergunta, self._models[0], resultado)
                return resultado

            if not mensagem.tool_calls:
                # Chegou aqui sem tool_calls estruturado E sem ser uma resposta final válida:
                # ou o conteúdo "vazou" a sintaxe de tool-calling como texto, ou a resposta foi
                # cortada antes de terminar (finish_reason != "stop"). Nos dois casos, não é
                # confiável aceitar como resposta — pede uma resposta curta e completa na próxima
                # rodada (ou, se já era a última rodada, cai no fallback de dados brutos abaixo).
                logger.warning(
                    "Modelo %s devolveu conteúdo não confiável como resposta final (finish_reason=%s); descartando.",
                    modelo_usado, finish_reason,
                )
                messages.append({"role": "assistant", "content": mensagem.content or ""})
                pedido_correcao = (
                    "Isso não é uma resposta válida. Se precisar consultar o banco, use a ferramenta "
                    "executar_sql pelo mecanismo correto de tool calling. Caso já tenha os dados "
                    "suficientes, responda em um parágrafo CURTO e completo (sem cortar no meio), "
                    "sem nenhuma marcação de ferramenta."
                )
                messages.append({"role": "user", "content": pedido_correcao})
                continue

            # registra a mensagem do assistente (com as tool_calls) e executa cada ferramenta pedida
            messages.append(mensagem.model_dump(exclude_none=True))
            for chamada in mensagem.tool_calls:
                nome = chamada.function.name
                try:
                    argumentos = json.loads(chamada.function.arguments or "{}")
                except json.JSONDecodeError:
                    argumentos = {}

                if nome == "executar_sql" and "sql" in argumentos:
                    consultas_sql.append(argumentos["sql"])

                resultado_tool = executar_tool_call(nome, argumentos)
                if nome == "executar_sql" and "erro" not in resultado_tool:
                    ultimo_resultado_sql = resultado_tool  # guarda para o fallback, se precisar

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": chamada.id,
                        "name": nome,
                        "content": json.dumps(resultado_tool, ensure_ascii=False, default=str),
                    }
                )

        # excedeu o número de rodadas permitidas sem uma resposta final em texto.
        if ultimo_resultado_sql is not None:
            # rede de segurança: o modelo não conseguiu "fechar" a resposta em prosa, mas já tínhamos
            # dados reais de uma consulta válida — devolve-os em vez de só um aviso genérico.
            tabela = _formatar_tabela(ultimo_resultado_sql["colunas"], ultimo_resultado_sql["linhas"])
            resposta_fallback = (
                "O modelo não conseguiu formular a explicação final em texto dentro do limite de "
                f"{self._max_rounds} rodadas, mas a última consulta executada com sucesso encontrou "
                f"estes dados:\n\n{tabela}"
            )
            return RespostaAgente(
                pergunta=pergunta,
                resposta=resposta_fallback,
                modelo_usado=modelo_usado,
                consultas_sql=consultas_sql,
                chamadas_api=chamadas_api,
                rodadas_tool_calling=self._max_rounds,
                erro="resposta_final_nao_formulada",
            )

        return RespostaAgente(
            pergunta=pergunta,
            resposta=(
                "Não consegui concluir dentro do limite de interações com o modelo "
                f"({self._max_rounds} rodadas), e nenhuma consulta válida foi executada. Isso evita "
                "gastar a cota diária em loops. Tente reformular a pergunta de forma mais específica."
            ),
            modelo_usado=modelo_usado,
            consultas_sql=consultas_sql,
            chamadas_api=chamadas_api,
            rodadas_tool_calling=self._max_rounds,
            erro="limite_de_rodadas_excedido",
        )

    def close(self) -> None:
        if self._cache is not None:
            self._cache.close()
