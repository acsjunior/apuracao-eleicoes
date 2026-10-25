"""Mapa do Brasil (coropleto por estado ou região) com o andamento da apuração."""

import json
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go

import tse

CODIGO_UF = {
    "11": "RO", "12": "AC", "13": "AM", "14": "RR", "15": "PA", "16": "AP", "17": "TO",
    "21": "MA", "22": "PI", "23": "CE", "24": "RN", "25": "PB", "26": "PE", "27": "AL",
    "28": "SE", "29": "BA", "31": "MG", "32": "ES", "33": "RJ", "35": "SP",
    "41": "PR", "42": "SC", "43": "RS", "50": "MS", "51": "MT", "52": "GO", "53": "DF",
}  # fmt: skip

GEOJSON = json.loads((Path(__file__).parent / "data" / "ufs.geojson").read_text())
for feature in GEOJSON["features"]:
    feature["id"] = CODIGO_UF[feature["properties"]["codarea"]]
    # A malha do IBGE tem o sentido dos anéis oposto ao que o Plotly espera; sem inverter, o fundo é pintado.
    geometria = feature["geometry"]
    if geometria["type"] == "Polygon":
        geometria["coordinates"] = [anel[::-1] for anel in geometria["coordinates"]]
    else:
        geometria["coordinates"] = [[anel[::-1] for anel in poligono] for poligono in geometria["coordinates"]]

METRICAS = ["Líder e margem", "% de um candidato", "% apurado"]
MARGEM_MAX = 50  # diferença (p.p.) a partir da qual a cor satura
COR_VAZIO = "#e5e7eb"


def _milhar(n: float) -> str:
    return f"{int(n):,}".replace(",", ".")


def unidades(votos: pd.DataFrame, situacao: pd.DataFrame, nivel: str):
    """Votos, % e avanço da apuração por estado ou região (sem Brasil e Exterior)."""
    chave = "uf" if nivel == "Estado" else "regiao"
    regioes = list(tse.REGIOES)
    r = tse.resumir(votos[votos.regiao.isin(regioes)], chave)
    pct = r.pivot(index=chave, columns="candidato", values="pct")
    nvotos = r.pivot(index=chave, columns="candidato", values="votos")
    s = situacao[situacao.regiao.isin(regioes)].groupby(chave)[["secoes_totais", "secoes_totalizadas"]].sum()
    apurado = (s.secoes_totalizadas / s.secoes_totais.where(s.secoes_totais > 0) * 100).fillna(0)
    return chave, pct, nvotos, apurado, s.secoes_totais - s.secoes_totalizadas


def figura(votos: pd.DataFrame, situacao: pd.DataFrame, nivel: str, metrica: str, candidato: str | None) -> go.Figure:
    chave, pct, nvotos, apurado, faltam = unidades(votos, situacao, nivel)
    ufs = list(CODIGO_UF.values())
    unidade = {uf: uf if chave == "uf" else tse.UF_REGIAO[uf.lower()] for uf in ufs}
    sem_votos = nvotos.sum(axis=1) == 0

    textos = []
    for uf in ufs:
        u = unidade[uf]
        titulo = uf if chave == "uf" else f"Região {u} ({uf})"
        linhas = [f"{c}: {_milhar(nvotos.loc[u, c])} ({pct.loc[u, c]:.2f}%)" for c in pct.columns]
        linhas.append(f"Apurado: {apurado[u]:.2f}% · faltam {_milhar(faltam[u])} seções")
        textos.append(f"<b>{titulo}</b><br>" + "<br>".join(linhas))

    if metrica == "% apurado":
        z = [apurado[unidade[uf]] for uf in ufs]
        escala, zmin, zmax = [[0, "#f0fdf4"], [1, "#15803d"]], 0, 100
        colorbar = dict(title="% apurado")
    elif metrica == "% de um candidato":
        z = [None if sem_votos[unidade[uf]] else pct.loc[unidade[uf], candidato] for uf in ufs]
        escala, zmin, zmax = [[0, "#ffffff"], [1, tse.cor(candidato)]], 0, 100
        colorbar = dict(title=f"% {candidato}")
    else:
        # Dois líderes nacionais, em ordem alfabética para as cores não trocarem durante a apuração.
        a, b = sorted(nvotos.sum().nlargest(2).index)
        z = [None if sem_votos[unidade[uf]] else pct.loc[unidade[uf], a] - pct.loc[unidade[uf], b] for uf in ufs]
        escala, zmin, zmax = [[0, tse.cor(b)], [0.5, "#ffffff"], [1, tse.cor(a)]], -MARGEM_MAX, MARGEM_MAX
        colorbar = dict(
            title="Vantagem (p.p.)",
            tickvals=[-MARGEM_MAX, 0, MARGEM_MAX],
            ticktext=[f"{b} +{MARGEM_MAX}", "empate", f"{a} +{MARGEM_MAX}"],
        )

    colorbar |= dict(thickness=14, len=0.7)
    comum = dict(geojson=GEOJSON, locations=ufs, customdata=textos, hovertemplate="%{customdata}<extra></extra>")
    linha = dict(color="white", width=0.8)
    fig = go.Figure(
        [
            go.Choropleth(z=[0] * len(ufs), colorscale=[[0, COR_VAZIO], [1, COR_VAZIO]], showscale=False, marker_line=linha, **comum),
            go.Choropleth(z=z, colorscale=escala, zmin=zmin, zmax=zmax, colorbar=colorbar, marker_line=linha, **comum),
        ]
    )
    fig.update_geos(
        projection_type="mercator",
        lonaxis_range=[-74, -34],
        lataxis_range=[-34, 6],
        visible=False,
        bgcolor="rgba(0,0,0,0)",
    )
    fig.update_layout(height=620, margin=dict(l=0, r=0, t=0, b=0), paper_bgcolor="rgba(0,0,0,0)")
    return fig
