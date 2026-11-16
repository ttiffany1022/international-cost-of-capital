"""International Cost of Capital App (Streamlit)

Compares a project's cost of equity, cost of debt and WACC in USD and in the
currency of a selected country.

Data sources (fetched live, cached, refreshable):
  * Industry unlevered betas  - Damodaran, betas.xls (US industries)
  * BBB (Baa2) USD spread     - Damodaran, ratings.html (large non-financial firms)
  * Corporate tax rates       - OECD Corporate Tax Statistics (SDMX API, combined
                                statutory CIT rate, latest year)
  * 10-year government yields - Trading Economics, /bonds page
"""

import io
import re
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import requests
import streamlit as st

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
BETAS_URL = "https://pages.stern.nyu.edu/~adamodar/pc/datasets/betas.xls"
RATINGS_URL = "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/ratings.html"
OECD_PAGE_URL = (
    "https://www.oecd.org/en/publications/corporate-tax-statistics-2024_9c27d6e8-en/"
    "full-report/statutory-corporate-income-tax-rates_440f9286.html"
)
OECD_API_URL = (
    "https://sdmx.oecd.org/public/rest/data/OECD.CTP.TPS,DSD_TAX_CIT@DF_CIT,/all"
    "?startPeriod=2020&format=csvfilewithlabels"
)
TE_URL = "https://tradingeconomics.com/bonds"
# Backup for yields: OECD long-term (10Y) government bond rates, monthly
OECD_LTR_URL = (
    "https://sdmx.oecd.org/public/rest/data/OECD.SDD.STES,DSD_STES@DF_FINMARK,/.M.IRLT.PA....."
    "?startPeriod=2025-01&format=csvfilewithlabels"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

DEFAULT_INDUSTRY = "Software (System & Application)"
DEFAULT_COUNTRY = "United States"
DEFAULT_ERP = 6.5   # %
DEFAULT_DEBT = 30   # % of capital

# Fallbacks used only if a live fetch fails (values as last checked, Nov 2026)
FALLBACK_BETAS = pd.DataFrame(
    {"Industry": ["Software (System & Application)", "Total Market"],
     "Unlevered beta": [1.23, 0.72]}
)
FALLBACK_SPREAD = 1.11      # % (Baa2/BBB, Jan 2026 dataset)
FALLBACK_US_TAX = 25.53     # % (OECD combined statutory, 2026)
FALLBACK_US_YIELD = 5.147   # %

# Name differences between OECD and Trading Economics
OECD_TO_TE = {
    "Korea": "South Korea",
    "Türkiye": "Turkey",
    "Czechia": "Czech Republic",
    "Slovak Republic": "Slovakia",
    "China (People's Republic of)": "China",
    "Chinese Taipei": "Taiwan",
    "Hong Kong, China": "Hong Kong",
    "Viet Nam": "Vietnam",
    "Russian Federation": "Russia",
}


def now_utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


# ---------------------------------------------------------------------------
# Data fetchers (cached; "Refresh data" clears the cache)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=6 * 3600, show_spinner=False)
def fetch_betas():
    r = requests.get(BETAS_URL, headers=HEADERS, timeout=30)
    r.raise_for_status()
    xls = pd.ExcelFile(io.BytesIO(r.content))
    sheet = next((s for s in xls.sheet_names if "industry" in s.lower()), xls.sheet_names[0])
    raw = pd.read_excel(xls, sheet_name=sheet, header=None)

    # Find header row containing "Industry Name"
    hdr = None
    for i in range(min(40, len(raw))):
        if raw.iloc[i].astype(str).str.strip().str.lower().eq("industry name").any():
            hdr = i
            break
    if hdr is None:
        raise ValueError("Could not find 'Industry Name' header in betas.xls")

    # Date label usually sits in the rows above the header
    date_txt = ""
    for i in range(hdr):
        for v in raw.iloc[i].dropna().astype(str):
            if re.search(r"(19|20)\d{2}", v) or "date" in v.lower():
                date_txt = (date_txt + " " + v).strip()
    df = raw.iloc[hdr + 1:].copy()
    df.columns = [str(c).strip() for c in raw.iloc[hdr]]
    ub_col = next(c for c in df.columns if c.lower() == "unlevered beta")
    out = df[["Industry Name", ub_col]].rename(
        columns={"Industry Name": "Industry", ub_col: "Unlevered beta"})
    out["Unlevered beta"] = pd.to_numeric(out["Unlevered beta"], errors="coerce")
    out = out.dropna().reset_index(drop=True)
    out["Industry"] = out["Industry"].astype(str).str.strip()
    return out, (date_txt or "see file"), now_utc()


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def fetch_bbb_spread():
    r = requests.get(RATINGS_URL, headers=HEADERS, timeout=30)
    r.raise_for_status()
    html = r.text
    m = re.search(r"Baa2/BBB.*?(\d+\.\d+)\s*%", html, flags=re.S | re.I)
    if not m:
        raise ValueError("Baa2/BBB row not found")
    spread = float(m.group(1))  # first occurrence = large non-financial firms table
    d = re.search(r"as of\s+([A-Za-z]+\s+\d{4})", html)
    return spread, (d.group(1) if d else "see page"), now_utc()


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def fetch_tax_rates():
    r = requests.get(OECD_API_URL, headers=HEADERS, timeout=60)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))
    # Combined (central + sub-central) statutory CIT rate
    sel = df[(df["MEASURE"] == "CIT_C") & (df["TARGETING"] == "ST") & (df["SECTOR"] == "S13")]
    sel = sel.dropna(subset=["OBS_VALUE"])
    latest = sel.sort_values("TIME_PERIOD").groupby("Reference area").tail(1)
    out = latest[["Reference area", "TIME_PERIOD", "OBS_VALUE"]].rename(
        columns={"Reference area": "Country", "TIME_PERIOD": "Year", "OBS_VALUE": "Tax rate"})
    out["Country"] = out["Country"].replace(OECD_TO_TE)
    return out.set_index("Country"), now_utc()


