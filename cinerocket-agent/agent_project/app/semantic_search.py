"""Busca por similaridade textual sobre as sinopses dos filmes (agente híbrido, bônus).

Importante ser honesto sobre a técnica: isto é uma busca **lexical TF-IDF +
similaridade de cosseno**, não uma busca semântica por embeddings densos
(ex.: sentence-transformers ou embeddings de um LLM). TF-IDF encontra
sinopses que compartilham PALAVRAS parecidas com a consulta — funciona bem
para "filmes sobre viagem no tempo" (porque sinopses desse tema tendem a usar
vocabulário parecido), mas não captura similaridade puramente conceitual sem
sobreposição de vocabulário. Optamos por TF-IDF (scikit-learn) em vez de
embeddings porque: (1) roda 100% local, sem gastar nenhuma chamada da cota
diária do OpenRouter; (2) não exige baixar um modelo de embeddings adicional;
(3) é suficiente para o caso de uso (explorar sinopses), e documentamos a
limitação em vez de chamar de "semântica" sem ressalva.
"""

from __future__ import annotations

import sqlite3

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from app.config import settings

STOPWORDS_PT_EN = frozenset(
    ["a", "o", "os", "as", "um", "uma", "umas", "uns", "de", "do", "da", "dos", "das", "em", "no", "na", "nos", "nas", "por", "para", "com", "que", "é", "foi", "são", "era", "the", "a", "an", "of", "in", "on", "at", "to", "for", "with", "is", "are", "was", "were", "this", "that", "and", "or", "his", "her", "their", "its"]
)


class BuscaSinopses:
    """Carrega as sinopses uma única vez e permite buscar por similaridade."""

    def __init__(self) -> None:
        self._vectorizer: TfidfVectorizer | None = None
        self._matrix = None
        self._ids: list[str] = []
        self._titulos: list[str] = []
        self._sinopses: list[str] = []

    def carregar(self) -> None:
        conn = sqlite3.connect(f"file:{settings.database_path}?mode=ro", uri=True)
        try:
            cur = conn.execute(
                "SELECT sk_movie_id, titulo, sinopse FROM dim_movies "
                "WHERE sinopse IS NOT NULL AND length(trim(sinopse)) > 0"
            )
            for sk, titulo, sinopse in cur.fetchall():
                self._ids.append(sk)
                self._titulos.append(titulo)
                self._sinopses.append(sinopse)
        finally:
            conn.close()

        self._vectorizer = TfidfVectorizer(
            lowercase=True,
            stop_words=list(STOPWORDS_PT_EN),
            max_features=50_000,
            ngram_range=(1, 2),
        )
        self._matrix = self._vectorizer.fit_transform(self._sinopses)

    def buscar(self, consulta: str, top_k: int = 5) -> list[dict]:
        if self._vectorizer is None or self._matrix is None:
            raise RuntimeError("Chame carregar() antes de buscar().")
        vetor_consulta = self._vectorizer.transform([consulta])
        similaridades = cosine_similarity(vetor_consulta, self._matrix).ravel()
        melhores = similaridades.argsort()[::-1][:top_k]
        return [
            {
                "sk_movie_id": self._ids[i],
                "titulo": self._titulos[i],
                "similaridade": round(float(similaridades[i]), 4),
                "sinopse": self._sinopses[i][:300],
            }
            for i in melhores
            if similaridades[i] > 0
        ]
