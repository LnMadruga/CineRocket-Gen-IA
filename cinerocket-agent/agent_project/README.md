# CineData Analytics — Agente Text-to-SQL

Projeto do **Rocket Lab 2026.2 (Visagio) — Atividade GenAI**. Um agente que responde perguntas em
linguagem natural sobre o catálogo de filmes da CineData Analytics, consultando o banco de dados
(camada Gold, `cinerocket.db`) em tempo real via *tool calling* — sem precisar saber SQL.

- **Linguagem:** Python
- **Framework de agentes:** loop de *tool calling* escrito à mão sobre o SDK `openai` (ver "Decisões de arquitetura")
- **Modelo:** modelos gratuitos (`:free`) via **OpenRouter**, com fallback entre 4 modelos
- **Entregável:** cobre as 3 formas aceitas pelo enunciado — módulo Python importável, notebook Jupyter e módulo de backend FastAPI
- **Banco:** SQLite (`cinerocket.db`, 10 tabelas em Star Schema — fornecido pelo professor)

## Sumário

- [Pré-requisitos](#pré-requisitos)
- [Passo a passo rápido](#passo-a-passo-rápido)
- [Como usar](#como-usar)
- [Estrutura do projeto](#estrutura-do-projeto)
- [Decisões de arquitetura](#decisões-de-arquitetura)
- [Guardrails de segurança](#guardrails-de-segurança)
- [Qualidade dos dados e armadilhas conhecidas](#qualidade-dos-dados-e-armadilhas-conhecidas)
- [Gerenciando a cota da API](#gerenciando-a-cota-da-api)
- [Avaliação (eval)](#avaliação-eval)
- [Testes](#testes)
- [Solução de problemas](#solução-de-problemas)

## Pré-requisitos

- Python 3.11+
- Uma conta gratuita no [OpenRouter](https://openrouter.ai) e uma chave de API (veja o
  [Guia OpenRouter do Rocket Lab](docs/guia-openrouter.html) fornecido na atividade)
- O arquivo `cinerocket.db` fornecido pelo professor (não incluído neste repositório — veja por quê
  na seção [Solução de problemas](#solução-de-problemas))

## Passo a passo rápido

```bash
# 1. Ambiente virtual e dependências
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 2. Configuração
cp .env.example .env
# edite .env e preencha OPENROUTER_API_KEY (gerada em https://openrouter.ai/keys)

# 3. Banco de dados
cp /caminho/para/cinerocket.db data/cinerocket.db

# 4. Confere se está tudo certo (não gasta cota da API)
python scripts/check_setup.py
```

Se o `check_setup.py` não apontar nenhum `✘`, está pronto para usar.

## Como usar

### Opção A — Python direto (script ou REPL)

```python
from app.agent import CinerocketAgent

agente = CinerocketAgent()
resultado = agente.perguntar("Quais são os 5 filmes mais populares?")
print(resultado.resposta)
print(resultado.consultas_sql)   # o SQL que o modelo gerou sozinho
print(resultado.chamadas_api)    # quantas requisições essa pergunta consumiu
agente.close()
```

### Opção B — Notebook Jupyter

```bash
jupyter notebook notebooks/demo.ipynb
```

O notebook tem uma pergunta de exemplo por categoria do enunciado, mais uma demonstração do
cache e da busca híbrida sobre sinopses. **Cada célula de pergunta gasta cota da API** — rode uma
de cada vez.

### Opção C — Backend FastAPI

```bash
uvicorn app.main:app --reload
```

Abra `http://localhost:8000/docs` (Swagger UI) e teste o endpoint `POST /ask` com:

```json
{ "pergunta": "Quais são os 10 filmes com maior receita em reais?" }
```

`GET /health` confirma que a API subiu (não chama o modelo). Não há interface de chat — o
enunciado deixa isso explicitamente opcional.

## Estrutura do projeto

```text
.
├── app/
│   ├── config.py          # configuração (.env): chave da API, modelos, caminho do banco
│   ├── db.py               # conexão somente-leitura + guardrails de SQL
│   ├── schema.py            # dicionário de dados: schema + armadilhas de negócio (vai no prompt)
│   ├── tools.py              # definição das ferramentas (tool calling): executar_sql, buscar_sinopses_similares
│   ├── semantic_search.py     # busca TF-IDF sobre as sinopses (bônus: agente híbrido)
│   ├── cache.py                 # cache de respostas em disco (economiza cota)
│   ├── agent.py                  # loop de tool-calling + fallback entre modelos
│   └── main.py                    # backend FastAPI (POST /ask, GET /health)
├── notebooks/demo.ipynb    # demonstração interativa
├── eval/
│   ├── golden_questions.py  # 14 perguntas com SQL de referência (gabarito), cobrindo as 5 categorias
│   └── run_eval.py           # roda o agente e compara com o gabarito
├── scripts/check_setup.py  # confere .env, banco e modelos antes de usar
├── tests/                  # pytest: guardrails, loop do agente (mockado), API FastAPI
└── data/                   # cinerocket.db vai aqui (não versionado — ver .gitignore)
```

## Decisões de arquitetura

**Framework de agentes:** um loop de *tool calling* escrito à mão (~80 linhas, `app/agent.py`)
sobre o SDK `openai`, em vez de um framework pronto (LangChain, CrewAI, etc.). Com só 2
ferramentas, um framework pesado adicionaria abstração sem necessidade e dificultaria controlar
exatamente quantas chamadas de API cada pergunta consome — crítico com o limite de 50
requisições/dia do free tier.

**Schema completo no prompt, não descoberto via tool call:** o dicionário de dados inteiro
(`app/schema.py`) — tabelas, colunas e as armadilhas de negócio abaixo — já vai no prompt de
sistema. Isso custa mais tokens por chamada, mas economiza 1-2 chamadas inteiras por pergunta que
seriam gastas só "explorando" o schema. Com a cota em requisições (não em tokens), essa troca
compensa.

**Duas ferramentas, não mais:** `executar_sql` (consultas estruturadas) e
`buscar_sinopses_similares` (busca textual sobre sinopses, para perguntas sem coluna estruturada
correspondente — ex.: "filmes parecidos com uma história de viagem no tempo"). Uma superfície
pequena reduz a chance do modelo "se perder" escolhendo ferramenta errada.

**Fallback entre 4 modelos gratuitos:** se um modelo retorna 429 (pool lotado — comum em horário
de pico, conforme o Guia OpenRouter), o agente tenta o próximo da lista automaticamente, sem gastar
uma chamada "void". A lista padrão (`app/config.py`) pode ser sobrescrita via `OPENROUTER_MODELS`
no `.env`.

**Cache de respostas (`app/cache.py`):** perguntas repetidas (mesmo texto normalizado + mesmo
modelo) são respondidas do cache local, sem chamar a API de novo — importante ao iterar no prompt
durante o desenvolvimento.

**Agente híbrido (SQL + TF-IDF):** a busca por sinopse usa **TF-IDF + similaridade de cosseno**
(scikit-learn), não embeddings densos. É uma escolha deliberada e documentada como tal no código
(`app/semantic_search.py`): roda 100% local (não gasta cota), não exige baixar um modelo de
embeddings, e é suficiente para o caso de uso — mas é importante não chamar isso de "busca
semântica" sem a ressalva de que é lexical.

## Guardrails de segurança

O agente só pode **ler** dados. Como o SQL final é escrito por um LLM, a proteção é feita em
**três camadas independentes** (`app/db.py`):

1. Conexão SQLite aberta em **modo somente-leitura no nível do arquivo** (`?mode=ro` na URI) —
   mesmo um comando de escrita que escape da validação falha ao executar.
2. `PRAGMA query_only = ON`, reforçando a mesma garantia na camada do driver.
3. Um validador de texto (`validar_sql`) exige uma única instrução `SELECT`/`WITH`, sem `;` no
   meio, e rejeita `INSERT/UPDATE/DELETE/DROP/ALTER/CREATE/ATTACH/PRAGMA/VACUUM` etc. como palavra
   inteira (não bloqueia `created_at` por conter "CREATE" como substring).

As camadas 1 e 2 foram testadas chamando o SQLite diretamente, sem passar pelo validador — a
escrita é recusada mesmo assim (`tests/test_guardrails.py`).

Também há um **teto de linhas devolvidas** (200 por padrão) e um **timeout por consulta** (10s,
via `progress_handler` do SQLite), para que uma consulta pesada não trave o agente nem estoure o
contexto do modelo.

## Qualidade dos dados e armadilhas conhecidas

Descobertas inspecionando `cinerocket.db` diretamente (não suposições) — documentadas em
`app/schema.py` e injetadas no prompt do agente:

- **`lucro_usd`/`lucro_brl` nunca é `NULL`**, mas é calculado como
  `COALESCE(receita, 0) - COALESCE(orcamento, 0)`. Como **~96% dos filmes não têm receita
  informada**, um filme sem receita aparece com lucro negativo (= -orçamento) ou zero — não
  significa prejuízo real, significa dado ausente. Toda pergunta sobre lucro/margem/receita
  precisa filtrar `WHERE receita_usd IS NOT NULL AND orcamento_usd IS NOT NULL` (o próprio
  enunciado já sinaliza isso: "considerando apenas filmes com receita informada").
- **`dim_people` tem nomes duplicados com `sk_person_id` diferentes** (371.792 nomes distintos
  para 424.656 chaves) — a mesma pessoa pode ter mais de uma chave surrogate. Contagens por ator
  devem agrupar por `nome_pessoa` (texto), não só por `sk_person_id`. Há também ruído de origem
  (nomes puramente numéricos, ex. `"0.6"`) filtrado com `nome_pessoa GLOB '*[A-Za-z]*'`.
- **Armadilha de performance real, encontrada testando:** calcular uma data de referência com
  `WITH ref AS (SELECT MAX(...) ...) ... JOIN ref ON 1=1` e depois filtrar
  `bridge_movie_person` (745 mil linhas) por ela fez o otimizador do SQLite escolher um plano de
  execução catastrófico (de alguns segundos para **mais de 5 minutos**). Trocar por uma
  **subconsulta escalar** (`(SELECT MAX(...) ...)` direto no `WHERE`) resolveu — a consulta roda
  em ~4s. Essa recomendação está no prompt do agente.
- Agrupar direto por `nome_pessoa` sobre `bridge_movie_person` também pode ficar lento (o SQLite
  tende a escanear `dim_people` inteira, 424 mil linhas, como tabela condutora); agregar primeiro
  por `sk_person_id` e só depois juntar o nome é mais rápido.

## Gerenciando a cota da API

O free tier do OpenRouter permite **50 requisições/dia**, e cada pergunta ao agente consome
normalmente **2 chamadas** (1 para decidir e rodar o SQL, 1 para formular a resposta final com o
resultado) — mais, se o guardrail recusar uma consulta e o modelo precisar corrigir. Formas que
este projeto usa para não desperdiçar cota:

- Schema completo no prompt (não gasta chamadas "explorando" a estrutura do banco).
- Cache de respostas em disco (perguntas repetidas não chamam a API de novo).
- Fallback entre modelos em caso de 429 (pool lotado), em vez de insistir no mesmo modelo.
- Limite de rodadas de tool-calling por pergunta (`MAX_TOOL_CALL_ROUNDS=4`, configurável),
  evitando loops acidentais.
- `eval/run_eval.py` sempre pede confirmação antes de gastar cota, e aceita `--limit` para rodar
  só algumas perguntas por vez.

## Avaliação (eval)

`eval/golden_questions.py` tem **14 perguntas com SQL de referência** escrito à mão (um por
categoria do enunciado — Bilheteria e Finanças, Popularidade e Engajamento, Elenco e Equipe,
Gêneros e Produtoras, Avaliações dos Usuários), já aplicando as regras de negócio acima.

```bash
# Não gasta cota: só roda o gabarito contra o banco e mostra o resultado esperado
python -m eval.run_eval --dry-run

# Gasta cota: roda o agente de verdade e compara os DADOS da última consulta que ele
# executou com o gabarito (não compara o texto da resposta, que varia de frase para frase)
python -m eval.run_eval --limit 5
```

## Testes

```bash
pytest -q
```

27 testes, divididos em:

- **`test_guardrails.py`** — roda contra o banco real, sem precisar de chave de API: confirma que
  SQL de escrita é recusado (inclusive testando a conexão somente-leitura diretamente, sem passar
  pelo validador) e que consultas de leitura legítimas funcionam.
- **`test_agent_loop.py`** — usa um **cliente OpenAI falso** (dublê) para validar o loop de
  tool-calling, o fallback entre modelos em caso de 429, o limite de rodadas, e o despacho correto
  de cada ferramenta — sem gastar nenhuma chamada real. Veja por que no topo do arquivo: este
  sandbox de desenvolvimento não tem saída de rede para `openrouter.ai`, e testes automatizados não
  devem depender de nem gastar uma cota de 50/dia.
- **`test_api.py`** — testa o FastAPI (`/health`, validação de entrada) sem chamar a API externa.

O teste de ponta a ponta com a API **real** precisa ser feito manualmente, com uma chave válida —
use `scripts/check_setup.py` e depois `eval/run_eval.py --limit 1` para o primeiro teste.

## Solução de problemas

- **`cinerocket.db` não está no repositório:** é proposital (`.gitignore`) — o arquivo tem ~554 MB,
  acima do limite de 100 MB do GitHub. Copie o arquivo fornecido pelo professor para `data/`.
- **`RuntimeError: OPENROUTER_API_KEY não definido`:** copie `.env.example` para `.env` e preencha
  a chave.
- **`429` ao chamar o modelo:** normal — o agente tenta o próximo modelo da lista automaticamente.
  Se todos os 4 falharem, você provavelmente atingiu a cota diária (confira em
  [openrouter.ai/activity](https://openrouter.ai/activity)) ou está em horário de pico nos modelos
  gratuitos. O reset é à meia-noite UTC (21h em Brasília).
- **Consulta "interrompida: excedeu o limite de 10s":** o guardrail de timeout funcionou como
  esperado — normalmente indica uma consulta sem filtro suficiente sobre uma tabela grande
  (`bridge_movie_person`, `dim_people`). Veja a seção de armadilhas de performance acima.
- **`ConsultaRecusada: Palavra-chave não permitida`:** o modelo tentou gerar algo fora de um
  `SELECT`/`WITH` puro. O erro é devolvido ao próprio modelo como resultado da ferramenta, e ele
  normalmente se corrige na rodada seguinte.
