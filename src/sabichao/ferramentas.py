"""Ferramentas dos pesquisadores: navegar no catálogo, procurar tabelas, descrevê-las, tirar
estatísticas e rodar SQL somente leitura. Cada chamada vira um `Passo` (o juiz e a tela leem)."""
from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from .catalogo import FonteCatalogo, catalogo_visivel
from .documentacao import rotulo
from .perfil import perfil_texto, perfilar_tabela
from .seguranca import nome_seguro, ocultar_segredos
from .sql_guard import SQLNaoPermitido, validar_sql

MAX_CHARS_RESULTADO = 6000  # quanto de cada resultado a IA lê


def _ferramenta(nome: str, descricao: str, **props: str) -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": nome,
            "description": descricao,
            "parameters": {
                "type": "object",
                "properties": {k: {"type": "string", "description": v} for k, v in props.items()},
                "required": list(props),
            },
        },
    }


DEFINICOES = [
    _ferramenta("listar_catalogos", "Lista os catálogos do Unity Catalog que você pode consultar."),
    _ferramenta("listar_schemas", "Lista os schemas de um catálogo.", catalogo="nome do catálogo"),
    _ferramenta("listar_tabelas", "Lista as tabelas e views de um schema, com tipo e comentário.",
                catalogo="nome do catálogo", schema="nome do schema"),
    _ferramenta("buscar_tabelas",
                "Procura tabelas pelo nome, pelo comentário ou pelo nome/comentário das colunas. "
                "Use um termo curto (uma palavra, ex.: 'cliente', 'venda', 'cpf').",
                termo="palavra a procurar"),
    _ferramenta("descrever_tabela",
                "Metadados de uma tabela: tipo, formato, dono, datas, comentário e colunas (tipo e comentário).",
                tabela="nome completo catalogo.schema.tabela"),
    _ferramenta("estatisticas_tabela",
                "Perfil estatístico: total de linhas, tamanho, nulos %, distintos, mín/máx, média, desvio, "
                "quartis, valores mais comuns e amostra de cada coluna. Mais caro: use quando a pergunta pedir.",
                tabela="nome completo catalogo.schema.tabela"),
    _ferramenta("executar_sql",
                "Executa UMA consulta SELECT (Databricks SQL) somente leitura, com nomes completos "
                "catalogo.schema.tabela. Use para contagens, distribuições e números específicos.",
                sql="a consulta SELECT"),
]


@dataclass
class Passo:
    ferramenta: str
    argumentos: Dict[str, Any]
    texto: str                                 # o que a IA lê
    tabela: Optional[Dict[str, Any]] = None    # {"colunas": [...], "linhas": [...]} para a tela
    sql: Optional[str] = None
    erro: Optional[str] = None
    duracao_ms: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class Cache:
    """Resultados compartilhados entre os pesquisadores (e entre perguntas) por alguns minutos."""

    def __init__(self, ttl_segundos: int = 600):
        self.ttl = ttl_segundos
        self._dados: Dict[Tuple, Tuple[float, Any]] = {}
        self._trava = threading.Lock()

    def obter(self, chave: Tuple, calcular: Callable[[], Any]) -> Any:
        with self._trava:
            item = self._dados.get(chave)
            if item and time.time() - item[0] < self.ttl:
                return item[1]
        valor = calcular()
        with self._trava:
            self._dados[chave] = (time.time(), valor)
        return valor


def _tabela_texto(colunas: List[str], linhas: List[List[Any]]) -> str:
    cab = " | ".join(colunas)
    corpo = "\n".join(" | ".join("NULL" if x is None else str(x) for x in linha) for linha in linhas)
    return f"{cab}\n{corpo}" if linhas else f"{cab}\n(nenhuma linha)"


def _cortar(texto: str) -> str:
    return texto if len(texto) <= MAX_CHARS_RESULTADO else texto[:MAX_CHARS_RESULTADO] + "\n…(resultado cortado)"


