#!/usr/bin/env python3
"""
ontologie.py  –  ordnet die Ereignistabelle zu Objekten und Verknüpfungen
und misst, was nach jedem Ereignis tatsächlich passiert ist.

Läuft ohne KI, nach ereignisse.py und vor der Vorausschau.

1. OBJEKTE  (ereignisse/objekte.json)
   Akteure    zusammengeführt über Namensformen (AG, Inc., SE … entfernt),
              mit Typ, Börsenkürzel (aus aktien.py), Rollen, Kategorien,
              Ländern, erster/letzter Nennung, Ereignisliste
   Güter      Gut + HS-Kapitel, Häufigkeit, Preisrichtungen, Marktsymbol
   Seewege    Häufigkeit, Kategorien, letzte Ereignisse

2. VERKNÜPFUNGEN  (ereignisse/verknuepfungen.jsonl)
   ereignis → akteur   (Rolle: verursacher | betroffen | reagiert)
   ereignis → gut, ereignis → route, akteur → kürzel
   ereignis → ereignis "folge_von": jüngstes früheres Ereignis (≤ 21 Tage)
              derselben These oder mit gleichem Gut und gemeinsamem Akteur/Land.
              Daraus Kettenlänge und Tage seit Vorgänger – Eskalationsfolgen
              werden als Kette sichtbar und sind ein Merkmal fürs Modell.

3. WIRKUNG  (zurück in die Tabelle: wirkung_sym, r1/r5/r20, z1/z5/z20, abnormal5)
   Ereignisstudie nach MacKinlay (1997): Kurs am letzten Handelstag VOR dem
   Ereignis als Basis; Rendite nach 1, 5 und 20 Handelstagen; standardisiert
   mit der Tagesvolatilität der 60 Handelstage davor (z = r / (σ·√h)), damit
   Öl, Chips und Versicherer vergleichbar sind; abnormal5 = Rendite minus
   Vergleichsindex (DAX für deutsche Werte, S&P 500 sonst; Rohstoffe ohne).
   Messgröße: Messpunkt der These, sonst Kürzel des ersten verknüpften
   Unternehmens, sonst Marktsymbol des Guts.

4. NACHHALL  (nachhall7)
   Zahl der Meldungen in den sieben Tagen danach, die mindestens zwei der
   drei seltensten Titelwörter enthalten – wie lange ein Thema trägt.

5. AUSWERTUNG  (ereignisse/auswertung.json)
   Je Kategorie, Status, Schwere, Reichweite, Seeweg, Dauer, Bestätigung:
   Anzahl, mittlere standardisierte Bewegung (in Preisrichtung), Anteil
   deutlicher Bewegungen (|z| > 2), t-Wert, mittlerer Nachhall. Damit lässt
   sich messen, welche Ereignistypen Märkte wirklich bewegen – Grundlage für
   Evaluation und spätere Regressionen auf die Wirkung selbst.

Die Wirkungsspalten sind Ergebnisse, keine Merkmale: Das Modell darf sie
nicht als Eingabe nutzen (sie liegen erst nach dem Ereignis vor).
"""

import ast
import json
import math
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import ereignisse as ev
import prognose_modell as pm

ORDNER = ev.ORDNER
BERLIN = ZoneInfo("Europe/Berlin")
KETTE_TAGE = 21
HORIZONTE = (1, 5, 20)
RECHTSFORM = re.compile(r"\b(ag|se|inc|corp|corporation|co|ltd|plc|gmbh|group|gruppe|konzern|holding|sa|nv|kgaa|llc)\b\.?", re.I)
ALIAS = {"vereinigte staaten": "usa", "us": "usa", "united states": "usa", "us-regierung": "usa",
         "europäische union": "eu", "european union": "eu", "eu-kommission": "europäische kommission",
         "volksrepublik china": "china", "russische föderation": "russland", "russia": "russland",
         "bundesrepublik deutschland": "deutschland", "germany": "deutschland", "bundesregierung": "deutschland"}
# HS-Kapitel → Marktsymbol im Archiv (erster vorhandener gewinnt)
HS_SYMBOL = {"27": ["BZ=F", "CL=F", "NG=F", "TTF=F"], "10": ["ZW=F", "ZC=F"], "71": ["GC=F", "SI=F"], "74": ["HG=F"],
             "26": ["HG=F"], "85": ["SMH", "SOXX"], "84": ["SMH"], "12": ["ZS=F"], "17": ["SB=F"], "09": ["KC=F"],
             "18": ["CC=F"], "76": ["ALI=F"], "52": ["CT=F"], "08": ["OJ=F"]}
