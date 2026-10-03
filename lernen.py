#!/usr/bin/env python3
"""
lernen.py  –  die Vorausschau lernt aus ihrer eigenen Bilanz

Jede Einschätzung wird bei ihrer Entstehung protokolliert (lern_protokoll.json):
Messpunkt, Richtung, Startwert, Frist, die Merkmale des gelernten Modells und
die Wahrscheinlichkeit jedes "Experten". Erreicht sie ihre Frist, wird
anhand des Marktarchivs entschieden, ob der Kurs in Thesenrichtung lief.

Daraus drei Verfahren, alle ohne Fremdbibliotheken und eng an den
Referenzimplementierungen orientiert:

1. GEWICHTE DER EXPERTEN – Fixed-Share
   Herbster & Warmuth (1998), "Tracking the Best Expert", Machine Learning 32;
   Erweiterung von Hedge, Freund & Schapire (1997), JCSS 55.
   Experten: Grundrate, regelbasiertes Verfahren (Evidenzindex, Archiv-Fälle,
   Sicherheit), gelerntes Modell (Regression). Nach jedem geprüften Fall:
       w_i ← w_i · exp(−η · (p_i − y)²)        (Brier-Verlust, beschränkt auf [0, 1])
       w_i ← (1 − α) · w_i + α / N             (Fixed-Share: Anteil zurück an alle)
   η_t = √(8 ln N / t) (übliche Wahl für beschränkte Verluste), α = 0,02.
   Fehlt ein Experte bei einem Fall (keine Regression), wird nur unter den
   vorhandenen normiert ("schlafende Experten", Freund u. a. 1997).
   Die kombinierte Wahrscheinlichkeit ist der gewichtete Mittelwert
   (lineares Meinungspooling).

2. KALIBRIERUNG – Platt-Skalierung
   Platt (1999); Zielwerte wie in scikit-learn (CalibratedClassifierCV,
   method="sigmoid"): y+ = (N+ + 1)/(N+ + 2), y− = 1/(N− + 2).
       p_kal = σ(a · logit(p) + b)
   Mit Ridge-Strafe zur Identität (a = 1, b = 0, λ = 8): Bei wenigen Fällen
   bleibt die Kalibrierung fast neutral, mit mehr Fällen greift sie stärker.
   Angewandt erst ab 30 geprüften Fällen.

3. GÜTE – Brier-Zerlegung nach Murphy (1973), J. Appl. Meteor. 12:
       Brier = Zuverlässigkeit − Trennschärfe + Unsicherheit
   dazu ein Zuverlässigkeitsdiagramm (Zehntel-Klassen) und eine Lernkurve
   (gleitender Brier-Wert über die letzten 30 Fälle, je Woche).

Außerdem liefert lernen.py die geprüften eigenen Fälle als zusätzliche
Trainingsfälle an das gelernte Modell (prognose_modell.py).
"""

import json
import math
import os
from datetime import date, datetime, timedelta, timezone

PROTOKOLL = "lern_protokoll.json"
ZUSTAND = "lernen.json"
ALPHA = 0.02
MIN_KALIBRIERUNG = 30
MIN_POOL = 10
EXPERTEN = ["grundrate", "verfahren", "regression", "wirkung"]
NAMEN = {"grundrate": "Grundrate (wie oft der Kurs ohnehin so läuft)",
         "verfahren": "Regelwerk (Evidenzindex, Archiv-Fälle, Sicherheit der KI)",
         "regression": "Gelerntes Modell (Regression)",
         "wirkung": "Wirkungsmodell aus der Ereignistabelle (analyse_auto.py)"}


def _laden(pfad, leer):
    try:
        return json.load(open(pfad, encoding="utf-8"))
    except (OSError, ValueError):
        return leer


def _speichern(pfad, daten):
    tmp = pfad + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(daten, fh, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, pfad)


def _logit(p):
    p = min(1 - 1e-6, max(1e-6, p))
    return math.log(p / (1 - p))


def _sig(z):
    return 1 / (1 + math.exp(-max(-30, min(30, z))))


# ── Protokoll ────────────────────────────────────────────────────────
def protokollieren(eintraege_heute, heute):
    """eintraege_heute: [{id, sym, richtung, tage, startwert, experten:{…}, p, merkmale}] – je Tag und These einmal."""
    prot = _laden(PROTOKOLL, {"faelle": []})
    bekannt = {f["schluessel"] for f in prot["faelle"]}
    neu = 0
    for e in eintraege_heute:
        k = f"{heute}:{e['id']}"
        if k in bekannt or not e.get("sym") or e.get("richtung") not in (1, -1):
            continue
        prot["faelle"].append(dict(e, schluessel=k, datum=heute, ergebnis=None))
        neu += 1
    _speichern(PROTOKOLL, prot)
    return neu


