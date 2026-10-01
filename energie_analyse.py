#!/usr/bin/env python3
"""
energie_analyse.py  –  schreibt energie_analyse.json

Macht aus den Energiedaten (energie_reihen.json aus dem eigenen Dashboard,
dazu markets_archive für Brent, TTF, Henry Hub und Dollar) drei Dinge:

1. HISTORISCHE EINORDNUNG je Kennzahl (Diesel, Super E5/E10, Heizöl,
   Strompreis Day-Ahead, Gasspeicher, TTF, Brent, CO₂, Henry Hub, Anteil
   Erneuerbare): letzter Wert, Veränderung über 7, 30 und 365 Tage, Lage
   im Verlauf der letzten drei Jahre (Perzentil, Tief, Hoch) und der Wert
   am selben Tag des Vorjahres – wichtig bei saisonalen Größen wie dem
   Gasspeicher.

2. WEITERGABE: Wie schnell und wie stark kommen Großhandelspreise bei
   Verbrauchern an? Gemessen aus den eigenen Daten: Wochenveränderungen
   der Quelle (etwa Brent in Euro) gegen die des Ziels (Diesel) mit
   0 bis 6 Wochen Verzug; der Verzug mit dem stärksten Zusammenhang gewinnt,
   dazu die Elastizität ("10 % Brent ≈ 3 % Diesel") und die Güte (r).
   Nur ausgewiesen, wenn genug Wochen da sind und r ≥ 0,3.

3. PROGNOSEN: Was die Dashboard-Daten von EIA, Weltbank und IWF zu
   Brent, Erdgas und Strom erwarten (Dateien mit steo/forecast/prognose
   im Namen) – als Einordnung für die Vorausschau.

Vorausschau und Deep Dive lesen energie_analyse.json und bekommen daraus
belegte Zahlen; der Trend-Reiter "Energie" zeigt alles in der App.
"""

import json
import math
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone

OUT = "energie_analyse.json"

# Kennzahl → (Name, Einheit-Hinweis, Muster für Pfad, Muster für Feld)
KENNZAHLEN = [
    ("diesel", "Diesel (Tankstelle)", "", r"tank|fuel|kraftstoff|preis|price", r"^diesel$|diesel"),
    ("e5", "Super E5 (Tankstelle)", "", r"tank|fuel|kraftstoff|preis|price", r"^e5$|super.?e5"),
    ("e10", "Super E10 (Tankstelle)", "", r"tank|fuel|kraftstoff|preis|price", r"^e10$|super.?e10"),
    ("heizoel", "Heizöl", "", r"heiz|heating|tecson", r"price|preis|eur|value|wert|heizoel"),
    ("strom", "Strompreis Day-Ahead", "", r"spot|day.?ahead|dayahead|strompreis", r"price|preis|eur|value|wert"),
    ("gasspeicher", "Gasspeicher Deutschland (Füllstand)", "%", r"agsi|storage|speicher", r"full|fill|pct|percent|füll|level"),
    ("ttf", "TTF Erdgas", "EUR/MWh", r"ttf|gas", r"ttf|price|preis|value"),
    ("co2", "CO₂-Zertifikate", "EUR/t", r"co2|ets|eua|emission", r"price|preis|co2|eua|value"),
    ("brent", "Brent", "USD/Barrel", r"brent|oil|öl", r"brent|price|preis|close|value"),
    ("henryhub", "Henry Hub (US-Erdgas)", "USD/MMBtu", r"henry|hh|steo", r"henry|hh"),
    ("ee", "Anteil Erneuerbare am Strom", "%", r"smard|mix|erneuer|renew", r"ee|renew|erneuer|share|anteil"),
]
# Ersatz aus dem eigenen Marktarchiv (markets_archive), falls das Dashboard die Reihe nicht hat
MARKT_ERSATZ = {"brent": "BZ=F", "ttf": "TTF=F", "henryhub": "NG=F", "co2": "EUA", "eurusd": "EURUSD=X"}
# Weitergabe: Quelle → Ziel
WEITERGABE = [("brent_eur", "diesel"), ("brent_eur", "e5"), ("brent_eur", "heizoel"), ("ttf", "strom"), ("co2", "strom")]


def laden(pfad, leer):
    try:
        return json.load(open(pfad, encoding="utf-8"))
    except (OSError, ValueError):
        return leer


def datum(x):
    try:
        return date.fromisoformat(str(x)[:10])
    except ValueError:
        return None


