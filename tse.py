"""Coleta e agregação dos resultados da apuração presidencial publicados pelo TSE."""

import zlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pandas as pd

BASE = "https://resultados.tse.jus.br/oficial"
FUSO = ZoneInfo("America/Sao_Paulo")

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

# Abaixo disso a apuração é inicial demais para indicar tendência; a partir de PLENO os números são confiáveis.
MIN_APURADO = 5
PLENO_APURADO = 30

CORES_CANDIDATOS = {"LULA": "#d62728", "FLAVIO": "#1f5fbf"}
PALETA_OUTROS = ["#2ca02c", "#9467bd", "#ff7f0e", "#8c564b", "#17becf", "#bcbd22", "#e377c2", "#7f7f7f"]


class FormatoInesperado(Exception):
    """Os arquivos do TSE não têm a estrutura que o painel espera."""


@dataclass
class Apuracao:
    votos: pd.DataFrame
    situacao: pd.DataFrame
    publicado_em: datetime | None
    ausentes: list[str]  # UFs cujo arquivo o TSE ainda não publicou


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
    if r.status_code == 404:
        return None  # ainda não publicado
    r.raise_for_status()
    return r.json()


def _int(valor: str | None) -> int:
    return int(valor or 0)


def _candidatos(dados: dict) -> list[dict]:
    return [
        {
            "numero": c["n"],
            "candidato": c["nmu"],
            "partido": par["sg"],
            "votos": _int(c["vap"]),
            # "Eleito", "2º turno", "Não eleito" ou vazio enquanto não definido
            "status": c["st"] if c["e"] == "s" else "",
        }
        for agr in dados["carg"][0]["agr"]
        for par in agr["par"]
        for c in par["cand"]
    ]


def _parse(resultados: dict[str, dict | None]) -> Apuracao:
    votos, situacao = [], []
    for uf, dados in resultados.items():
        if dados is None:
            continue
        regiao = UF_REGIAO.get(uf, "Brasil")
        for c in _candidatos(dados):
            votos.append({"uf": uf.upper(), "regiao": regiao, **c})
        s, v, e = dados["s"], dados["v"], dados["e"]
        situacao.append(
            {
                "uf": uf.upper(),
                "regiao": regiao,
                "secoes_totais": _int(s["ts"]),
                "secoes_totalizadas": _int(s["st"]),
                "brancos": _int(v["vb"]),
                "nulos": _int(v["tvn"]),
                "validos": _int(v["vv"]),
                "eleitorado": _int(e["te"]),
                "eleitorado_apurado": _int(e["est"]),
            }
        )
    br = resultados["br"]
    publicado = None
    if br.get("dg") and br.get("hg"):
        publicado = datetime.strptime(f"{br['dg']} {br['hg']}", "%d/%m/%Y %H:%M:%S").replace(tzinfo=FUSO)
    ausentes = [uf.upper() for uf, d in resultados.items() if d is None]
    return Apuracao(pd.DataFrame(votos), pd.DataFrame(situacao), publicado, ausentes)


def coletar(eleicao: dict) -> Apuracao:
    """Baixa Brasil + 27 UFs + exterior. Levanta exceção se o arquivo nacional não existir ou mudar de formato."""
    codigos = ["br", EXTERIOR, *UF_REGIAO.keys() - {EXTERIOR}]
    with httpx.Client(timeout=20) as client, ThreadPoolExecutor(10) as pool:
        resultados = dict(zip(codigos, pool.map(lambda uf: _baixar(client, eleicao, uf), codigos)))
    if resultados["br"] is None:
        raise FileNotFoundError("O TSE ainda não publicou o resultado nacional deste turno.")
    try:
        return _parse(resultados)
    except (KeyError, IndexError, TypeError, ValueError) as e:
        raise FormatoInesperado(f"formato dos dados do TSE mudou ({type(e).__name__}: {e})") from e


def resumir(votos: pd.DataFrame, por: str | None) -> pd.DataFrame:
    """Votos e % (sobre votos válidos) por candidato, opcionalmente agrupado por `por`."""
    chaves = ([por] if por else []) + ["candidato", "partido"]
    df = votos.groupby(chaves, as_index=False)["votos"].sum()
    grupo = df.groupby(por)["votos"].transform("sum") if por else pd.Series(df["votos"].sum(), index=df.index)
    df["pct"] = (df["votos"] / grupo.where(grupo > 0) * 100).fillna(0)
    return df


def projecao(total: pd.DataFrame, br: pd.Series) -> dict | None:
    """Estimativa de votos restantes e do que o 2º colocado precisaria para virar.

    Supõe que as seções ainda não apuradas têm o mesmo comparecimento e % de válidos das já apuradas.
    """
    if len(total) < 2 or br.eleitorado_apurado <= 0:
        return None
    ordenado = total.sort_values("votos", ascending=False)
    lider, segundo = ordenado.iloc[0], ordenado.iloc[1]
    vantagem = int(lider.votos - segundo.votos)
    restantes = (br.eleitorado - br.eleitorado_apurado) * br.validos / br.eleitorado_apurado
    precisa = 0.5 * (1 + vantagem / restantes) if restantes > 0 else float("inf")
    return {
        "lider": lider.candidato,
        "segundo": segundo.candidato,
        "vantagem": vantagem,
        "restantes": int(restantes),
        "precisa": precisa,  # fração dos votos restantes que o 2º precisaria; >= 1 significa irreversível
    }
