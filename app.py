import html
from datetime import datetime

import httpx
import pandas as pd
import streamlit as st

import mapa
import tse

st.set_page_config(page_title="Apuração Presidencial", page_icon="🗳️", layout="wide")

@st.cache_data(ttl=3600)
def eleicoes() -> list[dict]:
    with httpx.Client(timeout=20) as client:
        return tse.descobrir_eleicoes(client)


@st.cache_data(ttl=30, show_spinner=False)
def coletar(turno: dict) -> tuple[tse.Apuracao, datetime]:
    """Uma única consulta ao TSE a cada 30s, compartilhada por todos os visitantes."""
    return tse.coletar(turno), datetime.now(tse.FUSO)


@st.cache_resource
def _ultimo_ok() -> dict:
    return {}


def obter(turno: dict) -> tuple[tuple[tse.Apuracao, datetime], Exception | None]:
    """Dados do TSE; se a consulta falhar, devolve o último resultado bom junto com o erro."""
    try:
        resultado = coletar(turno)
    except Exception as e:  # noqa: BLE001 - qualquer falha cai no último dado bom, se houver
        guardado = _ultimo_ok().get(turno["cd"])
        if guardado is None:
            raise
        return guardado, e
    _ultimo_ok()[turno["cd"]] = resultado
    return resultado, None


def fmt_int(n: float) -> str:
    return f"{int(n):,}".replace(",", ".")


def fmt_pct(x: float, casas: int = 2) -> str:
    return f"{x:.{casas}f}%"


def idade(minutos: float) -> str:
    if minutos < 1:
        return "agora há pouco"
    if minutos < 60:
        return f"há {int(minutos)} min"
    return f"há {int(minutos // 60)}h{int(minutos % 60):02d}"


def descricao_falha(e: Exception) -> str:
    if isinstance(e, tse.FormatoInesperado):
        return str(e)
    if isinstance(e, httpx.HTTPError):
        return "sem conexão com o TSE"
    return f"{type(e).__name__}: {e}"


def destaque(votos: pd.DataFrame, br: pd.Series, pct_secoes: float, turno: dict) -> None:
    """Quem lidera, situação oficial (eleito / 2º turno) e, no 2º turno, a estimativa do que falta."""
    nacional = votos[votos.regiao == "Brasil"]
    total = tse.resumir(nacional, None).sort_values("votos", ascending=False)
    status = dict(zip(nacional.candidato, nacional.status))
    definidos = [c for c in total.candidato if status.get(c) not in (None, "", "Não eleito")]

    def nome(c: str) -> str:
        return f"<span style='color:{tse.cor(c)}'>{html.escape(c)}</span>"

    with st.container(border=True):
        if definidos:
            for c in definidos:
                st.markdown(f"### {'🏆' if status[c] == 'Eleito' else '➡️'} {nome(c)} — {status[c]}", unsafe_allow_html=True)
            st.caption("Situação divulgada pelo TSE.")
            return
        if total.votos.sum() == 0 or len(total) < 2:
            st.markdown("### Aguardando os primeiros resultados")
            return
        lider, segundo = total.iloc[0], total.iloc[1]
        vantagem = int(lider.votos - segundo.votos)
        if vantagem == 0:
            st.markdown("### Empate técnico entre os dois primeiros colocados")
        else:
            st.markdown(
                f"### {nome(lider.candidato)} lidera por {fmt_int(vantagem)} votos ({lider.pct - segundo.pct:.2f} p.p. sobre {nome(segundo.candidato)})",
                unsafe_allow_html=True,
            )
        if turno["turno"] != 2:
            return
        if pct_secoes < tse.PLENO_APURADO:
            st.caption(f"A estimativa de votos restantes aparece a partir de {tse.PLENO_APURADO}% das seções apuradas.")
            return
        proj = tse.projecao(total, br)
        if proj is None:
            return
        c1, c2, c3 = st.columns(3)
        c1.metric("Votos válidos restantes (estimativa)", fmt_int(proj["restantes"]))
        if proj["precisa"] >= 1:
            c2.metric(f"Para virar, {proj['segundo']} precisaria de", "mais votos do que restam")
            c3.metric("Situação (estimativa)", "Vantagem irreversível")
        else:
            c2.metric(f"Para virar, {proj['segundo']} precisa de", f"{proj['precisa'] * 100:.1f}% dos restantes")
        st.caption(
            "Estimativa própria, não oficial: supõe que as urnas ainda não apuradas têm o mesmo comparecimento e a mesma "
            "proporção de votos válidos das já apuradas."
        )


def cartoes(df: pd.DataFrame) -> None:
    df = df.sort_values("votos", ascending=False).head(4)
    for col, (_, c) in zip(st.columns(len(df)), df.iterrows()):
        col.metric(f"{c.candidato} ({c.partido})", fmt_int(c.votos), fmt_pct(c.pct), delta_color="off")


