import pytest

from sabichao.catalogo import catalogo_visivel
from sabichao.sql_guard import SQLNaoPermitido, validar_sql


def so_vendas(cat):
    return catalogo_visivel(cat, ["vendas"])


def todos(cat):
    return catalogo_visivel(cat, [])


def test_select_no_catalogo_liberado_ganha_limit():
    sql = validar_sql("SELECT status, count(*) FROM vendas.comercial.pedidos GROUP BY status", so_vendas, 100)
    assert "LIMIT 100" in sql


def test_limit_maior_que_o_maximo_e_reduzido_e_menor_e_mantido():
    assert "LIMIT 100" in validar_sql("SELECT * FROM vendas.comercial.pedidos LIMIT 999999", so_vendas, 100)
    assert "LIMIT 5" in validar_sql("SELECT * FROM vendas.comercial.pedidos LIMIT 5", so_vendas, 100)
    assert "LIMIT 100" in validar_sql("SELECT * FROM vendas.comercial.pedidos LIMIT 10 * 1000", so_vendas, 100)


@pytest.mark.parametrize("sql", [
    "SELECT * FROM rh.pessoas.salarios",
    "SELECT * FROM vendas.comercial.pedidos UNION ALL SELECT * FROM rh.pessoas.salarios",
    "SELECT * FROM vendas.comercial.pedidos WHERE id IN (SELECT pessoa_id FROM rh.pessoas.salarios)",
])
def test_bloqueia_catalogo_nao_liberado(sql):
    with pytest.raises(SQLNaoPermitido, match="não está liberado"):
        validar_sql(sql, so_vendas, 100)


def test_system_nunca_e_liberado_mesmo_sem_lista():
    with pytest.raises(SQLNaoPermitido):
        validar_sql("SELECT * FROM system.access.audit", todos, 100)
    assert validar_sql("SELECT * FROM rh.pessoas.salarios", todos, 10)


@pytest.mark.parametrize("sql", [
    "DROP TABLE vendas.comercial.pedidos",
    "DELETE FROM vendas.comercial.pedidos",
    "INSERT INTO vendas.comercial.pedidos VALUES (1)",
    "UPDATE vendas.comercial.pedidos SET valor = 0",
    "SELECT 1 FROM vendas.comercial.pedidos; DROP TABLE vendas.comercial.pedidos",
    "SELECT http_request('https://x', 'GET') FROM vendas.comercial.pedidos",
    "SELECT ai_query('m', status) FROM vendas.comercial.pedidos",
    "SELECT secret('escopo', 'chave') FROM vendas.comercial.pedidos",
    "SELECT * FROM read_files('/Volumes/x')",
    "SELECT * FROM vendas.comercial.pedidos VERSION AS OF 1",
    "SELECT * FROM pedidos",
    "",
])
def test_bloqueia_escrita_funcoes_perigosas_e_nomes_incompletos(sql):
    with pytest.raises(SQLNaoPermitido):
        validar_sql(sql, so_vendas, 100)


def test_cte_e_permitida():
    sql = "WITH pagos AS (SELECT * FROM vendas.comercial.pedidos WHERE status = 'PAGO') SELECT count(*) FROM pagos"
    assert "LIMIT" in validar_sql(sql, so_vendas, 50)
