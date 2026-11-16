# International Cost of Capital App

A Streamlit app that compares a project's **cost of equity, cost of debt and WACC in USD and in the currency of any selected country**.

## Data (fetched live, cached, with a Refresh button)
| Input | Source |
|---|---|
| Industry unlevered beta | Damodaran, `betas.xls` (U.S. industries, "Unlevered beta") |
| USD BBB spread | Damodaran, `ratings.html` (Baa2/BBB, large non-financial service firms) |
| Corporate tax rates | OECD Corporate Tax Statistics, combined statutory CIT rate (pulled from the OECD SDMX API, latest year) |
| 10-year government yields | Trading Economics `/bonds` page (scraped, no API key) |

Source dates and fetch times are shown in the app. If a fetch fails, the app shows a warning, uses a fallback value, and every input can be overwritten manually.

## Features
- Choose industry and target country; adjust equity risk premium and target debt share
- Manual overrides: unlevered beta, U.S. and foreign tax rates, USD BBB spread, both 10Y yields, and the foreign-currency BBB spread (auto-calculated when the override is off)
- Toggle the project tax rate between the U.S. and the selected-country rate
- Side-by-side USD vs foreign-currency results, all-industry comparison, CSV downloads
- Defaults: Software (System & Application), United States, U.S. tax rate, 6.5% ERP, 30% debt

## Formulas
- D/E = d / (1 − d); levered beta βL = βU × [1 + (1 − t) × D/E]
- Ke$ = rf$ + βL × ERP; Kd$ = rf$ + BBB spread
- Foreign-currency cost of equity: 1 + Re* = (1 + Re) × (1 + Rf*) / (1 + Rf)
- Foreign-currency spread: s* = s × (1 + Rf*) / (1 + Rf), Rd* = Rf* + s* (or a manual spread when the override is on)
- WACC = (1 − d) × Ke + d × Kd × (1 − t)

## Course example check (Colombia)
βU 0.80, d 30%, τ 35%, ERP 6.5%, Rf 4.0%, Rf* 12.0%, s 1.5% → Re 10.6486% / 19.1600%, Rd 5.5000% / 13.6154%, WACC 8.5265% / 16.0670% (USD / COP). The app reproduces these exactly.

## Built-in sanity checks (verified)
- Equal government yields → identical USD and foreign results (spread override off)
- Debt share 0% → WACC = cost of equity
- Overriding the foreign-currency spread leaves USD results unchanged

## Run locally
```bash
pip install -r requirements.txt
streamlit run app.py
```
