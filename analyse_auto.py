#!/usr/bin/env python3
"""
analyse_auto.py  –  semi-autonomer Analysealgorithmus über der Ereignistabelle

Läuft nach ontologie.py und vor der Vorausschau, ohne KI. Niemand muss die
Auswertung lesen: der Algorithmus sucht Muster selbst, sichert sie ab, prüft
sie an neueren Daten, übernimmt Bewährtes und sortiert Nachlassendes aus.

AUTONOMIE (asymmetrisch)
  automatisch      Mustersuche, Tests, Prüfung, Lebenszyklus der Befunde,
                   Wirkungsmodell als Experte (Gewicht regelt Fixed-Share
                   selbst – es korrigiert sich, wenn es schlecht vorhersagt)
  mit Freigabe     Änderungen an der Wissensbasis (Effekt verstärken,
                   vorläufige These bestätigen, These streichen)
  wahlweise auto   nur ABSCHWÄCHEN von Thesen, deren Wirkung sich nicht
                   bestätigt (vorsichtige Richtung), über
                   analyse_freigabe.json → "autonomie": "abschwaechen"

1. MUSTERSUCHE (Untergruppen-Entdeckung)
   Bedingungen werden aus den Daten erzeugt: jeder Wert jeder Spalte
   (kategorie=lieferstoerung, route=hormus …), Schwellen (schwere ≥ 4,
   kette ≥ 2, unsicherheit ≥ 0,5 …) und Paare davon. Getestet je Bedingung
   gegen drei Zielgrößen:
     richtung   standardisierte Bewegung in erwarteter Richtung (z5), Mittel ≠ 0
     betrag     |z5| größer als beim Rest (bewegt es überhaupt?)
     nachhall   Folgemeldungen (log) größer als beim Rest
   t-Test (Student, Welch beim Vergleich), exakte t-Verteilung.

2. ABSICHERUNG GEGEN ZUFALLSFUNDE
   Benjamini-Hochberg (1995): Falscherkennungsrate ≤ 10 % über alle Tests.
   Zeitliche Trennung: entdeckt wird auf den älteren 70 %, geprüft auf den
   neuesten 30 %. Bestätigt nur bei gleichem Vorzeichen, einseitigem
   p < 0,05 und mindestens halber Effektstärke im Prüfzeitraum (≥ 8 Fälle).
   Simuliert: unter 0,1 Scheinbefunde je Datensatz ohne echte Muster.

3. LEBENSZYKLUS  (ereignisse/befunde.json)
   kandidat → bestätigt → nachlassend → verworfen, mit Verlauf. Bestätigte
   Befunde werden laufend an den letzten 90 Tagen gemessen (Drift). Drei
   Läufe in Folge ohne Bestätigung → nachlassend, sechs → verworfen;
   Vorzeichenwechsel zählt doppelt.

4. WIRKUNGSMODELL
   Ridge-Regression der gerichteten Bewegung auf die Ereignisspalten,
   zeitlich geordnete Kreuzvalidierung (wachsendes Fenster). Nutzbar nur bei
   R² außerhalb der Stichprobe > 0 und mindestens 40 Fällen. Daraus die
   Wahrscheinlichkeit, dass der Messpunkt in Thesenrichtung läuft:
   P = Φ(μ̂ / σ̂) – vierter Experte "wirkung" im Fixed-Share-Verbund.
   Bestätigte Muster aus 1–3 werden automatisch als Spalten aufgenommen
   (bis zu fünf); ob sie helfen, entscheidet die Prüfung außerhalb der
   Stichprobe – der Kreis aus Entdecken, Prüfen und Nutzen schließt sich.

5. PRÜFUNG DER WISSENSBASIS
   Je These: gemessene Wirkung (n, Mittel, Trefferquote, q-Wert) gegen die
   eingetragene Stärke. Daraus Vorschläge mit Begründung:
   abschwaechen | verstaerken | bestaetigen (vorläufig → fest) | pruefen.

FREIGABE  (analyse_freigabe.json, von Hand gepflegt)
  {"autonomie": "vorschlagen" | "abschwaechen",
   "angenommen": ["VS-…"], "abgelehnt": ["VS-…"]}
  Angenommene Vorschläge werden im nächsten Lauf angewendet und
  protokolliert, abgelehnte nicht wieder vorgeschlagen.
  Der Bericht steht im Protokoll des Workflow-Laufs (Zusammenfassung).
"""