VERGLEICH = {"EUR": ["^GDAXI", "^STOXX50E"], "USD": ["^GSPC", "SPY", "URTH"]}
ROHSTOFF = re.compile(r"=F$")


def _laden(pfad, leer):
    try:
        return json.load(open(pfad, encoding="utf-8"))
    except (OSError, ValueError):
        return leer


def schluessel(name):
    n = RECHTSFORM.sub(" ", name.lower().replace("-", " "))
    n = re.sub(r"[^\wäöüß&]+", " ", n).strip()
    return ALIAS.get(n, n)


def kuerzel_tabelle():
    """Name → Yahoo-Kürzel aus aktien.py (ohne den Code auszuführen)."""
    try:
        baum = ast.parse(open("aktien.py", encoding="utf-8").read())
        for n in baum.body:
            if isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "AKTIEN_VORGABE" for t in n.targets):
                return {schluessel(t[1]): (t[0], t[2]) for t in ast.literal_eval(n.value)}
    except (OSError, ValueError, SyntaxError):
        pass
    return {}


def kuerzel_fuer(k, kuerzel):
    """Exakt, sonst über die führenden Wörter ("micron technology" → "micron")."""
    if k in kuerzel:
        return kuerzel[k]
    w = k.split()
    for n in range(len(w) - 1, 0, -1):
        if " ".join(w[:n]) in kuerzel:
            return kuerzel[" ".join(w[:n])]
    return None


def reihe_oder_none(sym):
    r = pm.kursreihe(sym) if sym else []
    return r if len(r) >= 30 else None


# ── Wirkung (Ereignisstudie) ─────────────────────────────────────────
def wirkung(sym, tag, reihe, vergleich=None):
    i0 = pm._index_bis(reihe, tag - timedelta(days=1))
    if i0 < 61:
        return None
    p0 = reihe[i0][1]
    tr = [math.log(reihe[k][1] / reihe[k - 1][1]) for k in range(i0 - 59, i0 + 1)]
    mu = sum(tr) / len(tr)
    sd = math.sqrt(sum((x - mu) ** 2 for x in tr) / (len(tr) - 1)) or 1e-9
    out = {"wirkung_sym": sym}
    for h in HORIZONTE:
        if i0 + h < len(reihe):
            r = reihe[i0 + h][1] / p0 - 1
            out[f"r{h}"] = round(r, 5)
            out[f"z{h}"] = round(math.log(1 + r) / (sd * math.sqrt(h)), 3)
    if "r5" in out and vergleich:
        j0 = pm._index_bis(vergleich, tag - timedelta(days=1))
        if j0 >= 0 and j0 + 5 < len(vergleich):
            out["abnormal5"] = round(out["r5"] - (vergleich[j0 + 5][1] / vergleich[j0][1] - 1), 5)
    return out if "r1" in out else None


# ── Nachhall ─────────────────────────────────────────────────────────
WORT = re.compile(r"[a-zäöüß]{5,}")


class Nachhall:
    def __init__(self, artikel):
        self.tage = defaultdict(list)          # datum → [wortmenge]
        self.df = Counter()
        for a in artikel:
            d = str(a.get("date") or "")[:10]
            if len(d) == 10:
                w = set(WORT.findall((a.get("title") or "").lower()))
                self.tage[d].append(w)
                self.df.update(w)

    def zaehlen(self, titel, tag):
        w = sorted(set(WORT.findall(titel.lower())), key=lambda x: self.df.get(x, 0))
        w = [x for x in w if self.df.get(x, 0) > 1][:3]
        if len(w) < 2:
            return None
        n = 0
        for k in range(1, 8):
            for menge in self.tage.get((tag + timedelta(days=k)).isoformat(), ()):
                if sum(1 for x in w if x in menge) >= 2:
                    n += 1
        return n


# ── Auswertung ───────────────────────────────────────────────────────
def gruppenwerte(zeilen, feld):
    gruppen = defaultdict(list)
    for z in zeilen:
        if z.get("z5") is None:
            continue
        schl = z.get(feld)
        if schl in (None, ""):
            continue
        richtung = (z.get("preis") or 1) if ROHSTOFF.search(z.get("wirkung_sym") or "") else 1
        gruppen[str(schl)].append((z["z5"] * richtung, abs(z["z5"]), z.get("nachhall7")))
    out = {}
    for g, werte in gruppen.items():
        n = len(werte)
        if n < 3:
            continue
        zs = [w[0] for w in werte]
        m = sum(zs) / n
        sd = math.sqrt(sum((x - m) ** 2 for x in zs) / (n - 1)) if n > 1 else 0
        nh = [w[2] for w in werte if w[2] is not None]
        out[g] = {"n": n, "z5_mittel": round(m, 3), "betrag_mittel": round(sum(w[1] for w in werte) / n, 3),
                  "anteil_deutlich": round(sum(1 for w in werte if w[1] > 2) / n, 3),
                  "t": round(m / (sd / math.sqrt(n)), 2) if sd > 0 else None,
                  "nachhall_mittel": round(sum(nh) / len(nh), 1) if nh else None}
    return dict(sorted(out.items(), key=lambda x: -x[1]["n"]))