def reihen_finden():
    """Kennzahl → {datum: wert} aus energie_reihen.json; je Kennzahl die längste passende Reihe."""
    roh = laden("energie_reihen.json", {}).get("reihen") or {}
    gefunden = {}
    for pfad, r in roh.items():
        felder = r.get("felder") or []
        for key, name, einheit, m_pfad, m_feld in KENNZAHLEN:
            if not re.search(m_pfad, pfad, re.I):
                continue
            for i, f in enumerate(felder):
                if not re.search(m_feld, f, re.I):
                    continue
                werte = {}
                for p in r.get("punkte") or []:
                    d, v = datum(p[0]), p[1 + i] if len(p) > 1 + i else None
                    if d and isinstance(v, (int, float)) and not isinstance(v, bool):
                        werte[d] = float(v)
                if len(werte) >= 30 and len(werte) > len(gefunden.get(key, {}).get("werte", {})):
                    gefunden[key] = {"werte": werte, "quelle": f"{pfad} · {f}", "name": name, "einheit": einheit}
                break
    return gefunden


def markt_reihe(sym):
    werte = {}
    if not os.path.isdir("markets_archive"):
        return werte
    for f in sorted(os.listdir("markets_archive")):
        if f.endswith(".json"):
            e = (laden(os.path.join("markets_archive", f), {}).get("werte") or {}).get(sym) or {}
            for d, v in (e.get("tage") or {}).items():
                dd = datum(d)
                if dd and isinstance(v, (int, float)):
                    werte[dd] = float(v)
    return werte


def wert_am(werte, tag, toleranz=6):
    """Wert am Tag oder dem nächsten Tag davor (höchstens toleranz Tage)."""
    for i in range(toleranz + 1):
        d = tag - timedelta(days=i)
        if d in werte:
            return werte[d]
    return None


def einordnen(werte):
    tage = sorted(werte)
    letzt = tage[-1]
    w = werte[letzt]
    aend = {}
    for n in (7, 30, 365):
        alt = wert_am(werte, letzt - timedelta(days=n))
        aend[f"d{n}"] = round((w / alt - 1) * 100, 1) if alt else None
    drei = [werte[d] for d in tage if d >= letzt - timedelta(days=3 * 365)]
    perz = round(sum(1 for x in drei if x <= w) / len(drei) * 100) if drei else None
    vorjahr = wert_am(werte, letzt - timedelta(days=365), 4)
    woche = [[d.isoformat(), round(werte[d], 4)] for d in tage if d >= letzt - timedelta(days=365)][::5]
    return {"datum": letzt.isoformat(), "wert": round(w, 4), **aend, "perzentil_3j": perz,
            "tief_3j": round(min(drei), 4) if drei else None, "hoch_3j": round(max(drei), 4) if drei else None,
            "jahre": round((letzt - tage[0]).days / 365, 1), "vorjahr": round(vorjahr, 4) if vorjahr else None, "verlauf": woche}


def wochen(werte):
    """Wochenwerte (freitags bzw. letzter Wert der Woche) → {Wochenbeginn: wert}"""
    out = {}
    for d in sorted(werte):
        out[d - timedelta(days=d.weekday())] = werte[d]
    return out


def weitergabe(quelle, ziel):
    q, z = wochen(quelle), wochen(ziel)
    gemeinsam = sorted(set(q) & set(z))
    if len(gemeinsam) < 40:
        return None
    dq = {w: math.log(q[w] / q[w - timedelta(weeks=1)]) for w in gemeinsam if w - timedelta(weeks=1) in q and q[w] > 0 and q[w - timedelta(weeks=1)] > 0}
    dz = {w: math.log(z[w] / z[w - timedelta(weeks=1)]) for w in gemeinsam if w - timedelta(weeks=1) in z and z[w] > 0 and z[w - timedelta(weeks=1)] > 0}
    best, gesamt = None, 0.0
    for lag in range(0, 7):
        paare = [(dq[w - timedelta(weeks=lag)], dz[w]) for w in dz if w - timedelta(weeks=lag) in dq]
        if len(paare) < 30:
            continue
        xs, ys = [p[0] for p in paare], [p[1] for p in paare]
        mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
        sxx = sum((x - mx) ** 2 for x in xs)
        syy = sum((y - my) ** 2 for y in ys)
        sxy = sum((x - mx) * (y - my) for x, y in paare)
        if sxx <= 0 or syy <= 0:
            continue
        r = sxy / math.sqrt(sxx * syy)
        gesamt += sxy / sxx           # Wochenveränderungen sind kaum korreliert → Summe der Steigungen ≈ Gesamtweitergabe
        if best is None or r > best["r"]:
            best = {"verzug_wochen": lag, "r": round(r, 2), "elastizitaet": round(sxy / sxx, 2), "wochen": len(paare)}
    if best:
        best["gesamt_6w"] = round(gesamt, 2)
    return best if best and best["r"] >= 0.3 else (dict(best, schwach=True) if best else None)