import hashlib
import json
import math
import os
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import ereignisse as ev
import prognose_modell as pm

BERLIN = ZoneInfo("Europe/Berlin")
BEFUNDE = os.path.join(ev.ORDNER, "befunde.json")
ANALYSE = "analyse.json"
FREIGABE = "analyse_freigabe.json"
BASIS = "vorausschau_basis.json"
MIN_N = int(os.environ.get("ANALYSE_MIN_N", "8"))
FDR = float(os.environ.get("ANALYSE_FDR", "0.10"))
ANTEIL_ENTDECKUNG = 0.7
PRUEF_P = 0.05            # einseitig im Prüfzeitraum, zusätzlich ≥ halbe Effektstärke
MAX_PAARE = 600
STUFEN = ["schwach", "mittel", "stark"]
EFFEKT_VORGABE = {}
ROHSTOFF_ENDE = "=F"


def _laden(pfad, leer):
    try:
        return json.load(open(pfad, encoding="utf-8"))
    except (OSError, ValueError):
        return leer


def _speichern(pfad, daten, kompakt=False):
    tmp = pfad + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(daten, fh, ensure_ascii=False, **({"separators": (",", ":")} if kompakt else {"indent": 1}))
    os.replace(tmp, pfad)


# ── Statistik ohne Fremdbibliotheken ─────────────────────────────────
def _betacf(a, b, x):
    qab, qap, qam = a + b, a + 1, a - 1
    c, d = 1.0, 1 - qab * x / qap
    d = 1 / (d if abs(d) > 1e-30 else 1e-30)
    h = d
    for m in range(1, 200):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1 + aa * d
        d = 1 / (d if abs(d) > 1e-30 else 1e-30)
        c = 1 + aa / c if abs(1 + aa / c) > 1e-30 else 1e-30
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1 + aa * d
        d = 1 / (d if abs(d) > 1e-30 else 1e-30)
        c = 1 + aa / c if abs(1 + aa / c) > 1e-30 else 1e-30
        dl = d * c
        h *= dl
        if abs(dl - 1) < 3e-12:
            break
    return h


def betai(a, b, x):
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    bt = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log(1 - x))
    return bt * _betacf(a, b, x) / a if x < (a + 1) / (a + b + 2) else 1 - bt * _betacf(b, a, 1 - x) / b


def p_zweiseitig(t, df):
    if df <= 0 or t is None or math.isnan(t):
        return 1.0
    return betai(df / 2, 0.5, df / (df + t * t))


def _mw_sd(x):
    n = len(x)
    m = sum(x) / n
    return m, (math.sqrt(sum((v - m) ** 2 for v in x) / (n - 1)) if n > 1 else 0.0)


def t_einstichprobe(x):
    n = len(x)
    if n < 3:
        return None
    m, sd = _mw_sd(x)
    if sd == 0:
        return None
    t = m / (sd / math.sqrt(n))
    return {"n": n, "effekt": round(m, 3), "t": round(t, 2), "p": p_zweiseitig(t, n - 1)}


def t_welch(a, b):
    if len(a) < 3 or len(b) < 3:
        return None
    ma, sa = _mw_sd(a)
    mb, sb = _mw_sd(b)
    va, vb = sa * sa / len(a), sb * sb / len(b)
    if va + vb == 0:
        return None
    t = (ma - mb) / math.sqrt(va + vb)
    df = (va + vb) ** 2 / ((va * va / (len(a) - 1) if len(a) > 1 else 0) + (vb * vb / (len(b) - 1) if len(b) > 1 else 0) or 1e-12)
    return {"n": len(a), "effekt": round(ma - mb, 3), "t": round(t, 2), "p": p_zweiseitig(t, df)}


def benjamini_hochberg(ps, m=None):
    """q-Werte (Benjamini & Hochberg 1995). m = Zahl der Hypothesen, aus denen
    ausgewählt wurde (bei Vorauswahl größer als die Zahl der Tests; die nicht
    getesteten zählen wie p = 1 am Ende der Rangfolge)."""
    k = len(ps)
    m = max(k, m or 0)
    ordnung = sorted(range(k), key=lambda i: ps[i])
    q = [1.0] * k
    vorher = 1.0
    for rang in range(k, 0, -1):
        i = ordnung[rang - 1]
        vorher = min(vorher, ps[i] * m / rang)
        q[i] = vorher
    return q


