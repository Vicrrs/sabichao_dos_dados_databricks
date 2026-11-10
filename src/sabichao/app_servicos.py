"""Serviços da app Streamlit: tudo o que conversa com o Databricks fica aqui.

Identidade: com *user authorization* habilitada na app, o Databricks envia o token de quem está
logado no header `x-forwarded-access-token` e a app age COMO ESSA PESSOA (vê as IAs, warehouses e
tabelas que ela vê). Sem isso, a app usa a conta de serviço (service principal) dela.
"""
from __future__ import annotations

import io
import os
import re
from typing import Any, Dict, List, Optional

from .agentes import Juiz, Pesquisador, Sabichao
from .catalogo import DatabricksCatalogo
from .config import ConfigSabichao
from .dados import executar_sql_warehouse
from .descoberta import Recursos, descobrir, listar_catalogos
from .documentacao import OPCIONAIS, Documentacao, FonteDocumentada
from .ferramentas import Cache, Ferramentas
from .llm import ServingLLM, testar_ferramentas
from .seguranca import citar, ocultar_segredos

ARQUIVO_CONFIG = "sabichao.json"


def escopos_faltando(w) -> List[str]:
    """Testa o token do usuário em APIs que exigem escopo; devolve os escopos que faltam."""
    import requests

    faltando: List[str] = []
    try:
        host = w.config.host.rstrip("/")
        cab = w.config.authenticate()
    except Exception:  # noqa: BLE001 - sem como testar: assume que está ok
        return []
    for caminho in ("/api/2.0/serving-endpoints", "/api/2.0/sql/warehouses"):
        try:
            r = requests.get(host + caminho, headers=cab, timeout=15)
        except Exception:  # noqa: BLE001
            continue
        if r.status_code == 403 and "required scopes" in r.text:
            m = re.search(r"required scopes?:\s*([\w.:\-, ]+?)\s*(\[|\"|$)", r.text)
            faltando.append(m.group(1).strip() if m else caminho)
    return faltando


