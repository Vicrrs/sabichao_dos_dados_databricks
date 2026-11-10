"""App Streamlit do Sabichão dos Dados (Databricks Apps).

Abas:
  💬 Chat           pergunte sobre as tabelas: se existem, quais há sobre um assunto, o que têm e as estatísticas
  ⚙️ Configuração   escolhe as 3 IAs (Pesquisador A, Pesquisador B e Juiz), o SQL Warehouse e os catálogos
"""
from __future__ import annotations

import os
import queue
import sys
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd
import streamlit as st

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ / "src"))

from sabichao.app_servicos import Servicos  # noqa: E402
from sabichao.config import ConfigSabichao  # noqa: E402
from sabichao.descoberta import mensagem_erro  # noqa: E402
from sabichao.ferramentas import Cache  # noqa: E402

st.set_page_config(page_title="Sabichão dos Dados", page_icon="🦉", layout="wide")

ss = st.session_state
ICONES = {"listar_catalogos": "📚", "listar_schemas": "🗂️", "listar_tabelas": "📋", "buscar_tabelas": "🔍",
          "descrever_tabela": "📝", "estatisticas_tabela": "📊", "executar_sql": "🧮"}
VENCEDOR = {"A": "Pesquisador A", "B": "Pesquisador B", "empate": "empate (os dois chegaram ao mesmo)",
            "nenhum": "nenhum dos dois achou a resposta"}
EXEMPLOS = [
    "Existe alguma tabela de clientes?",
    "Quais tabelas falam de vendas?",
    "O que tem na tabela main.default.minha_tabela?",
    "Me dê as estatísticas da tabela main.default.minha_tabela",
]


# =================================================================== estado
def servicos() -> Servicos:
    """Recria os clientes quando o token do usuário muda (ele é renovado a cada requisição)."""
    headers = dict(st.context.headers or {})
    token = {k.lower(): v for k, v in headers.items()}.get("x-forwarded-access-token")
    atual = ss.get("servicos")
    if atual is None or (token and atual.token_hash != hash(token)):
        novo = Servicos.do_contexto(headers, w_app=getattr(atual, "w_app", None))
        if atual is not None:
            novo.cache = atual.cache
        ss.servicos = novo
    return ss.servicos


def carregar_config():
    cfg = None
    try:
        cfg = servicos().ler_config(ss.catalogo, ss.schema)
    except Exception as e:  # noqa: BLE001
        st.warning(f"Não consegui ler a configuração: {mensagem_erro(e)}")
    ss.cfg = cfg or ConfigSabichao()
    ss.cfg_salva = cfg is not None
    ss.carregado_de = (ss.catalogo, ss.schema)


def recursos(forcar: bool = False):
    if forcar or "recursos" not in ss:
        with st.spinner("Listando as IAs e os SQL Warehouses do workspace…"):
            ss.recursos = servicos().recursos()
    return ss.recursos


def catalogos() -> List[str]:
    if "lista_catalogos" not in ss:
        try:
            ss.lista_catalogos = servicos().catalogos()
        except Exception as e:  # noqa: BLE001
            st.warning(f"Não foi possível listar os catálogos: {mensagem_erro(e)}")
            ss.lista_catalogos = []
    return ss.lista_catalogos


# ================================================================ inicialização
ss.setdefault("catalogo", os.environ.get("PROJETO_CATALOGO", "main"))
ss.setdefault("schema", os.environ.get("PROJETO_SCHEMA", "sabichao"))
if ss.get("carregado_de") != (ss.catalogo, ss.schema):
    carregar_config()
ss.setdefault("chat", [])
ss.setdefault("conversa_id", str(uuid.uuid4()))
ss.setdefault("tokens", 0)
cfg: ConfigSabichao = ss.cfg

