import re
from pathlib import Path

import pytest

from sabichao.config import ConfigSabichao
from sabichao.seguranca import ocultar_segredos

RAIZ = Path(__file__).resolve().parents[1]


def test_config_ida_e_volta_e_campos_faltando():
    cfg = ConfigSabichao(llm_pesquisador_a="a", llm_juiz="j", catalogos_liberados=["Vendas", "rh"])
    cfg.validar()
    assert cfg.catalogos_liberados == ["rh", "vendas"]
    assert cfg.faltando() == ["IA do Pesquisador B", "SQL Warehouse"]
    volta = ConfigSabichao.from_json(cfg.to_json().replace('"llm_juiz"', '"campo_antigo": 1, "llm_juiz"'))
    assert volta == cfg
    assert ConfigSabichao.from_json(None) == ConfigSabichao()


@pytest.mark.parametrize("kw", [{"catalogos_liberados": ["x`; DROP"]}, {"max_iteracoes": 0}, {"max_linhas": 100000}])
def test_config_invalida(kw):
    with pytest.raises(ValueError):
        ConfigSabichao(**kw).validar()


def test_segredos_somem_das_mensagens():
    texto = ocultar_segredos("falhou com dapi" + "0" * 32 + " e Authorization: Bearer abcdefghijklmnop")
    assert "dapi0000" not in texto and "abcdefghijklmnop" not in texto


def test_repositorio_sem_tokens_e_app_yaml_sem_segredos():
    for arq in RAIZ.rglob("*"):
        if arq.is_file() and arq.suffix in {".py", ".yaml", ".yml", ".json", ".md", ".txt"} and ".venv" not in arq.parts:
            assert not re.search(r"dapi[0-9a-f]{32}", arq.read_text(encoding="utf-8", errors="ignore")), arq
    assert not re.search(r"(?im)^\s*-?\s*name:\s*\S*(TOKEN|SECRET|SENHA)", (RAIZ / "app.yaml").read_text())