def _te_yields():
    r = requests.get(TE_URL, headers=HEADERS, timeout=30)
    r.raise_for_status()
    tables = pd.read_html(io.StringIO(r.text))
    rows = []
    for t in tables:
        cols = [str(c) for c in t.columns]
        if "Yield" not in cols:
            continue
        # country name is the first non-empty text column (header = region e.g. "Major10Y")
        name_col = next(c for c in t.columns if str(c) != "Yield"
                        and pd.to_numeric(t[c], errors="coerce").isna().all()
                        and t[c].notna().any())
        sub = pd.DataFrame({
            "Country": t[name_col].astype(str).str.strip(),
            "Yield": pd.to_numeric(t["Yield"], errors="coerce"),
            "As of": t["Date"].astype(str) if "Date" in t.columns else "",
        })
        rows.append(sub)
    if not rows:
        raise ValueError("No yield tables found on Trading Economics")
    out = pd.concat(rows).dropna(subset=["Yield"]).drop_duplicates("Country")
    return out.set_index("Country")


def _oecd_yields():
    r = requests.get(OECD_LTR_URL, headers=HEADERS, timeout=60)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text)).dropna(subset=["OBS_VALUE"])
    latest = df.sort_values("TIME_PERIOD").groupby("Reference area").tail(1)
    out = pd.DataFrame({"Country": latest["Reference area"].replace(OECD_TO_TE).values,
                        "Yield": latest["OBS_VALUE"].astype(float).values,
                        "As of": latest["TIME_PERIOD"].astype(str).values})
    return out.set_index("Country")


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_yields():
    """Trading Economics first. TE sits behind a bot challenge that often blocks
    cloud servers, so fall back to OECD monthly 10Y government bond yields."""
    try:
        return _te_yields(), now_utc(), "Trading Economics bonds"
    except Exception as e:  # noqa: BLE001
        te_err = f"{type(e).__name__}: {e}"
    return _oecd_yields(), now_utc(), f"OECD long-term interest rates (backup; Trading Economics blocked: {te_err})"


def safe(fn):
    try:
        return fn(), None
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"