class Ferramentas:
    definicoes = DEFINICOES

    def __init__(
        self,
        fonte: FonteCatalogo,
        catalogos_liberados: Optional[List[str]] = None,
        max_linhas: int = 200,
        linhas_perfil: int = 100_000,
        cache: Optional[Cache] = None,
    ):
        self.fonte = fonte
        self.liberados = [c.lower() for c in catalogos_liberados or []]
        self.max_linhas = max_linhas
        self.linhas_perfil = linhas_perfil
        self.cache = cache or Cache()

    # ------------------------------------------------------------- escopo
    def _permitido(self, catalogo: str) -> bool:
        return catalogo_visivel(catalogo, self.liberados)

    def _catalogo(self, nome: str) -> str:
        cat = nome_seguro(nome, 1)
        if not self._permitido(cat):
            raise PermissionError(f"o catálogo {cat} não está liberado para o Sabichão")
        return cat

    def _tabela(self, nome: str) -> str:
        tab = nome_seguro(nome, 3)
        self._catalogo(tab.split(".")[0])
        return tab

    # ------------------------------------------------------------ execução
    def executar(self, nome: str, argumentos: Dict[str, Any]) -> Passo:
        inicio = time.perf_counter()
        metodo = getattr(self, f"_f_{nome}", None) if nome in {d["function"]["name"] for d in DEFINICOES} else None
        if metodo is None:
            passo = Passo(nome, argumentos, f"Erro: ferramenta '{nome}' não existe.", erro="ferramenta desconhecida")
        else:
            try:
                passo = metodo(**{k: str(v) for k, v in argumentos.items()})
            except TypeError as e:
                passo = Passo(nome, argumentos, f"Erro nos argumentos: {e}", erro=str(e))
            except (ValueError, PermissionError) as e:
                passo = Passo(nome, argumentos, f"Erro: {e}", erro=str(e))
            except Exception as e:  # noqa: BLE001
                msg = ocultar_segredos(str(e))[:400]
                passo = Passo(nome, argumentos, f"Erro: {msg}", erro=msg)
        passo.argumentos = argumentos
        passo.texto = _cortar(passo.texto)
        passo.duracao_ms = int((time.perf_counter() - inicio) * 1000)
        return passo

    def _f_listar_catalogos(self) -> Passo:
        cats = [c for c in self.cache.obter(("catalogos",), self.fonte.catalogos) if self._permitido(c)]
        return Passo("listar_catalogos", {}, "Catálogos: " + (", ".join(cats) or "(nenhum)"),
                     tabela={"colunas": ["catálogo"], "linhas": [[c] for c in cats]})

    def _f_listar_schemas(self, catalogo: str) -> Passo:
        cat = self._catalogo(catalogo)
        itens = self.cache.obter(("schemas", cat), lambda: self.fonte.schemas(cat))
        linhas = [[s["schema"], s.get("comentario", "")] for s in itens]
        return Passo("listar_schemas", {}, f"Schemas de {cat} ({len(linhas)}):\n" + _tabela_texto(["schema", "comentario"], linhas),
                     tabela={"colunas": ["schema", "comentário"], "linhas": linhas})

    def _f_listar_tabelas(self, catalogo: str, schema: str) -> Passo:
        cat, sch = self._catalogo(catalogo), nome_seguro(schema, 1)
        itens = self.cache.obter(("tabelas", cat, sch), lambda: self.fonte.tabelas(cat, sch))
        linhas = [[t["tabela"], t.get("tipo", ""), t.get("comentario", "")] for t in itens]
        return Passo("listar_tabelas", {}, f"Tabelas de {cat}.{sch} ({len(linhas)}):\n" + _tabela_texto(["tabela", "tipo", "comentario"], linhas),
                     tabela={"colunas": ["tabela", "tipo", "comentário"], "linhas": linhas})

    def _f_buscar_tabelas(self, termo: str) -> Passo:
        termo = termo.strip()
        if not termo:
            raise ValueError("informe um termo para a busca")
        itens = self.cache.obter(("buscar", termo.lower(), tuple(self.liberados)), lambda: self.fonte.buscar(termo, self.liberados))
        itens = [t for t in itens if self._permitido(t.get("catalogo", ""))]
        linhas = [[f"{t['catalogo']}.{t['schema']}.{t['tabela']}", t.get("tipo", ""), t.get("comentario") or "",
                   t.get("coluna_encontrada") or ""] for t in itens]
        texto = (f"Tabelas que citam '{termo}' ({len(linhas)}):\n"
                 + _tabela_texto(["tabela", "tipo", "comentario", "coluna_que_cita"], linhas)
                 if linhas else f"Nenhuma tabela cita '{termo}' no nome, na descrição ou nas colunas.")
        return Passo("buscar_tabelas", {}, texto,
                     tabela={"colunas": ["tabela", "tipo", "comentário", "coluna que cita"], "linhas": linhas})

    def _f_descrever_tabela(self, tabela: str) -> Passo:
        tab = self._tabela(tabela)
        info = self.cache.obter(("info", tab), lambda: self.fonte.info_tabela(tab))
        cab = (f"Tabela {info['tabela']} ({info.get('tipo')}, {info.get('formato')}) · dono {info.get('dono')} · "
               f"criada {info.get('criada_em')} · atualizada {info.get('atualizada_em')}")
        if info.get("comentario"):
            cab += f"\nComentário: {info['comentario']}"
        doc = info.get("documentacao")
        if doc:
            cab += f"\nDocumentação ({rotulo(doc['status'])}): {doc['descricao']}"
            if doc.get("linha"):
                cab += f"\nCada linha representa: {doc['linha']}"
            if doc.get("descricao_schema"):
                cab += f"\nSchema: {doc['descricao_schema']}"
        if info.get("documentacao_erro"):
            cab += f"\n(Não consegui ler a tabela de documentação: {info['documentacao_erro']})"
        nomes = ["coluna", "tipo", "comentario", "particao"]
        linhas = [[c["nome"], c["tipo"], c.get("comentario", ""), "sim" if c.get("particao") else ""] for c in info["colunas"]]
        if doc:
            # Comentário do UC e documentação lado a lado, com o status da revisão de cada coluna.
            nomes = ["coluna", "tipo", "comentario_uc", "documentacao", "status_revisao", "particao"]
            linhas = [[c["nome"], c["tipo"], c.get("comentario_uc", c.get("comentario", "")), c.get("documentacao", ""),
                       c.get("status_doc", ""), "sim" if c.get("particao") else ""] for c in info["colunas"]]
        return Passo("descrever_tabela", {}, cab + f"\nColunas ({len(linhas)}):\n" + _tabela_texto(nomes, linhas),
                     tabela={"colunas": [n.replace("_", " ") for n in nomes], "linhas": linhas})

    def _f_estatisticas_tabela(self, tabela: str) -> Passo:
        tab = self._tabela(tabela)
        perfil = self.cache.obter(("perfil", tab, self.linhas_perfil), lambda: perfilar_tabela(self.fonte, tab, self.linhas_perfil))
        return Passo("estatisticas_tabela", {}, perfil_texto(perfil), tabela=perfil.tabela_colunas(),
                     erro="; ".join(perfil.erros) or None)

    def _f_executar_sql(self, sql: str) -> Passo:
        try:
            seguro = validar_sql(sql, self._permitido, self.max_linhas)
        except SQLNaoPermitido as e:
            return Passo("executar_sql", {}, f"SQL bloqueado: {e}", sql=sql, erro=str(e))
        r = self.fonte.consultar(seguro)
        if r.erro:
            return Passo("executar_sql", {}, f"Erro no SQL: {r.erro}\nCorrija e tente de novo.", sql=seguro, erro=r.erro)
        aviso = f"\n(limitado a {self.max_linhas} linhas)" if len(r.linhas) >= self.max_linhas or r.truncado else ""
        return Passo("executar_sql", {}, f"{len(r.linhas)} linha(s):\n" + _tabela_texto(r.colunas, r.linhas) + aviso,
                     tabela={"colunas": r.colunas, "linhas": r.linhas}, sql=seguro)
