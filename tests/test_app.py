"""Roda a app Streamlit de verdade (AppTest) com o Databricks simulado."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fakes import CatalogoFalso, LLMFalso, chamar

from sabichao.agentes import Juiz, Pesquisador, Sabichao
from sabichao.app_servicos import Servicos
from sabichao.config import ConfigSabichao
from sabichao.descoberta import ModeloIA, Recursos, WarehouseInfo
from sabichao.ferramentas import Ferramentas

AppTest = pytest.importorskip("streamlit.testing.v1").AppTest
APP = str(Path(__file__).resolve().parents[1] / "app.py")


class ServicosFalsos:
    identidade, usuario, motivo, token_hash = "usuario", "ana@empresa.com", None, None
    id_conta_servico = "sp"
    caminho_config = staticmethod(Servicos.caminho_config)

    def __init__(self, cfg):
        self.cfg, self.gravadas = cfg, []
        self.w_app = SimpleNamespace(config=SimpleNamespace(host="https://exemplo.databricks.com"))
        self.cache = None

    def recursos(self):
        return Recursos(llms=[ModeloIA("databricks-meta-llama-3-3-70b-instruct", "foundation", True),
                              ModeloIA("databricks-claude-sonnet-4", "foundation", True)],
                        warehouses=[WarehouseInfo("wh1", "Serverless", "RUNNING", True)])

    def catalogos(self):
        return ["rh", "vendas"]

    def ler_config(self, catalogo, schema):
        return self.cfg

    def preparar_projeto(self, *a):
        return []

    def gravar_config(self, catalogo, schema, cfg):
        self.gravadas.append(cfg)

    def sabichao(self, cfg):
        def p(nome, llm):
            return Pesquisador(nome, llm, Ferramentas(CatalogoFalso(), cfg.catalogos_liberados))
        juiz = LLMFalso(json.dumps({"vencedor": "A", "justificativa": "achou", "divergencias": [], "confianca": "alta",
                                    "resposta_final": "Sim, existe **vendas.comercial.clientes**."}))
        return Sabichao(p("Pesquisador A", LLMFalso(chamar("buscar_tabelas", termo="cliente"), "Existe clientes")),
                        p("Pesquisador B", LLMFalso("Não sei")), Juiz(juiz))


def rodar(monkeypatch, cfg):
    sv = ServicosFalsos(cfg)
    monkeypatch.setattr(Servicos, "do_contexto", classmethod(lambda cls, *a, **k: sv))
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    assert not at.exception, at.exception
    return at, sv


def test_sem_configuracao_pede_para_escolher_as_ias(monkeypatch):
    at, sv = rodar(monkeypatch, None)
    assert any("Configuração" in i.value for i in at.info)
    assert at.chat_input[0].disabled
    # salva com as sugestões: A e B ficam com IAs diferentes
    at.button(key="FormSubmitter:form_config-💾 Salvar configuração").click().run()
    assert not at.exception, at.exception
    cfg = sv.gravadas[-1]
    assert cfg.llm_pesquisador_a != cfg.llm_pesquisador_b and cfg.warehouse_id == "wh1" and not cfg.faltando()


def test_chat_responde_com_o_veredito_do_juiz(monkeypatch):
    cfg = ConfigSabichao("databricks-meta-llama-3-3-70b-instruct", "databricks-claude-sonnet-4",
                         "databricks-claude-sonnet-4", "wh1", ["vendas"])
    at, _ = rodar(monkeypatch, cfg)
    at.chat_input[0].set_value("Existe tabela de clientes?").run()
    assert not at.exception, at.exception
    textos = " ".join(m.value for m in at.markdown)
    assert "vendas.comercial.clientes" in textos and "Existe tabela de clientes?" in textos
