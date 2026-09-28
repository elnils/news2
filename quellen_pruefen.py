#!/usr/bin/env python3
"""
quellen_pruefen.py  –  schreibt quellen_status.json (einmal am Tag)

Prüft jede Datenquelle, auf die sich Märkte und Vorausschau stützen – nicht
nur, ob sie antwortet, sondern ob die Antwort stimmt:

  ERREICHBAR   Antwortet die Quelle? (HTTP, gültiges JSON)
  IDENTITÄT    Ist das Kürzel das, was wir meinen? Währung und Börse aus der
               Antwort gegen die Erwartung; bei ETFs ein Stichwort im Namen
               ("Insurance", "Semiconductor").
  AKTUALITÄT   Wie alt ist der letzte Kurs? Mehr als 4 Tage: Warnung,
               mehr als 10 Tage: Fehler (Wochenenden und Feiertage eingerechnet).
  PLAUSIBEL    Liegt der Wert in einer sinnvollen Spanne? (Brent zwischen 20
               und 250 USD – ein Wert von 0,7 hieße: falsche Einheit.)
  BESTAND      Für alles in markets.json, auch Werte ohne Yahoo-Kürzel (etwa
               die Bundrendite): Reihe vorhanden, letzter Wert gesetzt, nicht
               fünfmal derselbe Wert hintereinander (eingefrorener Feed).

Außerdem Pegelonline (beide Adressen) und die GDELT-Schnittstelle.

Ergebnis je Quelle: ok | warnung | fehler, mit Begründung. vorausschau.py
rechnet für Messpunkte mit Status "fehler" keine Wahrscheinlichkeit; die
App zeigt Auffälliges im Abschnitt "Datenquellen".
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from urllib.parse import quote, urlencode
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError

OUT = "quellen_status.json"
TIMEOUT = int(os.environ.get("QP_TIMEOUT", "15"))
PAUSE = float(os.environ.get("QP_PAUSE", "0.3"))
FORCE = os.environ.get("QP_FORCE", "") == "1"
UA = {"User-Agent": "Mozilla/5.0 (Presseschau-Quellenpruefung)"}

# Kürzel · Währung · Stichwort im Namen (leer = nicht prüfen) · plausible Spanne
ERWARTUNG = {
    "^GDAXI": ("EUR", "DAX", (5000, 60000)), "^STOXX50E": ("EUR", "", (1500, 15000)),
    "^GSPC": ("USD", "S&P", (1500, 20000)), "^NDX": ("USD", "NASDAQ", (4000, 50000)),
    "^DJI": ("USD", "Dow", (10000, 100000)), "^N225": ("JPY", "Nikkei", (10000, 100000)),
    "^HSI": ("HKD", "HANG SENG", (8000, 60000)), "^FTSE": ("GBP", "FTSE", (4000, 20000)),
    "^FCHI": ("EUR", "CAC", (3000, 15000)), "^MDAXI": ("EUR", "", (10000, 60000)), "^TECDAX": ("EUR", "", (1000, 10000)),
    "BZ=F": ("USD", "Brent", (20, 250)), "CL=F": ("USD", "Crude", (15, 250)),
    "TTF=F": ("EUR", "", (5, 400)), "NG=F": ("USD", "Natural Gas", (1, 25)),
    "GC=F": ("USD", "Gold", (1000, 10000)), "HG=F": ("USD", "Copper", (1.5, 12)),
    "^TNX": ("USD", "", (0.3, 10)), "EURUSD=X": ("USD", "", (0.8, 1.6)), "EURCNY=X": ("CNY", "", (5.5, 10)),
    "ZW=F": ("USX", "Wheat", (250, 1500)), "ZC=F": ("USX", "Corn", (200, 1000)),
    "CC=F": ("USD", "Cocoa", (1000, 20000)), "KC=F": ("USX", "Coffee", (60, 800)), "SB=F": ("USX", "Sugar", (5, 50)),
    "OJ=F": ("USX", "Orange", (50, 900)),
    "SMH": ("USD", "Semiconductor", (50, 2000)), "HACK": ("USD", "Cyber", (10, 300)),
    "REMX": ("USD", "Rare Earth", (5, 300)), "JETS": ("USD", "Jets", (5, 200)),
    "EXV5.DE": ("EUR", "Automobiles", (10, 200)), "EXV7.DE": ("EUR", "Chemicals", (30, 400)),
    "EXV9.DE": ("EUR", "Travel", (5, 100)), "EXH5.DE": ("EUR", "Insurance", (10, 200)),
    "DFEN.DE": ("EUR", "Defense", (5, 200)), "INRG.L": ("", "Clean Energy", (1, 5000)),
    "IWDA.AS": ("EUR", "World", (30, 300)), "VWCE.DE": ("EUR", "All-World", (30, 400)),
    "EXS1.DE": ("EUR", "DAX", (50, 600)), "XMEU.DE": ("EUR", "Europe", (30, 300)),
    "EXH1.DE": ("EUR", "Oil", (10, 200)), "XDWH.DE": ("EUR", "Health", (20, 200)),
}
# Werte ohne Yahoo-Kürzel: nur Bestand und Spanne prüfen
NUR_BESTAND = {"DE10Y": (-1, 10), "DEBASE27": (10, 500), "EUA": (5, 250), "PEGEL_KAUB": (0, 1200)}


def eintrag(name, typ, sym, status, detail, **mehr):
    return {"name": name, "typ": typ, "sym": sym, "status": status, "detail": detail, **mehr}


def yahoo_pruefen(sym, name_app):
    erw = ERWARTUNG.get(sym)
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(sym)}?range=5d&interval=1d"
    try:
        with urlopen(Request(url, headers=UA), timeout=TIMEOUT) as r:
            j = json.loads(r.read().decode("utf-8", "replace"))
    except HTTPError as e:
        return eintrag(name_app, "Yahoo Finance", sym, "fehler", f"HTTP {e.code} – Kürzel unbekannt oder gesperrt")
    except (URLError, ValueError, TimeoutError) as e:
        return eintrag(name_app, "Yahoo Finance", sym, "fehler", f"nicht erreichbar ({type(e).__name__})")
    res = ((j.get("chart") or {}).get("result") or [None])[0]
    if not res:
        fehl = ((j.get("chart") or {}).get("error") or {}).get("description", "leere Antwort")
        return eintrag(name_app, "Yahoo Finance", sym, "fehler", f"keine Daten: {fehl}")
    meta = res.get("meta") or {}
    probleme, status = [], "ok"
    preis = meta.get("regularMarketPrice")
    zeit = meta.get("regularMarketTime")
    alter_h = round((time.time() - zeit) / 3600, 1) if zeit else None
    if alter_h is None:
        probleme.append("kein Zeitstempel"); status = "warnung"
    elif alter_h > 240:
        probleme.append(f"letzter Kurs {round(alter_h/24)} Tage alt"); status = "fehler"
    elif alter_h > 96:
        probleme.append(f"letzter Kurs {round(alter_h/24)} Tage alt"); status = "warnung"
    if erw:
        waehrung, stichwort, (lo, hi) = erw
        if waehrung and meta.get("currency") and meta.get("currency") != waehrung:
            probleme.append(f"Währung {meta.get('currency')} statt {waehrung}")
            status = "warnung" if status == "ok" else status
        namen = " ".join(str(meta.get(k) or "") for k in ("longName", "shortName"))
        if stichwort and namen.strip() and stichwort.lower() not in namen.lower():
            probleme.append(f"Name „{namen.strip()[:50]}“ enthält nicht „{stichwort}“")
            status = "warnung" if status == "ok" else status
        if preis is not None and not (lo <= preis <= hi):
            probleme.append(f"Wert {preis} außerhalb der plausiblen Spanne {lo}–{hi}")
            status = "fehler"
    return eintrag(name_app, "Yahoo Finance", sym, status, "; ".join(probleme) or "in Ordnung",
                   wert=preis, alter_h=alter_h, waehrung=meta.get("currency"), boerse=meta.get("exchangeName"))


def bestand_pruefen(grp, item):
    sym, name = item.get("sym"), item.get("n", item.get("sym"))
    last = item.get("last")
    reihe = [x for x in ((item.get("series") or {}).get("1m") or item.get("s") or []) if x is not None]
    probleme, status = [], "ok"
    if last is None:
        return eintrag(name, f"Bestand ({grp})", sym, "fehler", "kein aktueller Wert in markets.json")
    if len(reihe) < 5:
        probleme.append("Monatsreihe fehlt oder ist zu kurz"); status = "warnung"
    elif len(set(reihe[-5:])) == 1:
        probleme.append("fünfmal derselbe Wert hintereinander – Feed eingefroren?"); status = "warnung"
    spanne = NUR_BESTAND.get(sym) or (ERWARTUNG.get(sym) or (None, None, None))[2]
    if spanne and not (spanne[0] <= last <= spanne[1]):
        probleme.append(f"Wert {last} außerhalb der plausiblen Spanne {spanne[0]}–{spanne[1]}"); status = "fehler"
    return eintrag(name, f"Bestand ({grp})", sym, status, "; ".join(probleme) or "in Ordnung", wert=last)


def pegel_pruefen():
    for basis in ("https://www.pegelonline.wsv.de", "https://www.pegelstaende.de"):
        url = f"{basis}/webservices/rest-api/v2/stations/KAUB/W/currentmeasurement.json"
        try:
            with urlopen(Request(url, headers=UA), timeout=TIMEOUT) as r:
                d = json.loads(r.read().decode("utf-8", "replace"))
            ts = d.get("timestamp", "")
            try:
                alter = (datetime.now(timezone.utc) - datetime.fromisoformat(ts)).total_seconds() / 3600
            except ValueError:
                alter = None
            status = "ok" if alter is not None and alter < 6 else "warnung"
            return eintrag("Rheinpegel Kaub", "Pegelonline", "PEGEL_KAUB", status,
                           f"{d.get('value')} cm, Stand {ts[:16]}" + ("" if status == "ok" else " – Messung älter als 6 Stunden"),
                           wert=d.get("value"), adresse=basis)
        except (HTTPError, URLError, ValueError, TimeoutError):
            continue
    return eintrag("Rheinpegel Kaub", "Pegelonline", "PEGEL_KAUB", "fehler", "weder pegelonline.wsv.de noch pegelstaende.de erreichbar")


def gdelt_pruefen():
    pfad = "/api/v2/doc/doc?" + urlencode({"query": "oil price", "mode": "timelinevolraw", "timespan": "3d", "format": "json"})
    grund = ""
    for versuch, basis in enumerate(("https://api.gdeltproject.org", "http://api.gdeltproject.org")):
        try:
            if versuch:
                time.sleep(6)                     # GDELT drosselt schnelle Folgeanfragen
            with urlopen(Request(basis + pfad, headers=UA), timeout=35) as r:
                j = json.loads(r.read().decode("utf-8", "replace"))
            n = len(((j.get("timeline") or [{}])[0].get("data") or []))
            return eintrag("GDELT DOC 2.0", "Nachrichtenvolumen", "GDELT", "ok" if n else "warnung",
                           (f"{n} Zeitpunkte geliefert" if n else "Antwort ohne Zeitreihe") + (" (über http)" if versuch else ""))
        except HTTPError as e:
            grund = f"HTTP {e.code}"
        except (URLError, ValueError, TimeoutError) as e:
            grund = f"{type(e).__name__}: {getattr(e, 'reason', e)}"
    return eintrag("GDELT DOC 2.0", "Nachrichtenvolumen", "GDELT", "warnung",
                   f"nicht erreichbar ({grund}) – die Vorausschau läuft ohne zweites Signal")


def main():
    heute = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        alt = json.load(open(OUT, encoding="utf-8"))
    except Exception:
        alt = {}
    if not FORCE and str(alt.get("geprueft", ""))[:10] == heute:
        print("Quellenprüfung: heute schon gelaufen.")
        return 0
    try:
        mk = json.load(open("markets.json", encoding="utf-8"))
    except Exception:
        mk = {"groups": []}
    quellen = []
    namen = {}
    for gr in mk.get("groups") or []:
        for i in gr.get("items") or []:
            namen[i.get("sym")] = i.get("n", i.get("sym"))
            quellen.append(bestand_pruefen(gr.get("grp", ""), i))
    for sym in ERWARTUNG:
        quellen.append(yahoo_pruefen(sym, namen.get(sym, sym)))
        time.sleep(PAUSE)
    quellen.append(pegel_pruefen())
    quellen.append(gdelt_pruefen())
    # Je Kürzel das schlechteste Ergebnis nach vorn (Bestand und Abruf zusammen)
    rang = {"fehler": 0, "warnung": 1, "ok": 2}
    quellen.sort(key=lambda q: (rang[q["status"]], q["name"]))
    zus = {s: sum(1 for q in quellen if q["status"] == s) for s in ("ok", "warnung", "fehler")}
    out = {"geprueft": datetime.now(timezone.utc).isoformat(), "zusammenfassung": zus, "quellen": quellen}
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, OUT)
    print(f"→ {OUT}: {zus['ok']} in Ordnung, {zus['warnung']} Warnungen, {zus['fehler']} Fehler")
    for q in quellen:
        if q["status"] != "ok":
            print(f"  {q['status'].upper():<8} {q['sym']:<10} {q['name'][:30]:<30} {q['detail']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