def phi(z):
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


# ── Zielgrößen ───────────────────────────────────────────────────────
def zielwerte(zeilen, basis):
    """Gerichtete Bewegung: Thesenrichtung, wenn am Messpunkt gemessen; bei
    Rohstoffen die erwartete Preisrichtung; sonst nur Betrag und Nachhall."""
    mp = {e["id"]: (e.get("messpunkt") or {}) for e in basis}
    for z in zeilen:
        z["_r"] = None
        if z.get("z5") is None:
            continue
        m = mp.get(z.get("these")) or {}
        if m.get("sym") and m.get("sym") == z.get("wirkung_sym") and m.get("richtung") in (1, -1):
            z["_r"] = z["z5"] * m["richtung"]
        elif (z.get("wirkung_sym") or "").endswith(ROHSTOFF_ENDE) and z.get("preis") in (1, -1):
            z["_r"] = z["z5"] * z["preis"]
    return zeilen


ZIELE = {
    "richtung": lambda z: z.get("_r"),
    "betrag": lambda z: abs(z["z5"]) if z.get("z5") is not None else None,
    "nachhall": lambda z: math.log1p(z["nachhall7"]) if isinstance(z.get("nachhall7"), (int, float)) else None,
}


# ── Bedingungen aus den Daten erzeugen ───────────────────────────────
KATEGORISCH = ["kategorie", "status", "umfang", "dauer", "route", "neuheit", "quelle_art", "hs"]
LISTEN = {"branchen": "branche", "laender": "land"}
SCHWELLEN = [("schwere", ">=", 4), ("schwere", "<=", 2), ("kette", ">=", 2), ("kette", ">=", 3),
             ("unsicherheit", ">=", 0.5), ("unsicherheit", "<=", 0.1), ("bestaetigt", "==", 1), ("haeuser", ">=", 5),
             ("haeuser", ">=", 10), ("ausbreitung_std", "<=", 3), ("konkret", ">=", 0.5), ("eskalation", ">=", 0.3),
             ("groesse", ">=", 0.3), ("angebot", "==", -1), ("nachfrage", "==", -1), ("preis", "==", 1), ("preis", "==", -1)]


def _gilt(z, b):
    feld, op, wert = b
    if op == "∋":
        return wert in (z.get(feld) or [])
    v = z.get(feld)
    if op == "=":
        return v == wert
    if not isinstance(v, (int, float)) or isinstance(v, bool) and op != "==":
        return False
    return {"==": v == wert, ">=": v >= wert, "<=": v <= wert}[op]


def bedingungen(zeilen):
    einzeln = []
    for f in KATEGORISCH:
        for w, n in Counter(z.get(f) for z in zeilen if z.get(f) not in (None, "", "keine", "unklar")).items():
            if n >= MIN_N:
                einzeln.append((f, "=", w))
    for f in LISTEN:
        for w, n in Counter(x for z in zeilen for x in (z.get(f) or [])).items():
            if n >= MIN_N:
                einzeln.append((f, "∋", w))
    for b in SCHWELLEN:
        if sum(1 for z in zeilen if _gilt(z, b)) >= MIN_N:
            einzeln.append(b)
    menge = {b: frozenset(i for i, z in enumerate(zeilen) if _gilt(z, b)) for b in einzeln}
    paare = []
    for i, a in enumerate(einzeln):
        for b in einzeln[i + 1:]:
            if a[0] == b[0]:
                continue
            n = len(menge[a] & menge[b])
            if n >= MIN_N and n < min(len(menge[a]), len(menge[b])):
                paare.append(((a, b), n))
    paare.sort(key=lambda x: -x[1])
    return [(b,) for b in einzeln] + [p for p, _ in paare[:MAX_PAARE]]


def text_bedingung(bed):
    t = []
    for f, op, w in bed:
        t.append(f"{LISTEN.get(f, f)} {'enthält' if op == '∋' else op} {w}")
    return " und ".join(t)


def bid(bed, ziel):
    return "B-" + hashlib.sha1((ziel + "|" + repr(sorted(bed))).encode()).hexdigest()[:10]


