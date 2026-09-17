#!/usr/bin/env python3
"""
fetch_markets.py  –  schreibt markets.json für die Ansicht "Märkte"

Eigener Lauf, getrennt von fetch_news.py: Kurse ändern sich im Minutentakt,
Feeds nicht, und wenn Yahoo einmal klemmt, soll das den Nachrichtenlauf nicht
aufhalten. Im Workflow also ein zweiter Schritt:

    - name: Märkte
      run: python fetch_markets.py
      continue-on-error: true

Quelle ist die öffentliche Chart-Schnittstelle von Yahoo Finance
(kein Schlüssel nötig, kein Konto). Sie ist nicht vertraglich zugesichert –
deshalb schreibt das Skript die Datei nur bei Erfolg neu und lässt die alte
sonst stehen. Jeder Wert trägt "stand", damit das Frontend zeigen kann, wie
alt er ist.

WAS YAHOO LIEFERT und was nicht:
  geht        Indizes, Devisen, Staatsanleihen-Renditen, Öl, US-Gas,
              europäisches Gas (TTF-Future) – siehe WERTE unten.
              Je Wert zwei Abrufe (5 Jahre wöchentlich, 1 Monat täglich);
              daraus entstehen alle fünf Zeiträume im Frontend.
  geht nicht  Deutscher Strom-Terminkontrakt (Base/Peak) und CO2 (EUA).
              Die liegen bei der EEX; deren freie Marktdaten stehen unter
              eex.com als Tagesdatei bereit, brauchen aber einen eigenen
              Abruf. Bis dahin fehlen die beiden Kacheln einfach – das
              Frontend zeigt nur, was in markets.json steht.

Prüfen, bevor es in den Workflow geht:
    python3 fetch_markets.py --pruefen
Das listet je Symbol, ob Yahoo Daten liefert; Symbole ändern sich gelegentlich.
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range={range}&interval={interval}"
# Zwei Abrufe je Wert reichen für alle Zeiträume:
#   5 Jahre wöchentlich  → "5 Jahre" und "1 Jahr"
#   1 Monat täglich      → "1 Monat", "7 Tage" und die Tagesveränderung
ABRUFE = [("5y", "1wk"), ("1mo", "1d")]
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
TIMEOUT = int(os.environ.get("MARKET_TIMEOUT", "12"))
PAUSE = float(os.environ.get("MARKET_PAUSE", "0.25"))   # Abstand zwischen Abrufen
NUR = os.environ.get("MARKET_NUR", "")                  # Komma-Liste: nur diese Gruppen
OUT = "markets.json"

# (Gruppe, Anzeigename, Yahoo-Symbol, Einheit, Nachkommastellen)
# Einheit "pp" heißt: Veränderung in Prozentpunkten statt Prozent (Renditen).
WERTE = [
    # ── Indizes Europa ──────────────────────────────────────────────
    ("Indizes Europa",     "DAX",                 "^GDAXI",    "",           0),
    ("Indizes Europa",     "MDAX",                "^MDAXI",    "",           0),
    ("Indizes Europa",     "SDAX",                "^SDAXI",    "",           0),
    ("Indizes Europa",     "TecDAX",              "^TECDAX",   "",           0),
    ("Indizes Europa",     "Euro Stoxx 50",       "^STOXX50E", "",           0),
    ("Indizes Europa",     "Stoxx Europe 600",    "^STOXX",    "",           0),
    ("Indizes Europa",     "FTSE 100",            "^FTSE",     "",           0),
    ("Indizes Europa",     "CAC 40",              "^FCHI",     "",           0),
    ("Indizes Europa",     "IBEX 35",             "^IBEX",     "",           0),
    ("Indizes Europa",     "FTSE MIB",            "FTSEMIB.MI","",           0),
    ("Indizes Europa",     "SMI",                 "^SSMI",     "",           0),
    ("Indizes Europa",     "AEX",                 "^AEX",      "",           0),
    ("Indizes Europa",     "ATX",                 "^ATX",      "",           0),
    ("Indizes Europa",     "OMX Stockholm 30",    "^OMX",      "",           0),
    ("Indizes Europa",     "WIG20",               "WIG20.WA",  "",           0),
    # ── Indizes Welt ────────────────────────────────────────────────
    ("Indizes Welt",       "S&P 500",             "^GSPC",     "",           0),
    ("Indizes Welt",       "Nasdaq 100",          "^NDX",      "",           0),
    ("Indizes Welt",       "Dow Jones",           "^DJI",      "",           0),
    ("Indizes Welt",       "Russell 2000",        "^RUT",      "",           0),
    ("Indizes Welt",       "VIX (Volatilität)",   "^VIX",      "",           2),
    ("Indizes Welt",       "Nikkei 225",          "^N225",     "",           0),
    ("Indizes Welt",       "Hang Seng",           "^HSI",      "",           0),
    ("Indizes Welt",       "CSI 300",             "000300.SS", "",           0),
    ("Indizes Welt",       "Shanghai Composite",  "000001.SS", "",           0),
    ("Indizes Welt",       "Kospi",               "^KS11",     "",           0),
    ("Indizes Welt",       "Sensex",              "^BSESN",    "",           0),
    ("Indizes Welt",       "Bovespa",             "^BVSP",     "",           0),
    ("Indizes Welt",       "TSX",                 "^GSPTSE",   "",           0),
    ("Indizes Welt",       "ASX 200",             "^AXJO",     "",           0),
    # ── Branchen (STOXX Europe 600) ─────────────────────────────────
    ("Branchen",           "Banken Europa",       "SX7P.Z",    "",           0),
    ("Branchen",           "Auto Europa",         "SXAP.Z",    "",           0),
    ("Branchen",           "Chemie Europa",       "SX4P.Z",    "",           0),
    ("Branchen",           "Technologie Europa",  "SX8P.Z",    "",           0),
    ("Branchen",           "Versorger Europa",    "SX6P.Z",    "",           0),
    ("Branchen",           "Öl & Gas Europa",     "SXEP.Z",    "",           0),
    ("Branchen",           "Gesundheit Europa",   "SXDP.Z",    "",           0),
    ("Branchen",           "Bau Europa",          "SXOP.Z",    "",           0),
    ("Branchen",           "Rüstung Europa",      "EXV1.DE",   "EUR",        2),
    # ── ETFs breit ──────────────────────────────────────────────────
    ("ETFs breit",         "iShares Core MSCI World",        "IWDA.AS", "EUR", 2),
    ("ETFs breit",         "Vanguard FTSE All-World",        "VWCE.DE", "EUR", 2),
    ("ETFs breit",         "iShares Core S&P 500",           "CSPX.AS", "EUR", 2),
    ("ETFs breit",         "iShares Core MSCI EM",           "EIMI.AS", "EUR", 2),
    ("ETFs breit",         "Xtrackers MSCI Europe",          "XMEU.DE", "EUR", 2),
    ("ETFs breit",         "iShares Core DAX",               "EXS1.DE", "EUR", 2),
    ("ETFs breit",         "iShares MSCI Japan",             "IJPA.AS", "EUR", 2),
    ("ETFs breit",         "iShares MSCI China",             "ICGA.AS", "EUR", 2),
    ("ETFs breit",         "Xtrackers MSCI World Small Cap", "XDWS.DE", "EUR", 2),
    # ── ETFs Themen ─────────────────────────────────────────────────
    ("ETFs Themen",        "VanEck Defense",                 "DFEN.DE", "EUR", 2),
    ("ETFs Themen",        "iShares Global Clean Energy",    "INRG.L",  "USD", 2),
    ("ETFs Themen",        "Invesco Solar",                  "TAN",     "USD", 2),
    ("ETFs Themen",        "iShares Digitalisation",         "DGTL.L",  "USD", 2),
    ("ETFs Themen",        "iShares Automation & Robotics",  "RBOT.L",  "USD", 2),
    ("ETFs Themen",        "L&G Cyber Security",             "ISPY.L",  "USD", 2),
    ("ETFs Themen",        "iShares Ageing Population",       "AGED.L", "USD", 2),
    ("ETFs Themen",        "VanEck Semiconductor",           "SMH",     "USD", 2),
    ("ETFs Themen",        "iShares Global Infrastructure",  "INFR.L",  "USD", 2),
    ("ETFs Themen",        "Xtrackers Future Mobility",      "XMOV.DE", "EUR", 2),
    # ── ETFs Anleihen ───────────────────────────────────────────────
    ("ETFs Anleihen",      "iShares Core Euro Govt Bond",    "IEGA.AS", "EUR", 2),
    ("ETFs Anleihen",      "iShares Euro Corporate Bond",    "IEAC.AS", "EUR", 2),
    ("ETFs Anleihen",      "iShares USD Treasury 7-10J",     "IDTM.AS", "USD", 2),
    ("ETFs Anleihen",      "iShares High Yield Euro",        "IHYG.L",  "EUR", 2),
    # ── Energie ─────────────────────────────────────────────────────
    ("Energie",            "Brent",               "BZ=F",      "USD/Barrel", 2),
    ("Energie",            "WTI",                 "CL=F",      "USD/Barrel", 2),
    ("Energie",            "Erdgas TTF",          "TTF=F",     "EUR/MWh",    2),
    ("Energie",            "Erdgas Henry Hub",    "NG=F",      "USD/MMBtu",  2),
    ("Energie",            "Heizöl",              "HO=F",      "USD/Gallone",3),
    ("Energie",            "Benzin (RBOB)",       "RB=F",      "USD/Gallone",3),
    ("Energie",            "Kohle (Newcastle)",   "MTF=F",     "USD/t",      2),
    ("Energie",            "Uran (Sprott)",       "U-UN.TO",   "CAD",        2),
    # ── Rohstoffe ───────────────────────────────────────────────────
    ("Rohstoffe",          "Gold",                "GC=F",      "USD/Unze",   2),
    ("Rohstoffe",          "Silber",              "SI=F",      "USD/Unze",   2),
    ("Rohstoffe",          "Platin",              "PL=F",      "USD/Unze",   2),
    ("Rohstoffe",          "Kupfer",              "HG=F",      "USD/lb",     3),
    ("Rohstoffe",          "Aluminium",           "ALI=F",     "USD/t",      2),
    ("Rohstoffe",          "Weizen",              "ZW=F",      "US-Cent/bu", 2),
    ("Rohstoffe",          "Mais",                "ZC=F",      "US-Cent/bu", 2),
    ("Rohstoffe",          "Sojabohnen",          "ZS=F",      "US-Cent/bu", 2),
    ("Rohstoffe",          "Raps",                "RS=F",      "CAD/t",      2),
    ("Rohstoffe",          "Kaffee",              "KC=F",      "US-Cent/lb", 2),
    ("Rohstoffe",          "Zucker",              "SB=F",      "US-Cent/lb", 2),
    ("Rohstoffe",          "Baumwolle",           "CT=F",      "US-Cent/lb", 2),
    # ── Zinsen ──────────────────────────────────────────────────────
    ("Zinsen",             "US-Staatsanleihe 10J","^TNX",      "%",          2),
    ("Zinsen",             "US-Staatsanleihe 30J","^TYX",      "%",          2),
    ("Zinsen",             "US-Staatsanleihe 5J", "^FVX",      "%",          2),
    ("Zinsen",             "US-Geldmarkt 13W",    "^IRX",      "%",          2),
    # ── Devisen ─────────────────────────────────────────────────────
    ("Devisen",            "EUR/USD",             "EURUSD=X",  "",           4),
    ("Devisen",            "EUR/CHF",             "EURCHF=X",  "",           4),
    ("Devisen",            "EUR/GBP",             "EURGBP=X",  "",           4),
    ("Devisen",            "EUR/JPY",             "EURJPY=X",  "",           2),
    ("Devisen",            "EUR/CNY",             "EURCNY=X",  "",           4),
    ("Devisen",            "EUR/PLN",             "EURPLN=X",  "",           4),
    ("Devisen",            "EUR/SEK",             "EURSEK=X",  "",           4),
    ("Devisen",            "EUR/TRY",             "EURTRY=X",  "",           2),
    ("Devisen",            "USD/JPY",             "JPY=X",     "",           2),
    ("Devisen",            "USD/CNY",             "CNY=X",     "",           4),
    ("Devisen",            "Dollar-Index",        "DX-Y.NYB",  "",           2),
    # ── Krypto ──────────────────────────────────────────────────────
    ("Krypto",             "Bitcoin",             "BTC-EUR",   "EUR",        0),
    ("Krypto",             "Ethereum",            "ETH-EUR",   "EUR",        0),
    # ── Einzelwerte mit politischem Bezug ───────────────────────────
    ("Unternehmen",        "Volkswagen",          "VOW3.DE",   "EUR",        2),
    ("Unternehmen",        "Mercedes-Benz",       "MBG.DE",    "EUR",        2),
    ("Unternehmen",        "BMW",                 "BMW.DE",    "EUR",        2),
    ("Unternehmen",        "Siemens",             "SIE.DE",    "EUR",        2),
    ("Unternehmen",        "Rheinmetall",         "RHM.DE",    "EUR",        2),
    ("Unternehmen",        "RWE",                 "RWE.DE",    "EUR",        2),
    ("Unternehmen",        "E.ON",                "EOAN.DE",   "EUR",        2),
    ("Unternehmen",        "BASF",                "BAS.DE",    "EUR",        2),
    ("Unternehmen",        "Deutsche Bank",       "DBK.DE",    "EUR",        2),
    ("Unternehmen",        "Deutsche Telekom",    "DTE.DE",    "EUR",        2),
    ("Unternehmen",        "Airbus",              "AIR.PA",    "EUR",        2),
    ("Unternehmen",        "TotalEnergies",       "TTE.PA",    "EUR",        2),
    ("Unternehmen",        "ASML",                "ASML.AS",   "EUR",        2),
    ("Unternehmen",        "Nvidia",              "NVDA",      "USD",        2),
    ("Unternehmen",        "Tesla",               "TSLA",      "USD",        2),
]
# Hinweis: ^TNX ist die US-Rendite (10 Jahre). Für die Bundesanleihe gibt es bei
# Yahoo kein verlässliches freies Symbol; nimm die Bundesbank-Zeitreihe
# (BBSIS.D.I.ZST.ZI.EUR.S1311.B.A604._Z.R.A.A._Z._Z.A) oder lass die Kachel weg.
# Den Anzeigenamen dann bitte mit ändern, sonst steht Falsches im Frontend.


def hole(sym, bereich, intervall):
    url = CHART.format(sym=sym, range=bereich, interval=intervall)
    req = Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def werte_aus(roh):
    """Gibt (schlusskurse, waehrung) zurück – Lücken (None) werden übersprungen."""
    res = (roh.get("chart") or {}).get("result") or []
    if not res:
        return [], ""
    r0 = res[0]
    quote = ((r0.get("indicators") or {}).get("quote") or [{}])[0]
    closes = [c for c in (quote.get("close") or []) if isinstance(c, (int, float))]
    waehrung = (r0.get("meta") or {}).get("currency") or ""
    return closes, waehrung


def formatiere(x, stellen):
    s = f"{x:,.{stellen}f}"
    # deutsche Schreibweise: 18.412,35
    return s.replace(",", "§").replace(".", ",").replace("§", ".")


def einen(gruppe, name, sym, einheit, stellen):
    woche, waehrung = werte_aus(hole(sym, *ABRUFE[0]))
    tag, w2 = werte_aus(hole(sym, *ABRUFE[1]))
    if len(tag) < 2 and len(woche) < 2:
        raise ValueError("keine Kursreihe")
    if not tag:
        tag = woche
    if not woche:
        woche = tag
    r = lambda xs: [round(x, 6) for x in xs]
    reihen = {
        "1t": r(tag[-2:]),          # Tagesveränderung (Schlusskurs zu Schlusskurs)
        "1w": r(tag[-6:]),
        "1m": r(tag[-23:]),
        "1j": r(woche[-53:]),
        "max": r(woche),
    }
    letzter = tag[-1]
    return {
        "gruppe": gruppe,
        "n": name,
        "sym": sym,
        "unit": einheit,
        "dec": stellen,
        "last": round(letzter, 6),
        "v": formatiere(letzter, stellen) + (" %" if einheit == "%" else ""),
        "series": reihen,
        "s": reihen["1m"][-7:],      # kurze Kurve für ältere Ansichten
        "waehrung": waehrung or w2,
    }


def main():
    pruefen = "--pruefen" in sys.argv
    gruppen_filter = {g.strip() for g in NUR.split(",") if g.strip()}
    werte = [w for w in WERTE if not gruppen_filter or w[0] in gruppen_filter]
    items, fehler = [], []
    for gruppe, name, sym, einheit, stellen in werte:
        try:
            items.append(einen(gruppe, name, sym, einheit, stellen))
            print(f"  ok   {name:<18} {sym}")
        except (HTTPError, URLError, ValueError, KeyError, json.JSONDecodeError) as e:
            fehler.append((name, sym, f"{type(e).__name__}: {e}"))
            print(f"  ---  {name:<18} {sym}  {type(e).__name__}: {e}")
        time.sleep(PAUSE)   # freundlich bleiben, sonst kommt 429

    if pruefen:
        print(f"\n{len(items)} von {len(werte)} Symbolen liefern Daten.")
        return 0 if items else 1

    if not items:
        print("Kein einziger Kurs abrufbar – markets.json bleibt unverändert.")
        return 1

    gruppen = []
    for g in dict.fromkeys(g for g, *_ in werte):
        teil = [i for i in items if i["gruppe"] == g]
        if teil:
            gruppen.append({"grp": g, "items": [{k: v for k, v in i.items() if k != "gruppe"} for i in teil]})

    out = {
        "updated": datetime.now(timezone.utc).isoformat(),
        "quelle": "Yahoo Finance (Chart-API), Tagesschlusskurse",
        "zeitraeume": {"1t": "1 Tag", "1w": "7 Tage", "1m": "1 Monat", "1j": "1 Jahr", "max": "5 Jahre"},
        "hinweis": "Kurse dienen der Einordnung, nicht dem Handel. Verzögert.",
        "fehlend": [{"n": n, "sym": s, "grund": g} for n, s, g in fehler],
        "groups": gruppen,
    }
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, separators=(",", ":"))
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, OUT)
    print(f"\n→ {OUT}: {sum(len(g['items']) for g in gruppen)} Werte, {len(fehler)} fehlend")
    return 0


if __name__ == "__main__":
    sys.exit(main())
