"""Documentação que mora numa TABELA (e não nos comentários do Unity Catalog).

Formato esperado (o que o notebook `uc_auto_doc_revisao` grava): 1 linha por coluna documentada,
com `caminho_tabela`, `coluna`, `descricao_tabela`, `descricao_coluna` e, se existirem,
`descricao_linha`, `descricao_schema`, `descricao_catalogo`, `catalogo`, `schema` e os campos de
revisão do time responsável (`status_revisao`, `descricao_corrigida`, `observacao_revisao`,
`revisado_por`, `revisado_em`).

Regras:
  - `rejeitado` nunca é usado; com `somente_revisada`, só `aprovado` e `corrigido`;
  - `descricao_corrigida` (quando preenchida) vale mais que a descrição gerada;
  - todo texto vindo daqui é marcado com o status, para a IA avisar quando não foi revisado.

`FonteDocumentada` envolve a fonte do catálogo e completa os metadados com essa documentação, então
buscar, listar, descrever e as estatísticas passam a enxergá-la sem mudar mais nada.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .catalogo import FonteCatalogo
from .dados import ResultadoSQL
from .seguranca import citar, nome_seguro

OBRIGATORIAS = ("caminho_tabela", "coluna", "descricao_tabela", "descricao_coluna")
OPCIONAIS = ("descricao_linha", "descricao_schema", "descricao_catalogo", "catalogo", "schema",
             "status_revisao", "descricao_corrigida", "observacao_revisao", "revisado_por", "revisado_em")
REVISADOS = ("aprovado", "corrigido")
ROTULO_STATUS = {"aprovado": "doc aprovada pelo time", "corrigido": "doc corrigida pelo time",
                 "pendente": "doc NÃO revisada", "parcial": "doc parcialmente revisada"}
MAX_BUSCA = 50


def rotulo(status: Optional[str]) -> str:
    return ROTULO_STATUS.get((status or "pendente").lower(), f"doc {status}")


@dataclass
class DocColuna:
    descricao: str
    status: str = "pendente"
    observacao: str = ""
    revisado_por: str = ""


@dataclass
class DocTabela:
    tabela: str
    descricao: str = ""
    linha: str = ""
    descricao_schema: str = ""
    descricao_catalogo: str = ""
    colunas: Dict[str, DocColuna] = field(default_factory=dict)

    @property
    def status(self) -> str:
        """Status da tabela como um todo: aprovado/corrigido só se todas as colunas foram revisadas."""
        todos = {c.status for c in self.colunas.values()} or {"pendente"}
        if todos <= set(REVISADOS):
            return "corrigido" if "corrigido" in todos else "aprovado"
        return "parcial" if todos & set(REVISADOS) else "pendente"

    def to_dict(self) -> Dict[str, Any]:
        return {"descricao": self.descricao, "linha": self.linha, "status": self.status,
                "descricao_schema": self.descricao_schema, "descricao_catalogo": self.descricao_catalogo}


def _q(nome: str) -> str:
    return "`" + nome.replace("`", "``") + "`"


def _txt(v: Any) -> str:
    return "" if v is None else str(v).strip()


class Documentacao:
    def __init__(self, fonte: FonteCatalogo, tabela: str, somente_revisada: bool = False):
        self.fonte = fonte
        self.tabela = nome_seguro(tabela, 3)
        self.ref = citar(self.tabela)
        self.somente_revisada = somente_revisada
        self._colunas: Optional[set] = None
        self._trava = threading.Lock()

    # ------------------------------------------------------------ estrutura
    def colunas(self) -> set:
        with self._trava:
            if self._colunas is None:
                info = self.fonte.info_tabela(self.tabela)
                self._colunas = {c["nome"].lower() for c in info.get("colunas", [])}
            return self._colunas

    def problemas(self) -> List[str]:
        try:
            faltando = [c for c in OBRIGATORIAS if c not in self.colunas()]
        except Exception as e:  # noqa: BLE001
            return [f"não consegui ler {self.tabela}: {e}"]
        return [f"faltam as colunas: {', '.join(faltando)}"] if faltando else []

    def _c(self, nome: str) -> str:
        """A coluna, ou NULL se a tabela não a tiver (as opcionais podem faltar)."""
        return _q(nome) if nome in self.colunas() else "CAST(NULL AS STRING)"

    def _descricao_coluna(self) -> str:
        return f"coalesce(nullif(trim({self._c('descricao_corrigida')}), ''), {_q('descricao_coluna')})"

    def _filtro_status(self) -> str:
        if "status_revisao" not in self.colunas():
            return "AND 1 = 0" if self.somente_revisada else ""
        st = f"coalesce(lower({_q('status_revisao')}), 'pendente')"
        if self.somente_revisada:
            return f"AND {st} IN ('aprovado', 'corrigido')"
        return f"AND {st} <> 'rejeitado'"

    def _consultar(self, sql: str, parametros: Dict[str, str]) -> ResultadoSQL:
        r = self.fonte.consultar(sql, parametros)
        if r.erro:
            raise RuntimeError(f"tabela de documentação {self.tabela}: {r.erro}")
        return r

    # -------------------------------------------------------------- leitura
    def da_tabela(self, tabela: str) -> Optional[DocTabela]:
        sql = f"""
            SELECT {_q('coluna')} AS coluna, {self._descricao_coluna()} AS descricao,
                   coalesce(lower({self._c('status_revisao')}), 'pendente') AS status,
                   {self._c('observacao_revisao')} AS observacao, {self._c('revisado_por')} AS revisado_por,
                   {_q('descricao_tabela')} AS descricao_tabela, {self._c('descricao_linha')} AS descricao_linha,
                   {self._c('descricao_schema')} AS descricao_schema, {self._c('descricao_catalogo')} AS descricao_catalogo
            FROM {self.ref}
            WHERE lower({_q('caminho_tabela')}) = :tabela {self._filtro_status()}
        """  # nosec B608 - nomes fixos/validados; o valor vai como parâmetro
        r = self._consultar(sql, {"tabela": tabela.lower()})
        if not r.linhas:
            return None
        doc = DocTabela(tabela.lower())
        for linha in r.linhas:
            v = dict(zip(r.colunas, linha))
            doc.descricao = doc.descricao or _txt(v["descricao_tabela"])
            doc.linha = doc.linha or _txt(v["descricao_linha"])
            doc.descricao_schema = doc.descricao_schema or _txt(v["descricao_schema"])
            doc.descricao_catalogo = doc.descricao_catalogo or _txt(v["descricao_catalogo"])
            if _txt(v["coluna"]) and _txt(v["descricao"]):
                doc.colunas[_txt(v["coluna"]).lower()] = DocColuna(
                    _txt(v["descricao"]), _txt(v["status"]) or "pendente", _txt(v["observacao"]), _txt(v["revisado_por"]))
        return doc

    def das_tabelas(self, catalogo: str, schema: str) -> Dict[str, Dict[str, str]]:
        """{tabela: {descricao, status}} de um schema inteiro (uma consulta só)."""
        sql = f"""
            SELECT lower({_q('caminho_tabela')}) AS tabela, max({_q('descricao_tabela')}) AS descricao,
                   min(CASE WHEN coalesce(lower({self._c('status_revisao')}), 'pendente') IN ('aprovado', 'corrigido')
                            THEN 1 ELSE 0 END) AS revisada,
                   max(CASE WHEN coalesce(lower({self._c('status_revisao')}), 'pendente') IN ('aprovado', 'corrigido')
                            THEN 1 ELSE 0 END) AS alguma_revisada
            FROM {self.ref}
            WHERE lower({_q('caminho_tabela')}) LIKE :prefixo {self._filtro_status()}
            GROUP BY 1
        """  # nosec B608
        r = self._consultar(sql, {"prefixo": f"{catalogo.lower()}.{schema.lower()}.%"})
        saida = {}
        for tab, desc, revisada, alguma in r.linhas:
            status = "aprovado" if int(revisada or 0) else ("parcial" if int(alguma or 0) else "pendente")
            saida[tab] = {"descricao": _txt(desc), "status": status}
        return saida

    def dos_schemas(self, catalogo: str) -> Dict[str, str]:
        if not {"catalogo", "schema", "descricao_schema"} <= self.colunas():
            return {}
        sql = f"""
            SELECT lower({_q('schema')}) AS s, max({_q('descricao_schema')}) AS d
            FROM {self.ref} WHERE lower({_q('catalogo')}) = :catalogo {self._filtro_status()} GROUP BY 1
        """  # nosec B608
        r = self._consultar(sql, {"catalogo": catalogo.lower()})
        return {s: _txt(d) for s, d in r.linhas if _txt(d)}

    def buscar(self, termo: str) -> List[Dict[str, Any]]:
        """Tabelas cujo nome, descrição, colunas ou descrição das colunas citam o termo."""
        desc_col = self._descricao_coluna()
        cita_coluna = f"(lower({_q('coluna')}) LIKE :termo OR lower(coalesce({desc_col}, '')) LIKE :termo)"
        sql = f"""
            SELECT lower({_q('caminho_tabela')}) AS tabela, max({_q('descricao_tabela')}) AS descricao,
                   max(CASE WHEN {cita_coluna} THEN {_q('coluna')} END) AS coluna_encontrada
            FROM {self.ref}
            WHERE (lower({_q('caminho_tabela')}) LIKE :termo OR lower(coalesce({_q('descricao_tabela')}, '')) LIKE :termo
                   OR {cita_coluna}) {self._filtro_status()}
            GROUP BY 1 ORDER BY 1 LIMIT {MAX_BUSCA}
        """  # nosec B608
        r = self._consultar(sql, {"termo": f"%{termo.strip().lower()}%"})
        achados = []
        for tab, desc, coluna in r.linhas:
            partes = str(tab).split(".")
            if len(partes) != 3:
                continue
            achados.append({"catalogo": partes[0], "schema": partes[1], "tabela": partes[2], "tipo": "",
                            "comentario": _txt(desc), "coluna_encontrada": _txt(coluna), "origem": "documentação"})
        return achados

    def resumo(self) -> List[Dict[str, Any]]:
        """Quantas tabelas/colunas há em cada status de revisão (para a tela de configuração)."""
        sql = f"""
            SELECT coalesce(lower({self._c('status_revisao')}), 'pendente') AS status,
                   count(DISTINCT {_q('caminho_tabela')}) AS tabelas, count(*) AS colunas
            FROM {self.ref} GROUP BY 1 ORDER BY 1
        """  # nosec B608
        r = self._consultar(sql, {})
        return [dict(zip(r.colunas, linha)) for linha in r.linhas]


def _com_marca(texto: str, status: str) -> str:
    return f"{texto} [{rotulo(status)}]" if texto else ""


class FonteDocumentada:
    """A fonte do catálogo + a tabela de documentação. Comentário do UC, quando existe, continua valendo;
    a documentação entra onde ele falta e também aparece à parte, com o status da revisão."""

    def __init__(self, fonte: FonteCatalogo, doc: Documentacao):
        self.fonte, self.doc = fonte, doc

    def catalogos(self) -> List[str]:
        return self.fonte.catalogos()

    def consultar(self, sql: str, parametros: Optional[Dict[str, str]] = None) -> ResultadoSQL:
        return self.fonte.consultar(sql, parametros)

    def schemas(self, catalogo: str) -> List[Dict[str, Any]]:
        itens = self.fonte.schemas(catalogo)
        try:
            docs = self.doc.dos_schemas(catalogo)
        except Exception:  # noqa: BLE001 - sem a documentação, fica o que o UC tem
            return itens
        for s in itens:
            if not s.get("comentario") and docs.get(s["schema"].lower()):
                s["comentario"] = _com_marca(docs[s["schema"].lower()], "pendente")
        return itens

    def tabelas(self, catalogo: str, schema: str) -> List[Dict[str, Any]]:
        itens = self.fonte.tabelas(catalogo, schema)
        try:
            docs = self.doc.das_tabelas(catalogo, schema)
        except Exception:  # noqa: BLE001
            return itens
        for t in itens:
            d = docs.get(t["tabela"].lower())
            if d and not t.get("comentario"):
                t["comentario"] = _com_marca(d["descricao"], d["status"])
        return itens

    def buscar(self, termo: str, catalogos: List[str]) -> List[Dict[str, Any]]:
        achados = {f"{t['catalogo']}.{t['schema']}.{t['tabela']}".lower(): t for t in self.fonte.buscar(termo, catalogos)}
        try:
            da_doc = self.doc.buscar(termo)
        except Exception:  # noqa: BLE001
            da_doc = []
        for t in da_doc:
            if catalogos and t["catalogo"] not in {c.lower() for c in catalogos}:
                continue
            nome = f"{t['catalogo']}.{t['schema']}.{t['tabela']}"
            if nome in achados:
                atual = achados[nome]
                atual["comentario"] = atual.get("comentario") or t["comentario"]
                atual["coluna_encontrada"] = t["coluna_encontrada"]
            else:
                achados[nome] = t
        return [achados[k] for k in sorted(achados)]

    def info_tabela(self, tabela: str) -> Dict[str, Any]:
        info = self.fonte.info_tabela(tabela)
        try:
            doc = self.doc.da_tabela(tabela)
        except Exception as e:  # noqa: BLE001
            info["documentacao_erro"] = str(e)[:300]
            return info
        if doc is None:
            return info
        info["documentacao"] = doc.to_dict()
        if not info.get("comentario"):
            info["comentario"] = _com_marca(doc.descricao, doc.status)
        for c in info.get("colunas", []):
            c["comentario_uc"] = c.get("comentario", "")
            d = doc.colunas.get(c["nome"].lower())
            if not d:
                continue
            c["documentacao"], c["status_doc"] = d.descricao, d.status
            if d.observacao:
                c["observacao_doc"] = d.observacao
            if not c.get("comentario"):
                c["comentario"] = _com_marca(d.descricao, d.status)
        return info
