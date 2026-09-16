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

CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=1mo&interval=1d"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
TIMEOUT = int(os.environ.get("MARKET_TIMEOUT", "12"))
OUT = "markets.json"

# (Gruppe, Anzeigename, Yahoo-Symbol, Einheit, Nachkommastellen)
# Einheit "pp" heißt: Veränderung in Prozentpunkten statt Prozent (Renditen).
WERTE = [
    ("Indizes",            "DAX",              "^GDAXI",   "",            0),
    ("Indizes",            "Euro Stoxx 50",    "^STOXX50E", "",           0),
    ("Indizes",            "S&P 500",          "^GSPC",    "",            0),
    ("Indizes",            "Hang Seng",        "^HSI",     "",            0),

    ("Energie",            "Brent",            "BZ=F",     "USD/Barrel",  2),
    ("Energie",            "WTI",              "CL=F",     "USD/Barrel",  2),
    ("Energie",            "Erdgas TTF",       "TTF=F",    "EUR/MWh",     2),
    ("Energie",            "Erdgas Henry Hub", "NG=F",     "USD/MMBtu",   2),

    ("Zinsen & Devisen",   "Bund 10 Jahre",    "^TNX",     "%",           2),   # s. Hinweis unten
    ("Zinsen & Devisen",   "EUR/USD",          "EURUSD=X", "",            4),
    ("Zinsen & Devisen",   "EUR/CNY",          "EURCNY=X", "",            4),
    ("Zinsen & Devisen",   "Gold",             "GC=F",     "USD/Unze",    2),
]
# Hinweis: ^TNX ist die US-Rendite (10 Jahre). Für die Bundesanleihe gibt es bei
# Yahoo kein verlässliches freies Symbol; nimm die Bundesbank-Zeitreihe
# (BBSIS.D.I.ZST.ZI.EUR.S1311.B.A604._Z.R.A.A._Z._Z.A) oder lass die Kachel weg.
# Den Anzeigenamen dann bitte mit ändern, sonst steht Falsches im Frontend.


def hole(sym):
    req = Request(CHART.format(sym=sym), headers={"User-Agent": UA, "Accept": "application/json"})
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
    closes, waehrung = werte_aus(hole(sym))
    if len(closes) < 2:
        raise ValueError("keine Kursreihe")
    letzter, vorher = closes[-1], closes[-2]
    if einheit == "%":
        delta = round(letzter - vorher, 2)          # Prozentpunkte
        pp = True
    else:
        delta = round((letzter - vorher) / vorher * 100, 2) if vorher else 0.0
        pp = False
    return {
        "gruppe": gruppe,
        "n": name + (f" ({einheit})" if einheit and einheit != "%" else ""),
        "sym": sym,
        "v": formatiere(letzter, stellen) + (" %" if einheit == "%" else ""),
        "c": delta,
        "pp": pp,
        "s": [round(c, 4) for c in closes[-7:]],     # Mini-Kurve im Frontend
        "waehrung": waehrung,
    }


def main():
    pruefen = "--pruefen" in sys.argv
    items, fehler = [], []
    for gruppe, name, sym, einheit, stellen in WERTE:
        try:
            items.append(einen(gruppe, name, sym, einheit, stellen))
            print(f"  ok   {name:<18} {sym}")
        except (HTTPError, URLError, ValueError, KeyError, json.JSONDecodeError) as e:
            fehler.append((name, sym, f"{type(e).__name__}: {e}"))
            print(f"  ---  {name:<18} {sym}  {type(e).__name__}: {e}")
        time.sleep(0.4)   # freundlich bleiben, sonst kommt 429

    if pruefen:
        print(f"\n{len(items)} von {len(WERTE)} Symbolen liefern Daten.")
        return 0 if items else 1

    if not items:
        print("Kein einziger Kurs abrufbar – markets.json bleibt unverändert.")
        return 1

    gruppen = []
    for g in dict.fromkeys(g for g, *_ in WERTE):
        teil = [i for i in items if i["gruppe"] == g]
        if teil:
            gruppen.append({"grp": g, "items": [{k: v for k, v in i.items() if k != "gruppe"} for i in teil]})

    out = {
        "updated": datetime.now(timezone.utc).isoformat(),
        "quelle": "Yahoo Finance (Chart-API), Tagesschlusskurse",
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
