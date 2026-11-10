"""IA de chat servida no Databricks (Model Serving), com suporte a ferramentas (tool calling).

Usa o formato OpenAI Chat Completions do endpoint `/serving-endpoints/{nome}/invocations`,
que vale para Foundation Model APIs (Llama, Claude, GPT, Gemini...) e external models.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol


@dataclass
class ChamadaFerramenta:
    id: str
    nome: str
    argumentos: Dict[str, Any]
    argumentos_brutos: str = ""


@dataclass
class RespostaLLM:
    conteudo: str = ""
    chamadas: List[ChamadaFerramenta] = field(default_factory=list)
    tokens_entrada: int = 0
    tokens_saida: int = 0

    def mensagem_assistente(self) -> Dict[str, Any]:
        """A mensagem que volta para o histórico antes das respostas das ferramentas."""
        msg: Dict[str, Any] = {"role": "assistant", "content": self.conteudo or None}
        if self.chamadas:
            msg["tool_calls"] = [
                {
                    "id": c.id,
                    "type": "function",
                    "function": {"name": c.nome, "arguments": c.argumentos_brutos or json.dumps(c.argumentos)},
                }
                for c in self.chamadas
            ]
        return msg


class LLM(Protocol):
    endpoint: str

    def conversar(self, mensagens: List[Dict[str, Any]], ferramentas: Optional[List[Dict[str, Any]]] = None) -> RespostaLLM: ...


def _texto(conteudo: Any) -> str:
    """Alguns modelos devolvem blocos (texto, raciocínio...): fica só o texto."""
    if conteudo is None:
        return ""
    if isinstance(conteudo, str):
        return conteudo
    if isinstance(conteudo, list):
        return "".join(
            p.get("text", "") for p in conteudo if isinstance(p, dict) and p.get("type", "text") == "text"
        )
    return str(conteudo)


def _argumentos(bruto: Any) -> Dict[str, Any]:
    if isinstance(bruto, dict):
        return bruto
    try:
        valor = json.loads(bruto or "{}")
        return valor if isinstance(valor, dict) else {}
    except (TypeError, ValueError):
        return {}


def interpretar_resposta(resp: Dict[str, Any]) -> RespostaLLM:
    escolhas = resp.get("choices") or []
    msg = (escolhas[0].get("message") if escolhas else None) or {}
    chamadas = []
    for i, c in enumerate(msg.get("tool_calls") or []):
        fn = c.get("function") or {}
        bruto = fn.get("arguments")
        chamadas.append(ChamadaFerramenta(
            id=c.get("id") or f"chamada_{i}",
            nome=fn.get("name") or "",
            argumentos=_argumentos(bruto),
            argumentos_brutos=bruto if isinstance(bruto, str) else json.dumps(bruto or {}),
        ))
    uso = resp.get("usage") or {}
    return RespostaLLM(
        conteudo=_texto(msg.get("content")).strip(),
        chamadas=chamadas,
        tokens_entrada=int(uso.get("prompt_tokens") or 0),
        tokens_saida=int(uso.get("completion_tokens") or 0),
    )


class ServingLLM:
    def __init__(self, w, endpoint: str, temperatura: float = 0.0, max_tokens: int = 4000):
        self.w, self.endpoint, self.temperatura, self.max_tokens = w, endpoint, temperatura, max_tokens

    def conversar(self, mensagens: List[Dict[str, Any]], ferramentas: Optional[List[Dict[str, Any]]] = None) -> RespostaLLM:
        corpo: Dict[str, Any] = {"messages": mensagens, "temperature": self.temperatura, "max_tokens": self.max_tokens}
        if ferramentas:
            corpo["tools"] = ferramentas
            corpo["tool_choice"] = "auto"
        resp = self.w.api_client.do("POST", f"/serving-endpoints/{self.endpoint}/invocations", body=corpo)
        return interpretar_resposta(resp)


FERRAMENTA_TESTE = {
    "type": "function",
    "function": {
        "name": "contar_tabelas",
        "description": "Conta as tabelas de um schema.",
        "parameters": {
            "type": "object",
            "properties": {"schema": {"type": "string", "description": "nome do schema"}},
            "required": ["schema"],
        },
    },
}


def testar_ferramentas(llm: LLM) -> str:
    """Confere se a IA responde e se sabe chamar ferramentas (os pesquisadores dependem disso)."""
    r = llm.conversar(
        [{"role": "user", "content": "Use a ferramenta para contar as tabelas do schema 'vendas'."}],
        [FERRAMENTA_TESTE],
    )
    if any(c.nome == "contar_tabelas" for c in r.chamadas):
        return "ok"
    return "respondeu, mas sem chamar a ferramenta (pode não suportar tool calling)"