def prognosen():
    out = []
    for pfad, z in ((laden("energie.json", {}).get("dateien")) or {}).items():
        if not re.search(r"steo|forecast|prognos|outlook|futures|termin", pfad, re.I):
            continue
        werte = z.get("letzter") or z.get("werte") or {}
        if werte:
            out.append({"quelle": z.get("name") or pfad, "datei": pfad,
                        "werte": {k: v for k, v in list(werte.items())[:12]}})
        for k, t in (z.get("teile") or {}).items():
            w = t.get("letzter") or t.get("werte") or {}
            if w:
                out.append({"quelle": f"{z.get('name') or pfad} · {k}", "datei": pfad, "werte": {kk: vv for kk, vv in list(w.items())[:12]}})
    return out[:20]


def main():
    gefunden = reihen_finden()
    for key, sym in MARKT_ERSATZ.items():
        if key not in gefunden:
            w = markt_reihe(sym)
            if len(w) >= 30:
                gefunden[key] = {"werte": w, "quelle": f"Marktarchiv {sym}",
                                 "name": dict((k, n) for k, n, *_ in KENNZAHLEN).get(key, "Euro in Dollar" if key == "eurusd" else key),
                                 "einheit": dict((k, e) for k, n, e, *_ in KENNZAHLEN).get(key, "")}
    # Brent in Euro (für die Weitergabe an deutsche Verbraucherpreise)
    if "brent" in gefunden and "eurusd" in gefunden:
        b, fx = gefunden["brent"]["werte"], gefunden["eurusd"]["werte"]
        gefunden["brent_eur"] = {"werte": {d: v / fx_v for d, v in b.items() if (fx_v := wert_am(fx, d, 4))},
                                 "quelle": "Brent ÷ EUR/USD", "name": "Brent in Euro", "einheit": "EUR/Barrel"}
    elif "brent" in gefunden:
        gefunden["brent_eur"] = dict(gefunden["brent"], name="Brent (in Dollar, ohne Umrechnung)")
    kennzahlen = []
    for key, g in gefunden.items():
        if key in ("eurusd",) or len(g["werte"]) < 30:
            continue
        e = einordnen(g["werte"])
        kennzahlen.append({"key": key, "name": g["name"], "einheit": g["einheit"], "quelle": g["quelle"], **e})
    wg = []
    for q, z in WEITERGABE:
        if q in gefunden and z in gefunden:
            r = weitergabe(gefunden[q]["werte"], gefunden[z]["werte"])
            if r:
                nq, nz = gefunden[q]["name"], gefunden[z]["name"]
                komma = lambda x, n=1: f"{x:.{n}f}".replace(".", ",")
                wo = "derselben Woche" if r["verzug_wochen"] == 0 else f"{r['verzug_wochen']} Woche{'n' if r['verzug_wochen'] > 1 else ''}"
                text = (f"10 % bei {nq} kommen innerhalb von sechs Wochen mit etwa {komma(r['gesamt_6w'] * 10)} % bei {nz} an, "
                        f"am stärksten nach {wo} (r = {komma(r['r'], 2)}, {r['wochen']} Wochen Daten)") if not r.get("schwach") else \
                       f"Zwischen {nq} und {nz} kein klarer Zusammenhang in den Wochendaten (r = {komma(r['r'], 2)})"
                wg.append({"von": q, "nach": z, **r, "text": text})
    daten = {"stand": datetime.now(timezone.utc).isoformat(), "kennzahlen": sorted(kennzahlen, key=lambda k: k["key"]),
             "weitergabe": wg, "prognosen": prognosen()}
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(daten, fh, ensure_ascii=False, separators=(",", ":"))
    print(f"→ {OUT}: {len(kennzahlen)} Kennzahlen ({', '.join(k['key'] for k in kennzahlen)}), "
          f"{len(wg)} Weitergabe-Messungen, {len(daten['prognosen'])} Prognosen")
    for w in wg:
        print("   ", w["text"])
    return 0


def zeilen_fuer_ki(max_zeilen=14):
    """Kurzfassung für Vorausschau und Deep Dive: Kennzahl mit Einordnung, Weitergabe, Prognosen."""
    d = laden(OUT, {})
    z = []
    for k in d.get("kennzahlen") or []:
        teile = [f"{k['name']}: {k['wert']}{(' ' + k['einheit']) if k.get('einheit') else ''} ({k['datum']})"]
        teile += [f"{n} T. {k[f'd{n}']:+}%" for n in (7, 30, 365) if k.get(f"d{n}") is not None]
        if k.get("perzentil_3j") is not None:
            teile.append(f"{k['perzentil_3j']}. Perzentil der letzten {min(3, k.get('jahre') or 3)} Jahre")
        if k.get("vorjahr") is not None:
            teile.append(f"Vorjahr {k['vorjahr']}")
        z.append("- " + ", ".join(teile))
    z += [f"- Weitergabe: {w['text']}" for w in d.get("weitergabe") or []]
    z += [f"- Prognose {p['quelle']}: " + ", ".join(f"{k} {v}" for k, v in list(p["werte"].items())[:6]) for p in (d.get("prognosen") or [])[:4]]
    return "\n".join(z[:max_zeilen + 8])


if __name__ == "__main__":
    sys.exit(main())
