"""Testes do módulo FastAPI que não envolvem chamar a API externa do OpenRouter
(isso exigiria rede e gastaria cota — ver README para o teste manual com a API real)."""

from fastapi.testclient import TestClient

from app.main import app


def test_health_check():
    with TestClient(app) as client:
        resp = client.get("/health")
        assert resp.status_code == 200
        corpo = resp.json()
        assert corpo["status"] == "ok"
        assert "models" in corpo


def test_openapi_expoe_as_rotas_esperadas():
    with TestClient(app) as client:
        resp = client.get("/openapi.json")
        assert resp.status_code == 200
        assert set(resp.json()["paths"].keys()) == {"/health", "/ask"}


def test_ask_com_pergunta_vazia_e_rejeitado_na_validacao():
    with TestClient(app) as client:
        resp = client.post("/ask", json={"pergunta": "a"})  # menor que min_length=3
        assert resp.status_code == 422