# ===================================================================== sidebar
with st.sidebar:
    st.markdown("## 🦉 Sabichão dos Dados")
    st.caption("Duas IAs pesquisam, uma IA julga.")
    sv = servicos()
    if sv.identidade == "usuario":
        st.success(f"Conectado como **{sv.usuario or 'você'}**. O Sabichão usa as suas permissões.")
    else:
        st.warning(
            (sv.motivo or "A app está usando a **própria conta de serviço**.")
            + f"\n\nEla só enxerga o que foi concedido a ela (application id `{sv.id_conta_servico}`)."
        )
    with st.expander("🩺 Diagnóstico de acesso"):
        st.caption(f"Host: `{getattr(getattr(sv.w_app, 'config', None), 'host', '?')}` · identidade: **{sv.identidade}**")
        if st.button("Testar acesso à API", use_container_width=True):
            st.dataframe(pd.DataFrame(sv.diagnostico()), hide_index=True, use_container_width=True)
            st.caption("200 = ok · 401/403 = token sem permissão/escopo · 30x = redirecionamento para login.")

    st.markdown("#### Time do Sabichão")
    for papel, nome in (("🔎 Pesquisador A", cfg.llm_pesquisador_a), ("🔎 Pesquisador B", cfg.llm_pesquisador_b),
                        ("⚖️ Juiz", cfg.llm_juiz)):
        st.markdown(f"{papel}: `{nome}`" if nome else f"{papel}: _não escolhido_")
    if cfg.catalogos_liberados:
        st.caption("Catálogos: " + ", ".join(cfg.catalogos_liberados))
    if cfg.tabela_documentacao:
        st.caption(f"📘 Documentação: `{cfg.tabela_documentacao}`"
                   + (" (só revisada)" if cfg.doc_somente_revisada else ""))
    if ss.tokens:
        st.metric("Tokens nesta sessão", f"{ss.tokens:,}".replace(",", "."))
    if st.button("🧹 Nova conversa", use_container_width=True):
        ss.chat, ss.conversa_id = [], str(uuid.uuid4())
        st.rerun()

    with st.expander("📁 Projeto"):
        st.caption("Onde a configuração fica salva (um Volume no Unity Catalog).")
        novo_cat = st.text_input("Catálogo", ss.catalogo)
        novo_sch = st.text_input("Schema", ss.schema)
        if st.button("Abrir projeto", use_container_width=True) and (novo_cat, novo_sch) != (ss.catalogo, ss.schema):
            ss.catalogo, ss.schema = novo_cat.strip(), novo_sch.strip()
            st.rerun()
        st.caption(f"`{Servicos.caminho_config(ss.catalogo, ss.schema)}`")

aba_chat, aba_cfg = st.tabs(["💬 Chat", "⚙️ Configuração"])


# =================================================================== exibição
def mostrar_tabela(tabela: Dict[str, Any] | None):
    if tabela and tabela.get("linhas"):
        df = pd.DataFrame(tabela["linhas"], columns=tabela["colunas"]).astype(str).replace("None", "")
        st.dataframe(df, hide_index=True, use_container_width=True, height=min(38 + 35 * len(df), 280))


def rotulo_passo(p: Dict[str, Any]) -> str:
    args = ", ".join(f"{v}" for k, v in (p.get("argumentos") or {}).items() if k != "sql")
    marca = "⚠️" if p.get("erro") else ICONES.get(p["ferramenta"], "🛠️")
    return f"{marca} {p['ferramenta']}({args})"


def mostrar_relatorio(rel: Dict[str, Any]):
    st.caption(f"`{rel['endpoint']}` · {len(rel['passos'])} passo(s) · "
               f"{rel['tokens_entrada'] + rel['tokens_saida']} tokens · {rel['duracao_ms'] / 1000:.1f}s")
    if rel.get("erro"):
        st.error(rel["erro"])
    st.markdown(rel.get("resposta") or "_(sem resposta)_")
    if rel["passos"]:
        st.markdown("---\n**Passos (o que as ferramentas devolveram):**")
    # Streamlit não aceita expander dentro de expander: os passos vão em sequência.
    for p in rel["passos"]:
        st.markdown(f"**{rotulo_passo(p)}** · {p.get('duracao_ms', 0) / 1000:.1f}s")
        if p.get("sql"):
            st.code(p["sql"], language="sql")
        if p.get("erro"):
            st.warning(p["erro"])
        if (p.get("tabela") or {}).get("linhas"):
            mostrar_tabela(p["tabela"])
        else:
            st.caption(p["texto"][:600])


