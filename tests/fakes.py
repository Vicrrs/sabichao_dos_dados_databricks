"""Dublês para testes: IA com respostas roteirizadas e catálogo em memória com SQL real (SQLite)."""
from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional

import sqlglot
from sqlglot import exp

from sabichao.dados import ResultadoSQL
from sabichao.llm import ChamadaFerramenta, RespostaLLM


class LLMFalso:
    """Devolve as respostas na ordem e guarda as mensagens que recebeu."""

    def __init__(self, *respostas, endpoint: str = "ia-falsa", erro: Exception | None = None):
        self.respostas = list(respostas)
        self.endpoint = endpoint
        self.erro = erro
        self.recebidas: List[List[Dict[str, Any]]] = []
        self.ferramentas: List[Optional[list]] = []

    def conversar(self, mensagens, ferramentas=None) -> RespostaLLM:
        self.recebidas.append([dict(m) for m in mensagens])
        self.ferramentas.append(ferramentas)
        if self.erro:
            raise self.erro
        r = self.respostas.pop(0)
        return r if isinstance(r, RespostaLLM) else RespostaLLM(conteudo=r, tokens_entrada=10, tokens_saida=5)


def chamar(nome: str, id_: str = "c1", **args) -> RespostaLLM:
    """Resposta da IA pedindo uma ferramenta."""
    return RespostaLLM(chamadas=[ChamadaFerramenta(id_, nome, args)], tokens_entrada=10, tokens_saida=5)


TABELAS = {
    "vendas.comercial.pedidos": {
        "comentario": "Pedidos de venda (1 linha por pedido)",
        "colunas": [("id", "int", "id do pedido"), ("cliente_id", "int", ""), ("valor", "double", "valor em R$"),
                    ("status", "string", "PAGO ou CANCELADO")],
        "linhas": [(1, 10, 100.0, "PAGO"), (2, 11, 50.0, "CANCELADO"), (3, 10, 80.0, "PAGO")],
    },
    "vendas.comercial.clientes": {
        "comentario": "Cadastro de clientes",
        "colunas": [("id", "int", ""), ("nome", "string", "nome do cliente"), ("uf", "string", "")],
        "linhas": [(10, "Ana", "SP"), (11, "Bruno", "RJ")],
    },
    "rh.pessoas.salarios": {
        "comentario": "Salários (restrito)",
        "colunas": [("pessoa_id", "int", ""), ("salario", "double", "")],
        "linhas": [(1, 9999.0)],
    },
    # Tabela SEM nenhum comentário no UC: só a tabela de documentação sabe o que ela é.
    "vendas.comercial.tb_xpto_01": {
        "comentario": "",
        "colunas": [("cd_cli", "int", ""), ("vl_lim", "double", ""), ("dt_ref", "string", "")],
        "linhas": [(10, 5000.0, "2026-10-01")],
    },
}

# Formato do notebook uc_auto_doc_revisao (1 linha por coluna + campos de revisão; colunas resumidas).
DOC = "governanca.doc.uc_doc_revisao"
_DOC_COLS = ["catalogo", "schema", "tabela", "caminho_tabela", "coluna", "descricao_catalogo", "descricao_schema",
             "descricao_tabela", "descricao_linha", "descricao_coluna", "status_revisao", "descricao_corrigida",
             "observacao_revisao", "revisado_por"]
_T = ("vendas", "comercial", "tb_xpto_01", "vendas.comercial.tb_xpto_01")
_DESC_T = ("Catálogo de vendas", "Área comercial", "Limite de crédito concedido por cliente", "um cliente por mês de referência")
DOC_LINHAS = [
    (*_T, "cd_cli", *_DESC_T, "Código do cliente", "aprovado", None, None, "time.credito"),
    (*_T, "vl_lim", *_DESC_T, "Valor do limite", "corrigido", "Limite de crédito em R$ concedido no mês", "era em R$", "time.credito"),
    (*_T, "dt_ref", *_DESC_T, "Data de referência", "pendente", None, None, None),
    ("vendas", "comercial", "pedidos", "vendas.comercial.pedidos", "status", *_DESC_T[:2], "Pedidos",
     "um pedido", "Situação do pedido", "rejeitado", None, "descrição errada", "time.vendas"),
]
TABELAS[DOC] = {
    "comentario": "Descrições geradas por LLM para revisão do time responsável",
    "colunas": [(c, "string", "") for c in _DOC_COLS],
    "linhas": DOC_LINHAS,
}


