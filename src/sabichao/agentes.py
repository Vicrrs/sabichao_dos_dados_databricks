"""Os três agentes do Sabichão.

    pergunta ─┬─► Pesquisador A (IA 1) ─┐  cada um estuda a pergunta e investiga o catálogo
              │   pensa → ferramenta →  │  sozinho, sem ver o outro
              └─► Pesquisador B (IA 2) ─┤
                                        ▼
                                  Juiz (IA 3)  confere as respostas contra as evidências
                                        │      (o que as ferramentas devolveram) e escreve
                                        ▼      a resposta final
                                     usuário
"""
from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .ferramentas import Ferramentas, Passo
from .llm import LLM
from .seguranca import ocultar_segredos

Evento = Callable[[Dict[str, Any]], None]

PROMPT_PESQUISADOR = """Você é um pesquisador de dados do "Sabichão dos Dados", um assistente que responde
perguntas sobre as tabelas do Unity Catalog do Databricks: se uma tabela existe, quais tabelas há
sobre um assunto, o que cada tabela contém e as estatísticas dela.

Como trabalhar:
1. Estude a pergunta: o que exatamente o usuário quer saber? Que termos podem aparecer nos nomes
   ou comentários das tabelas (pense em sinônimos e em português/inglês, ex.: cliente/customer)?
2. Investigue com as ferramentas. Comece amplo (buscar_tabelas, listar_*) e aprofunde
   (descrever_tabela, estatisticas_tabela, executar_sql) só no que for relevante.
3. Responda SOMENTE com base no que as ferramentas mostraram. Nunca invente tabela, coluna ou número.
   Se não encontrou, diga que não encontrou e o que procurou.
4. Sempre cite o nome completo das tabelas (catalogo.schema.tabela).
5. Muitas tabelas não têm comentário no Unity Catalog: a descrição pode vir de uma tabela de
   documentação da governança e aparece marcada com o status da revisão do time responsável
   ([doc aprovada pelo time], [doc corrigida pelo time], [doc NÃO revisada]...). Use essa documentação,
   mas quando ela não tiver sido revisada, avise o usuário que a descrição ainda não foi validada.

Responda em português do Brasil, em Markdown, de forma objetiva: primeiro a resposta direta,
depois os detalhes (tabelas, colunas, números). Termine com uma linha "Confiança: alta|média|baixa".{extra}"""

PROMPT_FINALIZAR = (
    "Você atingiu o limite de passos. Responda agora com o que já apurou, sem chamar ferramentas, "
    "e diga o que ficou pendente."
)

PROMPT_JUIZ = """Você é o JUIZ do "Sabichão dos Dados". Dois pesquisadores investigaram a mesma pergunta
sobre as tabelas do Unity Catalog, cada um por conta própria. Você recebe a resposta de cada um e as
EVIDÊNCIAS (o que as ferramentas realmente devolveram a cada um).

Seu trabalho:
1. Confira cada afirmação das respostas contra as evidências. Afirmação sem evidência não vale.
2. Compare: onde concordam, onde divergem e quem está certo em cada divergência.
3. Escreva a RESPOSTA FINAL para o usuário: a melhor combinação do que está comprovado, completa e
   objetiva, em Markdown e em português do Brasil. Cite tabelas pelo nome completo. Se nenhum dos
   dois encontrou a resposta, diga isso com clareza e sugira o que o usuário pode perguntar.
   Não mencione "pesquisador A/B" nem "evidências" na resposta final: fale direto com o usuário.
4. Descrições marcadas como [doc NÃO revisada] ou [doc parcialmente revisada] vieram da tabela de
   documentação e ainda não foram validadas pelo time responsável: mantenha esse aviso na resposta final.

Responda SOMENTE com um JSON válido:
{{"vencedor": "A" | "B" | "empate" | "nenhum",
  "justificativa": "1-3 frases: por que essa resposta é a mais confiável",
  "divergencias": ["pontos em que os pesquisadores discordaram (vazio se nenhum)"],
  "confianca": "alta" | "média" | "baixa",
  "resposta_final": "a resposta em Markdown"}}{extra}"""

MAX_CHARS_EVIDENCIA = 1500   # por passo
MAX_CHARS_EVIDENCIAS = 14000  # por pesquisador


