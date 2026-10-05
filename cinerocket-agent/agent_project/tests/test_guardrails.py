"""Testes dos guardrails de SQL — rodam 100% local, contra o banco real,
sem precisar de chave de API (não envolvem o modelo).
"""

import pytest

from app.db import ConsultaRecusada, executar_consulta, validar_sql

CONSULTAS_PROIBIDAS = [
    "DROP TABLE dim_movies",
    "INSERT INTO dim_movies (sk_movie_id) VALUES ('x')",
    "UPDATE dim_movies SET titulo = 'x'",
    "DELETE FROM dim_movies",
    "ALTER TABLE dim_movies ADD COLUMN x TEXT",
    "CREATE TABLE x (a INT)",
    "SELECT * FROM dim_movies; DROP TABLE dim_movies",
    "PRAGMA table_info(dim_movies)",
    "ATTACH DATABASE 'outro.db' AS outro",
    "VACUUM",
    "",
    "   ",
]


@pytest.mark.parametrize("sql", CONSULTAS_PROIBIDAS)
def test_bloqueia_consultas_proibidas(sql):
    with pytest.raises(ConsultaRecusada):
        validar_sql(sql)


def test_nao_bloqueia_coluna_created_at():
    # "created_at" contém "CREATE" como substring — não deve disparar o bloqueio de palavra inteira
    validar_sql("SELECT created_at FROM movie_reviews LIMIT 1")


def test_permite_select_simples():
    validar_sql("SELECT titulo FROM dim_movies LIMIT 1")


def test_permite_with_cte():
    validar_sql("WITH t AS (SELECT 1 AS x) SELECT x FROM t")


def test_executa_consulta_real_contra_o_banco():
    resultado = executar_consulta("SELECT COUNT(*) AS total FROM dim_movies")
    assert resultado.columns == ["total"]
    assert resultado.rows[0][0] > 90_000  # esperado ~95.645


def test_teto_de_linhas_e_respeitado():
    resultado = executar_consulta("SELECT sk_movie_id FROM dim_movies", max_rows=5)
    assert len(resultado.rows) == 5
    assert resultado.truncated is True


def test_camada_de_conexao_bloqueia_escrita_mesmo_sem_validador():
    """Defesa em profundidade: mesmo chamando o sqlite3 diretamente (sem passar pelo
    validador), a conexão somente-leitura deve recusar a escrita."""
    import sqlite3

    from app.config import settings

    conn = sqlite3.connect(f"file:{settings.database_path}?mode=ro", uri=True)
    conn.execute("PRAGMA query_only = ON;")
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("UPDATE dim_movies SET titulo = 'HACK' WHERE sk_movie_id = 'x'")
    finally:
        conn.close()