def testen(zeilen, bed, ziel):
    fn = ZIELE[ziel]
    drin = [fn(z) for z in zeilen if all(_gilt(z, b) for b in bed)]
    drin = [v for v in drin if v is not None]
    if ziel == "richtung":
        return t_einstichprobe(drin) if len(drin) >= 3 else None
    rest = [fn(z) for z in zeilen if not all(_gilt(z, b) for b in bed)]
    return t_welch(drin, [v for v in rest if v is not None])


# ── Mustersuche mit FDR, zeitlicher Prüfung und Lebenszyklus ─────────
def mustersuche(zeilen, heute):
    """Gestufte Suche: erst Einzelbedingungen, Paare nur aus Bedingungen mit
    schwachem Signal (p < 0,25) für dieselbe Zielgröße. Benjamini-Hochberg je
    Familie (Zielgröße × Stufe); bei Paaren über alle Kandidatenpaare gerechnet,
    damit die Vorauswahl die Fehlerrate nicht verzerrt. Der eigentliche Schutz gegen Zufallsfunde ist
    die Prüfung an den neuesten 30 %, die bei der Suche nie benutzt werden."""
    zeilen = sorted(zeilen, key=lambda z: z["datum"])
    k = int(len(zeilen) * ANTEIL_ENTDECKUNG)
    entd, pruef = zeilen[:k], zeilen[k:]
    grenze90 = (heute - timedelta(days=90)).isoformat()
    jung = [z for z in zeilen if z["datum"] >= grenze90]
    alle = bedingungen(entd)
    einzeln = [b for b in alle if len(b) == 1]
    paare = [b for b in alle if len(b) == 2]
    familien = {}
    for ziel in ZIELE:
        e_tests = [(bed, testen(entd, bed, ziel)) for bed in einzeln]
        e_tests = [(bed, r) for bed, r in e_tests if r and r["n"] >= MIN_N]
        familien[(ziel, 1)] = (e_tests, len(e_tests))
        schwach = {bed[0] for bed, r in e_tests if r["p"] < 0.25}
        p_tests = [(bed, testen(entd, bed, ziel)) for bed in paare if bed[0] in schwach and bed[1] in schwach]
        # Korrektur über ALLE Kandidatenpaare, nicht nur die vorausgewählten
        familien[(ziel, 2)] = ([(bed, r) for bed, r in p_tests if r and r["n"] >= MIN_N], len(paare))
    n_tests = sum(len(v[0]) for v in familien.values())
    ergebnisse = {}
    for (ziel, stufe), (tests, m) in familien.items():
        if not tests:
            continue
        qs = benjamini_hochberg([r["p"] for _, r in tests], m)
        for (bed, r), q in zip(tests, qs):
            if q > FDR:
                continue
            v = testen(pruef, bed, ziel)
            if not v or v["n"] < max(8, MIN_N):
                urteil = "kandidat"
            elif v["effekt"] * r["effekt"] < 0:
                urteil = "widerlegt"
            elif v["p"] / 2 < PRUEF_P and abs(v["effekt"]) >= 0.5 * abs(r["effekt"]):
                urteil = "bestaetigt"
            else:
                urteil = "offen"
            d = testen(jung, bed, ziel)
            ergebnisse[bid(bed, ziel)] = {"bedingung": [list(x) for x in bed], "text": text_bedingung(bed), "ziel": ziel,
                                          "entdeckung": {**r, "p": round(r["p"], 5), "q": round(q, 4)},
                                          "pruefung": {**v, "p": round(v["p"], 5)} if v else None,
                                          "drift90": {**d, "p": round(d["p"], 5)} if d else None, "urteil": urteil}
    return ergebnisse, n_tests


