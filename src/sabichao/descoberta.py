"""Descobre o que existe no workspace: IAs de chat, SQL Warehouses e o Unity Catalog.

Na app, o cliente é criado com o token de quem está logado, então lista exatamente o que
essa pessoa pode usar.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List

from .seguranca import ocultar_segredos

CATALOGOS_OCULTOS = ("system", "__databricks_internal")


@dataclass
class ModeloIA:
    nome: str
    origem: str  # "foundation" | "externo" | "custom"
    pronto: bool
    detalhe: str = ""

    @property
    def rotulo(self) -> str:
        status = "" if self.pronto else " (não pronto)"
        extra = f" · {self.detalhe}" if self.detalhe else ""
        return f"{self.nome} [{self.origem}{extra}]{status}"


@dataclass
class WarehouseInfo:
    id: str
    nome: str
    estado: str
    serverless: bool = False
    tamanho: str = ""

    @property
    def rotulo(self) -> str:
        tipo = "serverless" if self.serverless else self.tamanho
        return f"{self.nome} [{tipo}, {self.estado}]"


@dataclass
class Recursos:
    llms: List[ModeloIA] = field(default_factory=list)
    warehouses: List[WarehouseInfo] = field(default_factory=list)
    avisos: List[str] = field(default_factory=list)


def _valor(enum_ou_str) -> str:
    return str(getattr(enum_ou_str, "value", enum_ou_str) or "")


def _eh_chat(endpoint) -> bool:
    t = (endpoint.task or "").lower()
    return "chat" in t or "completions" in t


def _origem(endpoint) -> tuple[str, str]:
    entidades = (endpoint.config.served_entities if endpoint.config else None) or []
    for e in entidades:
        if e.external_model is not None:
            prov = _valor(getattr(e.external_model, "provider", "")) or "externo"
            return "externo", f"{prov}: {getattr(e.external_model, 'name', '')}"
        if e.foundation_model is not None:
            return "foundation", getattr(e.foundation_model, "display_name", "") or ""
        if e.entity_name:
            if e.entity_name.startswith("system.ai."):
                return "foundation", e.entity_name
            return "custom", e.entity_name
    if (endpoint.name or "").startswith("databricks-"):
        return "foundation", ""
    return "custom", ""


def listar_llms(w) -> List[ModeloIA]:
    lista = []
    for ep in w.serving_endpoints.list():
        if not _eh_chat(ep):
            continue
        origem, detalhe = _origem(ep)
        pronto = _valor(ep.state.ready if ep.state else "") == "READY"
        lista.append(ModeloIA(ep.name, origem, pronto, detalhe))
    return sorted(lista, key=lambda m: (not m.pronto, m.origem != "foundation", m.nome))


def listar_warehouses(w) -> List[WarehouseInfo]:
    lista = [
        WarehouseInfo(
            id=wh.id,
            nome=wh.name,
            estado=_valor(wh.state),
            serverless=bool(wh.enable_serverless_compute),
            tamanho=wh.cluster_size or "",
        )
        for wh in w.warehouses.list()
    ]
    return sorted(lista, key=lambda x: (not x.serverless, x.estado != "RUNNING", x.nome))


def mensagem_erro(e: Exception) -> str:
    """Resume erros do SDK; para falta de escopo, diz exatamente o que liberar na app."""
    texto = ocultar_segredos(str(e))
    m = re.search(r"does not have required scopes?:\s*([\w.:\-, ]+?)\s*(\[|$)", texto)
    if m:
        return (
            f"falta o escopo `{m.group(1).strip()}` na user authorization da app. Vá em Apps → (sua app) → "
            "Authorization → User authorization, adicione o escopo, salve e reabra a app em uma aba "
            "anônima (para autorizar de novo)."
        )
    if "unable to parse response" in texto:
        status = re.search(r"<\s*(\d{3}[^\n`]*)", texto)
        detalhe = status.group(1).strip() if status else "status desconhecido"
        return (
            f"o Databricks respondeu HTTP {detalhe} sem JSON. "
            "Abra '🩺 Diagnóstico de acesso' na barra lateral para ver o motivo."
        )
    return texto.split(" Config: ")[0][:400]


def descobrir(w) -> Recursos:
    """Lista tudo; uma falha vira aviso e não interrompe o resto."""
    r = Recursos()
    try:
        r.llms = listar_llms(w)
    except Exception as e:  # noqa: BLE001
        r.avisos.append(f"Não foi possível listar os endpoints de IA: {mensagem_erro(e)}")
    try:
        r.warehouses = listar_warehouses(w)
    except Exception as e:  # noqa: BLE001
        r.avisos.append(f"Não foi possível listar os SQL Warehouses: {mensagem_erro(e)}")
    return r


def listar_catalogos(w) -> List[str]:
    return sorted(c.name for c in w.catalogs.list() if c.name and c.name not in CATALOGOS_OCULTOS)
