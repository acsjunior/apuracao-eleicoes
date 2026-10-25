"""Coleta e agregação dos resultados da apuração presidencial publicados pelo TSE."""

import zlib
from concurrent.futures import ThreadPoolExecutor

import httpx
import pandas as pd

BASE = "https://resultados.tse.jus.br/oficial"

REGIOES = {
    "Norte": ["ac", "ap", "am", "pa", "ro", "rr", "to"],
    "Nordeste": ["al", "ba", "ce", "ma", "pb", "pe", "pi", "rn", "se"],
    "Centro-Oeste": ["df", "go", "mt", "ms"],
    "Sudeste": ["es", "mg", "rj", "sp"],
    "Sul": ["pr", "rs", "sc"],
}
EXTERIOR = "zz"
UF_REGIAO = {uf: regiao for regiao, ufs in REGIOES.items() for uf in ufs}
UF_REGIAO[EXTERIOR] = "Exterior"


CORES_CANDIDATOS = {"LULA": "#d62728", "FLAVIO": "#1f5fbf"}
PALETA_OUTROS = ["#2ca02c", "#9467bd", "#ff7f0e", "#8c564b", "#17becf", "#bcbd22", "#e377c2", "#7f7f7f"]


def cor(candidato: str) -> str:
    """Cor fixa do candidato (vermelho Lula, azul Flávio); os demais recebem uma cor estável da paleta."""
    nome = candidato.upper()
    for chave, hex_ in CORES_CANDIDATOS.items():
        if chave in nome:
            return hex_
    return PALETA_OUTROS[zlib.crc32(nome.encode()) % len(PALETA_OUTROS)]


def descobrir_eleicoes(client: httpx.Client) -> list[dict]:
    """Retorna as eleições presidenciais (turnos) do ciclo mais recente."""
    config = client.get(f"{BASE}/comum/config/ele-c.json").json()
    ciclo = config["pl"][0]
    federal = next(e for e in ciclo["e"] if e["tp"] == "8")
    turnos = [{"turno": 1, "cd": federal["cd"]}]
    if federal["cdt2"]:
        turnos.append({"turno": 2, "cd": federal["cdt2"]})
    return [{**t, "ciclo": ciclo["c"]} for t in turnos]


def _baixar(client: httpx.Client, eleicao: dict, uf: str) -> dict | None:
    cd = int(eleicao["cd"])
    url = f"{BASE}/{eleicao['ciclo']}/{cd}/dados/{uf}/{uf}-c0001-e{cd:06d}-u.json"
    r = client.get(url, headers={"Cache-Control": "no-cache"})
    return r.json() if r.status_code == 200 else None


def _int(valor: str | None) -> int:
    return int(valor or 0)


def _candidatos(dados: dict) -> list[dict]:
    return [
        {"numero": c["n"], "candidato": c["nmu"], "partido": par["sg"], "votos": _int(c["vap"])}
        for agr in dados["carg"][0]["agr"]
        for par in agr["par"]
        for c in par["cand"]
    ]


def coletar(eleicao: dict) -> tuple[pd.DataFrame, pd.DataFrame, str]:
    """Baixa Brasil + 27 UFs + exterior.

    Retorna (votos por candidato por UF, situação da apuração por UF, horário do TSE).
    """
    codigos = ["br", EXTERIOR, *UF_REGIAO.keys() - {EXTERIOR}]
    with httpx.Client(timeout=20) as client, ThreadPoolExecutor(10) as pool:
        resultados = dict(zip(codigos, pool.map(lambda uf: _baixar(client, eleicao, uf), codigos)))

    votos, situacao = [], []
    for uf, dados in resultados.items():
        if dados is None:
            continue
        for c in _candidatos(dados):
            votos.append({"uf": uf.upper(), "regiao": UF_REGIAO.get(uf, "Brasil"), **c})
        s, v = dados["s"], dados["v"]
        situacao.append(
            {
                "uf": uf.upper(),
                "regiao": UF_REGIAO.get(uf, "Brasil"),
                "secoes_totais": _int(s["ts"]),
                "secoes_totalizadas": _int(s["st"]),
                "brancos": _int(v["vb"]),
                "nulos": _int(v["tvn"]),
            }
        )

    horario = resultados["br"]["hg"] if resultados.get("br") else ""
    data = resultados["br"]["dg"] if resultados.get("br") else ""
    return pd.DataFrame(votos), pd.DataFrame(situacao), f"{data} {horario}".strip()


def resumir(votos: pd.DataFrame, por: str | None) -> pd.DataFrame:
    """Votos e % (sobre votos válidos) por candidato, opcionalmente agrupado por `por`."""
    chaves = ([por] if por else []) + ["candidato", "partido"]
    df = votos.groupby(chaves, as_index=False)["votos"].sum()
    grupo = df.groupby(por)["votos"].transform("sum") if por else pd.Series(df["votos"].sum(), index=df.index)
    df["pct"] = (df["votos"] / grupo.where(grupo > 0) * 100).fillna(0)
    return df
