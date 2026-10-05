"""Dicionário de dados do cinerocket.db: schema técnico + notas de negócio.

Por que isso existe como um módulo próprio (em vez de deixar o modelo
descobrir o schema sozinho via tool calls): cada chamada ao modelo consome a
cota diária do OpenRouter (50 requisições/dia no free tier). Se o agente
precisasse de 1-2 chamadas extras só para "explorar" o schema a cada
pergunta, a cota se esgotaria rapidamente. Por isso o schema COMPLETO e as
armadilhas de dados mais importantes já vão no prompt inicial — o modelo
só gasta chamadas adicionais para rodar a consulta e formular a resposta.

As notas de negócio abaixo vêm de inspecionar os dados reais (não são
suposições): ver README, seção "Qualidade dos dados e armadilhas conhecidas".
"""

from __future__ import annotations

import sqlite3

from app.config import settings

DATA_DICTIONARY = """
## Esquema do banco (SQLite, somente leitura) — Star Schema do catálogo CineData

dim_movies (1 linha por filme)
  sk_movie_id        TEXT PK
  id_filme           TEXT   -- chave natural de origem (TMDB)
  titulo             TEXT
  data_lancamento    DATE   -- formato 'YYYY-MM-DD', pode ser futura (filme não lançado ainda)
  ano_lancamento     INT
  duracao_minutos    INT
  idioma_original    TEXT
  status_filme       TEXT   -- 'Lançado' | 'Pós-Produção' | 'Em Produção' | 'Planejado'
  sinopse            TEXT

dim_genres (sk_genre_id PK, nome_genero)        -- 19 gêneros (Action, Drama, Comedy, ...)
dim_companies (sk_company_id PK, nome_produtora)
dim_people (sk_person_id PK, nome_pessoa, tipo_pessoa)  -- tipo_pessoa: 'Ator' | 'Diretor' | 'Roteirista'

bridge_movie_genre   (sk_movie_id, sk_genre_id)     -- N:N filme x gênero
bridge_movie_company (sk_movie_id, sk_company_id)   -- N:N filme x produtora
bridge_movie_person  (sk_movie_id, sk_person_id)    -- N:N filme x pessoa

fact_movies_performance (1 linha por filme, sk_movie_id PK/FK -> dim_movies)
  orcamento_usd, receita_usd, lucro_usd            NUMERIC  -- USD
  orcamento_brl, receita_brl, lucro_brl            NUMERIC  -- BRL
  popularidade                                      DOUBLE
  nota_tmdb   DOUBLE (escala 0-10)   qtd_tmdb INT
  nota_imdb   DOUBLE (escala 0-10)   qtd_imdb INT

dim_reviews (resumo agregado; só existe para filmes que têm avaliação de usuário)
  sk_review_id PK, sk_movie_id FK, qtd_avaliacoes_usuarios INT, nota_media_usuarios DOUBLE (escala 0-10)

movie_reviews (1 linha por avaliação individual de usuário)
  id PK, sk_movie_review_id, sk_movie_id FK, name TEXT, rating DOUBLE (escala 0-10), text TEXT, created_at DATETIME

## Armadilhas de dados conhecidas — leia antes de escrever qualquer consulta

1. **lucro_usd/lucro_brl NUNCA é NULL, mas pode ser enganoso.** Ele é calculado como
   COALESCE(receita, 0) - COALESCE(orcamento, 0). Como ~96% dos filmes NÃO têm
   receita_usd informada, um filme sem receita aparece com lucro = -orcamento
   (parecendo prejuízo) ou lucro = 0 (quando orçamento também falta) — isso
   não significa que o filme deu prejuízo de verdade, significa que o dado é
   desconhecido. **Toda pergunta sobre lucro/margem/receita deve filtrar
   explicitamente `WHERE receita_usd IS NOT NULL AND orcamento_usd IS NOT NULL`**
   antes de agregar ou ordenar por essas colunas. O enunciado do usuário pode
   não mencionar esse filtro, mas você deve aplicá-lo mesmo assim.

2. **Margem de lucro** não é uma coluna — calcule como
   `(receita_usd - orcamento_usd) / orcamento_usd` (ou use lucro_usd/orcamento_usd),
   sempre dentro do mesmo filtro de não-nulos do item 1, e evite dividir por
   orcamento_usd = 0.

3. **nota_tmdb e nota_imdb podem ser NULL** (filme sem avaliação cadastrada
   naquela base). Use AVG()/comparações apenas onde IS NOT NULL — AVG() do
   SQLite já ignora NULL automaticamente, mas comparações diretas (ex.: maior
   divergência) precisam do filtro explícito nos dois lados.

4. **dim_people tem nomes duplicados com sk_person_id diferentes** (a mesma
   pessoa pode ter mais de uma chave surrogate, herdado da origem dos dados).
   Para contar "quantos filmes um ator fez" de forma robusta, agrupe por
   `nome_pessoa` (texto), não apenas por `sk_person_id`. Além disso, alguns
   registros de dim_people são ruído de origem (ex.: nomes puramente
   numéricos como "0.6") — ao listar atores/diretores, é razoável ignorar
   nomes que não contêm nenhuma letra (`nome_pessoa GLOB '*[A-Za-z]*'`).

5. **"Últimos N anos" deve ser calculado a partir da data mais recente
   REALMENTE lançada na base, não da data de hoje** — há filmes com
   `status_filme` futuro ('Planejado', 'Em Produção') e `data_lancamento` no
   futuro. Use uma **subconsulta escalar** para a data de referência:
   `AND data_lancamento > date((SELECT MAX(data_lancamento) FROM dim_movies WHERE status_filme='Lançado'), '-N years')`.
   **Evite** o padrão `WITH ref AS (SELECT MAX(...) ...) ... JOIN ref ON 1=1`
   — em bridge_movie_person (745 mil linhas) esse padrão confunde o otimizador
   do SQLite e pode levar a consulta de poucos segundos para vários minutos
   (verificado na prática). Subconsulta escalar resolve o mesmo problema sem
   esse risco.

5b. **Ao agrupar por `nome_pessoa` (texto) sobre bridge_movie_person**, prefira
   agregar primeiro por `sk_person_id` (usa os índices existentes e é rápido)
   e só depois juntar com dim_people para exibir o nome — agrupar direto por
   `nome_pessoa` faz o SQLite escanear dim_people inteira (424 mil linhas)
   como tabela condutora do plano de execução, o que é visivelmente mais
   lento quando combinado com filtros seletivos (ex.: por data).

6. **dim_reviews só tem linha para filmes com ao menos 1 avaliação de
   usuário** (não é 1 linha por filme). Para "filmes mais avaliados", use
   `qtd_avaliacoes_usuarios` dessa tabela; para comparar com a nota IMDb,
   junte com fact_movies_performance e filtre os NULLs de ambos os lados.

7. **Nunca faça JOIN de bridge_movie_person sem filtrar tipo_pessoa quando a
   pergunta for sobre atores, diretores OU roteiristas especificamente** —
   a mesma pessoa pode aparecer com papéis diferentes em filmes diferentes.

## Regras gerais de resposta

- Gere SOMENTE SELECT (ou WITH ... SELECT). Nunca INSERT/UPDATE/DELETE/DDL.
- Sempre inclua LIMIT em consultas que listam (não agregam) muitas linhas.
- "Receita" / "Faturamento" / "Bilheteria" são sinônimos de receita_usd (ou receita_brl, se o usuário pedir em reais).
- Ao final, explique a resposta em português, de forma direta, citando os números encontrados — não apenas despeje a tabela.
- Assim que o resultado de uma consulta já responder à pergunta, PARE de chamar ferramentas e escreva a resposta final em texto imediatamente. Não refine a mesma consulta mais de uma vez sem necessidade — cada chamada extra consome a cota diária.
"""


def montar_system_prompt() -> str:
    return (
        "Você é um analista de dados sênior da CineData Analytics. Você responde perguntas em "
        "linguagem natural sobre o catálogo de filmes consultando o banco de dados através da "
        "ferramenta `executar_sql`. Você NUNCA escreve a resposta final sem antes ter executado "
        "ao menos uma consulta SQL e visto o resultado real — nunca estime ou invente números.\n\n"
        f"{DATA_DICTIONARY}"
    )


def resumo_schema_legivel() -> str:
    """Pequeno resumo (tabela: nº de linhas) — útil para depuração e para o README/notebook."""
    linhas = []
    conn = sqlite3.connect(f"file:{settings.database_path}?mode=ro", uri=True)
    try:
        tabelas = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name != 'alembic_version'"
            ).fetchall()
        ]
        for t in sorted(tabelas):
            n = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            linhas.append(f"  {t:<28} {n:>8} linhas")
    finally:
        conn.close()
    return "\n".join(linhas)
