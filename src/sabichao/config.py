"""Configuração do Sabichão: as três IAs, o SQL Warehouse, os catálogos liberados e os limites.

A app grava este JSON no Volume do projeto (`/Volumes/<catalogo>/<schema>/config/sabichao.json`),
então a escolha vale para todo mundo que abre a app.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from typing import List, Optional

from .seguranca import nome_seguro


@dataclass
class ConfigSabichao:
    llm_pesquisador_a: str = ""
    llm_pesquisador_b: str = ""
    llm_juiz: str = ""
    warehouse_id: str = ""
    catalogos_liberados: List[str] = field(default_factory=list)  # vazio = todos que a pessoa enxerga
    # Tabela com a documentação das outras tabelas (formato do notebook uc_auto_doc_revisao), opcional.
    tabela_documentacao: str = ""
    doc_somente_revisada: bool = False  # True = só usa o que o time aprovou ou corrigiu
    temperatura_a: float = 0.0
    temperatura_b: float = 0.4  # um pouco mais criativo: os dois exploram caminhos diferentes
    temperatura_juiz: float = 0.0
    max_iteracoes: int = 8
    max_linhas: int = 200
    linhas_perfil: int = 100_000
    janela_historico: int = 8
    instrucoes_extra: str = ""

    def faltando(self) -> List[str]:
        itens = [("llm_pesquisador_a", "IA do Pesquisador A"), ("llm_pesquisador_b", "IA do Pesquisador B"),
                 ("llm_juiz", "IA do Juiz"), ("warehouse_id", "SQL Warehouse")]
        return [rotulo for campo, rotulo in itens if not getattr(self, campo)]

    def validar(self) -> "ConfigSabichao":
        self.catalogos_liberados = sorted({nome_seguro(c, 1) for c in self.catalogos_liberados})
        self.tabela_documentacao = nome_seguro(self.tabela_documentacao, 3) if self.tabela_documentacao.strip() else ""
        for campo, minimo, maximo in (("max_iteracoes", 1, 20), ("max_linhas", 1, 5000),
                                      ("linhas_perfil", 1000, 10_000_000), ("janela_historico", 0, 40)):
            valor = int(getattr(self, campo))
            if not minimo <= valor <= maximo:
                raise ValueError(f"{campo} deve ficar entre {minimo} e {maximo}")
            setattr(self, campo, valor)
        return self

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)

    @classmethod
    def from_json(cls, texto: Optional[str]) -> "ConfigSabichao":
        if not texto:
            return cls()
        dados = json.loads(texto)
        validos = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in dados.items() if k in validos})