def lebenszyklus(alt, neu, heute):
    """kandidat → bestätigt → nachlassend → verworfen (mit Gedächtnis)."""
    tag = heute.isoformat()
    befunde = dict(alt)
    for i, e in neu.items():
        b = befunde.get(i) or {"id": i, "erstmals": tag, "status": "kandidat", "fehlschlaege": 0, "verlauf": []}
        b.update({k: e[k] for k in ("bedingung", "text", "ziel", "entdeckung", "pruefung", "drift90")})
        drift_kippt = (b["status"] == "bestaetigt" and e.get("drift90") and e["drift90"]["n"] >= 4
                       and e["drift90"]["effekt"] * e["entdeckung"]["effekt"] < 0)
        if e["urteil"] == "bestaetigt" and not drift_kippt:
            b["status"], b["fehlschlaege"], b["zuletzt_bestaetigt"] = "bestaetigt", 0, tag
        elif e["urteil"] == "widerlegt" or drift_kippt:
            b["fehlschlaege"] += 2
        elif b["status"] == "bestaetigt":
            b["fehlschlaege"] += 1
        b["verlauf"] = (b["verlauf"] + [{"datum": tag, "urteil": "drift" if drift_kippt else e["urteil"],
                                          "effekt": e["entdeckung"]["effekt"],
                                          "pruef": (e.get("pruefung") or {}).get("effekt")}])[-30:]
        befunde[i] = b
    for i, b in befunde.items():
        if i not in neu:                     # nicht mehr signifikant entdeckt
            b["fehlschlaege"] = b.get("fehlschlaege", 0) + (1 if b["status"] in ("bestaetigt", "nachlassend") else 0)
            if b["status"] == "kandidat":
                b["status"] = "verworfen"
        if b["status"] in ("bestaetigt", "nachlassend"):
            b["status"] = "verworfen" if b["fehlschlaege"] >= 6 else "nachlassend" if b["fehlschlaege"] >= 3 else b["status"]
    # Verworfene nach 60 Tagen vergessen
    grenze = (heute - timedelta(days=60)).isoformat()
    return {i: b for i, b in befunde.items() if not (b["status"] == "verworfen" and (b["verlauf"] or [{}])[-1].get("datum", "") < grenze)}


# ── Wirkungsmodell (Ridge, zeitlich geordnete Kreuzvalidierung) ──────
KAT_SPALTEN = ["konflikt", "sanktion", "handel_zoll", "lieferstoerung", "produktion_kapazitaet", "preis_markt",
               "regulierung", "geld_fiskal", "naturereignis", "infrastruktur_cyber"]
SPALTEN = (["konstante"] + [f"kat_{k}" for k in KAT_SPALTEN] +
           ["status", "schwere", "umfang", "dauer", "kette", "unsicherheit", "bestaetigt", "konkret", "eskalation",
            "groesse", "log_haeuser", "seeweg", "preis_richtung"])


def vektor(z, richtung=1, zusatz=()):
    st = {"geschehen": 1.0, "angekuendigt": 0.5, "erwogen": -0.5, "dementiert": -1.0}.get(z.get("status"), 0.0)
    um = {"lokal": 0.0, "national": 0.33, "regional": 0.67, "global": 1.0}.get(z.get("umfang"), 0.0)
    du = {"tage": 0.0, "wochen": 0.33, "monate": 0.67, "dauerhaft": 1.0}.get(z.get("dauer"), 0.0)
    return ([1.0] + [1.0 if z.get("kategorie") == k else 0.0 for k in KAT_SPALTEN] +
            [st, ((z.get("schwere") or 3) - 3) / 2, um, du, min(4, (z.get("kette") or 1) - 1) / 4,
             float(z.get("unsicherheit") or 0), float(z.get("bestaetigt") or 0), float(z.get("konkret") or 0),
             float(z.get("eskalation") or 0), float(z.get("groesse") or 0), math.log1p(z.get("haeuser") or 1) / 3,
             0.0 if z.get("route") in (None, "", "keine") else 1.0, float(z.get("preis") or 0) * richtung] +
            [1.0 if all(_gilt(z, tuple(b)) for b in bed) else 0.0 for bed in zusatz])


def ridge(X, y, lam):
    p = len(X[0])
    A = [[sum(x[i] * x[j] for x in X) + (lam if i == j and i > 0 else 0) for j in range(p)] for i in range(p)]
    b = [sum(x[i] * yi for x, yi in zip(X, y)) for i in range(p)]
    return pm._loesen(A, b)