def mostrar_detalhes(d: Dict[str, Any]):
    v = d["veredito"]
    partes = [f"⚖️ Juiz `{v.get('endpoint') or '—'}`"]
    if v.get("vencedor"):
        partes.append(f"mais confiável: **{VENCEDOR.get(v['vencedor'], v['vencedor'])}**")
    if v.get("confianca"):
        partes.append(f"confiança {v['confianca']}")
    partes.append(f"{d.get('tokens', 0)} tokens")
    st.caption(" · ".join(partes))
    with st.expander("🔍 Como o Sabichão chegou nessa resposta"):
        if v.get("justificativa"):
            st.markdown(f"**Juiz:** {v['justificativa']}")
        if v.get("divergencias"):
            st.markdown("**Divergências entre os pesquisadores:**\n" + "\n".join(f"- {x}" for x in v["divergencias"]))
        abas = st.tabs([f"🔎 {r['nome']}" for r in d["relatorios"]])
        for aba, rel in zip(abas, d["relatorios"]):
            with aba:
                mostrar_relatorio(rel)


def acompanhar(sab, pergunta: str, historico: List[Dict[str, str]]):
    """Roda o Sabichão numa thread e mostra, ao vivo, o que cada pesquisador está fazendo."""
    fila: queue.Queue = queue.Queue()
    saida: Dict[str, Any] = {}

    def rodar():
        try:
            saida["r"] = sab.responder(pergunta, historico, fila.put)
        except Exception as e:  # noqa: BLE001
            saida["erro"] = e

    t = threading.Thread(target=rodar, daemon=True)
    t.start()
    c1, c2 = st.columns(2)
    caixas = {"Pesquisador A": c1.status("🔎 Pesquisador A estudando a pergunta…", expanded=True),
              "Pesquisador B": c2.status("🔎 Pesquisador B estudando a pergunta…", expanded=True)}
    juiz = None
    while t.is_alive() or not fila.empty():
        try:
            ev = fila.get(timeout=0.25)
        except queue.Empty:
            continue
        nome, tipo = ev["agente"], ev["tipo"]
        if nome == "juiz":
            if tipo == "inicio":
                juiz = st.status(f"⚖️ Juiz (`{ev['endpoint']}`) conferindo as evidências…", expanded=False)
            elif juiz is not None:
                juiz.update(label="⚖️ Juiz deu o veredito", state="complete")
            continue
        caixa = caixas[nome]
        if tipo == "inicio":
            caixa.update(label=f"🔎 {nome} (`{ev['endpoint']}`) pesquisando…")
        elif tipo == "chamada":
            args = ", ".join(str(v) for v in ev["argumentos"].values())
            caixa.write(f"{ICONES.get(ev['ferramenta'], '🛠️')} `{ev['ferramenta']}`({args[:120]})")
        elif tipo == "passo" and ev["passo"].erro:
            caixa.caption(f"⚠️ {ev['passo'].erro[:200]}")
        elif tipo == "fim":
            rel = ev["relatorio"]
            if rel.erro:
                caixa.update(label=f"❌ {nome}: {rel.erro[:80]}", state="error", expanded=False)
            else:
                caixa.update(label=f"✅ {nome}: {len(rel.passos)} passo(s) em {rel.duracao_ms / 1000:.0f}s",
                             state="complete", expanded=False)
    t.join()
    if "erro" in saida:
        raise saida["erro"]
    return saida["r"]