@dataclass
class Relatorio:
    """O que um pesquisador entregou."""
    nome: str
    endpoint: str
    resposta: str = ""
    passos: List[Passo] = field(default_factory=list)
    tokens_entrada: int = 0
    tokens_saida: int = 0
    duracao_ms: int = 0
    erro: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["passos"] = [p.to_dict() for p in self.passos]
        return d


@dataclass
class Veredito:
    resposta_final: str
    vencedor: str = "nenhum"
    justificativa: str = ""
    divergencias: List[str] = field(default_factory=list)
    confianca: str = ""
    endpoint: str = ""
    tokens_entrada: int = 0
    tokens_saida: int = 0
    duracao_ms: int = 0
    erro: Optional[str] = None


@dataclass
class ResultadoSabichao:
    pergunta: str
    veredito: Veredito
    relatorios: List[Relatorio]

    @property
    def tokens(self) -> int:
        return self.veredito.tokens_entrada + self.veredito.tokens_saida + sum(
            r.tokens_entrada + r.tokens_saida for r in self.relatorios
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pergunta": self.pergunta,
            "veredito": asdict(self.veredito),
            "relatorios": [r.to_dict() for r in self.relatorios],
            "tokens": self.tokens,
        }


def _extra(texto: str) -> str:
    return f"\n\nInstruções adicionais de quem configurou:\n{texto.strip()}" if texto and texto.strip() else ""


def _erro(e: Exception) -> str:
    return ocultar_segredos(str(e)).split(" Config: ")[0][:400]


class Pesquisador:
    def __init__(self, nome: str, llm: LLM, ferramentas: Ferramentas, max_iteracoes: int = 8, instrucoes_extra: str = ""):
        self.nome, self.llm, self.ferramentas = nome, llm, ferramentas
        self.max_iteracoes = max_iteracoes
        self.sistema = PROMPT_PESQUISADOR.format(extra=_extra(instrucoes_extra))

    def pesquisar(self, pergunta: str, historico: List[Dict[str, str]], evento: Evento = lambda _: None) -> Relatorio:
        rel = Relatorio(self.nome, self.llm.endpoint)
        inicio = time.perf_counter()
        msgs: List[Dict[str, Any]] = [{"role": "system", "content": self.sistema}, *historico,
                                      {"role": "user", "content": pergunta}]
        evento({"agente": self.nome, "tipo": "inicio", "endpoint": self.llm.endpoint})
        try:
            for _ in range(self.max_iteracoes):
                r = self.llm.conversar(msgs, self.ferramentas.definicoes)
                rel.tokens_entrada += r.tokens_entrada
                rel.tokens_saida += r.tokens_saida
                if not r.chamadas:
                    rel.resposta = r.conteudo
                    break
                msgs.append(r.mensagem_assistente())
                for c in r.chamadas:
                    evento({"agente": self.nome, "tipo": "chamada", "ferramenta": c.nome, "argumentos": c.argumentos})
                    passo = self.ferramentas.executar(c.nome, c.argumentos)
                    rel.passos.append(passo)
                    evento({"agente": self.nome, "tipo": "passo", "passo": passo})
                    msgs.append({"role": "tool", "tool_call_id": c.id, "content": passo.texto})
            else:
                msgs.append({"role": "user", "content": PROMPT_FINALIZAR})
                r = self.llm.conversar(msgs)
                rel.tokens_entrada += r.tokens_entrada
                rel.tokens_saida += r.tokens_saida
                rel.resposta = r.conteudo
        except Exception as e:  # noqa: BLE001
            rel.erro = _erro(e)
        rel.duracao_ms = int((time.perf_counter() - inicio) * 1000)
        evento({"agente": self.nome, "tipo": "fim", "relatorio": rel})
        return rel


def evidencias(rel: Relatorio) -> str:
    partes, total = [], 0
    for i, p in enumerate(rel.passos, 1):
        texto = p.texto if len(p.texto) <= MAX_CHARS_EVIDENCIA else p.texto[:MAX_CHARS_EVIDENCIA] + "…"
        bloco = f"[{i}] {p.ferramenta}({json.dumps(p.argumentos, ensure_ascii=False)})\n{texto}"
        if total + len(bloco) > MAX_CHARS_EVIDENCIAS:
            partes.append(f"… (+{len(rel.passos) - i + 1} passo(s) omitidos)")
            break
        partes.append(bloco)
        total += len(bloco)
    return "\n\n".join(partes) or "(nenhuma ferramenta usada)"


