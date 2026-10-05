"""Conjunto de perguntas de avaliação, com SQL de referência ("ground truth").

Cada entrada tem a consulta SQL que UM ANALISTA HUMANO escreveria (aplicando
as mesmas regras de negócio documentadas em app/schema.py — filtrar nulos de
receita/orçamento, usar a data de lançamento mais recente real para "últimos
N anos", etc.). O script eval/run_eval.py roda o agente e compara a consulta
final que ELE gerou com esta referência, executando as duas contra o banco e
comparando os resultados — não o texto da resposta (que varia de frase para
frase), mas os DADOS (que não deveriam variar se a consulta estiver certa).

As 14 perguntas cobrem as 5 categorias do enunciado (3 cada, exceto Avaliações
dos Usuários, com 2 — o enunciado traz 2 exemplos para essa categoria).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PerguntaOuro:
    categoria: str
    pergunta: str
    sql_referencia: str
    ordem_importa: bool = True  # se False, compara os resultados como conjunto (ignora a ordem)


PERGUNTAS: list[PerguntaOuro] = [
    # ---------------------------------------------------------------- Bilheteria e Finanças
    PerguntaOuro(
        categoria="Bilheteria e Finanças",
        pergunta="Quais são os 10 filmes com maior receita em reais (R$)?",
        sql_referencia="""
            SELECT m.titulo, f.receita_brl
            FROM fact_movies_performance f
            JOIN dim_movies m ON m.sk_movie_id = f.sk_movie_id
            WHERE f.receita_brl IS NOT NULL
            ORDER BY f.receita_brl DESC
            LIMIT 10
        """,
    ),
    PerguntaOuro(
        categoria="Bilheteria e Finanças",
        pergunta="Qual o lucro médio por gênero, considerando apenas filmes com receita informada?",
        sql_referencia="""
            SELECT g.nome_genero, AVG(f.lucro_usd) AS lucro_medio, COUNT(*) AS qtd_filmes
            FROM fact_movies_performance f
            JOIN bridge_movie_genre bg ON bg.sk_movie_id = f.sk_movie_id
            JOIN dim_genres g ON g.sk_genre_id = bg.sk_genre_id
            WHERE f.receita_usd IS NOT NULL AND f.orcamento_usd IS NOT NULL
            GROUP BY g.nome_genero
            ORDER BY lucro_medio DESC
        """,
        ordem_importa=False,
    ),
    PerguntaOuro(
        categoria="Bilheteria e Finanças",
        pergunta="Quais filmes têm a maior margem de lucro, entre os que possuem receita e orçamento informados?",
        sql_referencia="""
            SELECT m.titulo, (f.receita_usd - f.orcamento_usd) * 1.0 / f.orcamento_usd AS margem
            FROM fact_movies_performance f
            JOIN dim_movies m ON m.sk_movie_id = f.sk_movie_id
            WHERE f.receita_usd IS NOT NULL AND f.orcamento_usd IS NOT NULL AND f.orcamento_usd > 0
            ORDER BY margem DESC
            LIMIT 10
        """,
    ),
    # ---------------------------------------------------------------- Popularidade e Engajamento
    PerguntaOuro(
        categoria="Popularidade e Engajamento",
        pergunta="Quais são os 5 filmes mais populares?",
        sql_referencia="""
            SELECT m.titulo, f.popularidade
            FROM fact_movies_performance f
            JOIN dim_movies m ON m.sk_movie_id = f.sk_movie_id
            WHERE f.popularidade IS NOT NULL
            ORDER BY f.popularidade DESC
            LIMIT 5
        """,
    ),
    PerguntaOuro(
        categoria="Popularidade e Engajamento",
        pergunta="Quais filmes têm a maior divergência entre a nota TMDB e a nota IMDb?",
        sql_referencia="""
            SELECT m.titulo, f.nota_tmdb, f.nota_imdb, ABS(f.nota_tmdb - f.nota_imdb) AS divergencia
            FROM fact_movies_performance f
            JOIN dim_movies m ON m.sk_movie_id = f.sk_movie_id
            WHERE f.nota_tmdb IS NOT NULL AND f.nota_imdb IS NOT NULL
            ORDER BY divergencia DESC
            LIMIT 10
        """,
    ),
    PerguntaOuro(
        categoria="Popularidade e Engajamento",
        pergunta="Qual a nota média do IMDb por ano de lançamento?",
        sql_referencia="""
            SELECT m.ano_lancamento, AVG(f.nota_imdb) AS nota_media_imdb, COUNT(*) AS qtd_filmes
            FROM fact_movies_performance f
            JOIN dim_movies m ON m.sk_movie_id = f.sk_movie_id
            WHERE f.nota_imdb IS NOT NULL AND m.ano_lancamento IS NOT NULL
            GROUP BY m.ano_lancamento
            ORDER BY m.ano_lancamento
        """,
        ordem_importa=False,
    ),
    # ---------------------------------------------------------------- Elenco e Equipe
    PerguntaOuro(
        categoria="Elenco e Equipe",
        pergunta="Qual ator teve mais participações em filmes lançados nos últimos 5 anos?",
        sql_referencia="""
            SELECT p.nome_pessoa, COUNT(*) AS qtd_filmes
            FROM bridge_movie_person bp
            JOIN dim_movies m ON m.sk_movie_id = bp.sk_movie_id
            JOIN dim_people p ON p.sk_person_id = bp.sk_person_id AND p.tipo_pessoa = 'Ator'
            WHERE m.status_filme = 'Lançado'
              AND m.data_lancamento > date((SELECT MAX(data_lancamento) FROM dim_movies WHERE status_filme = 'Lançado'), '-5 years')
              AND m.data_lancamento <= (SELECT MAX(data_lancamento) FROM dim_movies WHERE status_filme = 'Lançado')
              AND p.nome_pessoa GLOB '*[A-Za-z]*'
            GROUP BY p.nome_pessoa
            ORDER BY qtd_filmes DESC
            LIMIT 10
        """,
    ),
    PerguntaOuro(
        categoria="Elenco e Equipe",
        pergunta="Quais diretores têm a maior nota média (considerando só quem dirigiu ao menos 5 filmes)?",
        sql_referencia="""
            SELECT p.nome_pessoa, AVG(f.nota_imdb) AS nota_media, COUNT(DISTINCT m.sk_movie_id) AS qtd_filmes
            FROM bridge_movie_person bp
            JOIN dim_people p ON p.sk_person_id = bp.sk_person_id AND p.tipo_pessoa = 'Diretor'
            JOIN dim_movies m ON m.sk_movie_id = bp.sk_movie_id
            JOIN fact_movies_performance f ON f.sk_movie_id = m.sk_movie_id
            WHERE f.nota_imdb IS NOT NULL AND p.nome_pessoa GLOB '*[A-Za-z]*'
            GROUP BY p.nome_pessoa
            HAVING COUNT(DISTINCT m.sk_movie_id) >= 5
            ORDER BY nota_media DESC
            LIMIT 10
        """,
    ),
    PerguntaOuro(
        categoria="Elenco e Equipe",
        pergunta="Qual dupla ator-diretor mais trabalhou junta?",
        sql_referencia="""
            SELECT ator.nome_pessoa AS ator, diretor.nome_pessoa AS diretor, COUNT(DISTINCT m.sk_movie_id) AS qtd_filmes
            FROM dim_movies m
            JOIN bridge_movie_person bp_a ON bp_a.sk_movie_id = m.sk_movie_id
            JOIN dim_people ator ON ator.sk_person_id = bp_a.sk_person_id AND ator.tipo_pessoa = 'Ator'
            JOIN bridge_movie_person bp_d ON bp_d.sk_movie_id = m.sk_movie_id
            JOIN dim_people diretor ON diretor.sk_person_id = bp_d.sk_person_id AND diretor.tipo_pessoa = 'Diretor'
            WHERE ator.nome_pessoa GLOB '*[A-Za-z]*' AND diretor.nome_pessoa GLOB '*[A-Za-z]*'
            GROUP BY ator.nome_pessoa, diretor.nome_pessoa
            ORDER BY qtd_filmes DESC
            LIMIT 10
        """,
    ),
    # ---------------------------------------------------------------- Gêneros e Produtoras
    PerguntaOuro(
        categoria="Gêneros e Produtoras",
        pergunta="Quantos filmes existem em cada gênero?",
        sql_referencia="""
            SELECT g.nome_genero, COUNT(DISTINCT bg.sk_movie_id) AS qtd_filmes
            FROM bridge_movie_genre bg
            JOIN dim_genres g ON g.sk_genre_id = bg.sk_genre_id
            GROUP BY g.nome_genero
            ORDER BY qtd_filmes DESC
        """,
        ordem_importa=False,
    ),
    PerguntaOuro(
        categoria="Gêneros e Produtoras",
        pergunta="Qual produtora teve o maior lucro total?",
        sql_referencia="""
            SELECT c.nome_produtora, SUM(f.lucro_usd) AS lucro_total
            FROM bridge_movie_company bc
            JOIN dim_companies c ON c.sk_company_id = bc.sk_company_id
            JOIN fact_movies_performance f ON f.sk_movie_id = bc.sk_movie_id
            WHERE f.receita_usd IS NOT NULL AND f.orcamento_usd IS NOT NULL
            GROUP BY c.nome_produtora
            ORDER BY lucro_total DESC
            LIMIT 10
        """,
    ),
    PerguntaOuro(
        categoria="Gêneros e Produtoras",
        pergunta="Qual gênero tem a maior margem de lucro média?",
        sql_referencia="""
            SELECT g.nome_genero, AVG((f.receita_usd - f.orcamento_usd) * 1.0 / f.orcamento_usd) AS margem_media
            FROM bridge_movie_genre bg
            JOIN dim_genres g ON g.sk_genre_id = bg.sk_genre_id
            JOIN fact_movies_performance f ON f.sk_movie_id = bg.sk_movie_id
            WHERE f.receita_usd IS NOT NULL AND f.orcamento_usd IS NOT NULL AND f.orcamento_usd > 0
            GROUP BY g.nome_genero
            ORDER BY margem_media DESC
        """,
        ordem_importa=False,
    ),
    # ---------------------------------------------------------------- Avaliações dos Usuários
    PerguntaOuro(
        categoria="Avaliações dos Usuários",
        pergunta="Quais são os filmes mais avaliados pelos usuários?",
        sql_referencia="""
            SELECT m.titulo, r.qtd_avaliacoes_usuarios
            FROM dim_reviews r
            JOIN dim_movies m ON m.sk_movie_id = r.sk_movie_id
            ORDER BY r.qtd_avaliacoes_usuarios DESC
            LIMIT 10
        """,
    ),
    PerguntaOuro(
        categoria="Avaliações dos Usuários",
        pergunta="Em quais filmes a nota média dos usuários mais diverge da nota do IMDb?",
        sql_referencia="""
            SELECT m.titulo, r.nota_media_usuarios, f.nota_imdb,
                   ABS(r.nota_media_usuarios - f.nota_imdb) AS divergencia
            FROM dim_reviews r
            JOIN dim_movies m ON m.sk_movie_id = r.sk_movie_id
            JOIN fact_movies_performance f ON f.sk_movie_id = r.sk_movie_id
            WHERE f.nota_imdb IS NOT NULL
            ORDER BY divergencia DESC
            LIMIT 10
        """,
    ),
]
