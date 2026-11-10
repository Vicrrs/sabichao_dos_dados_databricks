from fakes import DOC, CatalogoFalso, LLMFalso, chamar

from sabichao.agentes import Pesquisador
from sabichao.config import ConfigSabichao
from sabichao.dados import ResultadoSQL
from sabichao.documentacao import Documentacao, FonteDocumentada
from sabichao.ferramentas import Ferramentas
from sabichao.perfil import perfil_texto, perfilar_tabela

XPTO = "vendas.comercial.tb_xpto_01"


def fonte(somente_revisada=False):
    base = CatalogoFalso()
    return FonteDocumentada(base, Documentacao(base, DOC, somente_revisada))


def ferramentas(**kw):
    return Ferramentas(fonte(**kw), ["vendas"])


def test_formato_e_resumo_da_revisao():
    doc = Documentacao(CatalogoFalso(), DOC)
    assert doc.problemas() == []
    resumo = {r["status"]: r["colunas"] for r in doc.resumo()}
    assert resumo == {"aprovado": 1, "corrigido": 1, "pendente": 1, "rejeitado": 1}


def test_tabela_sem_as_colunas_obrigatorias_e_recusada():
    problemas = Documentacao(CatalogoFalso(), "vendas.comercial.pedidos").problemas()
    assert problemas and "caminho_tabela" in problemas[0]


def test_descrever_tabela_sem_comentario_usa_a_documentacao_com_status():
    p = ferramentas().executar("descrever_tabela", {"tabela": XPTO})
    assert "Limite de crédito concedido por cliente" in p.texto
    assert "doc parcialmente revisada" in p.texto and "um cliente por mês" in p.texto
    linhas = {l[0]: l for l in p.tabela["linhas"]}
    # descricao_corrigida vale mais que a gerada
    assert linhas["vl_lim"][3] == "Limite de crédito em R$ concedido no mês" and linhas["vl_lim"][4] == "corrigido"
    assert linhas["dt_ref"][4] == "pendente" and linhas["cd_cli"][2] == ""


def test_rejeitado_nunca_e_usado():
    info = fonte().info_tabela("vendas.comercial.pedidos")
    status = next(c for c in info["colunas"] if c["nome"] == "status")
    assert status["comentario"] == "PAGO ou CANCELADO" and "documentacao" not in status and "documentacao" not in info


def test_somente_revisada_ignora_o_pendente():
    info = fonte(somente_revisada=True).info_tabela(XPTO)
    cols = {c["nome"]: c for c in info["colunas"]}
    assert "documentacao" not in cols["dt_ref"] and cols["cd_cli"]["status_doc"] == "aprovado"
    assert info["documentacao"]["status"] == "corrigido"


def test_busca_acha_pelo_texto_da_documentacao():
    p = ferramentas().executar("buscar_tabelas", {"termo": "crédito"})
    linhas = {l[0]: l for l in p.tabela["linhas"]}
    assert XPTO in linhas and linhas[XPTO][3] == "vl_lim" and "Limite de crédito" in p.texto
    # o catálogo da própria tabela de documentação não está liberado: ela não aparece
    assert DOC not in linhas


def test_listar_tabelas_preenche_o_comentario_vazio():
    p = ferramentas().executar("listar_tabelas", {"catalogo": "vendas", "schema": "comercial"})
    linhas = {l[0]: l for l in p.tabela["linhas"]}
    assert linhas[XPTO][2] == "Limite de crédito concedido por cliente [doc parcialmente revisada]"
    assert linhas["vendas.comercial.pedidos"][2] == "Pedidos de venda (1 linha por pedido)"  # comentário do UC continua


def test_listar_schemas_usa_a_descricao_do_schema():
    p = ferramentas().executar("listar_schemas", {"catalogo": "vendas"})
    assert "Área comercial" in p.texto


def test_estatisticas_tambem_enxergam_a_documentacao():
    f = fonte()

    class SemSQL:
        """O perfil só precisa dos metadados aqui; as consultas de estatística voltam vazias."""
        def __getattr__(self, nome):
            return getattr(f, nome)

        def consultar(self, sql, parametros=None):
            if "uc_doc_revisao" in sql:
                return f.consultar(sql, parametros)
            return ResultadoSQL(erro="sem warehouse no teste")

    texto = perfil_texto(perfilar_tabela(SemSQL(), XPTO))
    assert "Limite de crédito em R$ concedido no mês [doc corrigida pelo time]" in texto


def test_tabela_de_documentacao_inacessivel_nao_quebra_o_sabichao():
    base = CatalogoFalso()
    f = Ferramentas(FonteDocumentada(base, Documentacao(base, "governanca.doc.nao_existe")), ["vendas"])
    p = f.executar("descrever_tabela", {"tabela": "vendas.comercial.pedidos"})
    assert p.erro is None and "valor em R$" in p.texto and "Não consegui ler a tabela de documentação" in p.texto
    assert f.executar("buscar_tabelas", {"termo": "cliente"}).erro is None


def test_pesquisador_recebe_a_documentacao_e_o_aviso_no_prompt():
    llm = LLMFalso(chamar("descrever_tabela", tabela=XPTO), "É o limite de crédito (doc ainda não toda revisada)")
    rel = Pesquisador("Pesquisador A", llm, ferramentas()).pesquisar("o que é tb_xpto_01?", [])
    assert "doc NÃO revisada" in llm.recebidas[0][0]["content"]
    assert "Limite de crédito" in llm.recebidas[1][-1]["content"] and rel.resposta


def test_config_valida_o_nome_da_tabela_de_documentacao():
    assert ConfigSabichao(tabela_documentacao=" Gov.Doc.Revisao ").validar().tabela_documentacao == "gov.doc.revisao"
    try:
        ConfigSabichao(tabela_documentacao="gov.doc").validar()
    except ValueError:
        pass
    else:
        raise AssertionError("nome com 2 partes deveria falhar")