def extrair_json(texto: str) -> Dict[str, Any]:
    texto = re.sub(r"```(?:json)?", "", texto or "")
    ini, fim = texto.find("{"), texto.rfind("}")
    if ini == -1 or fim <= ini:
        raise ValueError("resposta sem JSON")
    return json.loads(texto[ini: fim + 1])


class Juiz:
    def __init__(self, llm: LLM, instrucoes_extra: str = ""):
        self.llm = llm
        self.sistema = PROMPT_JUIZ.format(extra=_extra(instrucoes_extra))

    def julgar(self, pergunta: str, historico: List[Dict[str, str]], relatorios: List[Relatorio]) -> Veredito:
        inicio = time.perf_counter()
        conversa = "\n".join(f"{m['role']}: {m['content'][:500]}" for m in historico[-6:]) or "(início da conversa)"
        blocos = []
        for rel, letra in zip(relatorios, "AB"):
            resposta = rel.resposta or f"(não respondeu: {rel.erro or 'resposta vazia'})"
            blocos.append(f"=== PESQUISADOR {letra} ({rel.endpoint}) ===\nRESPOSTA:\n{resposta}\n\nEVIDÊNCIAS:\n{evidencias(rel)}")
        usuario = f"Conversa até aqui:\n{conversa}\n\nPERGUNTA: {pergunta}\n\n" + "\n\n".join(blocos)
        r = self.llm.conversar([{"role": "system", "content": self.sistema}, {"role": "user", "content": usuario}])
        v = Veredito(resposta_final=r.conteudo, endpoint=self.llm.endpoint,
                     tokens_entrada=r.tokens_entrada, tokens_saida=r.tokens_saida)
        try:
            d = extrair_json(r.conteudo)
            v.resposta_final = str(d.get("resposta_final") or "").strip() or r.conteudo
            v.vencedor = str(d.get("vencedor") or "nenhum")
            v.justificativa = str(d.get("justificativa") or "")
            v.divergencias = [str(x) for x in d.get("divergencias") or [] if str(x).strip()]
            v.confianca = str(d.get("confianca") or "")
        except (ValueError, TypeError):
            v.justificativa = "O juiz não devolveu o JSON esperado; a resposta dele foi usada como está."
        v.duracao_ms = int((time.perf_counter() - inicio) * 1000)
        return v


class Sabichao:
    def __init__(self, pesquisador_a: Pesquisador, pesquisador_b: Pesquisador, juiz: Juiz, janela_historico: int = 8):
        self.pesquisadores = [pesquisador_a, pesquisador_b]
        self.juiz = juiz
        self.janela = janela_historico

    def responder(self, pergunta: str, historico: Optional[List[Dict[str, str]]] = None, evento: Evento = lambda _: None) -> ResultadoSabichao:
        hist = [{"role": m["role"], "content": m["content"]} for m in (historico or [])
                if m.get("role") in ("user", "assistant") and m.get("content")][-self.janela:]
        with ThreadPoolExecutor(max_workers=2) as ex:
            futuros = [ex.submit(p.pesquisar, pergunta, hist, evento) for p in self.pesquisadores]
            relatorios = [f.result() for f in futuros]

        if all(r.erro and not r.resposta for r in relatorios):
            msg = "Os dois pesquisadores falharam:\n" + "\n".join(f"- **{r.nome}** (`{r.endpoint}`): {r.erro}" for r in relatorios)
            return ResultadoSabichao(pergunta, Veredito(resposta_final=f"⚠️ {msg}", erro=msg), relatorios)

        evento({"agente": "juiz", "tipo": "inicio", "endpoint": self.juiz.llm.endpoint})
        try:
            veredito = self.juiz.julgar(pergunta, hist, relatorios)
        except Exception as e:  # noqa: BLE001
            # Sem juiz, fica a resposta do pesquisador que respondeu com mais evidências.
            melhor = max((r for r in relatorios if r.resposta), key=lambda r: len(r.passos))
            veredito = Veredito(
                resposta_final=melhor.resposta, vencedor="AB"[relatorios.index(melhor)], erro=_erro(e),
                justificativa=f"O juiz falhou ({_erro(e)}); mostrando a resposta de {melhor.nome}, sem conferência.",
            )
        evento({"agente": "juiz", "tipo": "fim", "veredito": veredito})
        return ResultadoSabichao(pergunta, veredito, relatorios)
