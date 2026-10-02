#!/usr/bin/env python3
"""
prognose_modell.py  –  gelerntes Modell für die Vorausschau

FRAGE
Wenn ein Auslöser in der Presseschau steht (z. B. "Angriff auf
Ölinfrastruktur", berichtet von fünf Häusern), wie wahrscheinlich läuft der
Messpunkt (Brent) in den folgenden Handelstagen in die erwartete Richtung?

DATEN
Alle Tage im Archiv (bis 12 Monate), an denen ein Auslöser der
Wissensbasis von mindestens zwei Häusern berichtet wurde – über ALLE
Thesen gemeinsam (gepoolt, sonst zu wenige Fälle je These). Die Kurse
kommen mit Datum aus markets_archive.

MERKMALE (nur, was am jeweiligen Tag schon bekannt war)
  breite        log(1 + Häuser an dem Tag)
  ueberraschung log(Häuser am Tag / (1 + Häuser pro Tag in den 30 Tagen davor))
  serie         an wie vielen der 7 Vortage schon ≥ 2 Häuser berichteten
  momentum      Kursveränderung der 20 Vortage, in Richtung der These gedreht
  vola          Schwankung der 20 Vortage (Standardabweichung der Tagesrenditen)
  abstand       Abstand zum 60-Tage-Mittel, in Richtung der These gedreht
                (weit gelaufen → weniger Luft)

ZIEL
1, wenn der Kurs über die Frist der These in die erwartete Richtung lief.

VERFAHREN
Logistische Regression mit Ridge-Strafe (λ = 1) auf standardisierten
Merkmalen, Newton-Verfahren. Güte über eine zeitlich geordnete
Kreuzvalidierung in fünf Blöcken (nie mit Zukunftsdaten trainiert):
Trefferfläche AUC und Brier-Wert gegen die reine Grundrate.

Das Modell fließt nur ein, wenn es außerhalb der Trainingsdaten besser ist
als die Grundrate (AUC ≥ 0,55 und Brier besser). Sein Gewicht wächst mit
der Güte (höchstens 50 %).
"""

import json
import math
import os
from datetime import date, timedelta

MERKMALE = ["breite", "ueberraschung", "serie", "momentum", "vola", "abstand", "konkret", "eskalation", "groesse"]
NAMEN = {"breite": "Breite der Berichterstattung", "ueberraschung": "Überraschung gegenüber den 30 Vortagen",
         "serie": "Berichterstattung schon an Vortagen", "momentum": "Kursbewegung der 20 Vortage (in Thesenrichtung)",
         "vola": "Schwankung des Messpunkts", "abstand": "Abstand zum 60-Tage-Mittel (in Thesenrichtung)",
         "konkret": "Text: Geschehenes statt Möglichem (beschlossen, verhängt – nicht erwägt, droht)",
         "eskalation": "Text: Verschärfung statt Entspannung (Rekord, massiv – nicht Einigung, Waffenruhe)",
         "groesse": "Text: konkrete Größenangaben (Milliarden, Prozent, Barrel, Tonnen)"}

# ── Textmerkmale aus Schlagzeile und Vorspann ──
# Eigene kleine Wortlisten (deutsch und englisch). Bewusst nur Schlagzeile und
# Vorspann: Nur die liegen auch für die Archivmeldungen vor, aus denen das
# Modell lernt – Training und Anwendung sehen dieselbe Art Text.
import re as _re
_KONKRET = _re.compile(r"\b(beschlossen|beschließt|verhängt|verhängen|angegriffen|getroffen|gestoppt|stoppt|ausgefallen|"
                       r"eingestellt|gesperrt|blockiert|verabschiedet|unterzeichnet|in kraft|bestätigt|meldet|"
                       r"imposed|announced|approved|struck|halted|shut|closed|banned|signed|confirmed|seized)\b", _re.I)
_VAGE = _re.compile(r"\b(könnte|könnten|erwägt|erwägen|prüft|droht|drohen|warnt|befürchtet|plant|soll|sollen|wohl|möglich\w*|"
                    r"gerücht\w*|may|might|could|considers?|weighs|threatens?|warns?|plans?|reportedly|possible)\b", _re.I)
_SCHARF = _re.compile(r"\b(rekord\w*|massiv\w*|schwerst\w*|eskal\w*|notstand|explosion\w*|großangriff\w*|einbruch|"
                      r"verschärf\w*|krise|panik|record|massive|surge\w*|soar\w*|plunge\w*|escalat\w*|emergency|crisis)\b", _re.I)
_MILDE = _re.compile(r"\b(entspann\w*|einigung|waffenruhe|rücknahme|lockerung|beruhig\w*|erholung|kompromiss|"
                     r"eased?|easing|deal|truce|ceasefire|agreement|calm\w*|recover\w*|rollback)\b", _re.I)
_GROESSE = _re.compile(r"\d[\d.,]*\s*(%|prozent|milliard\w*|billion\w*|million\w*|mrd|barrel|tonnen|tons|mwh|gw|dollar|euro)", _re.I)