def pivot(df: pd.DataFrame, chave: str, ordem: list[str] | None = None) -> pd.DataFrame:
    """Uma linha por região/UF, com votos e % de cada candidato lado a lado."""
    largo = df.pivot(index=chave, columns="candidato", values=["votos", "pct"])
    largo.columns = [f"{cand} ({'votos' if m == 'votos' else '%'})" for m, cand in largo.columns]
    largo = largo.reset_index()
    if ordem:
        largo = largo.set_index(chave).reindex(ordem).dropna(how="all").reset_index()
    return largo


def progresso(situacao: pd.DataFrame, chave: str) -> pd.DataFrame:
    """Avanço da apuração (em seções) por região ou UF."""
    ap = situacao[situacao.uf != "BR"].groupby(chave, as_index=False)[["secoes_totais", "secoes_totalizadas"]].sum()
    ap["% apurado"] = ap.secoes_totalizadas / ap.secoes_totais.where(ap.secoes_totais > 0) * 100
    ap["seções que faltam"] = ap.secoes_totais - ap.secoes_totalizadas
    return ap[[chave, "% apurado", "seções que faltam"]].fillna(0)


COR_APURADO = "#15803d"
ESTILO_TABELA = """
<style>
.apu { max-height: %spx; overflow: auto; }
.apu table { border-collapse: collapse; width: 100%%; font-size: 0.9rem; }
.apu th, .apu td { padding: 6px 10px; border-bottom: 1px solid rgba(128,128,128,.25); text-align: right; white-space: nowrap; }
.apu th { position: sticky; top: 0; background: var(--background-color, #fff); z-index: 1; }
.apu th:first-child, .apu td:first-child { text-align: left; font-weight: 600; }
.apu .barra { display: flex; align-items: center; gap: 6px; min-width: 100px; }
.apu .trilho { flex: 1; height: 14px; border-radius: 3px; background: rgba(128,128,128,.2); overflow: hidden; }
.apu .preenchido { height: 100%%; }
.apu .barra span { width: 52px; text-align: right; }
</style>
"""


def _barra(pct: float, cor: str) -> str:
    return (
        f'<div class="barra"><div class="trilho"><div class="preenchido" style="width:{min(pct, 100):.2f}%;background:{cor}"></div></div>'
        f"<span>{pct:.2f}%</span></div>"
    )


def tabela_html(df: pd.DataFrame, chave: str, rotulo: str, altura: int = 520) -> None:
    """Tabela com barras na cor de cada candidato (o st.dataframe não aceita cor por coluna)."""
    candidatos = [c.removesuffix(" (votos)") for c in df.columns if c.endswith(" (votos)")]
    cabecalho = f"<th>{rotulo}</th>"
    for c in candidatos:
        cabecalho += f'<th style="color:{tse.cor(c)}">{html.escape(c)}<br>votos</th><th style="color:{tse.cor(c)}">%</th>'
    cabecalho += "<th>% apurado</th><th>Seções que faltam</th>"

    linhas = ""
    for _, r in df.iterrows():
        linhas += f"<tr><td>{html.escape(str(r[chave]))}</td>"
        for c in candidatos:
            linhas += f"<td>{int(r[f'{c} (votos)']):,}</td>".replace(",", ".")
            linhas += f"<td>{_barra(r[f'{c} (%)'], tse.cor(c))}</td>"
        linhas += f"<td>{_barra(r['% apurado'], COR_APURADO)}</td><td>{int(r['seções que faltam']):,}</td></tr>".replace(",", ".")

    st.html(ESTILO_TABELA % altura + f'<div class="apu"><table><thead><tr>{cabecalho}</tr></thead><tbody>{linhas}</tbody></table></div>')


with st.sidebar:
    st.header("Configuração")
    turnos = eleicoes()
    turno = st.radio("Turno", turnos, index=len(turnos) - 1, format_func=lambda t: f"{t['turno']}º turno")
    intervalo = st.slider("Atualizar a cada (s)", 15, 300, 60, step=15)
    st.caption("Fonte: resultados.tse.jus.br (dados públicos do TSE)")