# ======================================================================= chat
with aba_chat:
    falta = cfg.faltando()
    if falta:
        st.info("👋 Antes de conversar, escolha na aba **⚙️ Configuração**: " + ", ".join(falta) + ".")
    if not ss.chat:
        st.markdown("### 🦉 Pergunte sobre as suas tabelas")
        st.markdown("Duas IAs investigam o Unity Catalog cada uma do seu jeito; uma terceira IA confere o que "
                    "elas acharam e responde. Exemplos:")
        for ex in EXEMPLOS:
            st.markdown(f"- _{ex}_")
    for m in ss.chat:
        with st.chat_message(m["role"], avatar="🦉" if m["role"] == "assistant" else None):
            st.markdown(m["content"])
            if m.get("detalhes"):
                mostrar_detalhes(m["detalhes"])

    pergunta = st.chat_input("Pergunte sobre tabelas, colunas ou estatísticas…", disabled=bool(falta))
    if pergunta:
        historico = [{"role": m["role"], "content": m["content"]} for m in ss.chat]
        ss.chat.append({"role": "user", "content": pergunta})
        with st.chat_message("user"):
            st.markdown(pergunta)
        with st.chat_message("assistant", avatar="🦉"):
            try:
                resultado = acompanhar(servicos().sabichao(cfg), pergunta, historico)
                d = resultado.to_dict()
                ss.chat.append({"role": "assistant", "content": resultado.veredito.resposta_final, "detalhes": d})
                ss.tokens += resultado.tokens
            except Exception as e:  # noqa: BLE001
                ss.chat.append({"role": "assistant", "content": f"⚠️ Erro: {mensagem_erro(e)}"})
        st.rerun()


# ============================================================== configuração
def preferido(nomes: List[str], termos: List[str], evitar: List[str] = ()) -> str | None:
    """Primeiro endpoint cujo nome contém um dos termos (na ordem), sem repetir os já escolhidos."""
    for termo in termos:
        for n in nomes:
            if termo in n and n not in evitar:
                return n
    resto = [n for n in nomes if n not in evitar]
    return resto[0] if resto else (nomes[0] if nomes else None)


def indice(opcoes: List[str], valor: str | None, alternativa: str | None = None) -> int:
    if valor in opcoes:
        return opcoes.index(valor)
    return opcoes.index(alternativa) if alternativa in opcoes else 0