def textmerkmale(texte):
    """[konkret, eskalation, groesse] je in [−1, 1] bzw. [0, 1]; 0 ohne Texte."""
    if not texte:
        return [0.0, 0.0, 0.0]
    n = len(texte)
    k = sum(1 for t in texte if _KONKRET.search(t)) - sum(1 for t in texte if _VAGE.search(t))
    e = sum(1 for t in texte if _SCHARF.search(t)) - sum(1 for t in texte if _MILDE.search(t))
    g = sum(1 for t in texte if _GROESSE.search(t))
    return [k / n, e / n, g / n]


def _laden(pfad, leer):
    try:
        return json.load(open(pfad, encoding="utf-8"))
    except (OSError, ValueError):
        return leer


_REIHEN = {}


def kursreihe(sym):
    """[(datum, wert)] aufsteigend aus markets_archive."""
    if sym in _REIHEN:
        return _REIHEN[sym]
    werte = {}
    if os.path.isdir("markets_archive"):
        for f in sorted(os.listdir("markets_archive")):
            if f.endswith(".json"):
                for d, v in (((_laden(os.path.join("markets_archive", f), {}).get("werte") or {}).get(sym) or {}).get("tage") or {}).items():
                    try:
                        if isinstance(v, (int, float)) and v > 0:
                            werte[date.fromisoformat(d)] = float(v)
                    except ValueError:
                        pass
    _REIHEN[sym] = sorted(werte.items())
    return _REIHEN[sym]


def _index_bis(reihe, tag):
    """Index des letzten Kurses an oder vor dem Tag (binäre Suche)."""
    lo, hi = 0, len(reihe) - 1
    if not reihe or reihe[0][0] > tag:
        return -1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if reihe[mid][0] <= tag:
            lo = mid
        else:
            hi = mid - 1
    return lo


def merkmale(tag, haeuser_tag, tage_map, reihe, richtung, texte=None):
    """Merkmale am Tag; tage_map: {datum_iso: anzahl_haeuser}. None, wenn Kurse fehlen."""
    i = _index_bis(reihe, tag - timedelta(days=1))
    if i < 60:
        return None
    vor = [reihe[k][1] for k in range(i - 60, i + 1)]
    renditen = [math.log(vor[k] / vor[k - 1]) for k in range(len(vor) - 20, len(vor))]
    mittel = sum(renditen) / len(renditen)
    vola = math.sqrt(sum((r - mittel) ** 2 for r in renditen) / len(renditen))
    momentum = math.log(vor[-1] / vor[-21]) * richtung
    abstand = (vor[-1] / (sum(vor) / len(vor)) - 1) * richtung
    davor = [tage_map.get((tag - timedelta(days=k)).isoformat(), 0) for k in range(1, 31)]
    serie = sum(1 for k in range(1, 8) if tage_map.get((tag - timedelta(days=k)).isoformat(), 0) >= 2)
    return [math.log(1 + haeuser_tag), math.log((haeuser_tag + 0.5) / (1 + sum(davor) / 30)), serie,
            momentum, vola, abstand] + textmerkmale(texte)


def ergebnis(tag, reihe, tage, richtung):
    """1/0: lief der Kurs von vor dem Tag bis 'tage' Handelstage danach in Richtung? None ohne Daten."""
    i = _index_bis(reihe, tag - timedelta(days=1))
    if i < 0 or i + tage >= len(reihe):
        return None
    return 1 if (reihe[i + tage][1] / reihe[i][1] - 1) * richtung > 0 else 0


# ── Logistische Regression (Ridge, Newton), ohne Fremdbibliotheken ──
def _loesen(A, b):
    n = len(b)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(M[r][c]))
        M[c], M[p] = M[p], M[c]
        if abs(M[c][c]) < 1e-12:
            return [0.0] * n
        for r in range(n):
            if r != c:
                f = M[r][c] / M[c][c]
                M[r] = [M[r][k] - f * M[c][k] for k in range(n + 1)]
    return [M[i][n] / M[i][i] for i in range(n)]


def _sig(z):
    return 1 / (1 + math.exp(-max(-30, min(30, z))))


def anpassen(X, y, lam=1.0, schritte=25, gewichte=None):
    n, k = len(X), len(X[0])
    g_i = gewichte or [1.0] * n
    mu = [sum(r[j] for r in X) / n for j in range(k)]
    sd = [math.sqrt(sum((r[j] - mu[j]) ** 2 for r in X) / n) or 1.0 for j in range(k)]
    Z = [[1.0] + [(r[j] - mu[j]) / sd[j] for j in range(k)] for r in X]
    w = [0.0] * (k + 1)
    for _ in range(schritte):
        p = [_sig(sum(wi * zi for wi, zi in zip(w, z))) for z in Z]
        g = [sum(g_i[i] * (p[i] - y[i]) * Z[i][j] for i in range(n)) + (lam * w[j] if j else 0) for j in range(k + 1)]
        H = [[sum(g_i[i] * p[i] * (1 - p[i]) * Z[i][a] * Z[i][b] for i in range(n)) + (lam if a == b and a else 0)
              for b in range(k + 1)] for a in range(k + 1)]
        d = _loesen(H, g)
        w = [wi - di for wi, di in zip(w, d)]
        if max(abs(x) for x in d) < 1e-6:
            break
    return {"w": w, "mu": mu, "sd": sd}