class Servicos:
    motivo = None
    token_hash = None

    def __init__(self, w, identidade: str, w_app=None, usuario: Optional[str] = None):
        self.w = w                      # cliente usado nas ações (usuário, se disponível)
        self.w_app = w_app or w         # service principal da app
        self.identidade = identidade    # "usuario" | "app"
        self.usuario = usuario
        self.cache = Cache()            # resultados do catálogo compartilhados entre perguntas

    # --------------------------------------------------------------- criação
    @classmethod
    def do_contexto(cls, headers: Optional[Dict[str, str]] = None, w_app=None) -> "Servicos":
        """Cria os clientes a partir dos headers ATUAIS (o token do usuário é renovado pelo Databricks)."""
        from databricks.sdk import WorkspaceClient

        headers = {k.lower(): v for k, v in (headers or {}).items()}
        w_app = w_app or WorkspaceClient()
        token = headers.get("x-forwarded-access-token")
        usuario = headers.get("x-forwarded-email") or headers.get("x-forwarded-preferred-username")
        # IDENTIDADE_APP=app no app.yaml força o uso só da conta de serviço da app.
        if os.environ.get("IDENTIDADE_APP", "auto").lower() == "app":
            token = None
        motivo = None
        if token:
            w = WorkspaceClient(host=w_app.config.host, token=token, auth_type="pat")
            faltando = escopos_faltando(w)
            if faltando:
                motivo = (
                    "O seu token chegou sem os escopos " + ", ".join(f"`{e}`" for e in faltando)
                    + " (user authorization da app). Usando a conta de serviço da app em tudo."
                )
                sv = cls(w_app, "app", w_app, usuario)
            else:
                sv = cls(w, "usuario", w_app, usuario)
        else:
            sv = cls(w_app, "app", w_app, usuario)
        sv.token_hash = hash(token) if token else None
        sv.motivo = motivo
        return sv

    @property
    def id_conta_servico(self) -> str:
        return getattr(getattr(self.w_app, "config", None), "client_id", None) or "?"

    # ------------------------------------------------------------ descoberta
    def recursos(self) -> Recursos:
        """Lista com o usuário; o que falhar é tentado com a conta de serviço da app (plano B)."""
        r = descobrir(self.w)
        if self.w_app is self.w or not r.avisos:
            return r
        plano_b = descobrir(self.w_app)
        usados = []
        if not r.llms and plano_b.llms:
            r.llms = plano_b.llms
            usados.append("IAs")
        if not r.warehouses and plano_b.warehouses:
            r.warehouses = plano_b.warehouses
            usados.append("SQL Warehouses")
        if usados:
            r.avisos.append("Listei " + " e ".join(usados) + " com a conta de serviço da app (o seu token falhou).")
        return r

    def catalogos(self) -> List[str]:
        return listar_catalogos(self.w)

    def diagnostico(self) -> List[Dict[str, Any]]:
        """Chama a API crua com cada identidade e mostra status, tipo e início da resposta."""
        import requests

        resultado = []
        identidades = [("usuário", self.w)] if self.identidade == "usuario" else []
        identidades.append(("conta de serviço da app", self.w_app))
        for nome, cliente in identidades:
            for caminho in ("/api/2.0/preview/scim/v2/Me", "/api/2.0/serving-endpoints", "/api/2.0/sql/warehouses",
                            "/api/2.1/unity-catalog/catalogs"):
                linha: Dict[str, Any] = {"identidade": nome, "chamada": caminho}
                try:
                    host = cliente.config.host.rstrip("/")
                    cab = cliente.config.authenticate()
                    resp = requests.get(host + caminho, headers=cab, timeout=20, allow_redirects=False)
                    linha.update(status=resp.status_code, resposta=ocultar_segredos(resp.text[:200].replace("\n", " ")))
                except Exception as e:  # noqa: BLE001
                    linha.update(status="erro", resposta=ocultar_segredos(str(e))[:200])
                resultado.append(linha)
        return resultado

    # --------------------------------------------------------- armazenamento
    @staticmethod
    def caminho_config(catalogo: str, schema: str) -> str:
        return f"/Volumes/{catalogo}/{schema}/config/{ARQUIVO_CONFIG}"

    def preparar_projeto(self, catalogo: str, schema: str, warehouse_id: str) -> List[str]:
        """Cria o schema e o volume de configuração do projeto. Devolve erros, se houver."""
        try:
            projeto = citar(f"{catalogo}.{schema}", 2)  # vem do campo de texto da barra lateral
        except ValueError as e:
            return [str(e)]
        erros = []
        for sql in (f"CREATE SCHEMA IF NOT EXISTS {projeto}", f"CREATE VOLUME IF NOT EXISTS {projeto}.`config`"):
            r = executar_sql_warehouse(self.w, warehouse_id, sql, 10, 120)
            if r.erro:
                erros.append(f"{sql}: {r.erro}")
        return erros

    def ler_config(self, catalogo: str, schema: str) -> Optional[ConfigSabichao]:
        try:
            texto = self.w.files.download(self.caminho_config(catalogo, schema)).contents.read().decode("utf-8")
        except Exception:  # noqa: BLE001 - arquivo/volume ainda não existe
            return None
        return ConfigSabichao.from_json(texto)

    def gravar_config(self, catalogo: str, schema: str, cfg: ConfigSabichao) -> None:
        self.w.files.upload(self.caminho_config(catalogo, schema), io.BytesIO(cfg.to_json().encode("utf-8")), overwrite=True)

    # ---------------------------------------------------------------- agentes
    def llm(self, endpoint: str, temperatura: float = 0.0) -> ServingLLM:
        return ServingLLM(self.w, endpoint, temperatura=temperatura, max_tokens=4000)

    def testar_ia(self, endpoint: str) -> str:
        return testar_ferramentas(self.llm(endpoint))

    def verificar_documentacao(self, tabela: str, warehouse_id: str, somente_revisada: bool = False) -> Dict[str, Any]:
        """Confere se a tabela de documentação tem o formato esperado e quanto já foi revisado."""
        doc = Documentacao(DatabricksCatalogo(self.w, warehouse_id), tabela, somente_revisada)
        problemas = doc.problemas()
        if problemas:
            return {"problemas": problemas, "resumo": []}
        return {"problemas": [], "resumo": doc.resumo(), "opcionais_ausentes": [c for c in OPCIONAIS if c not in doc.colunas()]}

    def sabichao(self, cfg: ConfigSabichao) -> Sabichao:
        fonte = DatabricksCatalogo(self.w, cfg.warehouse_id)
        if cfg.tabela_documentacao:
            fonte = FonteDocumentada(fonte, Documentacao(fonte, cfg.tabela_documentacao, cfg.doc_somente_revisada))

        def ferramentas() -> Ferramentas:
            return Ferramentas(fonte, cfg.catalogos_liberados, cfg.max_linhas, cfg.linhas_perfil, self.cache)

        a = Pesquisador("Pesquisador A", self.llm(cfg.llm_pesquisador_a, cfg.temperatura_a), ferramentas(),
                        cfg.max_iteracoes, cfg.instrucoes_extra)
        b = Pesquisador("Pesquisador B", self.llm(cfg.llm_pesquisador_b, cfg.temperatura_b), ferramentas(),
                        cfg.max_iteracoes, cfg.instrucoes_extra)
        juiz = Juiz(self.llm(cfg.llm_juiz, cfg.temperatura_juiz), cfg.instrucoes_extra)
        return Sabichao(a, b, juiz, cfg.janela_historico)
