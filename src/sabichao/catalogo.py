"""O que o Sabichão sabe ler: metadados do Unity Catalog e consultas no SQL Warehouse."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Protocol

from .dados import ResultadoSQL, executar_sql_warehouse
from .descoberta import CATALOGOS_OCULTOS, listar_catalogos
from .seguranca import citar, nome_seguro


class FonteCatalogo(Protocol):
    def catalogos(self) -> List[str]: ...
    def schemas(self, catalogo: str) -> List[Dict[str, Any]]: ...
    def tabelas(self, catalogo: str, schema: str) -> List[Dict[str, Any]]: ...
    def buscar(self, termo: str, catalogos: List[str]) -> List[Dict[str, Any]]: ...
    def info_tabela(self, tabela: str) -> Dict[str, Any]: ...
    def consultar(self, sql: str, parametros: Optional[Dict[str, str]] = None) -> ResultadoSQL: ...


def _data(ms: Optional[int]) -> Optional[str]:
    if not ms:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _valor(enum_ou_str) -> str:
    return str(getattr(enum_ou_str, "value", enum_ou_str) or "")


# Procura no nome e no comentário da tabela e das colunas. `{fonte}` é `system.information_schema`
# ou `<catalogo>.information_schema`; o termo e os catálogos entram como parâmetros.
SQL_BUSCA = """
SELECT t.table_catalog AS catalogo, t.table_schema AS schema, t.table_name AS tabela,
       t.table_type AS tipo, t.comment AS comentario
FROM {fonte}.tables t
WHERE t.table_schema <> 'information_schema' {filtro_catalogos}
  AND (lower(t.table_name) LIKE :termo OR lower(coalesce(t.comment, '')) LIKE :termo
       OR EXISTS (SELECT 1 FROM {fonte}.columns c
                  WHERE c.table_catalog = t.table_catalog AND c.table_schema = t.table_schema
                    AND c.table_name = t.table_name
                    AND (lower(c.column_name) LIKE :termo OR lower(coalesce(c.comment, '')) LIKE :termo)))
ORDER BY 1, 2, 3
LIMIT {limite}
"""


class DatabricksCatalogo:
    """Unity Catalog (SDK) + SQL Warehouse, com a identidade do cliente `w`."""

    def __init__(self, w, warehouse_id: str, timeout_segundos: int = 120, max_resultados_busca: int = 50):
        self.w = w
        self.warehouse_id = warehouse_id
        self.timeout = timeout_segundos
        self.max_busca = max_resultados_busca

    def catalogos(self) -> List[str]:
        return listar_catalogos(self.w)

    def schemas(self, catalogo: str) -> List[Dict[str, Any]]:
        return sorted(
            ({"schema": s.name, "comentario": s.comment or ""}
             for s in self.w.schemas.list(catalog_name=catalogo) if s.name != "information_schema"),
            key=lambda x: x["schema"],
        )

    def tabelas(self, catalogo: str, schema: str) -> List[Dict[str, Any]]:
        lista = self.w.tables.list(catalog_name=catalogo, schema_name=schema, omit_columns=True, omit_properties=True)
        return sorted(
            ({"tabela": t.full_name, "tipo": _valor(t.table_type), "comentario": t.comment or ""} for t in lista),
            key=lambda x: x["tabela"],
        )

    def buscar(self, termo: str, catalogos: List[str]) -> List[Dict[str, Any]]:
        params = {"termo": f"%{termo.strip().lower()}%"}
        filtro = ""
        if catalogos:
            params.update({f"c{i}": c for i, c in enumerate(catalogos)})
            filtro = "AND t.table_catalog IN (" + ", ".join(f":c{i}" for i in range(len(catalogos))) + ")"
        else:
            filtro = "AND t.table_catalog NOT IN ('system', '__databricks_internal')"
        r = self.consultar(SQL_BUSCA.format(fonte="system.information_schema", filtro_catalogos=filtro, limite=self.max_busca), params)
        if not r.erro:
            return [dict(zip(r.colunas, linha)) for linha in r.linhas]
        # Sem acesso ao system.information_schema: procura catálogo por catálogo.
        achados: List[Dict[str, Any]] = []
        for cat in catalogos or self.catalogos():
            fonte = citar(cat, 1) + ".information_schema"
            r2 = self.consultar(SQL_BUSCA.format(fonte=fonte, filtro_catalogos="", limite=self.max_busca), {"termo": params["termo"]})
            if r2.erro:
                continue
            achados += [dict(zip(r2.colunas, linha)) for linha in r2.linhas]
            if len(achados) >= self.max_busca:
                break
        return achados[: self.max_busca]

    def info_tabela(self, tabela: str) -> Dict[str, Any]:
        t = self.w.tables.get(nome_seguro(tabela, 3))
        return {
            "tabela": t.full_name,
            "tipo": _valor(t.table_type),
            "formato": _valor(t.data_source_format),
            "comentario": t.comment or "",
            "dono": t.owner,
            "criada_em": _data(t.created_at),
            "atualizada_em": _data(t.updated_at),
            "colunas": [
                {
                    "nome": c.name,
                    "tipo": (c.type_text or _valor(c.type_name)).lower(),
                    "comentario": c.comment or "",
                    "nulavel": c.nullable,
                    "particao": c.partition_index is not None,
                }
                for c in t.columns or []
            ],
        }

    def consultar(self, sql: str, parametros: Optional[Dict[str, str]] = None) -> ResultadoSQL:
        return executar_sql_warehouse(self.w, self.warehouse_id, sql, 1000, self.timeout, parametros)


def catalogo_visivel(nome: str, liberados: List[str]) -> bool:
    nome = (nome or "").lower()
    if nome in CATALOGOS_OCULTOS:
        return False
    return not liberados or nome in {c.lower() for c in liberados}
