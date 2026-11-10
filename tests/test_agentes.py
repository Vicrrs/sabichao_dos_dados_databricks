import json

from fakes import CatalogoFalso, LLMFalso, chamar

from sabichao.agentes import Juiz, Pesquisador, Sabichao
from sabichao.ferramentas import Ferramentas
from sabichao.llm import interpretar_resposta


def pesquisador(nome, llm, max_iteracoes=5):
    return Pesquisador(nome, llm, Ferramentas(CatalogoFalso(), ["vendas"]), max_iteracoes)


def veredito(**kw):
    base = {"vencedor": "A", "justificativa": "A citou a tabela certa", "divergencias": [], "confianca": "alta",
            "resposta_final": "Sim: **vendas.comercial.clientes**."}
    return "```json\n" + json.dumps({**base, **kw}) + "\n```"


def test_pesquisador_usa_ferramentas_e_responde():
    llm = LLMFalso(chamar("buscar_tabelas", termo="cliente"), "Existe: vendas.comercial.clientes.\nConfiança: alta")
    eventos = []
    rel = pesquisador("Pesquisador A", llm).pesquisar("Existe tabela de clientes?", [], eventos.append)
    assert rel.resposta.startswith("Existe") and rel.erro is None
    assert [p.ferramenta for p in rel.passos] == ["buscar_tabelas"]
    assert rel.tokens_entrada == 20
    # a 2ª chamada à IA recebeu o pedido da ferramenta e o resultado dela
    segunda = llm.recebidas[1]
    assert segunda[-2]["tool_calls"][0]["function"]["name"] == "buscar_tabelas"
    assert segunda[-1]["role"] == "tool" and "vendas.comercial.clientes" in segunda[-1]["content"]
    assert [e["tipo"] for e in eventos] == ["inicio", "chamada", "passo", "fim"]


def test_pesquisador_no_limite_de_passos_responde_sem_ferramentas():
    llm = LLMFalso(chamar("listar_catalogos"), chamar("listar_catalogos"), "O que apurei até aqui")
    rel = pesquisador("Pesquisador A", llm, max_iteracoes=2).pesquisar("?", [])
    assert rel.resposta == "O que apurei até aqui"
    assert llm.ferramentas[-1] is None and "limite de passos" in llm.recebidas[-1][-1]["content"]


def test_pesquisador_registra_erro_da_ia():
    rel = pesquisador("Pesquisador B", LLMFalso(erro=RuntimeError("token=abcdef123456 expirou"))).pesquisar("?", [])
    assert rel.erro and "abcdef123456" not in rel.erro


def test_juiz_le_o_json_e_recebe_as_evidencias():
    a = pesquisador("Pesquisador A", LLMFalso(chamar("buscar_tabelas", termo="cliente"), "Tem clientes")).pesquisar("?", [])
    b = pesquisador("Pesquisador B", LLMFalso("Não existe")).pesquisar("?", [])
    llm_juiz = LLMFalso(veredito(divergencias=["B disse que não existe"]))
    v = Juiz(llm_juiz).julgar("Existe tabela de clientes?", [], [a, b])
    assert v.vencedor == "A" and v.resposta_final.startswith("Sim") and v.divergencias == ["B disse que não existe"]
    prompt = llm_juiz.recebidas[0][1]["content"]
    assert "buscar_tabelas" in prompt and "vendas.comercial.clientes" in prompt and "(nenhuma ferramenta usada)" in prompt


def test_juiz_sem_json_usa_o_texto():
    v = Juiz(LLMFalso("Resposta solta")).julgar("?", [], [])
    assert v.resposta_final == "Resposta solta" and "JSON" in v.justificativa


def test_sabichao_completo_com_historico():
    a = LLMFalso(chamar("descrever_tabela", tabela="vendas.comercial.pedidos"), "pedidos tem 4 colunas")
    b = LLMFalso(chamar("executar_sql", sql="SELECT count(*) AS n FROM vendas.comercial.pedidos"), "pedidos tem 3 linhas")
    j = LLMFalso(veredito(vencedor="empate", resposta_final="A tabela tem 4 colunas e 3 linhas."))
    sab = Sabichao(pesquisador("Pesquisador A", a), pesquisador("Pesquisador B", b), Juiz(j), janela_historico=2)
    historico = [{"role": "user", "content": "velha"}, {"role": "assistant", "content": "ok"},
                 {"role": "user", "content": "fale de pedidos"}, {"role": "assistant", "content": "claro"}]
    eventos = []
    r = sab.responder("e quantas linhas?", historico, eventos.append)
    assert r.veredito.resposta_final == "A tabela tem 4 colunas e 3 linhas."
    assert r.relatorios[1].passos[0].tabela["linhas"] == [[3]]
    assert r.tokens > 0 and json.dumps(r.to_dict(), default=str)
    # janela de 2 mensagens: "velha" não chega aos pesquisadores
    conteudos = [m["content"] for m in a.recebidas[0]]
    assert "velha" not in conteudos and "fale de pedidos" in conteudos
    assert {"juiz"} <= {e["agente"] for e in eventos}


def test_sabichao_quando_os_dois_falham_nao_chama_o_juiz():
    j = LLMFalso()
    sab = Sabichao(pesquisador("Pesquisador A", LLMFalso(erro=RuntimeError("fora"))),
                   pesquisador("Pesquisador B", LLMFalso(erro=RuntimeError("fora"))), Juiz(j))
    r = sab.responder("?")
    assert r.veredito.erro and "falharam" in r.veredito.resposta_final and not j.recebidas


def test_sabichao_quando_o_juiz_falha_usa_o_pesquisador_com_mais_evidencias():
    a = LLMFalso("chute sem olhar")
    b = LLMFalso(chamar("listar_catalogos"), "olhei o catálogo")
    sab = Sabichao(pesquisador("Pesquisador A", a), pesquisador("Pesquisador B", b), Juiz(LLMFalso(erro=RuntimeError("503"))))
    r = sab.responder("?")
    assert r.veredito.resposta_final == "olhei o catálogo" and r.veredito.vencedor == "B" and r.veredito.erro


def test_interpretar_resposta_do_endpoint():
    r = interpretar_resposta({
        "choices": [{"message": {"role": "assistant", "content": [{"type": "reasoning", "summary": []}, {"type": "text", "text": "oi"}],
                                 "tool_calls": [{"id": "x1", "type": "function",
                                                 "function": {"name": "listar_tabelas", "arguments": "{\"catalogo\": \"vendas\", \"schema\": \"comercial\"}"}}]}}],
        "usage": {"prompt_tokens": 7, "completion_tokens": 3},
    })
    assert r.conteudo == "oi" and r.chamadas[0].argumentos == {"catalogo": "vendas", "schema": "comercial"}
    assert (r.tokens_entrada, r.tokens_saida) == (7, 3)
    msg = r.mensagem_assistente()
    assert msg["tool_calls"][0]["function"]["arguments"].startswith("{\"catalogo\"")
    assert interpretar_resposta({"choices": [{"message": {"tool_calls": [{"function": {"name": "f", "arguments": "não é json"}}]}}]}).chamadas[0].argumentos == {}
