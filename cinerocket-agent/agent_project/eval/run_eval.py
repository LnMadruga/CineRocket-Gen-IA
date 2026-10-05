"""Harness de avaliação: compara o SQL que o AGENTE gerou com o SQL de referência.

Por que comparar os DADOS (resultado das duas consultas) e não o TEXTO da
resposta final do agente: a resposta em linguagem natural varia de frase
para frase mesmo quando a consulta está certa ("O filme mais popular é X"
vs. "X lidera o ranking de popularidade..."), o que tornaria uma comparação
textual frágil e pouco informativa. Comparando os dados, a avaliação é
objetiva: a última consulta SQL que o agente executou produziu os mesmos
números que um analista humano produziria?

Dois modos de uso:

  python -m eval.run_eval --dry-run
      Não chama a API (não gasta cota). Só roda as consultas de referência
      contra o banco e mostra os resultados esperados — útil para conferir
      o gabarito e para quem ainda não tem uma OPENROUTER_API_KEY configurada.

  python -m eval.run_eval --limit 5
      Roda o agente de verdade (gasta cota da API) nas N primeiras perguntas
      e compara com o gabarito. Comece com --limit baixo: cada pergunta pode
      custar de 2 a 4 requisições, e o free tier tem 50/dia.
"""

from __future__ import annotations

import argparse
import sys
import time

from app.agent import CinerocketAgent
from app.db import ConsultaRecusada, executar_consulta
from eval.golden_questions import PERGUNTAS


def _linhas_equivalentes(a: list[tuple], b: list[tuple], ordem_importa: bool, tolerancia: float = 1e-6) -> bool:
    def normaliza(linhas):
        normalizadas = []
        for linha in linhas:
            normalizada = tuple(round(v, 4) if isinstance(v, float) else v for v in linha)
            normalizadas.append(normalizada)
        return normalizadas if ordem_importa else sorted(normalizadas, key=str)

    return normaliza(a) == normaliza(b)


def rodar_dry_run() -> None:
    print(f"Modo dry-run: só o gabarito, sem chamar a API. {len(PERGUNTAS)} perguntas.\n")
    for p in PERGUNTAS:
        try:
            resultado = executar_consulta(p.sql_referencia, max_rows=5)
            amostra = resultado.rows[0] if resultado.rows else "(sem linhas)"
            print(f"[{p.categoria}] {p.pergunta}\n   gabarito (1ª linha): {amostra}\n")
        except ConsultaRecusada as exc:
            print(f"[{p.categoria}] {p.pergunta}\n   ERRO no gabarito: {exc}\n")


def rodar_avaliacao_real(limite: int) -> None:
    agente = CinerocketAgent()
    perguntas = PERGUNTAS[:limite]
    print(f"Avaliando {len(perguntas)} pergunta(s) com o agente real. Isso consome a cota da API.\n")

    acertos = 0
    total_chamadas_api = 0
    for i, p in enumerate(perguntas, 1):
        print(f"[{i}/{len(perguntas)}] ({p.categoria}) {p.pergunta}")
        t0 = time.time()
        resultado = agente.perguntar(p.pergunta)
        total_chamadas_api += resultado.chamadas_api
        print(f"   modelo: {resultado.modelo_usado} | chamadas: {resultado.chamadas_api} | "
              f"tempo: {time.time()-t0:.1f}s | cache: {resultado.do_cache}")

        gabarito = executar_consulta(p.sql_referencia, max_rows=50)

        if not resultado.consultas_sql:
            print("   ⚠ o agente não executou nenhuma consulta SQL — não é possível comparar.\n")
            continue

        try:
            do_agente = executar_consulta(resultado.consultas_sql[-1], max_rows=50)
        except ConsultaRecusada as exc:
            print(f"   ⚠ a última consulta do agente não pôde ser reexecutada: {exc}\n")
            continue

        bate = _linhas_equivalentes(do_agente.rows, gabarito.rows, p.ordem_importa)
        if bate:
            acertos += 1
            print("   ✔ dados batem com o gabarito")
        else:
            print("   ✘ dados DIFERENTES do gabarito")
            print(f"     agente  : {do_agente.rows[:3]}")
            print(f"     gabarito: {gabarito.rows[:3]}")
        print(f"   resposta do agente: {resultado.resposta[:200]}\n")

    agente.close()
    print(f"\nResumo: {acertos}/{len(perguntas)} perguntas com dados equivalentes ao gabarito.")
    print(f"Total de chamadas à API nesta avaliação: {total_chamadas_api} (cota diária free: 50).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Só mostra o gabarito, sem chamar a API.")
    parser.add_argument("--limit", type=int, default=5, help="Quantas perguntas rodar com o agente real (padrão 5).")
    args = parser.parse_args()

    if args.dry_run:
        rodar_dry_run()
    else:
        args.limit = min(args.limit, len(PERGUNTAS))
        confirmacao = input(
            f"Isso vai chamar a API real do OpenRouter para {args.limit} pergunta(s), "
            f"gastando parte da sua cota diária (50 req/dia no free tier). Continuar? [s/N] "
        )
        if confirmacao.strip().lower() != "s":
            print("Cancelado.")
            sys.exit(0)
        rodar_avaliacao_real(args.limit)
