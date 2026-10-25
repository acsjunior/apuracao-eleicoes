# Apuração Presidencial

Painel Streamlit que acompanha em tempo real a apuração das eleições presidenciais
(votos e % por candidato) no Brasil, por região e por estado, com mapa.

Dados: arquivos públicos de resultados do TSE (`resultados.tse.jus.br`).
Malha dos estados: IBGE (`data/ufs.geojson`).

## Rodar localmente

```bash
uv run streamlit run app.py
```