def wirkungsmodell(zeilen, basis, befunde=None):
    """Bestätigte Muster (Ziel richtung) werden als zusätzliche Spalten
    aufgenommen; die Kreuzvalidierung beginnt dann erst nach dem
    Entdeckungszeitraum (70 %), damit kein Vorwissen einfließt."""
    mp = {e["id"]: (e.get("messpunkt") or {}) for e in basis}
    zusatz = [b["bedingung"] for b in sorted((b for b in (befunde or {}).values()
                                              if b.get("status") == "bestaetigt" and b.get("ziel") == "richtung"),
                                             key=lambda b: b["entdeckung"]["q"])[:5]]
    zusatz_text = [text_bedingung([tuple(x) for x in bed]) for bed in zusatz]
    faelle = []
    for z in sorted(zeilen, key=lambda z: z["datum"]):
        if z.get("_r") is None:
            continue
        m = mp.get(z.get("these")) or {}
        richtung = m.get("richtung") if m.get("sym") == z.get("wirkung_sym") and m.get("richtung") in (1, -1) else (z.get("preis") or 1)
        faelle.append((vektor(z, richtung, zusatz), z["_r"]))
    spalten = SPALTEN + [f"muster: {t}" for t in zusatz_text]
    info = {"n": len(faelle), "nutzbar": False, "spalten": spalten, "zusatz": zusatz}
    if len(faelle) < 40:
        info["grund"] = f"zu wenige gerichtete Fälle ({len(faelle)} von 40)"
        return info
    bestes = None
    for lam in (1.0, 5.0, 20.0, 80.0):
        fehler, fehler0, start = 0.0, 0.0, int(len(faelle) * (ANTEIL_ENTDECKUNG if zusatz else 0.5))
        schritt = max(1, (len(faelle) - start) // 5)
        for s in range(start, len(faelle), schritt):
            X = [f[0] for f in faelle[:s]]
            y = [f[1] for f in faelle[:s]]
            beta = ridge(X, y, lam)
            for x, yi in faelle[s:s + schritt]:
                fehler += (yi - sum(a * b for a, b in zip(beta, x))) ** 2
                fehler0 += (yi - sum(y) / len(y)) ** 2
        r2 = 1 - fehler / fehler0 if fehler0 else 0
        if not bestes or r2 > bestes[1]:
            bestes = (lam, r2)
    lam, r2 = bestes
    beta = ridge([f[0] for f in faelle], [f[1] for f in faelle], lam)
    res = [f[1] - sum(a * b for a, b in zip(beta, f[0])) for f in faelle]
    sigma = math.sqrt(sum(r * r for r in res) / max(1, len(res) - len(beta)))
    info.update({"lambda": lam, "r2_oos": round(r2, 4), "beta": [round(b, 4) for b in beta], "sigma": round(sigma, 4),
                 "nutzbar": r2 > 0, "grund": "" if r2 > 0 else "sagt außerhalb der Stichprobe nicht besser vorher als der Mittelwert",
                 "staerkste": sorted(({"spalte": s, "beta": round(b, 3)} for s, b in zip(spalten[1:], beta[1:])),
                                     key=lambda x: -abs(x["beta"]))[:6]})
    return info


def wirkungs_p(info, zeile, richtung):
    """P(Messpunkt läuft in Thesenrichtung) = Φ(μ̂/σ̂); None, wenn nicht nutzbar."""
    if not info or not info.get("nutzbar") or not zeile:
        return None
    mu = sum(a * b for a, b in zip(info["beta"], vektor(zeile, richtung, info.get("zusatz") or ())))
    return min(0.9, max(0.1, phi(mu / max(0.3, info["sigma"]))))


# ── Prüfung der Wissensbasis ─────────────────────────────────────────
def thesenpruefung(zeilen, basis, freigabe):
    vorgabe = {}
    try:
        import vorausschau as vs
        vorgabe = vs.EFFEKT_VORGABE
    except Exception:
        pass
    gruppen = {}
    for z in zeilen:
        if z.get("these") and z.get("_r") is not None:
            gruppen.setdefault(z["these"], []).append((z["datum"], z["_r"]))
    tests, ids = [], []
    for t, werte in gruppen.items():
        werte.sort()
        r = t_einstichprobe([w for _, w in werte])
        if r and r["n"] >= MIN_N:
            tests.append((t, werte, r))
    qs = benjamini_hochberg([x[2]["p"] for x in tests]) if tests else []
    eintraege = {e["id"]: e for e in basis}
    vorschlaege, pruefung = [], {}
    abgelehnt = set(freigabe.get("abgelehnt") or [])
    for (t, werte, r), q in zip(tests, qs):
        e = eintraege.get(t)
        if not e:
            continue
        stufe = e.get("effekt") or vorgabe.get(t, "mittel")
        treffer = sum(1 for _, w in werte if w > 0) / len(werte)
        k = int(len(werte) * ANTEIL_ENTDECKUNG)
        spaet = t_einstichprobe([w for _, w in werte[k:]]) if len(werte) - k >= 4 else None
        pruefung[t] = {"n": r["n"], "effekt": r["effekt"], "treffer": round(treffer, 3), "q": round(q, 4),
                       "stufe": stufe, "vorlaeufig": bool(e.get("vorlaeufig")),
                       "spaet": {"n": spaet["n"], "effekt": spaet["effekt"]} if spaet else None}
        art, neu, grund = None, None, ""
        bestaetigt_spaet = spaet and spaet["effekt"] > 0
        if q <= FDR and r["effekt"] < 0:
            art, grund = "pruefen", f"Bewegung läuft signifikant GEGEN die These (z̄ {r['effekt']:+.2f}, q {q:.3f}, n {r['n']})"
        elif r["n"] >= 15 and r["effekt"] <= 0.1 and treffer <= 0.55 and stufe != "schwach":
            art, neu = "abschwaechen", STUFEN[max(0, STUFEN.index(stufe) - 1)] if stufe in STUFEN else "schwach"
            grund = f"Keine messbare Wirkung (z̄ {r['effekt']:+.2f}, Treffer {treffer:.0%}, n {r['n']})"
        elif q <= FDR and r["effekt"] > 0 and bestaetigt_spaet:
            if e.get("vorlaeufig"):
                art, grund = "bestaetigen", f"Wirkung bestätigt (z̄ {r['effekt']:+.2f}, q {q:.3f}, n {r['n']}, auch im neuesten Zeitraum)"
            elif stufe != "stark" and r["effekt"] >= 0.5:
                art, neu = "verstaerken", STUFEN[min(2, STUFEN.index(stufe) + 1)] if stufe in STUFEN else "stark"
                grund = f"Deutlich stärker als eingetragen (z̄ {r['effekt']:+.2f}, q {q:.3f}, n {r['n']})"
        if art:
            vid = f"VS-{t}-{art}"
            if vid not in abgelehnt:
                vorschlaege.append({"id": vid, "these": t, "titel": e.get("titel", "")[:80], "art": art, "von": stufe,
                                    "nach": neu, "begruendung": grund})
    return pruefung, vorschlaege


def anwenden(vorschlaege, freigabe, basis_datei, heute):
    angenommen = set(freigabe.get("angenommen") or [])
    auto = (freigabe.get("autonomie") or os.environ.get("ANALYSE_AUTONOMIE") or "vorschlagen") == "abschwaechen"
    basis = _laden(basis_datei, None)
    if not basis or not basis.get("eintraege"):
        return []
    angewendet = []
    nach_id = {e["id"]: e for e in basis["eintraege"]}
    for v in vorschlaege:
        freigegeben = v["id"] in angenommen or (auto and v["art"] == "abschwaechen")
        e = nach_id.get(v["these"])
        if not freigegeben or not e:
            continue
        if v["art"] in ("abschwaechen", "verstaerken") and v.get("nach"):
            e["effekt"] = v["nach"]
        elif v["art"] == "bestaetigen":
            e.pop("vorlaeufig", None)
        elif v["art"] == "pruefen" and v["id"] in angenommen:
            e["effekt"] = "schwach"
            e["gesperrt_von_analyse"] = heute          # bleibt drin, aber mit kleinster Stärke
        else:
            continue
        e.setdefault("analyse_protokoll", []).append({"datum": heute, "art": v["art"], "von": v.get("von"),
                                                      "nach": v.get("nach"), "grund": v["begruendung"],
                                                      "freigabe": "automatisch" if v["id"] not in angenommen else "von Hand"})
        angewendet.append(v)
    if angewendet:
        _speichern(basis_datei, basis)
    return angewendet


# ── Bericht ──────────────────────────────────────────────────────────
def bericht(befunde, wm, pruefung, vorschlaege, angewendet, n_tests, n_zeilen):
    zeilen = [f"## Analysealgorithmus", "",
              f"{n_zeilen} Ereignisse, {n_tests} Tests, Falscherkennungsrate ≤ {FDR:.0%}.", ""]
    st = Counter(b["status"] for b in befunde.values())
    zeilen.append(f"**Befunde:** {st.get('bestaetigt', 0)} bestätigt, {st.get('kandidat', 0)} Kandidaten, "
                  f"{st.get('nachlassend', 0)} nachlassend, {st.get('verworfen', 0)} verworfen")
    best = sorted((b for b in befunde.values() if b["status"] == "bestaetigt"), key=lambda b: b["entdeckung"]["q"])[:8]
    for b in best:
        zeilen.append(f"- {b['text']} → {b['ziel']} {b['entdeckung']['effekt']:+.2f} (n {b['entdeckung']['n']}, q {b['entdeckung']['q']:.3f})")
    zeilen += ["", f"**Wirkungsmodell:** " + (f"nutzbar, R² außerhalb der Stichprobe {wm['r2_oos']:.3f}, n {wm['n']}"
                                               if wm.get("nutzbar") else f"nicht genutzt – {wm.get('grund', '')}")]
    if vorschlaege:
        zeilen += ["", "**Vorschläge zur Wissensbasis** – annehmen: ID in `analyse_freigabe.json` unter `angenommen` eintragen"]
        for v in vorschlaege:
            zeilen.append(f"- `{v['id']}` {v['art']}" + (f" ({v['von']} → {v['nach']})" if v.get("nach") else "") +
                          f": {v['titel']} – {v['begruendung']}")
    if angewendet:
        zeilen += ["", "**Angewendet:** " + ", ".join(f"{v['id']}" for v in angewendet)]
    text = "\n".join(zeilen) + "\n"
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
            fh.write(text)
    return text


def main():
    heute = datetime.now(BERLIN).date()
    zeilen = list(ev.bestand().values())
    basis = (_laden(BASIS, {}) or {}).get("eintraege") or []
    if not basis:
        try:
            import vorausschau as vs
            basis = vs.DEFAULT_BASIS
        except Exception:
            basis = []
    freigabe = _laden(FREIGABE, None)
    if freigabe is None:
        freigabe = {"_hinweis": "Vorschläge des Analysealgorithmus freigeben: IDs unter 'angenommen' oder 'abgelehnt' eintragen. "
                                "'autonomie': 'vorschlagen' (alles nur vorschlagen) oder 'abschwaechen' (Abschwächungen "
                                "automatisch anwenden, Verstärkungen weiter nur mit Freigabe).",
                    "autonomie": "vorschlagen", "angenommen": [], "abgelehnt": []}
        _speichern(FREIGABE, freigabe)
    zielwerte(zeilen, basis)
    mit = [z for z in zeilen if z.get("z5") is not None or isinstance(z.get("nachhall7"), (int, float))]
    neu, n_tests = mustersuche(mit, heute) if len(mit) >= 2 * MIN_N else ({}, 0)
    alt = (_laden(BEFUNDE, {}) or {}).get("befunde") or {}
    befunde = lebenszyklus(alt, neu, heute)
    wm = wirkungsmodell(zeilen, basis, befunde)
    pruefung, vorschlaege = thesenpruefung(zeilen, basis, freigabe)
    angewendet = anwenden(vorschlaege, freigabe, BASIS, heute.isoformat())
    stand = datetime.now(BERLIN).isoformat(timespec="minutes")
    _speichern(BEFUNDE, {"stand": stand, "methode": "Untergruppen-Entdeckung, t-Tests, Benjamini-Hochberg, zeitliche Prüfung 70/30, "
                                                     "Drift 90 Tage", "befunde": befunde}, kompakt=True)
    alte_analyse = _laden(ANALYSE, {}) or {}
    protokoll = (alte_analyse.get("angewendet") or []) + [dict(v, datum=heute.isoformat()) for v in angewendet]
    _speichern(ANALYSE, {"stand": stand, "wirkungsmodell": wm, "thesen": pruefung, "vorschlaege": vorschlaege,
                         "angewendet": protokoll[-100:], "tests": n_tests, "ereignisse": len(zeilen)})
    for z in zeilen:
        z.pop("_r", None)
    print(bericht(befunde, wm, pruefung, vorschlaege, angewendet, n_tests, len(zeilen)).replace("**", ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
