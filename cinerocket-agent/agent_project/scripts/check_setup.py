"""Confere se o ambiente está pronto antes de usar o agente de verdade.

Não gasta nenhuma chamada da cota da API: só confere arquivo .env, presença
e integridade básica do banco, e (opcionalmente) se a chave do OpenRouter
tem o formato esperado. A validação de que a chave FUNCIONA só acontece na
primeira pergunta real feita ao agente.

Uso:
    python scripts/check_setup.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings


def main() -> int:
    ok = True

    print("1. Arquivo .env")
    if settings.openrouter_api_key:
        mascara = settings.openrouter_api_key[:10] + "..." if len(settings.openrouter_api_key) > 10 else "***"
        print(f"   OPENROUTER_API_KEY encontrada ({mascara})")
        if not settings.openrouter_api_key.startswith("sk-or-v1-"):
            print("   ⚠ a chave não começa com 'sk-or-v1-' — confira se copiou certo de openrouter.ai/keys")
    else:
        print("   ✘ OPENROUTER_API_KEY não definida. Copie .env.example para .env e preencha.")
        ok = False

    print("\n2. Banco de dados")
    if settings.database_path.exists():
        tamanho_mb = settings.database_path.stat().st_size / (1024 * 1024)
        print(f"   {settings.database_path} encontrado ({tamanho_mb:.0f} MB)")
        try:
            from app.db import executar_consulta

            r = executar_consulta("SELECT COUNT(*) FROM dim_movies")
            print(f"   {r.rows[0][0]} filmes em dim_movies — conexão somente-leitura funcionando.")
        except Exception as exc:
            print(f"   ✘ não consegui consultar o banco: {exc}")
            ok = False
    else:
        print(f"   ✘ banco não encontrado em {settings.database_path}.")
        print("     Copie o arquivo cinerocket.db fornecido pelo professor para a pasta data/.")
        ok = False

    print("\n3. Modelos configurados (ordem de fallback)")
    for m in settings.models:
        print(f"   - {m}")
    print("   (confira a disponibilidade atual em https://openrouter.ai/models?q=:free)")

    print("\n" + ("Tudo pronto. Rode: python -c \"from app.agent import CinerocketAgent; "
                  "a = CinerocketAgent(); print(a.perguntar('Quantos filmes existem no catálogo?').resposta)\""
                  if ok else "Corrija os itens com ✘ acima antes de continuar."))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