class CatalogoFalso:
    """Mesma interface do DatabricksCatalogo; SELECTs rodam de verdade no SQLite."""

    def __init__(self):
        self.con = sqlite3.connect(":memory:", check_same_thread=False)
        self.chamadas: List[tuple] = []
        self.executadas: List[str] = []
        for nome, t in TABELAS.items():
            cols = ", ".join(f"{c} {'TEXT' if tipo == 'string' else 'REAL' if tipo == 'double' else 'INT'}" for c, tipo, _ in t["colunas"])
            self.con.execute(f"CREATE TABLE {nome.replace('.', '__')} ({cols})")
            marcas = ", ".join("?" for _ in t["colunas"])
            self.con.executemany(f"INSERT INTO {nome.replace('.', '__')} VALUES ({marcas})", t["linhas"])

    def catalogos(self):
        self.chamadas.append(("catalogos",))
        return sorted({n.split(".")[0] for n in TABELAS})

    def schemas(self, catalogo):
        self.chamadas.append(("schemas", catalogo))
        return [{"schema": s, "comentario": ""} for s in sorted({n.split(".")[1] for n in TABELAS if n.startswith(catalogo + ".")})]

    def tabelas(self, catalogo, schema):
        self.chamadas.append(("tabelas", catalogo, schema))
        return [{"tabela": n, "tipo": "MANAGED", "comentario": t["comentario"]}
                for n, t in sorted(TABELAS.items()) if n.startswith(f"{catalogo}.{schema}.")]

    def buscar(self, termo, catalogos):
        self.chamadas.append(("buscar", termo, tuple(catalogos)))
        termo = termo.lower()
        achados = []
        for n, t in sorted(TABELAS.items()):
            textos = [n.split(".")[2], t["comentario"].lower()] + [c for c, _, _ in t["colunas"]] + [d.lower() for _, _, d in t["colunas"]]
            if any(termo in x for x in textos):
                cat, sch, tab = n.split(".")
                achados.append({"catalogo": cat, "schema": sch, "tabela": tab, "tipo": "MANAGED", "comentario": t["comentario"]})
        return achados

    def info_tabela(self, tabela):
        self.chamadas.append(("info", tabela))
        if tabela not in TABELAS:
            raise KeyError(f"TABLE_OR_VIEW_NOT_FOUND: {tabela}")
        t = TABELAS[tabela]
        return {"tabela": tabela, "tipo": "MANAGED", "formato": "DELTA", "comentario": t["comentario"], "dono": "dados@empresa",
                "criada_em": "2026-01-01 00:00 UTC", "atualizada_em": "2026-11-01 00:00 UTC",
                "colunas": [{"nome": c, "tipo": tipo, "comentario": d, "nulavel": True, "particao": False} for c, tipo, d in t["colunas"]]}

    def consultar(self, sql, parametros=None) -> ResultadoSQL:
        self.executadas.append(sql)
        try:
            arvore = sqlglot.parse_one(sql, read="databricks")
            for t in arvore.find_all(exp.Table):
                if t.catalog and t.db:
                    t.replace(exp.to_table(f"{t.catalog}__{t.db}__{t.name}"))
            cur = self.con.execute(arvore.sql(dialect="sqlite"), parametros or {})
            return ResultadoSQL(colunas=[d[0] for d in cur.description], linhas=[list(l) for l in cur.fetchall()])
        except Exception as e:  # noqa: BLE001
            return ResultadoSQL(erro=str(e))
