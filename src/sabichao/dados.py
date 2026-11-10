"""SQL no SQL Warehouse (Statement Execution API)."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .seguranca import ocultar_segredos


@dataclass
class ResultadoSQL:
    colunas: List[str] = field(default_factory=list)
    linhas: List[List[Any]] = field(default_factory=list)
    truncado: bool = False
    duracao_ms: int = 0
    erro: Optional[str] = None


def executar_sql_warehouse(
    w,
    warehouse_id: str,
    sql: str,
    max_linhas: int = 1000,
    timeout_segundos: int = 120,
    parametros: Optional[Dict[str, str]] = None,
) -> ResultadoSQL:
    """Executa SQL num SQL Warehouse. Valores voltam como texto.

    `parametros` vira marcadores nomeados (`:nome`): é assim que texto vindo da IA ou do usuário
    entra num SQL sem risco de injeção.
    """
    from databricks.sdk.service.sql import (
        Disposition,
        ExecuteStatementRequestOnWaitTimeout,
        Format,
        StatementParameterListItem,
        StatementState,
    )

    inicio = time.perf_counter()
    try:
        resp = w.statement_execution.execute_statement(
            statement=sql,
            warehouse_id=warehouse_id,
            wait_timeout="30s",
            on_wait_timeout=ExecuteStatementRequestOnWaitTimeout.CONTINUE,
            row_limit=max_linhas,
            format=Format.JSON_ARRAY,
            disposition=Disposition.INLINE,
            parameters=[StatementParameterListItem(name=k, value=v) for k, v in (parametros or {}).items()] or None,
        )
        limite = time.time() + timeout_segundos
        while resp.status.state in (StatementState.PENDING, StatementState.RUNNING):
            if time.time() > limite:
                w.statement_execution.cancel_execution(resp.statement_id)
                return ResultadoSQL(
                    erro=f"Consulta passou de {timeout_segundos}s e foi cancelada. Simplifique ou agregue mais.",
                    duracao_ms=int((time.perf_counter() - inicio) * 1000),
                )
            time.sleep(1)
            resp = w.statement_execution.get_statement(resp.statement_id)
    except Exception as e:  # noqa: BLE001
        return ResultadoSQL(erro=ocultar_segredos(str(e))[:500], duracao_ms=int((time.perf_counter() - inicio) * 1000))

    duracao = int((time.perf_counter() - inicio) * 1000)
    if resp.status.state != StatementState.SUCCEEDED:
        msg = resp.status.error.message if resp.status.error else str(resp.status.state)
        return ResultadoSQL(erro=ocultar_segredos(msg), duracao_ms=duracao)
    colunas = [c.name for c in resp.manifest.schema.columns] if resp.manifest and resp.manifest.schema else []
    linhas = (resp.result.data_array or []) if resp.result else []
    truncado = bool(resp.manifest and resp.manifest.truncated)
    return ResultadoSQL(colunas=colunas, linhas=linhas, truncado=truncado, duracao_ms=duracao)
