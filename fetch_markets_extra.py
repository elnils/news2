#!/usr/bin/env python3
"""
fetch_markets_extra.py  –  ergänzt markets.json um die Werte der Wirkungsketten

Läuft NACH fetch_markets.py und fasst dessen Gruppen nicht an: Es ersetzt
nur die Gruppen, die es selbst anlegt ("Agrarrohstoffe", "Branchen und
Themen", "Wasserstand"). Fällt ein Abruf aus, bleibt der alte Wert aus der
vorigen markets.json stehen – nie eine halb leere Datei.

Quellen:
  Yahoo Finance (Chart-Schnittstelle, wie fetch_markets.py) für Futures und ETFs
  Pegelonline der Wasserstraßen- und Schifffahrtsverwaltung für den Rheinpegel Kaub

Reihen je Wert wie in markets.json: 1t (Tag), 1w, 1m, 1j, max.
"""

import json
import os
import sys
import time
from urllib.parse import quote
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError

OUT = "markets.json"
TIMEOUT = int(os.environ.get("MARKET_TIMEOUT", "12"))
PAUSE = float(os.environ.get("MARKET_PAUSE", "0.25"))
UA = {"User-Agent": "Mozilla/5.0 (Presseschau; +https://github.com)"}

GRUPPEN = {
    "Agrarrohstoffe": [
        ("ZW=F", "Weizen", "USc/Bushel", 2),
        ("ZC=F", "Mais", "USc/Bushel", 2),
        ("CC=F", "Kakao", "USD/t", 0),
        ("KC=F", "Kaffee", "USc/lb", 2),
        ("SB=F", "Zucker", "USc/lb", 2),
    ],
    "Branchen und Themen": [
        ("SMH", "VanEck Semiconductor", "USD", 2),
        ("HACK", "Amplify Cybersecurity", "USD", 2),
        ("REMX", "VanEck Rare Earth & Strategic Metals", "USD", 2),
        ("JETS", "U.S. Global Jets", "USD", 2),
        ("EXV5.DE", "iShares STOXX Europe 600 Automobiles", "EUR", 2),
        ("EXV7.DE", "iShares STOXX Europe 600 Chemicals", "EUR", 2),
        ("EXV9.DE", "iShares STOXX Europe 600 Travel & Leisure", "EUR", 2),
        ("EXH5.DE", "iShares STOXX Europe 600 Insurance", "EUR", 2),
    ],
}
# Reihen: (Schlüssel, Zeitraum, Intervall)
REIHEN = [("1t", "1d", "15m"), ("1w", "5d", "60m"), ("1m", "1mo", "1d"), ("1j", "1y", "1d"), ("max", "5y", "1wk")]


def yahoo(sym, rng, iv):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(sym)}?range={rng}&interval={iv}"
    with urlopen(Request(url, headers=UA), timeout=TIMEOUT) as r:
        j = json.loads(r.read().decode("utf-8", "replace"))
    res = ((j.get("chart") or {}).get("result") or [None])[0]
    if not res:
        return None, []
    closes = (((res.get("indicators") or {}).get("quote") or [{}])[0].get("close") or [])
    reihe = [round(c, 4) for c in closes if c is not None]
    last = (res.get("meta") or {}).get("regularMarketPrice")
    return last, reihe


def wert_holen(sym, name, unit, dec):
    serien, last = {}, None
    for key, rng, iv in REIHEN:
        try:
            l, r = yahoo(sym, rng, iv)
            if r:
                serien[key] = r
            if l is not None and last is None:
                last = l
        except (HTTPError, URLError, ValueError, TimeoutError) as e:
            print(f"  {sym} {key}: {type(e).__name__}")
        time.sleep(PAUSE)
    if not serien:
        return None
    if last is None:
        last = next((serien[k][-1] for k in ("1t", "1w", "1m", "1j") if serien.get(k)), None)
    return {"sym": sym, "n": name, "unit": unit, "dec": dec, "last": last,
            "s": serien.get("1m") or serien.get("1j") or [], "series": serien}


def pegel_kaub():
    """Pegelonline: Wasserstand Kaub der letzten 30 Tage (Viertelstundenwerte).
    Die Schnittstelle ist auch unter pegelstaende.de erreichbar – die zweite
    Adresse springt ein, wenn die erste nicht antwortet."""
    daten, fehler = None, None
    for basis in ("https://www.pegelonline.wsv.de", "https://www.pegelstaende.de"):
        url = f"{basis}/webservices/rest-api/v2/stations/KAUB/W/measurements.json?start=P30D"
        try:
            with urlopen(Request(url, headers=UA), timeout=TIMEOUT) as r:
                daten = json.loads(r.read().decode("utf-8", "replace"))
            break
        except (HTTPError, URLError, ValueError, TimeoutError) as e:
            fehler = e
    if daten is None:
        raise fehler or URLError("Pegelonline nicht erreichbar")
    werte = [(d.get("timestamp", ""), d.get("value")) for d in daten if d.get("value") is not None]
    if not werte:
        return None
    alle = [v for _, v in werte]
    tage = {}
    for ts, v in werte:
        tage.setdefault(ts[:10], []).append(v)
    tagesmittel = [round(sum(v) / len(v), 1) for _, v in sorted(tage.items())]
    return {"sym": "PEGEL_KAUB", "n": "Rheinpegel Kaub", "unit": "cm", "dec": 0, "last": alle[-1],
            "s": tagesmittel, "series": {"1t": alle[-96:], "1w": tagesmittel[-7:], "1m": tagesmittel}}


def main():
    try:
        with open(OUT, encoding="utf-8") as fh:
            mk = json.load(fh)
    except Exception:
        mk = {"groups": []}
    alt = {g.get("grp"): {i.get("sym"): i for i in g.get("items") or []} for g in mk.get("groups") or []}
    neu = []
    for grp, werte in GRUPPEN.items():
        items = []
        for sym, name, unit, dec in werte:
            w = wert_holen(sym, name, unit, dec)
            if w is None and sym in (alt.get(grp) or {}):
                w = alt[grp][sym]                      # alter Stand bleibt
                print(f"  {sym}: Abruf fehlgeschlagen, alter Wert bleibt")
            if w:
                items.append(w)
        if items:
            neu.append({"grp": grp, "items": items})
    try:
        p = pegel_kaub()
    except (HTTPError, URLError, ValueError, TimeoutError) as e:
        print(f"  Pegel Kaub: {type(e).__name__}")
        p = (alt.get("Wasserstand") or {}).get("PEGEL_KAUB")
    if p:
        neu.append({"grp": "Wasserstand", "items": [p]})

    eigene = {g["grp"] for g in neu} | set(GRUPPEN) | {"Wasserstand"}
    mk["groups"] = [g for g in mk.get("groups") or [] if g.get("grp") not in eigene] + neu
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(mk, fh, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, OUT)
    print(f"→ {OUT}: {sum(len(g['items']) for g in neu)} Zusatzwerte in {len(neu)} Gruppen")
    return 0


if __name__ == "__main__":
    sys.exit(main())
