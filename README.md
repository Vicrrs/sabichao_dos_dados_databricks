# 🦉 Sabichão dos Dados · Databricks Apps + Streamlit

Um chat que responde perguntas sobre as **tabelas do Unity Catalog**: se uma tabela existe, quais
tabelas há sobre um assunto, o que cada uma contém e as **estatísticas** dela.

Cada pergunta passa por **três IAs**, escolhidas por você na própria app:

```
                  ┌─► 🔎 Pesquisador A (IA 1) ─┐   cada um estuda a pergunta e investiga o
  pergunta ───────┤    pensa → ferramenta →    │   catálogo sozinho, em paralelo, sem ver o outro
                  └─► 🔎 Pesquisador B (IA 2) ─┤
                                               ▼
                                     ⚖️ Juiz (IA 3)   confere cada resposta contra as EVIDÊNCIAS
                                               │      (o que as ferramentas realmente devolveram),
                                               ▼      aponta divergências e escreve a resposta final
                                            usuário

 Ferramentas dos pesquisadores:
   listar_catalogos · listar_schemas · listar_tabelas   navegar no Unity Catalog
   buscar_tabelas                                       procura no nome/comentário da tabela e das colunas
   descrever_tabela                                     tipo, formato, dono, datas, colunas e comentários
   estatisticas_tabela                                  linhas, tamanho, nulos %, distintos, mín/máx, média,
                                                        desvio, quartis, valores mais comuns e amostra
   executar_sql                                         um SELECT somente leitura (com LIMIT)
```

Na tela você vê, ao vivo, o que cada pesquisador está fazendo. Na resposta, o expander
**"Como o Sabichão chegou nessa resposta"** mostra o veredito do juiz, as divergências, a resposta
de cada pesquisador e cada passo (SQL e tabelas de resultado).

## Como colocar no Databricks

1. Neste workspace, crie um **Git folder** apontando para este repositório
   (**Workspace → Create → Git folder**).
2. **Compute → Apps → Create app → Create a custom app**. Dê um nome (ex.: `sabichao-dos-dados`) e crie.
3. Na página da app, em **Configure → User authorization**, adicione os escopos:
   `sql`, `model-serving`, `files`, `catalog.catalogs:read`, `catalog.schemas:read`, `catalog.tables:read`.
   Assim a app age **como quem está logado** e só mostra as IAs, warehouses e tabelas que essa pessoa enxerga.
   (Se a opção não aparecer, um admin precisa habilitar *user authorization* para Apps no workspace.)
4. Clique em **Deploy** e escolha como *source code path* a **raiz do Git folder** (a pasta que tem
   `app.py` e `app.yaml`).
5. Aguarde o status **Running** e abra a URL da app.
6. Na aba **⚙️ Configuração**:
   - escolha a IA do **Pesquisador A**, a do **Pesquisador B** e a do **Juiz** (a lista mostra todos os
     endpoints de chat do Model Serving que você pode usar);
   - escolha o **SQL Warehouse** (serverless recomendado);
   - opcionalmente, limite os **catálogos** que o Sabichão pode consultar;
   - clique em **💾 Salvar configuração** e depois em **🧪 Testar**.
7. Vá para **💬 Chat** e pergunte.

Depois de um `git pull` no Git folder, clique em **Deploy** de novo para a app pegar o código novo.

### Onde a configuração fica

Em `/Volumes/<catálogo>/<schema>/config/sabichao.json` (padrão `main.sabichao`, definido no `app.yaml`
e trocável na barra lateral em **📁 Projeto**). Ao salvar, a app cria o schema e o volume se não existirem,
então quem salva precisa de `CREATE SCHEMA` / `CREATE VOLUME` ali (ou crie antes e dê `READ/WRITE VOLUME`).
A configuração salva vale para todo mundo que abre a app.

### Documentação numa tabela de revisão (opcional)

Se as tabelas não têm comentários no Unity Catalog, mas estão documentadas numa **tabela de revisão**
(a que o notebook `uc_auto_doc_revisao` grava, ex.: `data_science_dev.churn_models.uc_doc_revisao`),
informe-a em **⚙️ Configuração → 📘 Tabela de documentação** e clique em **Verificar**.

- **Formato:** 1 linha por coluna. Obrigatórias: `caminho_tabela`, `coluna`, `descricao_tabela`,
  `descricao_coluna`. Usadas se existirem: `descricao_linha`, `descricao_schema`, `descricao_catalogo`,
  `catalogo`, `schema`, `status_revisao`, `descricao_corrigida`, `observacao_revisao`, `revisado_por`, `revisado_em`.