def _ergebnis(fall, kursreihe):
    """1/0, wenn die Frist abgelaufen ist und Kurse vorliegen; sonst None."""
    reihe = kursreihe(fall["sym"])
    try:
        start = date.fromisoformat(fall["datum"])
    except ValueError:
        return None
    nach = [i for i, (d, _) in enumerate(reihe) if d >= start]
    if not nach:
        return None
    i0 = nach[0]
    i1 = i0 + int(fall.get("tage") or 5)
    if i1 >= len(reihe):
        return None
    basis = fall.get("startwert") or reihe[max(0, i0 - 1)][1]
    return 1 if (reihe[i1][1] / basis - 1) * fall["richtung"] > 0 else 0


def pruefen(kursreihe):
    """Abgelaufene Fälle entscheiden. Gibt alle entschiedenen Fälle chronologisch zurück."""
    prot = _laden(PROTOKOLL, {"faelle": []})
    geaendert = False
    for f in prot["faelle"]:
        if f.get("ergebnis") is None:
            y = _ergebnis(f, kursreihe)
            if y is not None:
                f["ergebnis"] = y
                geaendert = True
    # sehr alte offene Fälle (z. B. Kurs nicht mehr verfügbar) nach 120 Tagen verwerfen
    grenze = (date.today() - timedelta(days=120)).isoformat()
    vorher = len(prot["faelle"])
    prot["faelle"] = [f for f in prot["faelle"] if f.get("ergebnis") is not None or f.get("datum", "") >= grenze]
    if geaendert or len(prot["faelle"]) != vorher:
        _speichern(PROTOKOLL, prot)
    return sorted((f for f in prot["faelle"] if f.get("ergebnis") is not None), key=lambda f: f["datum"])


# ── 1. Fixed-Share ───────────────────────────────────────────────────
def gewichte_lernen(faelle):
    w = {e: 1 / len(EXPERTEN) for e in EXPERTEN}
    verlauf = []
    for t, f in enumerate(faelle, 1):
        p = {e: v for e, v in (f.get("experten") or {}).items() if e in w and isinstance(v, (int, float))}
        if not p:
            continue
        eta = math.sqrt(8 * math.log(len(EXPERTEN)) / t)
        y = f["ergebnis"]
        wach = sum(w[e] for e in p)
        for e in p:                                  # nur wache Experten verlieren oder gewinnen
            w[e] *= math.exp(-eta * (p[e] - y) ** 2)
        neu_wach = sum(w[e] for e in p)
        for e in p:                                  # Masse der Wachen erhalten (schlafende Experten)
            w[e] *= wach / neu_wach if neu_wach else 1
        summe = sum(w.values())
        w = {e: (1 - ALPHA) * v / summe + ALPHA / len(EXPERTEN) for e, v in w.items()}
        verlauf.append({"datum": f["datum"], **{e: round(v, 4) for e, v in w.items()}})
    return w, verlauf


def poolen(experten, w):
    p = {e: v for e, v in experten.items() if e in w and isinstance(v, (int, float))}
    if not p:
        return None
    s = sum(w[e] for e in p)
    return sum(w[e] * v for e, v in p.items()) / s


# ── 2. Platt-Skalierung mit Ridge zur Identität ──────────────────────
def kalibrierung_lernen(faelle, lam=8.0):
    paare = [(_logit(f["p"]), f["ergebnis"]) for f in faelle if isinstance(f.get("p"), (int, float))]
    n_pos = sum(y for _, y in paare)
    n_neg = len(paare) - n_pos
    if len(paare) < MIN_KALIBRIERUNG or not n_pos or not n_neg:
        return None
    t_pos, t_neg = (n_pos + 1) / (n_pos + 2), 1 / (n_neg + 2)
    a, b = 1.0, 0.0
    for _ in range(50):                              # Newton auf negativer Log-Likelihood + λ((a−1)² + b²)/2
        ga = gb = haa = hab = hbb = 0.0
        for x, y in paare:
            t = t_pos if y else t_neg
            p = _sig(a * x + b)
            ga += (p - t) * x
            gb += (p - t)
            s = p * (1 - p)
            haa += s * x * x
            hab += s * x
            hbb += s
        ga += lam * (a - 1)
        gb += lam * b
        haa += lam
        hbb += lam
        det = haa * hbb - hab * hab
        if abs(det) < 1e-12:
            break
        da = (hbb * ga - hab * gb) / det
        db = (haa * gb - hab * ga) / det
        a, b = a - da, b - db
        if abs(da) + abs(db) < 1e-8:
            break
    return {"a": round(a, 4), "b": round(b, 4), "n": len(paare)}


