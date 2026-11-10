from fakes import CatalogoFalso

from sabichao.dados import ResultadoSQL
from sabichao.ferramentas import Cache, Ferramentas
from sabichao.perfil import perfil_texto, perfilar_tabela


def ferramentas(liberados=("vendas",), **kw):
    return Ferramentas(CatalogoFalso(), list(liberados), **kw)


def test_listar_catalogos_mostra_so_os_liberados():
    p = ferramentas().executar("listar_catalogos", {})
    assert "vendas" in p.texto and "rh" not in p.texto


def test_sem_lista_todos_os_catalogos_aparecem():
    assert "rh" in ferramentas(liberados=()).executar("listar_catalogos", {}).texto


def test_listar_tabelas_e_descrever():
    f = ferramentas()
    p = f.executar("listar_tabelas", {"catalogo": "vendas", "schema": "comercial"})
    assert "vendas.comercial.pedidos" in p.texto and len(p.tabela["linhas"]) == 3
    d = f.executar("descrever_tabela", {"tabela": "vendas.comercial.pedidos"})
    assert "valor em R$" in d.texto and d.erro is None


def test_catalogo_nao_liberado_vira_erro_para_a_ia():
    f = ferramentas()
    for nome, args in (("descrever_tabela", {"tabela": "rh.pessoas.salarios"}),
                       ("estatisticas_tabela", {"tabela": "rh.pessoas.salarios"}),
                       ("listar_schemas", {"catalogo": "rh"})):
        p = f.executar(nome, args)
        assert p.erro and "não está liberado" in p.texto
    assert not any(c[0] == "info" for c in f.fonte.chamadas)


def test_nome_com_injecao_e_recusado():
    p = ferramentas().executar("descrever_tabela", {"tabela": "vendas.comercial.pedidos`; DROP TABLE x; --"})
    assert p.erro and "nome inválido" in p.texto


def test_buscar_tabelas_por_coluna_e_filtra_catalogos():
    f = ferramentas()
    p = f.executar("buscar_tabelas", {"termo": "cliente"})
    assert "vendas.comercial.clientes" in p.texto and "vendas.comercial.pedidos" in p.texto  # coluna cliente_id
    assert "Nenhuma" in f.executar("buscar_tabelas", {"termo": "salario"}).texto


def test_executar_sql_roda_e_bloqueia():
    f = ferramentas()
    p = f.executar("executar_sql", {"sql": "SELECT status, count(*) AS n FROM vendas.comercial.pedidos GROUP BY status ORDER BY status"})
    assert p.erro is None and p.tabela["linhas"] == [["CANCELADO", 1], ["PAGO", 2]]
    assert "LIMIT" in p.sql
    b = f.executar("executar_sql", {"sql": "SELECT * FROM rh.pessoas.salarios"})
    assert b.erro and "bloqueado" in b.texto
    assert not any("salarios" in s for s in f.fonte.executadas)


def test_ferramenta_inexistente_e_argumentos_errados():
    f = ferramentas()
    assert f.executar("apagar_tudo", {}).erro
    assert f.executar("descrever_tabela", {"nome": "x"}).erro


def test_cache_e_compartilhado_entre_pesquisadores():
    fonte, cache = CatalogoFalso(), Cache()
    a, b = Ferramentas(fonte, ["vendas"], cache=cache), Ferramentas(fonte, ["vendas"], cache=cache)
    a.executar("descrever_tabela", {"tabela": "vendas.comercial.pedidos"})
    b.executar("descrever_tabela", {"tabela": "VENDAS.comercial.pedidos"})
    assert [c for c in fonte.chamadas if c[0] == "info"] == [("info", "vendas.comercial.pedidos")]


class FonteRoteirizada(CatalogoFalso):
    """Responde as consultas do perfil (funções do Databricks que o SQLite não tem)."""

    def consultar(self, sql, parametros=None):
        self.executadas.append(sql)
        if sql.startswith("SELECT count(*) AS n"):
            return ResultadoSQL(["n"], [["250000"]])
        if sql.startswith("DESCRIBE DETAIL"):
            return ResultadoSQL(["sizeInBytes", "numFiles"], [["1048576", "4"]])
        if sql.startswith("SELECT count(*) AS total"):
            # colunas simples: id, cliente_id, valor, status
            nomes = ["total"]
            valores = ["100000"]
            for i, (mn, mx) in enumerate([("1", "3"), ("10", "11"), ("50.0", "100.0"), ("CANCELADO", "PAGO")]):
                nomes += [f"nn_{i}", f"d_{i}", f"mn_{i}", f"mx_{i}"]
                valores += ["90000" if i == 2 else "100000", "3", mn, mx]
                if i < 3:
                    nomes += [f"av_{i}", f"dp_{i}", f"p25_{i}", f"p50_{i}", f"p75_{i}"]
                    valores += ["76.6667", "20.8", "50", "80", "100"]
            return ResultadoSQL(nomes, [valores])
        if "GROUP BY" in sql:
            return ResultadoSQL(["valor", "n"], [["PAGO", "2"], ["CANCELADO", "1"]])
        return ResultadoSQL(["id"], [["1"]])


def test_perfil_com_estatisticas_e_amostragem():
    fonte = FonteRoteirizada()
    p = perfilar_tabela(fonte, "vendas.comercial.pedidos", linhas_perfil=100_000)
    assert p.total_linhas == 250000 and p.tamanho_bytes == 1048576 and p.linhas_analisadas == 100000
    valor = next(c for c in p.colunas if c.nome == "valor")
    assert valor.nulos_pct == 10.0 and valor.mediana == 80 and valor.media == 76.6667
    status = next(c for c in p.colunas if c.nome == "status")
    assert status.top_valores == [["PAGO", 2], ["CANCELADO", 1]] and status.media is None
    assert any("LIMIT 100000" in s for s in fonte.executadas)  # estatísticas sobre amostra
    texto = perfil_texto(p)
    assert "mediana=80" in texto and "primeiras 100000 linhas" in texto
    assert p.tabela_colunas()["colunas"][0] == "coluna"