with aba_cfg:
    r = recursos()
    c1, c2 = st.columns([4, 1])
    c1.markdown(f"Encontrei **{len(r.llms)}** IAs de chat e **{len(r.warehouses)}** SQL Warehouses que você pode usar.")
    if c2.button("🔄 Atualizar listas"):
        ss.pop("lista_catalogos", None)
        recursos(forcar=True)
        st.rerun()
    for aviso in r.avisos:
        st.warning(aviso)
    if not ss.cfg_salva:
        st.info("Ainda não há configuração salva neste projeto. Escolha as IAs abaixo e clique em **Salvar**.")

    nomes = [m.nome for m in r.llms]
    rotulos = {m.nome: m.rotulo for m in r.llms}
    sug_a = preferido(nomes, ["llama-3-3-70b", "llama-4", "claude", "gpt"])
    sug_b = preferido(nomes, ["claude", "gpt", "gemini", "llama-4", "qwen"], [sug_a])
    sug_j = preferido(nomes, ["claude-opus", "claude-sonnet", "gpt-5", "claude", "gpt", "gemini"], [sug_a, sug_b])
    whs = {x.id: x.rotulo for x in r.warehouses}
    if cfg.warehouse_id and cfg.warehouse_id not in whs:
        whs[cfg.warehouse_id] = f"{cfg.warehouse_id} (salvo; não aparece na sua lista)"
    opcoes_cat = sorted(set(catalogos()) | set(cfg.catalogos_liberados))

    if not nomes:
        st.error("Nenhum endpoint de IA de chat disponível para você. Verifique as permissões em Serving.")
    with st.form("form_config"):
        st.markdown("#### 🧠 As três IAs")
        st.caption("Os pesquisadores precisam suportar **tool calling** (chamam as ferramentas do catálogo). "
                   "Usar modelos diferentes em A e B dá pontos de vista diferentes para o juiz comparar.")
        k1, k2, k3 = st.columns(3)
        with k1:
            st.markdown("**🔎 Pesquisador A**")
            ia_a = st.selectbox("IA do Pesquisador A", nomes, index=indice(nomes, cfg.llm_pesquisador_a, sug_a),
                                format_func=rotulos.get, label_visibility="collapsed")
            temp_a = st.slider("Temperatura A", 0.0, 1.0, float(cfg.temperatura_a), 0.1)
        with k2:
            st.markdown("**🔎 Pesquisador B**")
            ia_b = st.selectbox("IA do Pesquisador B", nomes, index=indice(nomes, cfg.llm_pesquisador_b, sug_b),
                                format_func=rotulos.get, label_visibility="collapsed")
            temp_b = st.slider("Temperatura B", 0.0, 1.0, float(cfg.temperatura_b), 0.1)
        with k3:
            st.markdown("**⚖️ Juiz**")
            ia_j = st.selectbox("IA do Juiz", nomes, index=indice(nomes, cfg.llm_juiz, sug_j),
                                format_func=rotulos.get, label_visibility="collapsed",
                                help="Compara as duas respostas com as evidências e escreve a final. Use o modelo mais forte.")
            temp_j = st.slider("Temperatura do Juiz", 0.0, 1.0, float(cfg.temperatura_juiz), 0.1)

        st.markdown("#### 🗄️ Dados")
        lista_wh = list(whs)
        warehouse = st.selectbox("SQL Warehouse", lista_wh, index=indice(lista_wh, cfg.warehouse_id), format_func=whs.get,
                                 help="Roda as buscas, as estatísticas e os SELECTs dos pesquisadores.")
        cats = st.multiselect("Catálogos que o Sabichão pode consultar (vazio = todos que cada pessoa enxerga)",
                              opcoes_cat, default=cfg.catalogos_liberados)

        st.markdown("#### 📘 Tabela de documentação (opcional)")
        st.caption("Se a documentação das tabelas não está nos comentários do Unity Catalog, mas numa tabela de revisão "
                   "(1 linha por coluna, como a gerada pelo notebook `uc_auto_doc_revisao`), informe-a aqui. "
                   "O Sabichão passa a buscar e descrever as tabelas com ela, sempre dizendo o status da revisão.")
        tab_doc = st.text_input("Tabela de documentação (catalogo.schema.tabela)", cfg.tabela_documentacao,
                                placeholder="data_science_dev.churn_models.uc_doc_revisao")
        somente_rev = st.checkbox("Usar só a documentação **aprovada** ou **corrigida** pelo time responsável",
                                  cfg.doc_somente_revisada,
                                  help="Desmarcado: usa também a pendente, avisando que ainda não foi revisada. "
                                       "Linhas 'rejeitado' nunca são usadas; 'descricao_corrigida' vale mais que a gerada.")

        with st.expander("Avançado"):
            a1, a2, a3, a4 = st.columns(4)
            max_iter = a1.number_input("Passos por pesquisador", 1, 20, int(cfg.max_iteracoes),
                                       help="Quantas rodadas de ferramentas cada pesquisador pode usar.")
            max_lin = a2.number_input("Máx. linhas por SELECT", 1, 5000, int(cfg.max_linhas))
            lin_perf = a3.number_input("Linhas para estatísticas", 1000, 10_000_000, int(cfg.linhas_perfil), step=10_000,
                                       help="Acima disso, as estatísticas usam as primeiras N linhas (a contagem total é sempre exata).")
            janela = a4.number_input("Mensagens de histórico", 0, 40, int(cfg.janela_historico))
            extra = st.text_area("Instruções extras para os três agentes (opcional)", cfg.instrucoes_extra, height=100,
                                 placeholder="Ex.: as tabelas de produção ficam no catálogo 'prd'; ignore schemas que começam com 'tmp_'.")
        enviar = st.form_submit_button("💾 Salvar configuração", type="primary", disabled=not nomes)

    if enviar:
        novo = ConfigSabichao(
            llm_pesquisador_a=ia_a or "", llm_pesquisador_b=ia_b or "", llm_juiz=ia_j or "", warehouse_id=warehouse or "",
            catalogos_liberados=list(cats), tabela_documentacao=tab_doc, doc_somente_revisada=somente_rev,
            temperatura_a=temp_a, temperatura_b=temp_b, temperatura_juiz=temp_j,
            max_iteracoes=max_iter, max_linhas=max_lin, linhas_perfil=lin_perf, janela_historico=janela,
            instrucoes_extra=extra,
        )
        try:
            novo.validar()
        except ValueError as e:
            st.error(str(e))
        else:
            ss.cfg = novo
            servicos().cache = Cache()  # a documentação/escopo pode ter mudado: nada de resultado velho
            erros =servicos().preparar_projeto(ss.catalogo, ss.schema, novo.warehouse_id) if novo.warehouse_id else []
            try:
                servicos().gravar_config(ss.catalogo, ss.schema, novo)
                ss.cfg_salva = True
                st.success(f"Configuração salva em `{Servicos.caminho_config(ss.catalogo, ss.schema)}`.")
            except Exception as e:  # noqa: BLE001
                st.warning(f"Vale só nesta sessão: não consegui gravar no Volume ({mensagem_erro(e)}).")
                for erro in erros:
                    st.caption(erro)
            if len({novo.llm_pesquisador_a, novo.llm_pesquisador_b}) == 1:
                st.info("Os dois pesquisadores usam a mesma IA. Funciona (as temperaturas diferentes ajudam), "
                        "mas IAs diferentes dão ao juiz pontos de vista mais independentes.")

    if cfg.tabela_documentacao:
        st.divider()
        st.markdown(f"#### 📘 Verificar a tabela de documentação `{cfg.tabela_documentacao}`")
        if st.button("Verificar", disabled=not cfg.warehouse_id):
            with st.spinner("Lendo a tabela de documentação…"):
                try:
                    v = servicos().verificar_documentacao(cfg.tabela_documentacao, cfg.warehouse_id, cfg.doc_somente_revisada)
                except Exception as e:  # noqa: BLE001
                    v = {"problemas": [mensagem_erro(e)], "resumo": []}
            for p in v["problemas"]:
                st.error(p)
            if v["resumo"]:
                st.success("Formato ok. Situação da revisão:")
                st.dataframe(pd.DataFrame(v["resumo"]), hide_index=True)
                if v.get("opcionais_ausentes"):
                    st.caption("Colunas opcionais que a tabela não tem (tudo funciona sem elas): "
                               + ", ".join(v["opcionais_ausentes"]))

    st.divider()
    st.markdown("#### 🧪 Testar as IAs escolhidas")
    st.caption("Manda uma pergunta curta a cada IA e confere se ela responde e se sabe chamar ferramentas.")
    if st.button("Testar", disabled=bool(cfg.faltando())):
        linhas = []
        for papel, ep in (("Pesquisador A", cfg.llm_pesquisador_a), ("Pesquisador B", cfg.llm_pesquisador_b), ("Juiz", cfg.llm_juiz)):
            with st.spinner(f"Testando {ep}…"):
                try:
                    resultado = servicos().testar_ia(ep)
                except Exception as e:  # noqa: BLE001
                    resultado = f"erro: {mensagem_erro(e)}"
            if papel == "Juiz" and resultado != "ok" and not resultado.startswith("erro"):
                resultado = "ok (o juiz não precisa de ferramentas)"
            linhas.append({"papel": papel, "endpoint": ep, "resultado": ("✅ " if resultado.startswith("ok") else "⚠️ ") + resultado})
        st.dataframe(pd.DataFrame(linhas), hide_index=True, use_container_width=True)