# ---------------------------------------------------------------------------
# Finance
# ---------------------------------------------------------------------------
def compute(beta_u, tax, debt, erp, rf_us, rf_fc, spread_us, spread_fc_override=None,
            method="Multiplicative (parity)"):
    """All rates as decimals. Returns dict of results."""
    de = debt / (1 - debt) if debt < 1 else np.inf
    beta_l = beta_u * (1 + (1 - tax) * de)

    ke_us = rf_us + beta_l * erp
    kd_us = rf_us + spread_us

    # Course formulas: F = (1 + Rf*) / (1 + Rf)
    F = (1 + rf_fc) / (1 + rf_us)
    ke_fc = (1 + ke_us) * F - 1      # 1 + Re* = (1 + Re)(1 + Rf*)/(1 + Rf)
    if spread_fc_override is None:
        spread_fc = spread_us * F    # s* = s (1 + Rf*)/(1 + Rf)
        kd_fc = rf_fc + spread_fc    # Rd* = Rf* + s*
    else:
        spread_fc = spread_fc_override
        kd_fc = rf_fc + spread_fc

    e = 1 - debt
    wacc_us = e * ke_us + debt * kd_us * (1 - tax)
    wacc_fc = e * ke_fc + debt * kd_fc * (1 - tax)
    return dict(beta_l=beta_l, de=de, ke_us=ke_us, kd_us=kd_us, ke_fc=ke_fc, kd_fc=kd_fc,
                spread_fc=spread_fc, wacc_us=wacc_us, wacc_fc=wacc_fc,
                kd_us_at=kd_us * (1 - tax), kd_fc_at=kd_fc * (1 - tax))


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------
st.set_page_config(page_title="International Cost of Capital", layout="wide")
st.title("International Cost of Capital")
st.caption("Cost of equity, cost of debt and WACC for a project in USD and in the selected country's currency.")

if st.sidebar.button("🔄 Refresh data", width="stretch"):
    st.cache_data.clear()
    st.rerun()

with st.spinner("Fetching latest data..."):
    betas_res, betas_err = safe(fetch_betas)
    spread_res, spread_err = safe(fetch_bbb_spread)
    tax_res, tax_err = safe(fetch_tax_rates)
    yld_res, yld_err = safe(fetch_yields)

betas = betas_res[0] if betas_res else FALLBACK_BETAS
yields = yld_res[0] if yld_res else pd.DataFrame(
    {"Yield": [FALLBACK_US_YIELD], "As of": ["fallback"]}, index=["United States"])
taxes = tax_res[0] if tax_res else pd.DataFrame(
    {"Year": ["fallback"], "Tax rate": [FALLBACK_US_TAX]}, index=["United States"])
spread_us_fetched = spread_res[0] if spread_res else FALLBACK_SPREAD

for name, err in [("Betas", betas_err), ("BBB spread", spread_err),
                  ("OECD tax rates", tax_err), ("Trading Economics yields", yld_err)]:
    if err:
        st.warning(f"{name}: live fetch failed, using fallback / manual values. ({err})")

# ---- Sidebar inputs --------------------------------------------------------
sb = st.sidebar
sb.header("Project settings")
industries = sorted(betas["Industry"].tolist())
industry = sb.selectbox("Industry", industries,
                        index=industries.index(DEFAULT_INDUSTRY) if DEFAULT_INDUSTRY in industries else 0)
countries = sorted(yields.index.tolist())
country = sb.selectbox("Selected country", countries,
                       index=countries.index(DEFAULT_COUNTRY) if DEFAULT_COUNTRY in countries else 0)
erp = sb.slider("Equity risk premium (%)", 0.0, 15.0, DEFAULT_ERP, 0.1)
debt_pct = sb.slider("Target debt share D/(D+E) (%)", 0, 90, DEFAULT_DEBT, 1)
tax_choice = sb.radio("Project tax rate", ["U.S. tax rate", "Selected-country tax rate"], index=0)
method = "parity"  # currency conversion follows the course formula (Fisher + relative PPP)

# Fetched values
beta_fetched = float(betas.loc[betas["Industry"] == industry, "Unlevered beta"].iloc[0])
rf_us_fetched = float(yields.loc["United States", "Yield"]) if "United States" in yields.index else FALLBACK_US_YIELD
rf_fc_fetched = float(yields.loc[country, "Yield"]) if country in yields.index else rf_us_fetched
tax_us_fetched = float(taxes.loc["United States", "Tax rate"]) if "United States" in taxes.index else FALLBACK_US_TAX
tax_fc_available = country in taxes.index
tax_fc_fetched = float(taxes.loc[country, "Tax rate"]) if tax_fc_available else None

sb.header("Manual overrides")


def override(label, fetched, key, fmt="%.4f", step=0.01):
    on = sb.checkbox(f"Override {label}", key=f"o_{key}")
    if on:
        return sb.number_input(f"{label}", value=float(fetched if fetched is not None else 0.0),
                               step=step, format=fmt, key=f"v_{key}"), True
    return fetched, False


beta_u, o1 = override("unlevered beta", beta_fetched, "beta", "%.3f")
tax_us, o2 = override("U.S. tax rate (%)", tax_us_fetched, "taxus", "%.2f", 0.1)
tax_fc_default = tax_fc_fetched if tax_fc_available else tax_us_fetched
if not tax_fc_available:
    sb.info(f"OECD has no tax rate for {country}; enter it manually below (default shown = U.S. rate).")