@st.fragment(run_every=intervalo)
def painel() -> None:
    try:
        (apuracao, consultado_em), falha = obter(turno)
    except FileNotFoundError as e:
        st.warning(str(e))
        return
    except Exception as e:  # noqa: BLE001 - sem dado anterior para exibir, mostrar a falha
        st.error(f"Não foi possível obter os dados do TSE: {descricao_falha(e)}")
        return
    votos, situacao = apuracao.votos, apuracao.situacao

    br = situacao[situacao.uf == "BR"].iloc[0]
    pct_secoes = br.secoes_totalizadas / br.secoes_totais * 100 if br.secoes_totais else 0
    agora = datetime.now(tse.FUSO)

    st.title(f"🗳️ Apuração Presidencial — {turno['turno']}º turno")
    if apuracao.publicado_em:
        minutos = (agora - apuracao.publicado_em).total_seconds() / 60
        st.caption(
            f"Atualização do TSE: {apuracao.publicado_em:%d/%m/%Y %H:%M:%S} ({idade(minutos)})  ·  "
            f"consultado às {consultado_em:%H:%M:%S}, a cada {intervalo}s"
        )
        if pct_secoes < 100 and minutos > 20:
            st.warning(
                f"O TSE não publica uma atualização {idade(minutos)} (última às {apuracao.publicado_em:%H:%M}). "
                "Os números abaixo podem estar desatualizados."
            )
    if falha:
        st.warning(
            f"⚠ Não consegui consultar o TSE agora ({descricao_falha(falha)}). "
            f"Exibindo o último resultado obtido às {consultado_em:%H:%M:%S}."
        )
    if apuracao.ausentes:
        st.warning(
            "O TSE ainda não publicou os arquivos de: " + ", ".join(apuracao.ausentes)
            + ". Os totais por região e por estado podem estar incompletos (o total do Brasil vem do arquivo nacional)."
        )
    if pct_secoes < tse.PLENO_APURADO:
        st.warning(
            f"**Apuração em fase inicial ({fmt_pct(pct_secoes)} das seções).** Os percentuais ainda não representam o resultado "
            "final: a ordem em que as urnas chegam favorece alguns estados e regiões."
        )
    destaque(votos, br, pct_secoes, turno)
    ufs = votos[votos.regiao != "Brasil"]
    aba_br, aba_mapa, aba_reg, aba_uf = st.tabs(["Brasil", "Mapa", "Regiões", "Estados"])

    with aba_mapa:
        c1, c2, c3 = st.columns([1, 1.5, 1.5])
        nivel = c1.radio("Agrupar por", ["Estado", "Região"], horizontal=True)
        metrica = c2.selectbox("Colorir por", mapa.METRICAS)
        candidato = None
        if metrica == "% de um candidato":
            candidato = c3.selectbox("Candidato", sorted(ufs.candidato.unique()))
        st.plotly_chart(mapa.figura(votos, situacao, nivel, metrica, candidato), width="stretch")
        exterior = votos[votos.regiao == "Exterior"].sort_values("votos", ascending=False)
        st.caption("Exterior (fora do mapa): " + " · ".join(f"{c.candidato} {c.votos:,}".replace(",", ".") for c in exterior.itertuples()))
        if metrica == "Líder e margem":
            st.caption(
                f"Cor saturada quando a vantagem chega a {mapa.MARGEM_MAX} p.p. Estados sem nenhum voto apurado ficam cinza. "
                "Passe o mouse sobre o estado para ver o quanto já foi apurado."
            )

    with aba_br:
        a, b, c = st.columns(3)
        a.metric("Seções apuradas", f"{br.secoes_totalizadas:,}".replace(",", "."), f"de {br.secoes_totais:,}".replace(",", "."), delta_color="off")
        b.metric("% apurado", f"{pct_secoes:.2f}%")
        c.metric("Seções que faltam", f"{br.secoes_totais - br.secoes_totalizadas:,}".replace(",", "."))
        st.progress(min(pct_secoes / 100, 1.0))
        st.divider()
        total = tse.resumir(votos[votos.regiao == "Brasil"], None)
        cartoes(total)
        if len(total) > 4:
            st.caption("Cartões dos 4 mais votados; o gráfico mostra todos os candidatos.")
        st.bar_chart(total.assign(cor=total.candidato.map(tse.cor)), x="candidato", y="votos", color="cor", horizontal=True)
        st.caption(f"Brancos: {br.brancos:,} · Nulos: {br.nulos:,}".replace(",", "."))

    with aba_reg:
        regioes = tse.resumir(ufs, "regiao")
        ordem = [*tse.REGIOES, "Exterior"]
        tab = pivot(regioes, "regiao", ordem).merge(progresso(situacao, "regiao"), on="regiao")
        tabela_html(tab, "regiao", "Região", altura=360)
        pct_regioes = regioes.pivot(index="regiao", columns="candidato", values="pct").reindex(ordem).dropna(how="all")
        st.bar_chart(pct_regioes, color=[tse.cor(c) for c in pct_regioes.columns])

    with aba_uf:
        por_uf = tse.resumir(ufs, "uf")
        escolha = st.multiselect("Filtrar estados", sorted(por_uf.uf.unique()))
        if escolha:
            por_uf = por_uf[por_uf.uf.isin(escolha)]
        ordem = sorted(por_uf.uf.unique())
        tab = pivot(por_uf, "uf", ordem).merge(progresso(situacao, "uf"), on="uf")
        tabela_html(tab, "uf", "UF", altura=800)

    st.divider()
    st.caption(
        "Painel independente e **não oficial**. Dados: Tribunal Superior Eleitoral "
        "([resultados.tse.jus.br](https://resultados.tse.jus.br)); malha dos estados: IBGE. "
        "Para o resultado oficial, consulte sempre o portal do TSE."
    )


painel()