def main():
    zeilen = ev.bestand()
    if not zeilen:
        print("  Ontologie: Ereignistabelle leer – nichts zu tun.")
        return 0
    heute = datetime.now(BERLIN).date()
    kuerzel = kuerzel_tabelle()
    basis = {e["id"]: e for e in ((_laden("vorausschau_basis.json", {}) or {}).get("eintraege") or [])}
    liste = sorted(zeilen.values(), key=lambda z: (z["datum"], z["id"]))

    # 1 + 2  Objekte und Verknüpfungen
    akteure, gueter, routen, links = {}, {}, {}, []
    for z in liste:
        for a in z.get("akteure") or []:
            if isinstance(a, str):
                a = {"name": a}
            k = schluessel(a.get("name") or "")
            if not k:
                continue
            o = akteure.setdefault(k, {"id": k, "name": a["name"], "typ": None, "kuerzel": None, "waehrung": None,
                                       "n": 0, "erste": z["datum"], "letzte": z["datum"], "rollen": Counter(),
                                       "kategorien": Counter(), "laender": Counter(), "ereignisse": []})
            o["n"] += 1
            o["letzte"] = z["datum"]
            o["typ"] = o["typ"] or a.get("typ")
            if a.get("rolle"):
                o["rollen"][a["rolle"]] += 1
            o["kategorien"][z.get("kategorie") or "?"] += 1
            o["laender"].update(z.get("laender") or [])
            o["ereignisse"] = (o["ereignisse"] + [z["id"]])[-60:]
            treffer = kuerzel_fuer(k, kuerzel)
            if treffer and not o["kuerzel"]:
                o["kuerzel"], o["waehrung"] = treffer
                links.append({"von": k, "nach": o["kuerzel"], "art": "akteur_kuerzel"})
            links.append({"von": z["id"], "nach": k, "art": "akteur", "rolle": a.get("rolle")})
        if z.get("gut"):
            g = z["gut"].strip().lower()
            o = gueter.setdefault(g, {"id": g, "gut": z["gut"], "hs": z.get("hs") or "", "n": 0, "preis": Counter(),
                                      "kategorien": Counter(), "ereignisse": []})
            o["n"] += 1
            o["hs"] = o["hs"] or z.get("hs") or ""
            o["preis"][str(z.get("preis") or 0)] += 1
            o["kategorien"][z.get("kategorie") or "?"] += 1
            o["ereignisse"] = (o["ereignisse"] + [z["id"]])[-60:]
            links.append({"von": z["id"], "nach": g, "art": "gut"})
        if z.get("route") not in (None, "", "keine"):
            o = routen.setdefault(z["route"], {"id": z["route"], "n": 0, "kategorien": Counter(), "ereignisse": []})
            o["n"] += 1
            o["kategorien"][z.get("kategorie") or "?"] += 1
            o["ereignisse"] = (o["ereignisse"] + [z["id"]])[-60:]
            links.append({"von": z["id"], "nach": z["route"], "art": "route"})

    # Ketten: jüngstes passendes früheres Ereignis
    for i, z in enumerate(liste):
        d = date.fromisoformat(z["datum"])
        ak = {schluessel(a["name"] if isinstance(a, dict) else a) for a in z.get("akteure") or []}
        best = None
        for v in reversed(liste[max(0, i - 400):i]):
            dv = date.fromisoformat(v["datum"])
            if (d - dv).days > KETTE_TAGE:
                break
            if dv >= d:
                continue
            gleiche_these = z.get("these") and v.get("these") == z["these"]
            akv = {schluessel(a["name"] if isinstance(a, dict) else a) for a in v.get("akteure") or []}
            gleiches_gut = z.get("gut") and (v.get("gut") or "").lower() == z["gut"].lower() and (
                ak & akv or set(z.get("laender") or []) & set(v.get("laender") or []))
            if gleiche_these or gleiches_gut:
                best = v
                break
        if best:
            z["vorgaenger"] = best["id"]
            z["kette"] = min(10, (best.get("kette") or 1) + 1)
            z["tage_seit_vorgaenger"] = (d - date.fromisoformat(best["datum"])).days
            links.append({"von": z["id"], "nach": best["id"], "art": "folge_von"})
        else:
            z["vorgaenger"], z["kette"], z["tage_seit_vorgaenger"] = None, 1, None

    # 3  Wirkung am Markt
    vgl = {w: next((r for r in (reihe_oder_none(s) for s in syms) if r), None) for w, syms in VERGLEICH.items()}
    gemessen = 0
    for z in liste:
        if z.get("z20") is not None:            # vollständig gemessen
            continue
        d = date.fromisoformat(z["datum"])
        if d >= heute:
            continue
        kandidaten = []
        mp = (basis.get(z.get("these")) or {}).get("messpunkt") or {}
        if mp.get("sym") not in (None, "auswahl"):
            kandidaten.append((mp["sym"], None))
        for a in z.get("akteure") or []:
            o = akteure.get(schluessel(a["name"] if isinstance(a, dict) else a))
            if o and o.get("kuerzel"):
                kandidaten.append((o["kuerzel"], o.get("waehrung")))
        kandidaten += [(s, None) for s in HS_SYMBOL.get(z.get("hs") or "", [])]
        for sym, wg in kandidaten:
            r = reihe_oder_none(sym)
            if not r:
                continue
            w = wirkung(sym, d, r, None if ROHSTOFF.search(sym) else vgl.get(wg or ("EUR" if sym.endswith(".DE") else "USD")))
            if w:
                z.update(w)
                gemessen += 1
                break

    # 4  Nachhall (nur für abgeschlossene 7-Tage-Fenster, einmalig)
    offen = [z for z in liste if z.get("nachhall7") is None and not z.get("nachhall_geprueft")
             and date.fromisoformat(z["datum"]) <= heute - timedelta(days=8)]
    if offen:
        import vorausschau as vs
        nh = Nachhall(vs.archiv_laden() + vs.artikel_laden())
        for z in offen:
            z["nachhall7"] = nh.zaehlen(z.get("titel") or "", date.fromisoformat(z["datum"]))
            z["nachhall_geprueft"] = True

    # 5  Auswertung
    mit_wirkung = [z for z in liste if z.get("z5") is not None]
    auswertung = {"stand": datetime.now(BERLIN).isoformat(timespec="minutes"), "ereignisse": len(liste),
                  "mit_wirkung": len(mit_wirkung),
                  "methode": "Ereignisstudie (MacKinlay 1997): Basis letzter Handelstag vor dem Ereignis, z = log-Rendite / "
                             "(σ der 60 Handelstage davor · √h); bei Rohstoffen in erwarteter Preisrichtung.",
                  "nach": {f: gruppenwerte(liste, f) for f in
                           ("kategorie", "status", "schwere", "umfang", "route", "dauer", "bestaetigt", "neuheit", "kette")}}
    # Speichern
    ev.speichern({z["id"]: z for z in liste})

    def _fertig(o, top=("rollen", "kategorien", "laender", "preis")):
        o = dict(o)
        for k in top:
            if k in o:
                o[k] = dict(Counter(o[k]).most_common(8))
        return o
    with open(os.path.join(ORDNER, "objekte.json"), "w", encoding="utf-8") as fh:
        json.dump({"stand": auswertung["stand"],
                   "akteure": [_fertig(o) for o in sorted(akteure.values(), key=lambda o: -o["n"])],
                   "gueter": [_fertig(o) for o in sorted(gueter.values(), key=lambda o: -o["n"])],
                   "routen": [_fertig(o) for o in sorted(routen.values(), key=lambda o: -o["n"])]},
                  fh, ensure_ascii=False, separators=(",", ":"))
    with open(os.path.join(ORDNER, "verknuepfungen.jsonl"), "w", encoding="utf-8") as fh:
        for l in links:
            fh.write(json.dumps(l, ensure_ascii=False, separators=(",", ":")) + "\n")
    with open(os.path.join(ORDNER, "auswertung.json"), "w", encoding="utf-8") as fh:
        json.dump(auswertung, fh, ensure_ascii=False, indent=1)

    ketten = sum(1 for z in liste if (z.get("kette") or 1) > 1)
    print(f"→ Ontologie: {len(akteure)} Akteure ({sum(1 for o in akteure.values() if o['kuerzel'])} mit Kürzel), "
          f"{len(gueter)} Güter, {len(routen)} Seewege, {len(links)} Verknüpfungen, {ketten} Ereignisse in Ketten; "
          f"Wirkung {gemessen} neu gemessen, {len(mit_wirkung)} insgesamt; Nachhall {len(offen)} neu")
    kat = auswertung["nach"]["kategorie"]
    if kat:
        print("  Bewegung je Kategorie (z5, n): " + ", ".join(f"{k} {v['z5_mittel']:+.2f} ({v['n']})" for k, v in list(kat.items())[:6]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