tax_fc, o3 = override(f"{country} tax rate (%)", tax_fc_default, "taxfc", "%.2f", 0.1)
spread_us, o4 = override("USD BBB spread (%)", spread_us_fetched, "spr", "%.2f", 0.01)
rf_us, o5 = override("U.S. 10Y yield (%)", rf_us_fetched, "rfus", "%.3f", 0.01)
rf_fc, o6 = override(f"{country} 10Y yield (%)", rf_fc_fetched, "rffc", "%.3f", 0.01)
spread_fc_on = sb.checkbox(f"Override {country}-currency BBB spread", key="o_sprfc")

tax = (tax_us if tax_choice == "U.S. tax rate" else tax_fc) / 100
debt = debt_pct / 100

# auto foreign spread (for default display of the override box)
auto = compute(beta_u, tax, debt, erp / 100, rf_us / 100, rf_fc / 100, spread_us / 100, None, method)
if spread_fc_on:
    spread_fc_val = sb.number_input(f"{country}-currency BBB spread (%)",
                                    value=round(auto["spread_fc"] * 100, 4), step=0.01, format="%.4f")
    res = compute(beta_u, tax, debt, erp / 100, rf_us / 100, rf_fc / 100, spread_us / 100,
                  spread_fc_val / 100, method)
else:
    sb.caption(f"Auto {country}-currency spread: {auto['spread_fc']*100:.3f}%")
    res = auto

# ---- Results --------------------------------------------------------------
cur = country if country != "United States" else "United States (same as USD)"
c1, c2 = st.columns(2)
for col, label, ke, kd, wacc, rf, spr in [
    (c1, "USD", res["ke_us"], res["kd_us"], res["wacc_us"], rf_us / 100, spread_us / 100),
    (c2, f"{country} currency", res["ke_fc"], res["kd_fc"], res["wacc_fc"], rf_fc / 100, res["spread_fc"]),
]:
    with col:
        st.subheader(label)
        m1, m2, m3 = st.columns(3)
        m1.metric("Cost of equity", f"{ke*100:.2f}%")
        m2.metric("Pre-tax cost of debt", f"{kd*100:.2f}%")
        m3.metric("WACC", f"{wacc*100:.2f}%")
        st.caption(f"Risk-free {rf*100:.3f}% · BBB spread {spr*100:.3f}% · After-tax Kd {kd*(1-tax)*100:.2f}%")

results = pd.DataFrame({
    "Item": ["Industry", "Selected country", "Unlevered beta", "Levered beta", "D/E", "Debt share",
             "Project tax rate", "Equity risk premium", "Risk-free rate", "BBB spread",
             "Cost of equity", "Pre-tax cost of debt", "After-tax cost of debt", "WACC"],
    "USD": [industry, country, f"{beta_u:.3f}", f"{res['beta_l']:.3f}", f"{res['de']:.4f}",
            f"{debt_pct}%", f"{tax*100:.2f}% ({tax_choice})", f"{erp:.2f}%", f"{rf_us:.3f}%",
            f"{spread_us:.3f}%", f"{res['ke_us']*100:.3f}%", f"{res['kd_us']*100:.3f}%",
            f"{res['kd_us_at']*100:.3f}%", f"{res['wacc_us']*100:.3f}%"],
    f"{country} currency": [industry, country, f"{beta_u:.3f}", f"{res['beta_l']:.3f}", f"{res['de']:.4f}",
                            f"{debt_pct}%", f"{tax*100:.2f}% ({tax_choice})", f"{erp:.2f}%", f"{rf_fc:.3f}%",
                            f"{res['spread_fc']*100:.3f}%" + (" (manual)" if spread_fc_on else " (auto)"),
                            f"{res['ke_fc']*100:.3f}%", f"{res['kd_fc']*100:.3f}%",
                            f"{res['kd_fc_at']*100:.3f}%", f"{res['wacc_fc']*100:.3f}%"],
})
st.subheader("Side-by-side detail")
st.dataframe(results, hide_index=True, width="stretch")
st.download_button("⬇️ Download results (CSV)", results.to_csv(index=False).encode(),
                   f"cost_of_capital_{industry[:20]}_{country}.csv".replace(" ", "_"), "text/csv")