def vorhersagen(m, x):
    z = m["w"][0] + sum(m["w"][j + 1] * (x[j] - m["mu"][j]) / m["sd"][j] for j in range(len(x)))
    return _sig(z)


def auc(p, y):
    pos = [a for a, b in zip(p, y) if b == 1]
    neg = [a for a, b in zip(p, y) if b == 0]
    if not pos or not neg:
        return None
    gewonnen = sum(1.0 if a > b else 0.5 if a == b else 0.0 for a in pos for b in neg)
    return gewonnen / (len(pos) * len(neg))


def trainieren(eintraege, archiv_art, treffer_fn, sym_fn, min_haeuser=2, extra=None, halbwertszeit=180):
    """Gepooltes Modell über alle Thesen. sym_fn(eintrag) → (sym, richtung, tage) oder None."""
    faelle = []
    for e in eintraege:
        mp = sym_fn(e)
        if not mp:
            continue
        sym, richtung, tage = mp
        reihe = kursreihe(sym)
        if len(reihe) < 90:
            continue
        roh = treffer_fn(e, archiv_art) or {}
        tage_map = {t: len(v["haeuser"]) for t, v in roh.items()}
        for t, n in tage_map.items():
            if n < min_haeuser:
                continue
            try:
                tag = date.fromisoformat(t)
            except ValueError:
                continue
            x = merkmale(tag, n, tage_map, reihe, richtung, roh[t].get("texte"))
            yv = ergebnis(tag, reihe, tage, richtung)
            if x is not None and yv is not None:
                faelle.append((tag, x, yv, e["id"]))
    # Eigene geprüfte Einschätzungen (lernen.py) kommen dazu
    n_eigen = len(extra or [])
    faelle += list(extra or [])
    faelle.sort(key=lambda f: f[0])
    n = len(faelle)
    # Gewicht je Fall: neuere zählen mehr (Halbwertszeit), eigene Einschätzungen 1,5-fach
    letzt = faelle[-1][0] if faelle else date.today()
    gew = [(1.5 if str(f[3]).startswith("eigen:") else 1.0) * 0.5 ** ((letzt - f[0]).days / halbwertszeit) for f in faelle]
    info = {"n": n, "eigene": n_eigen, "thesen": len({str(f[3]).replace("eigen:", "") for f in faelle}), "genutzt": False,
            "halbwertszeit_tage": halbwertszeit}
    if n < 80:
        info["grund"] = f"zu wenige Fälle im Archiv ({n}, nötig 80)"
        return None, info
    # zeitlich geordnete Kreuzvalidierung: Block k wird mit allen früheren Blöcken vorhergesagt
    bloecke = 5
    grenze = [round(n * i / bloecke) for i in range(bloecke + 1)]
    p_cv, y_cv, basis = [], [], []
    for b in range(1, bloecke):
        train, test = faelle[:grenze[b]], faelle[grenze[b]:grenze[b + 1]]
        if len(train) < 40 or not test:
            continue
        m = anpassen([f[1] for f in train], [f[2] for f in train], gewichte=gew[:grenze[b]])
        rate = sum(f[2] for f in train) / len(train)
        for f in test:
            p_cv.append(vorhersagen(m, f[1]))
            y_cv.append(f[2])
            basis.append(rate)
    modell = anpassen([f[1] for f in faelle], [f[2] for f in faelle], gewichte=gew)
    a = auc(p_cv, y_cv)
    brier = sum((p - yv) ** 2 for p, yv in zip(p_cv, y_cv)) / len(y_cv) if y_cv else None
    brier_b = sum((p - yv) ** 2 for p, yv in zip(basis, y_cv)) / len(y_cv) if y_cv else None
    info.update({"auc_cv": round(a, 3) if a else None, "brier_cv": round(brier, 4) if brier else None,
                 "brier_grundrate": round(brier_b, 4) if brier_b else None,
                 "trefferquote": round(sum(f[2] for f in faelle) / n, 3),
                 "koeffizienten": {NAMEN[k]: round(modell["w"][j + 1], 3) for j, k in enumerate(MERKMALE)}})
    if a and a >= 0.55 and brier is not None and brier_b is not None and brier < brier_b:
        info["genutzt"] = True
        info["gewicht"] = round(min(0.5, (a - 0.5) * 4), 2)
    else:
        info["grund"] = "außerhalb der Trainingsdaten nicht besser als die Grundrate"
    return modell, info


def heute(modell, eintrag, archiv_art, treffer_fn, sym, richtung, haeuser_heute, tag, texte=None):
    reihe = kursreihe(sym)
    tage_map = {t: len(v["haeuser"]) for t, v in (treffer_fn(eintrag, archiv_art) or {}).items()}
    x = merkmale(tag, haeuser_heute, tage_map, reihe, richtung, texte)
    if x is None or not modell:
        return None, None
    return vorhersagen(modell, x), dict(zip(MERKMALE, [round(v, 3) for v in x]))
