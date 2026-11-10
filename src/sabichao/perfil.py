"""Perfil estatístico de uma tabela: volume, nulos, distintos, mín/máx, média, desvio, quartis,
valores mais comuns e uma amostra. É o que responde "me fale sobre a tabela X"."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from .catalogo import FonteCatalogo
from .seguranca import citar

MAX_COLUNAS = 60          # colunas perfiladas por tabela
MAX_COLUNAS_TOP = 6       # colunas categóricas com "valores mais comuns"
MAX_DISTINTOS_TOP = 50    # só mostra valores mais comuns se a coluna tiver até isso de distintos
MAX_CHARS_VALOR = 80

TIPOS_NUMERICOS = ("tinyint", "smallint", "int", "bigint", "float", "double", "decimal", "byte", "short", "long", "real")
TIPOS_COMPLEXOS = ("array", "struct", "map", "binary", "variant", "geography", "geometry", "interval")
TIPOS_CATEGORICOS = ("string", "varchar", "char", "boolean")


@dataclass
class PerfilColuna:
    nome: str
    tipo: str
    comentario: str = ""
    nulos_pct: Optional[float] = None
    distintos: Optional[int] = None
    minimo: Any = None
    maximo: Any = None
    media: Optional[float] = None
    desvio: Optional[float] = None
    p25: Any = None
    mediana: Any = None
    p75: Any = None
    top_valores: List[List[Any]] = field(default_factory=list)


@dataclass
class PerfilTabela:
    tabela: str
    tipo: str = ""
    formato: str = ""
    comentario: str = ""
    dono: Optional[str] = None
    criada_em: Optional[str] = None
    atualizada_em: Optional[str] = None
    total_linhas: Optional[int] = None
    linhas_analisadas: Optional[int] = None
    tamanho_bytes: Optional[int] = None
    arquivos: Optional[int] = None
    particoes: List[str] = field(default_factory=list)
    colunas: List[PerfilColuna] = field(default_factory=list)
    amostra_colunas: List[str] = field(default_factory=list)
    amostra: List[List[Any]] = field(default_factory=list)
    erros: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def tabela_colunas(self) -> Dict[str, Any]:
        """As estatísticas por coluna como tabela (para a tela)."""
        cab = ["coluna", "tipo", "nulos %", "distintos", "mín", "máx", "média", "desvio", "p25", "mediana", "p75", "valores mais comuns"]
        linhas = [
            [c.nome, c.tipo, c.nulos_pct, c.distintos, c.minimo, c.maximo, _arred(c.media), _arred(c.desvio),
             c.p25, c.mediana, c.p75, ", ".join(f"{v} ({n})" for v, n in c.top_valores)]
            for c in self.colunas
        ]
        return {"colunas": cab, "linhas": linhas}


def _q(nome: str) -> str:
    return "`" + nome.replace("`", "``") + "`"


def _curto(v: Any) -> Any:
    if v is None or isinstance(v, (int, float, bool)):
        return v
    s = str(v)
    return s if len(s) <= MAX_CHARS_VALOR else s[: MAX_CHARS_VALOR - 1] + "…"


def _num(v: Any) -> Any:
    """O SQL Warehouse devolve tudo como texto; converte números."""
    if v is None or isinstance(v, (int, float)):
        return v
    try:
        f = float(v)
        return int(f) if f.is_integer() else f
    except (TypeError, ValueError):
        return v


def _arred(v: Any) -> Any:
    return round(v, 4) if isinstance(v, float) else v


def _eh(tipo: str, prefixos) -> bool:
    return tipo.lower().startswith(prefixos)


def perfilar_tabela(fonte: FonteCatalogo, tabela: str, linhas_perfil: int = 100_000) -> PerfilTabela:
    """Estatísticas sobre as primeiras `linhas_perfil` linhas (a contagem total é sempre exata)."""
    p = PerfilTabela(tabela=tabela)
    try:
        ref = citar(tabela)  # nome validado e entre crases: é o único texto variável nos SQLs abaixo
        info = fonte.info_tabela(tabela)
    except Exception as e:  # noqa: BLE001
        p.erros.append(f"não foi possível ler a tabela: {e}")
        return p
    p.tipo, p.formato, p.comentario = info.get("tipo", ""), info.get("formato", ""), info.get("comentario", "")
    p.dono, p.criada_em, p.atualizada_em = info.get("dono"), info.get("criada_em"), info.get("atualizada_em")
    p.particoes = [c["nome"] for c in info.get("colunas", []) if c.get("particao")]
    cols = info.get("colunas", [])
    p.colunas = [PerfilColuna(c["nome"], c["tipo"], c.get("comentario", "")) for c in cols[:MAX_COLUNAS]]
    if len(cols) > MAX_COLUNAS:
        p.erros.append(f"tabela com {len(cols)} colunas; estatísticas das {MAX_COLUNAS} primeiras")

    r = fonte.consultar(f"SELECT count(*) AS n FROM {ref}")  # nosec B608
    if r.erro:
        p.erros.append(f"contagem: {r.erro}")
    elif r.linhas:
        p.total_linhas = int(_num(r.linhas[0][0]))

    if p.formato.upper() == "DELTA":
        r = fonte.consultar(f"DESCRIBE DETAIL {ref}")
        if not r.erro and r.linhas:
            d = dict(zip(r.colunas, r.linhas[0]))
            p.tamanho_bytes, p.arquivos = _num(d.get("sizeInBytes")), _num(d.get("numFiles"))

    amostrada = p.total_linhas is None or p.total_linhas > linhas_perfil
    base = f"(SELECT * FROM {ref} LIMIT {int(linhas_perfil)}) AS base" if amostrada else ref  # nosec B608
    simples = [c for c in p.colunas if not _eh(c.tipo, TIPOS_COMPLEXOS)]
    if simples:
        partes = ["count(*) AS total"]
        for i, c in enumerate(simples):
            col = _q(c.nome)
            partes += [f"count({col}) AS nn_{i}", f"approx_count_distinct({col}) AS d_{i}",
                       f"min({col}) AS mn_{i}", f"max({col}) AS mx_{i}"]
            if _eh(c.tipo, TIPOS_NUMERICOS):
                partes += [f"avg({col}) AS av_{i}", f"stddev({col}) AS dp_{i}",
                           f"percentile_approx({col}, 0.25) AS p25_{i}", f"percentile_approx({col}, 0.5) AS p50_{i}",
                           f"percentile_approx({col}, 0.75) AS p75_{i}"]
        r = fonte.consultar(f"SELECT {', '.join(partes)} FROM {base}")  # nosec B608
        if r.erro:
            p.erros.append(f"estatísticas: {r.erro}")
        elif r.linhas:
            v = dict(zip(r.colunas, r.linhas[0]))
            total = _num(v.get("total")) or 0
            p.linhas_analisadas = total
            for i, c in enumerate(simples):
                nn = _num(v.get(f"nn_{i}")) or 0
                c.nulos_pct = round(100 * (1 - nn / total), 1) if total else None
                c.distintos = _num(v.get(f"d_{i}"))
                c.minimo, c.maximo = _curto(v.get(f"mn_{i}")), _curto(v.get(f"mx_{i}"))
                if _eh(c.tipo, TIPOS_NUMERICOS):
                    c.media, c.desvio = _num(v.get(f"av_{i}")), _num(v.get(f"dp_{i}"))
                    c.p25, c.mediana, c.p75 = _num(v.get(f"p25_{i}")), _num(v.get(f"p50_{i}")), _num(v.get(f"p75_{i}"))

    categoricas = [
        c for c in simples
        if _eh(c.tipo, TIPOS_CATEGORICOS) and c.distintos is not None and 0 < c.distintos <= MAX_DISTINTOS_TOP
    ][:MAX_COLUNAS_TOP]
    for c in categoricas:
        r = fonte.consultar(
            f"SELECT {_q(c.nome)} AS valor, count(*) AS n FROM {base} GROUP BY {_q(c.nome)} ORDER BY n DESC LIMIT 10"  # nosec B608
        )
        if not r.erro:
            c.top_valores = [[_curto(a), _num(b)] for a, b in r.linhas]

    r = fonte.consultar(f"SELECT * FROM {ref} LIMIT 5")  # nosec B608
    if r.erro:
        p.erros.append(f"amostra: {r.erro}")
    else:
        p.amostra_colunas = r.colunas
        p.amostra = [[_curto(x) for x in linha] for linha in r.linhas]
    return p


def perfil_texto(p: PerfilTabela) -> str:
    """O perfil em texto compacto, para a IA ler."""
    linhas = [f"TABELA {p.tabela}" + (f" — {p.comentario}" if p.comentario else "")]
    meta = [f"{k}: {v}" for k, v in (
        ("tipo", p.tipo), ("formato", p.formato), ("dono", p.dono), ("criada em", p.criada_em),
        ("atualizada em", p.atualizada_em), ("linhas", p.total_linhas), ("tamanho (bytes)", p.tamanho_bytes),
        ("arquivos", p.arquivos), ("partições", ", ".join(p.particoes)),
    ) if v not in (None, "")]
    linhas.append("; ".join(meta))
    if p.linhas_analisadas is not None and p.total_linhas is not None and p.linhas_analisadas < p.total_linhas:
        linhas.append(f"(estatísticas calculadas sobre as primeiras {p.linhas_analisadas} linhas)")
    linhas.append("Colunas:")
    for c in p.colunas:
        info = [c.tipo]
        if c.comentario:
            info.append(f'comentário: "{c.comentario}"')
        if c.nulos_pct is not None:
            info.append(f"nulos {c.nulos_pct}%")
        if c.distintos is not None:
            info.append(f"~{c.distintos} distintos")
        if c.minimo is not None or c.maximo is not None:
            info.append(f"mín={c.minimo} máx={c.maximo}")
        if c.media is not None:
            info.append(f"média={_arred(c.media)} desvio={_arred(c.desvio)} p25={c.p25} mediana={c.mediana} p75={c.p75}")
        if c.top_valores:
            info.append("valores: " + ", ".join(f"{v} ({n})" for v, n in c.top_valores))
        linhas.append(f"  - {c.nome}: " + "; ".join(str(x) for x in info))
    if p.amostra:
        linhas.append("Amostra:")
        linhas.append("  " + " | ".join(p.amostra_colunas))
        linhas += ["  " + " | ".join("NULL" if x is None else str(x) for x in linha) for linha in p.amostra]
    if p.erros:
        linhas.append("Observações: " + "; ".join(p.erros))
    return "\n".join(linhas)