- **Onde entra:** `buscar_tabelas` também procura no texto da documentação (nome, descrição da tabela,
  colunas e descrição das colunas); `listar_tabelas`/`listar_schemas` preenchem os comentários vazios;
  `descrever_tabela` mostra o comentário do UC e a documentação lado a lado, com o status de cada coluna;
  `estatisticas_tabela` usa as descrições no perfil.
- **Revisão do time:** `rejeitado` nunca é usado; `descricao_corrigida` vale mais que a gerada; o que é
  `pendente` aparece marcado como **[doc NÃO revisada]** e os agentes avisam o usuário. Marque
  *"Usar só a documentação aprovada ou corrigida"* para ignorar o pendente.
- **Comentário do UC continua valendo:** quando existe, ele é mostrado; a documentação completa o que falta.
- **Permissão:** quem usa a app (ou a conta de serviço) precisa de `SELECT` nessa tabela. Ela é lida com
  SQL fixo da app, então não precisa estar nos catálogos liberados para as IAs.

### Escolhendo as IAs

- **Pesquisadores** precisam de **tool calling**. Os modelos de chat das Foundation Model APIs
  (Llama 3.3/4, Claude, GPT, Gemini...) suportam. O botão **🧪 Testar** confere isso.
- Use **IAs diferentes em A e B**: o juiz ganha dois pontos de vista independentes. Se usar a mesma,
  as temperaturas diferentes (A = 0.0, B = 0.4 por padrão) ainda fazem os dois explorarem caminhos diferentes.
- **Juiz**: o modelo mais forte que você tiver. Ele não chama ferramentas; lê as duas respostas e as evidências.

### Conta de serviço da app

Usada só se a *user authorization* estiver desligada (`IDENTIDADE_APP=app` no `app.yaml`) ou se o token
do usuário vier sem os escopos. Nesse caso, dê a ela (aba *Authorization* da app): `CAN QUERY` nos
endpoints de IA, `CAN USE` no warehouse, `USE CATALOG/USE SCHEMA/SELECT` nos catálogos a consultar e
acesso ao volume de configuração. O expander **🩺 Diagnóstico de acesso** na barra lateral ajuda a achar
o que falta.

## Segurança

- **Unity Catalog é a barreira principal.** Com *user authorization*, cada pessoa só enxerga e consulta
  o que tem GRANT — inclusive *row filters* e *column masks*.
- **Somente leitura.** O `sql_guard` só deixa passar um único `SELECT`/`WITH`, com nomes completos,
  apenas nos catálogos liberados, sem funções que saem do banco (`http_request`, `secret`, `ai_*`,
  `read_files`, `remote_query`...), sem *time travel* e sempre com `LIMIT`. O catálogo `system` nunca é liberado.
- **Nomes validados.** Tabelas e catálogos pedidos pelas IAs passam por `nome_seguro` antes de virar SQL;
  o termo de busca vai como parâmetro (`:termo`), nunca concatenado.
- **Sem credenciais nas telas.** Mensagens de erro passam por `ocultar_segredos`.
- **Custo.** `estatisticas_tabela` lê no máximo *Linhas para estatísticas* (padrão 100 mil; a contagem total
  é sempre exata) e os resultados ficam em cache por 10 minutos, compartilhados entre os dois pesquisadores.

## Estrutura

```
app.py · app.yaml · requirements.txt  # app Streamlit (Databricks Apps)
src/sabichao/
    agentes.py        # Pesquisador (ciclo pensa → ferramenta → observa), Juiz e o orquestrador Sabichao
    ferramentas.py    # as ferramentas dos pesquisadores, escopo de catálogos, cache e o registro de cada passo
    perfil.py         # estatísticas da tabela (SQL no warehouse)
    documentacao.py   # tabela de documentação/revisão completando os metadados do UC
    catalogo.py       # Unity Catalog (SDK) + busca no information_schema + SQL no warehouse
    llm.py            # IA do Model Serving com tool calling (formato OpenAI Chat Completions)
    config.py         # as 3 IAs, warehouse, catálogos e limites (JSON no Volume)
    app_servicos.py   # identidade (usuário ou conta de serviço), descoberta, leitura/gravação da config
    descoberta.py     # lista IAs de chat, SQL Warehouses e catálogos
    sql_guard.py      # SQL somente leitura e só nos catálogos liberados
    dados.py          # Statement Execution API (com parâmetros)
    seguranca.py      # nomes seguros para SQL e ocultação de credenciais
tests/                # testes locais sem Databricks (IA simulada, SQL real em SQLite, app com AppTest)
```

## Rodar os testes localmente

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest -q
```