# ---- Assumptions ----------------------------------------------------------
with st.expander("Formulas and main assumptions", expanded=True):
    st.markdown(f"""
- **Levered beta (Hamada):** βL = βU × [1 + (1 − t) × D/E], with D/E = d / (1 − d).
- **USD cost of equity (CAPM):** Ke$ = rf$ + βL × ERP. The ERP is a USD (mature-market) premium applied to a U.S. industry beta.
- **USD cost of debt:** Kd$ = rf$ + BBB spread (Damodaran Baa2/BBB, large non-financial firms).
- **Foreign-currency cost of equity:** 1 + Re* = (1 + Re) × (1 + Rf*) / (1 + Rf). Fisher equation + relative PPP: the adjustment reflects the currency's expected depreciation.
- **Foreign-currency cost of debt:** s* = s × (1 + Rf*) / (1 + Rf) and Rd* = Rf* + s*, unless the foreign spread is overridden manually.
- **WACC:** E/V × Ke + D/V × Kd × (1 − t), same debt share, tax rate and ERP in both currencies.
- **Key approximations:** risk is measured relative to the U.S. market; U.S. industry betas proxy for project risk; 10-year government yields are risk-free proxies (they include term and liquidity premia and possibly sovereign default risk); equal real rates and relative PPP link yields to expected depreciation; no separate country equity premium; target leverage is constant, interest deductions are fully usable, and unlevering assumes zero debt beta.
- Tax rates are OECD combined (central + sub-central) statutory CIT rates, latest year available.
""")

# ---- All-industry comparison ---------------------------------------------
st.subheader("All-industry comparison")
rows = []
for _, r in betas.iterrows():
    x = compute(r["Unlevered beta"], tax, debt, erp / 100, rf_us / 100, rf_fc / 100, spread_us / 100,
                (spread_fc_val / 100) if spread_fc_on else None, method)
    rows.append({"Industry": r["Industry"], "Unlevered beta": r["Unlevered beta"],
                 "Levered beta": round(x["beta_l"], 3),
                 "Ke USD (%)": round(x["ke_us"] * 100, 3), "WACC USD (%)": round(x["wacc_us"] * 100, 3),
                 f"Ke {country} (%)": round(x["ke_fc"] * 100, 3),
                 f"WACC {country} (%)": round(x["wacc_fc"] * 100, 3)})
allind = pd.DataFrame(rows).sort_values("WACC USD (%)", ascending=False)
st.dataframe(allind.style.apply(
    lambda s: ["background-color: #fff3cd" if s["Industry"] == industry else "" for _ in s], axis=1),
    hide_index=True, width="stretch", height=400)
st.bar_chart(allind.set_index("Industry")[["WACC USD (%)", f"WACC {country} (%)"]])
st.download_button("⬇️ Download all-industry table (CSV)", allind.to_csv(index=False).encode(),
                   f"all_industries_{country}.csv".replace(" ", "_"), "text/csv")

# ---- Sources ---------------------------------------------------------------
st.subheader("Data sources and dates")
src = pd.DataFrame([
    ["Industry unlevered beta", "Damodaran betas.xls", BETAS_URL,
     (f"Data: {betas_res[1]} · fetched {betas_res[2]}" if betas_res else "FALLBACK"), "Manual" if o1 else "Fetched"],
    ["USD BBB spread", "Damodaran ratings.html (Baa2/BBB, large non-financial)", RATINGS_URL,
     (f"Data as of {spread_res[1]} · fetched {spread_res[2]}" if spread_res else "FALLBACK"), "Manual" if o4 else "Fetched"],
    ["Corporate tax rates", "OECD Corporate Tax Statistics (combined statutory CIT)", OECD_PAGE_URL,
     (f"U.S. year {taxes.loc['United States','Year'] if 'United States' in taxes.index else 'n/a'}"
      + (f", {country} year {taxes.loc[country,'Year']}" if tax_fc_available else f", {country}: not in OECD data")
      + (f" · fetched {tax_res[1]}" if tax_res else " · FALLBACK")),
     "Manual" if (o2 or o3) else "Fetched"],
    ["10Y government yields", (yld_res[2] if yld_res else "Trading Economics bonds"),
     (TE_URL if (not yld_res or yld_res[2].startswith("Trading")) else OECD_LTR_URL),
     (f"US as of {yields.loc['United States','As of'] if 'United States' in yields.index else 'n/a'}, "
      f"{country} as of {yields.loc[country,'As of'] if country in yields.index else 'n/a'} · fetched {yld_res[1]}"
      if yld_res else "FALLBACK"), "Manual" if (o5 or o6) else "Fetched"],
], columns=["Input", "Source", "URL", "Date", "Status"])
st.dataframe(src, hide_index=True, width="stretch",
             column_config={"URL": st.column_config.LinkColumn("URL")})