def kalibrieren(p, kal):
    return _sig(kal["a"] * _logit(p) + kal["b"]) if kal else p


# ── 3. Murphy-Zerlegung, Zuverlässigkeitsdiagramm, Lernkurve ─────────
def murphy(faelle):
    # bewertet wird die veröffentlichte Zahl (nach Kalibrierung), sonst die Rohzahl
    paare = [(f.get("p_final", f["p"]), f["ergebnis"]) for f in faelle if isinstance(f.get("p"), (int, float))]
    n = len(paare)
    if not n:
        return None
    ybar = sum(y for _, y in paare) / n
    klassen = {}
    for p, y in paare:
        klassen.setdefault(min(9, int(p * 10)), []).append((p, y))
    zuv = sum(len(v) * (sum(p for p, _ in v) / len(v) - sum(y for _, y in v) / len(v)) ** 2 for v in klassen.values()) / n
    tren = sum(len(v) * (sum(y for _, y in v) / len(v) - ybar) ** 2 for v in klassen.values()) / n
    diagramm = [{"klasse": f"{k * 10}–{k * 10 + 10} %", "vorhergesagt": round(sum(p for p, _ in v) / len(v), 3),
                 "eingetreten": round(sum(y for _, y in v) / len(v), 3), "n": len(v)} for k, v in sorted(klassen.items())]
    return {"n": n, "brier": round(sum((p - y) ** 2 for p, y in paare) / n, 4), "zuverlaessigkeit": round(zuv, 4),
            "trennschaerfe": round(tren, 4), "unsicherheit": round(ybar * (1 - ybar), 4),
            "trefferquote": round(sum(1 for p, y in paare if (p >= 0.5) == bool(y)) / n, 3), "diagramm": diagramm}


def lernkurve(faelle, fenster=30):
    kurve, woche = [], None
    for i, f in enumerate(faelle):
        w = datetime.fromisoformat(f["datum"]).strftime("%G-W%V")
        if w == woche and kurve:
            kurve.pop()
        woche = w
        teil = faelle[max(0, i + 1 - fenster):i + 1]
        b = sum((x.get("p_final", x["p"]) - x["ergebnis"]) ** 2 for x in teil) / len(teil)
        g = [x["experten"].get("grundrate") for x in teil if isinstance((x.get("experten") or {}).get("grundrate"), (int, float))]
        bg = sum((p - x["ergebnis"]) ** 2 for p, x in zip(g, teil)) / len(g) if g else None
        kurve.append({"woche": w, "brier": round(b, 4), "brier_grundrate": round(bg, 4) if bg is not None else None, "n": len(teil)})
    return kurve


# ── Gesamtlauf ───────────────────────────────────────────────────────
def aktualisieren(kursreihe):
    faelle = pruefen(kursreihe)
    w, verlauf = gewichte_lernen(faelle)
    kal = kalibrierung_lernen(faelle)
    zustand = {"stand": datetime.now(timezone.utc).isoformat(), "geprueft": len(faelle),
               "offen": len([f for f in _laden(PROTOKOLL, {"faelle": []})["faelle"] if f.get("ergebnis") is None]),
               "gewichte": {e: round(v, 4) for e, v in w.items()}, "namen": NAMEN,
               "pool_aktiv": len(faelle) >= MIN_POOL, "kalibrierung": kal, "guete": murphy(faelle),
               "lernkurve": lernkurve(faelle), "gewichtsverlauf": verlauf[-120:],
               "quellen": ["Herbster & Warmuth (1998): Tracking the Best Expert", "Freund & Schapire (1997): Hedge",
                           "Platt (1999): Probabilistic Outputs for SVMs; Zielwerte wie scikit-learn CalibratedClassifierCV",
                           "Murphy (1973): A New Vector Partition of the Probability Score"]}
    _speichern(ZUSTAND, zustand)
    return zustand, faelle


def trainingsfaelle(faelle):
    """Geprüfte eigene Fälle als (datum, merkmale, ergebnis, id) für das gelernte Modell."""
    out = []
    for f in faelle:
        x = f.get("merkmale")
        import prognose_modell
        felder = tuple(prognose_modell.MERKMALE)
        if isinstance(x, dict) and all(k in x for k in felder):
            try:
                out.append((date.fromisoformat(f["datum"]), [x[k] for k in felder],
                            f["ergebnis"], "eigen:" + f["id"]))
            except (KeyError, ValueError):
                pass
    return out
