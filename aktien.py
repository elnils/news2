#!/usr/bin/env python3
"""
aktien.py  –  ergänzt markets.json um Einzelaktien, schreibt bewertung.json

KURSE (jeder Lauf, ohne Schlüssel)
Eine Liste wichtiger Aktien (einstellbar über AKTIEN, Yahoo-Kürzel) wird wie
die übrigen Zusatzwerte abgerufen und als Gruppe "Aktien" in markets.json
geführt. In der App lassen sie sich damit auch zu "Meine Werte" hinzufügen.
Gleitende Durchschnitte, Kreuzungssignale, RSI und die Lage im Jahres- und
Fünfjahresband rechnet die App selbst aus diesen Reihen.

BEWERTUNG (einmal am Tag, nur mit FMP_API_KEY)
Mit einem kostenlosen Schlüssel von Financial Modeling Prep:
  – KGV heute und im Durchschnitt der letzten fünf Geschäftsjahre,
  – Abweichung davon ("40 % unter dem eigenen Schnitt"),
  – fairer Wert nach Gewinn = Gewinn je Aktie × Durchschnitts-KGV,
  – fairer Wert nach Cashflow = freier Cashflow je Aktie × Durchschnitts-KGV
    des Cashflows (Kurs/FCF), wenn vorhanden.
Die kostenlose Stufe erlaubt rund 250 Abfragen am Tag; drei je Aktie.
Fehlt der Schlüssel, bleibt bewertung.json unverändert – nichts bricht.

Keine Anlageempfehlung: Die Zahlen beschreiben, ob eine Aktie gemessen an
ihrer eigenen Geschichte teuer oder günstig bewertet ist, sonst nichts.
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from urllib.parse import quote, urlencode
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError

try:
    from fetch_markets_extra import wert_holen
except Exception:                                   # pragma: no cover
    wert_holen = None

MARKETS = "markets.json"
BEWERTUNG = "bewertung.json"
FMP_KEY = os.environ.get("FMP_API_KEY", "").strip()
# Der kostenlose Tarif von FMP deckt nur US-Börsen ab; deutsche Kürzel
# (DTE.DE, MBG.DE …) werden mit HTTP 403 abgelehnt. Deshalb standardmäßig
# nur Werte mit US-Kürzel bewerten (SAP über die US-Notierung "SAP").
# Mit bezahltem Tarif: FMP_ALLE=1.
FMP_ALLE = os.environ.get("FMP_ALLE") == "1"
FMP = "https://financialmodelingprep.com"
UA = {"User-Agent": "Mozilla/5.0 (Presseschau)"}

# Yahoo-Kürzel · Name · Währung · FMP-Kürzel (leer = wie Yahoo)
AKTIEN_VORGABE = [
    # Deutschland – Kurse und technische Lage; bewertet werden über FMP nur
    # die mit US-Notierung (SAP, Deutsche Bank)
    ("SAP.DE", "SAP", "EUR", "SAP"), ("SIE.DE", "Siemens", "EUR", "SIE.DE"), ("ALV.DE", "Allianz", "EUR", "ALV.DE"),
    ("DTE.DE", "Deutsche Telekom", "EUR", "DTE.DE"), ("MBG.DE", "Mercedes-Benz", "EUR", "MBG.DE"),
    ("BMW.DE", "BMW", "EUR", "BMW.DE"), ("BAS.DE", "BASF", "EUR", "BAS.DE"), ("RHM.DE", "Rheinmetall", "EUR", "RHM.DE"),
    ("IFX.DE", "Infineon", "EUR", "IFX.DE"), ("MUV2.DE", "Munich Re", "EUR", "MUV2.DE"), ("DBK.DE", "Deutsche Bank", "EUR", "DB"),
    ("VOW3.DE", "Volkswagen", "EUR", "VOW3.DE"), ("AIR.DE", "Airbus", "EUR", "AIR.PA"),
    # USA – alle mit Bewertung (kostenloser FMP-Tarif): Dow-Jones-Werte und
    # die großen Technologiewerte. Zwei Abfragen je Aktie, rund 90 am Tag.
    ("AAPL", "Apple", "USD", "AAPL"), ("MSFT", "Microsoft", "USD", "MSFT"), ("NVDA", "Nvidia", "USD", "NVDA"),
    ("MU", "Micron", "USD", "MU"), ("QCOM", "Qualcomm", "USD", "QCOM"), ("AMZN", "Amazon", "USD", "AMZN"), ("GOOGL", "Alphabet", "USD", "GOOGL"), ("META", "Meta", "USD", "META"),
    ("TSLA", "Tesla", "USD", "TSLA"), ("AVGO", "Broadcom", "USD", "AVGO"), ("AMD", "AMD", "USD", "AMD"),
    ("NFLX", "Netflix", "USD", "NFLX"), ("ORCL", "Oracle", "USD", "ORCL"), ("ADBE", "Adobe", "USD", "ADBE"),
    ("CRM", "Salesforce", "USD", "CRM"), ("CSCO", "Cisco", "USD", "CSCO"), ("INTC", "Intel", "USD", "INTC"),
    ("IBM", "IBM", "USD", "IBM"), ("BRK-B", "Berkshire Hathaway", "USD", "BRK-B"), ("JPM", "JPMorgan Chase", "USD", "JPM"),
    ("GS", "Goldman Sachs", "USD", "GS"), ("AXP", "American Express", "USD", "AXP"), ("V", "Visa", "USD", "V"),
    ("MA", "Mastercard", "USD", "MA"), ("TRV", "Travelers", "USD", "TRV"), ("UNH", "UnitedHealth", "USD", "UNH"),
    ("JNJ", "Johnson & Johnson", "USD", "JNJ"), ("LLY", "Eli Lilly", "USD", "LLY"), ("MRK", "Merck & Co.", "USD", "MRK"),
    ("AMGN", "Amgen", "USD", "AMGN"), ("PG", "Procter & Gamble", "USD", "PG"), ("KO", "Coca-Cola", "USD", "KO"),
    ("PEP", "PepsiCo", "USD", "PEP"), ("WMT", "Walmart", "USD", "WMT"), ("COST", "Costco", "USD", "COST"),
    ("HD", "Home Depot", "USD", "HD"), ("MCD", "McDonald's", "USD", "MCD"), ("NKE", "Nike", "USD", "NKE"),
    ("DIS", "Disney", "USD", "DIS"), ("BA", "Boeing", "USD", "BA"), ("CAT", "Caterpillar", "USD", "CAT"),
    ("HON", "Honeywell", "USD", "HON"), ("MMM", "3M", "USD", "MMM"), ("CVX", "Chevron", "USD", "CVX"),
    ("XOM", "ExxonMobil", "USD", "XOM"), ("VZ", "Verizon", "USD", "VZ"), ("SHW", "Sherwin-Williams", "USD", "SHW"),
]


def aktienliste():
    eigen = [x.strip() for x in os.environ.get("AKTIEN", "").split(",") if x.strip()]
    if not eigen:
        return AKTIEN_VORGABE
    bekannt = {a[0]: a for a in AKTIEN_VORGABE}
    return [bekannt.get(s, (s, s, "", s)) for s in eigen]


def kurse(liste):
    if wert_holen is None:
        print("  fetch_markets_extra.py fehlt – keine Aktienkurse")
        return
    try:
        mk = json.load(open(MARKETS, encoding="utf-8"))
    except (OSError, ValueError):
        mk = {"groups": []}
    alt = {i.get("sym"): i for g in mk.get("groups") or [] if g.get("grp") == "Aktien" for i in g.get("items") or []}
    items = []
    for sym, name, waehrung, _ in liste:
        w = wert_holen(sym, name, waehrung, 2)
        if w is None and sym in alt:
            w = alt[sym]
        if w:
            items.append(w)
    mk["groups"] = [g for g in mk.get("groups") or [] if g.get("grp") != "Aktien"]
    if items:
        mk["groups"].append({"grp": "Aktien", "items": items})
    tmp = MARKETS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(mk, fh, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, MARKETS)
    print(f"  Aktien: {len(items)} Kurse in markets.json")


def fmp(pfad, **params):
    """Erst die aktuelle Schnittstelle (/stable), dann die ältere (/api/v3)."""
    params["apikey"] = FMP_KEY
    sym = params.get("symbol", "")
    versuche = [f"{FMP}/stable/{pfad}?" + urlencode(params)]
    alt = dict(params)
    alt.pop("symbol", None)
    versuche.append(f"{FMP}/api/v3/{pfad}/{quote(sym)}?" + urlencode(alt))
    for url in versuche:
        try:
            with urlopen(Request(url, headers=UA), timeout=20) as r:
                d = json.loads(r.read().decode("utf-8", "replace"))
            if isinstance(d, dict) and d.get("Error Message"):
                continue
            if d:
                return d
        except HTTPError as e:
            if e.code in (401, 403):
                print(f"  FMP HTTP {e.code} – Schlüssel oder Tarif erlaubt {sym} nicht")
                return None
        except (URLError, ValueError, TimeoutError, OSError):
            continue
    return None


def zahl(d, *felder):
    for f in felder:
        v = d.get(f) if isinstance(d, dict) else None
        if isinstance(v, (int, float)) and v == v:
            return float(v)
    return None


def bewerten(liste):
    heute = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        alt = json.load(open(BEWERTUNG, encoding="utf-8"))
    except (OSError, ValueError):
        alt = {}
    if not FMP_KEY:
        print("  Bewertung: kein FMP_API_KEY hinterlegt – nur Kurse.")
        if not os.path.exists(BEWERTUNG):
            # Leere, gültige Datei – sonst meldet die App "noch nicht eingerichtet".
            with open(BEWERTUNG, "w", encoding="utf-8") as fh:
                json.dump({"datum": None, "quelle": "Financial Modeling Prep", "werte": {},
                           "hinweis": "Ohne FMP_API_KEY – Bewertung folgt, sobald der Schlüssel hinterlegt ist."}, fh)
        return
    if alt.get("datum") == heute and os.environ.get("AKTIEN_FORCE") != "1":
        print("  Bewertung: heute schon aktualisiert.")
        return
    werte = alt.get("werte") or {}
    abgelehnt = 0
    for sym, name, _, fsym in liste:
        if not FMP_ALLE and "." in fsym:
            continue                                   # nicht im kostenlosen Tarif
        if abgelehnt >= 3:
            print("  FMP lehnt wiederholt ab – Rest übersprungen (Schlüssel oder Tarif prüfen).")
            break
        q = fmp("quote", symbol=fsym)
        q = q[0] if isinstance(q, list) and q else (q or {})
        r = fmp("ratios", symbol=fsym, period="annual", limit=5)
        r = r if isinstance(r, list) else []
        preis = zahl(q, "price")
        eps = zahl(q, "eps")
        kgv = zahl(q, "pe")
        kgv_hist = [x for x in (zahl(j, "priceToEarningsRatio", "priceEarningsRatio") for j in r) if x and 0 < x < 200]
        pfcf_hist = [x for x in (zahl(j, "priceToFreeCashFlowRatio", "priceToFreeCashFlowsRatio") for j in r) if x and 0 < x < 300]
        if not (preis and kgv_hist):
            print(f"  {name}: keine Bewertungsdaten")
            abgelehnt += 1 if not (q or r) else 0
            continue
        abgelehnt = 0
        schnitt = sum(kgv_hist) / len(kgv_hist)
        if not kgv and eps:
            kgv = preis / eps if eps > 0 else None
        e = {"name": name, "fmp": fsym, "preis": preis, "kgv": round(kgv, 2) if kgv else None,
             "kgv_schnitt_5j": round(schnitt, 2), "jahre": len(kgv_hist), "stand": heute}
        if kgv:
            e["abweichung"] = round((kgv / schnitt - 1) * 100, 1)
        if eps and eps > 0:
            e["fair_gewinn"] = round(eps * schnitt, 2)
        if pfcf_hist and r:
            fcf_je = zahl(r[0], "freeCashFlowPerShare")
            if fcf_je and fcf_je > 0:
                e["fair_cashflow"] = round(fcf_je * sum(pfcf_hist) / len(pfcf_hist), 2)
        werte[sym] = e
        print(f"  {name}: KGV {e['kgv']} gegen Schnitt {e['kgv_schnitt_5j']} ({e.get('abweichung')} %)")
        time.sleep(0.4)
    out = {"datum": heute, "quelle": "Financial Modeling Prep", "werte": werte}
    tmp = BEWERTUNG + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, BEWERTUNG)
    print(f"→ {BEWERTUNG}: {len(werte)} Aktien bewertet")


def main():
    liste = aktienliste()
    kurse(liste)
    bewerten(liste)
    return 0


if __name__ == "__main__":
    sys.exit(main())
