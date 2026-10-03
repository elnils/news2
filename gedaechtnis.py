#!/usr/bin/env python3
"""
gedaechtnis.py  –  Langzeitgedächtnis der Presseschau (Backend, ohne KI)

GRUNDIDEE
Alles ist eine Zeile mit eigener ID. Zeilen verweisen auf andere Zeilen.
Erhärtet sich ein Zusammenhang statistisch, entsteht daraus eine NEUE Zeile
(Verknüpfung), die auf ihre beiden Ausgangszeilen verweist – und die
wiederum verblasst, wenn er nicht mehr beobachtet wird.

ZEILENTYPEN (Präfix der ID)
  M-  Meldung      vorsortiert: KI-Top-Meldungen, in Deep Dives zitierte und
                   thesenbestätigende Meldungen der Vorausschau.
                   Merkmale: Datum, Titel, Haus, Sprache, Quellenzahl, Region,
                   Begriffe, Namen, Textmerkmale, Herkunft; Verweise auf
                   Thesen (T-), Deep Dives (D-), Ereignisse (Ereignistabelle).
  D-  Deep Dive    Archiv aller Deep Dives (Thema, Analyse, Signal, Kennzahlen,
                   Meldungen) – nicht mehr nur sieben Tage.
  T-  These        Stand einer Einschätzung je Tag: Wahrscheinlichkeit,
                   Grundrate, Messpunkt, Sicherheit, belegende Meldungen
                   (die Modellerwartung als Datenpunkt).
  K-  Kennzahl     Amtliche Statistik je Beobachtung (Eurostat, EZB) aus den
                   Deep Dives; K-markt-… für Marktsymbole.
  B-  Begriff      Wörter und Sachbegriffe mit Tageshäufigkeit über ALLE
                   Meldungen (nicht nur die vorsortierten): Öl, Kartoffel …
  X-  Verknüpfung  abgeleitete Zeile, verweist auf zwei Zeilen:
                   begriff_begriff  treten gemeinsam auffällig oft auf
                   begriff_markt    Häufigkeit eines Begriffs geht mit
                                    Kursbewegungen einher (z. B. öl ↔ Brent)
                   these_deepdive   These und Deep Dive stützen sich auf
                                    dieselben Meldungen

MATHEMATIK
  Auffällige Begriffe  Anteil der letzten 2 Tage gegen die 28 Tage davor, quasi-Poisson:
                       z = (df − λ) / √(φ·λ), λ = n·p₀, φ = Überstreuung aus
                       den Vortagen (≥ 1). Auffällig ab z ≥ 3, df ≥ 5, ×1,5.
  Gemeinsames Auftreten  hypergeometrischer Test (exakt) über die
                       vorsortierten Meldungen der letzten 90 Tage, Lift ≥ 2,
                       mindestens 4 Meldungen an 2 Tagen aus 2 Häusern,
                       Benjamini-Hochberg q ≤ 0,05.
  Begriff ↔ Markt      Rangkorrelation (Spearman) des Tagesanteils mit der
                       Kursbewegung (Betrag) am selben und den 5 Folgetagen,
                       180 Tage, p < 0,01.
  Gedächtnisstärke     S ← S·e^(−Δt/τ) + 1 bei jeder Bestätigung, τ = 30 Tage
                       (Halbwertszeit ≈ 21 Tage). Unter 0,2 wandert die
                       Verknüpfung ins Archiv – vergessen, aber nicht gelöscht.

AUSGABE (Ordner gedaechtnis/)
  meldungen/JJJJ-MM.jsonl, deepdives/JJJJ-MM.jsonl, thesen/JJJJ-MM.jsonl,
  kennzahlen.jsonl, begriffe/JJJJ-MM.json (Tageshäufigkeiten),
  begriffe.json (Begriffsobjekte), verknuepfungen.json (+ _archiv.jsonl),
  lagebild.json (aktueller Stand für Vorausschau und Analyse), schema.json
"""

import hashlib
import json
import math
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

ORDNER = "gedaechtnis"
BERLIN = ZoneInfo("Europe/Berlin")
TAU = 30.0
FENSTER_PAARE = 90
NACHTRAG_TAGE = int(os.environ.get("GEDAECHTNIS_NACHTRAG", "90"))
MAX_BEGRIFFE_TAG = 4000

STOP = set("""
aber alle allem allen aller alles als also am an ander andere anderen auch auf aus bei beim bereits bis bisher bleibt
damit dann das dass dabei dazu dem den denen der deren des dessen die dies diese diesem diesen dieser dieses doch dort
durch ein eine einem einen einer eines einige er erst erste ersten es etwa euro für gab gibt gegen geht gehen hat hatte
haben hier hin ihr ihre ihren im immer in ins ist jahr jahre jahren jetzt kann kein keine kommt können könnte laut
lassen machen mehr mit muss nach neue neuen neuer nicht noch nun nur ob oder ohne prozent schon sei seien sein seine
seit sich sie sind so soll sollen sowie statt steht über um und uns unter viel viele vom von vor war waren was weil
weiter weitere wenn werden wie wieder will wir wird wo wurde wurden zu zum zur zwei drei vier fünf sagt sagte heute
gestern morgen woche wochen monat monaten tag tage tagen live ticker news update video bild bilder liveblog newsblog
the and for with from that this have has had will would could should about after over into more than been were their
they them what when where which while who why how also just only said says new year years week day days first last
amid after before says report reports according people time back make made take still other some most such very
""".split())
# Kopfbegriffe: Komposita zählen auch für den Kopf ("Kartoffelpreise" → kartoffel)
KOEPFE = {"öl": "öl", "oil": "öl", "gas": "gas", "strom": "strom", "weizen": "weizen", "wheat": "weizen",
          "kartoffel": "kartoffel", "potato": "kartoffel", "kaffee": "kaffee", "coffee": "kaffee", "kakao": "kakao",
          "cocoa": "kakao", "zucker": "zucker", "sugar": "zucker", "kupfer": "kupfer", "copper": "kupfer",
          "lithium": "lithium", "chip": "chip", "halbleiter": "chip", "semiconductor": "chip", "stahl": "stahl",
          "steel": "stahl", "gold": "gold", "dünger": "dünger", "fertilizer": "dünger", "zoll": "zoll", "tariff": "zoll",
          "butter": "butter", "milch": "milch", "fleisch": "fleisch", "uran": "uran"}
# Fortsetzungen nach der Kürzung durch norm() ("ernte" → "ernt", "reserve" → "reserv")
FORTSETZUNG = re.compile(r"^(preis|förder|liefer|markt|import|export|produkt|ernt|versorg|speicher|leitung|tank|bohr|"
                         r"konzern|reserv|embargo|sanktion|steuer|zoll|mangel|knapp|fass|raffin|kraftwerk|netz|pipelin|"
                         r"handel|lager|bauer|anbau|feld|min|hütt|werk|pric|suppl|output|market|shortag|crop|harvest|"
                         r"tanker|s$|e$|n$|es$|en$)")
OHNE_KOPF = {"gast", "gäste", "gasse", "gastronomie", "gastgeber", "goldman", "golden", "reise", "reisen", "reisende",
             "stromberg", "chipotle", "zollern", "oilers"}
SYMBOL = {"öl": ["BZ=F", "CL=F"], "gas": ["TTF=F", "NG=F"], "weizen": ["ZW=F"], "kaffee": ["KC=F"], "kakao": ["CC=F"],
          "zucker": ["SB=F"], "kupfer": ["HG=F"], "gold": ["GC=F"], "chip": ["SMH", "SOXX"], "uran": ["URA"]}
WORT = re.compile(r"[a-zäöüß][a-zäöüß-]{1,40}")


# ── Hilfen ───────────────────────────────────────────────────────────
def _laden(pfad, leer):
    try:
        return json.load(open(pfad, encoding="utf-8"))
    except (OSError, ValueError):
        return leer


def _schreiben(pfad, daten, kompakt=True):
    os.makedirs(os.path.dirname(pfad) or ".", exist_ok=True)
    tmp = pfad + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(daten, fh, ensure_ascii=False, **({"separators": (",", ":")} if kompakt else {"indent": 1}))
    os.replace(tmp, pfad)


def jsonl_laden(ordner):
    zeilen = {}
    if os.path.isdir(ordner):
        for f in sorted(os.listdir(ordner)):
            if f.endswith(".jsonl"):
                for z in open(os.path.join(ordner, f), encoding="utf-8"):
                    if z.strip():
                        try:
                            r = json.loads(z)
                            zeilen[r["id"]] = r
                        except (ValueError, KeyError):
                            pass
    return zeilen


def jsonl_schreiben(ordner, zeilen):
    os.makedirs(ordner, exist_ok=True)
    nach = defaultdict(list)
    for r in zeilen.values():
        nach[(r.get("datum") or "0000-00")[:7]].append(r)
    for monat, liste in nach.items():
        liste.sort(key=lambda r: (r.get("datum") or "", r["id"]))
        tmp = os.path.join(ordner, f"{monat}.jsonl.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            for r in liste:
                fh.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")
        os.replace(tmp, os.path.join(ordner, f"{monat}.jsonl"))


def tag_von(d):
    s = str(d or "")
    if len(s) >= 19:
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if dt.tzinfo:
                return dt.astimezone(BERLIN).date().isoformat()
        except ValueError:
            pass
    return s[:10] if re.match(r"\d{4}-\d{2}-\d{2}", s) else None


def haus_von(a):
    h = (a.get("haus") or "").lower()
    if h:
        return h
    m = re.match(r"https?://(?:www\d?\.)?([^/]+)", a.get("link") or "")
    return m.group(1).lower() if m else (a.get("source") or "").lower()


# ── Begriffe ─────────────────────────────────────────────────────────
def norm(w):
    if len(w) > 6 and w.endswith("ungen"):
        return w[:-3]
    if len(w) > 6 and w.endswith("en"):
        return w[:-2]
    if len(w) > 6 and w[-1] == "n" and w[-2] in "lr":
        return w[:-1]
    if len(w) > 6 and w[-1] == "e":
        return w[:-1]
    # Plural-s nur nach Konsonant oder e ("prices", "tanks"), nicht bei
    # Preis, Reis, Gas, Hormus, Kurs
    if len(w) > 4 and w[-1] == "s" and w[-2] not in "aiouäöüys" and not w.endswith(("urs", "ers")):
        return w[:-1]
    return w


def begriffe(text):
    """Normalisierte Begriffe eines Textes, Komposita auch unter ihrem Kopf."""
    out = set()
    for roh in WORT.findall((text or "").lower()):
        teile = [roh] + ([t for t in roh.split("-") if t] if "-" in roh else [])
        for t in teile:
            t = t.strip("-")
            if len(t) < 2 or t in STOP:
                continue
            n = norm(t)
            if (len(n) >= 4 or n in KOEPFE) and n not in STOP:
                out.add(n)
            if n in OHNE_KOPF or t in OHNE_KOPF:
                continue
            for kopf, ziel in KOEPFE.items():
                if n == kopf or (n.startswith(kopf) and FORTSETZUNG.match(n[len(kopf):])) or (n.endswith(kopf) and len(n) > len(kopf) + 2):
                    out.add(ziel)
    return out


# ── Statistik ────────────────────────────────────────────────────────
def _lchoose(n, k):
    return math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)


def hypergeom_oben(k, N, K, n):
    """P(X ≥ k) für X ~ Hypergeometrisch(N, K, n) – exakt."""
    hi = min(K, n)
    if k > hi:
        return 0.0
    basis = _lchoose(N, n)
    s = 0.0
    for i in range(k, hi + 1):
        s += math.exp(_lchoose(K, i) + _lchoose(N - K, n - i) - basis)
    return min(1.0, s)


def bh(ps):
    k = len(ps)
    ordnung = sorted(range(k), key=lambda i: ps[i])
    q, vorher = [1.0] * k, 1.0
    for rang in range(k, 0, -1):
        i = ordnung[rang - 1]
        vorher = min(vorher, ps[i] * k / rang)
        q[i] = vorher
    return q


def raenge(x):
    o = sorted(range(len(x)), key=lambda i: x[i])
    r = [0.0] * len(x)
    i = 0
    while i < len(o):
        j = i
        while j + 1 < len(o) and x[o[j + 1]] == x[o[i]]:
            j += 1
        for k in range(i, j + 1):
            r[o[k]] = (i + j) / 2 + 1
        i = j + 1
    return r


def spearman(x, y):
    n = len(x)
    if n < 10:
        return None, 1.0
    rx, ry = raenge(x), raenge(y)
    mx, my = sum(rx) / n, sum(ry) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    sx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    sy = math.sqrt(sum((b - my) ** 2 for b in ry))
    if not sx or not sy:
        return None, 1.0
    r = sxy / (sx * sy)
    t = r * math.sqrt((n - 2) / max(1e-12, 1 - r * r))
    try:
        import analyse_auto as aa
        p = aa.p_zweiseitig(t, n - 2)
    except Exception:
        p = math.erfc(abs(t) / math.sqrt(2))
    return round(r, 3), p


# ── 1. Zeilen sammeln ────────────────────────────────────────────────
def meldung_zeile(a, herkunft, heute, extra=None):
    titel = a.get("title") or a.get("titel") or ""
    text = titel + " " + (a.get("desc") or a.get("text") or "")
    try:
        import prognose_modell as pm
        tm = pm.textmerkmale([text])
    except Exception:
        tm = [0, 0, 0]
    z = {"id": "M-" + str(a.get("id") or hashlib.sha1((a.get("link") or titel).encode()).hexdigest()[:12]),
         "typ": "meldung", "datum": tag_von(a.get("date") or a.get("datum")) or heute, "titel": titel[:240],
         "haus": haus_von(a), "sprache": a.get("lang") or "", "link": a.get("link") or "",
         "begriffe": sorted(begriffe(text))[:30], "namen": [str(x)[:60] for x in (a.get("ents") or [])][:10],
         "konkret": round(tm[0], 3), "eskalation": round(tm[1], 3), "groesse": round(tm[2], 3),
         "herkunft": [herkunft], "verweise": [], "gesehen": heute}
    if extra:
        z.update(extra)
    return z


def zusammenfuehren(alt, neu):
    if not alt:
        return neu
    for k in ("herkunft", "verweise", "mitglieder"):
        if neu.get(k) or alt.get(k):
            alt[k] = sorted(set(alt.get(k) or []) | set(neu.get(k) or []))[:40]
    for k in ("quellen_n", "region"):
        if neu.get(k) and (not alt.get(k) or (k == "quellen_n" and neu[k] > alt[k])):
            alt[k] = neu[k]
    alt["gesehen"] = neu.get("gesehen") or alt.get("gesehen")
    return alt


def sammeln(heute):
    roh = (_laden("articles.json", {}) or {}).get("articles") or []
    nach_id = {a.get("id"): a for a in roh if a.get("id")}
    meldungen = jsonl_laden(os.path.join(ORDNER, "meldungen"))
    neu_m = 0

    def dazu(z):
        nonlocal neu_m
        if z["id"] not in meldungen:
            neu_m += 1
        meldungen[z["id"]] = zusammenfuehren(meldungen.get(z["id"]), z)

    # a) KI-Top-Meldungen: eine Zeile je Gruppe (Leitmeldung), Mitglieder als Verweise
    for reg, r in (((_laden("ki_meldungen.json", {}) or {}).get("schlagzeilen") or {}).get("regionen") or {}).items():
        for it in r.get("items") or []:
            ids = [i for i in (it.get("ids") or []) if i]
            lead = nach_id.get(it.get("id")) or (nach_id.get(ids[0]) if ids else None)
            if not lead and not it.get("schlagzeile"):
                continue
            basis = dict(lead or {}, title=it.get("schlagzeile") or (lead or {}).get("title"),
                         desc=it.get("text") or (lead or {}).get("desc"), id=(lead or {}).get("id") or it.get("id"))
            haeuser = {haus_von(nach_id[i]) for i in ids if i in nach_id}
            dazu(meldung_zeile(basis, "top", heute, {"region": reg, "quellen_n": len(haeuser) or len(ids) or 1,
                                                     "mitglieder": ["M-" + str(i) for i in ids if i != basis.get("id")]}))

    # b) Deep Dives: Archiv + zitierte Meldungen
    deepdives = jsonl_laden(os.path.join(ORDNER, "deepdives"))
    dd = _laden("deepdive.json", {}) or {}
    for stand in [{"datum": dd.get("datum"), "themen": dd.get("themen") or []}] + list(dd.get("historie") or []):
        for t in stand.get("themen") or []:
            if not t.get("analyse") or not stand.get("datum"):
                continue
            did = f"D-{stand['datum']}-{t.get('id')}"
            a = t["analyse"]
            mids = []
            for m in t.get("meldungen") or []:
                art = nach_id.get(m.get("id")) or {"id": m.get("id"), "title": m.get("titel"), "source": m.get("quelle"),
                                                   "link": m.get("link"), "date": m.get("datum")}
                z = meldung_zeile(art, "deepdive", heute, {"verweise": [did]})
                dazu(z)
                mids.append(z["id"])
            kerntext = " ".join([str(a.get("kern") or "")] + [str(x) for x in (a.get("stichpunkte") or [])])
            deepdives[did] = {"id": did, "typ": "deepdive", "datum": stand["datum"], "thema_id": t.get("id"),
                              "titel": t.get("thema"), "kern": str(a.get("kern") or "")[:1500],
                              "stichpunkte": [str(x)[:300] for x in (a.get("stichpunkte") or [])][:8],
                              "zahlen": a.get("zahlen") or [], "auswirkungen": a.get("auswirkungen") or {},
                              "signal": t.get("signal") or {}, "begriffe": sorted(begriffe(kerntext + " " + str(t.get("thema"))))[:40],
                              "kennzahlen": ["K-" + str(s.get("schluessel") or s.get("name") or "")[:40] for s in (t.get("statistik") or []) if isinstance(s, dict)][:10],
                              "verweise": mids[:40]}

    # c) Vorausschau: Thesenstand je Tag + belegende Meldungen
    thesen = jsonl_laden(os.path.join(ORDNER, "thesen"))
    vs = _laden("vorausschau.json", {}) or {}
    for k in vs.get("einschaetzungen") or []:
        if not k.get("kb_id"):
            continue
        tid = f"T-{k.get('datum') or vs.get('datum') or heute}-{k['kb_id']}"
        mids = []
        for q in k.get("quellen") or []:
            art = nach_id.get(q.get("id")) or {"id": q.get("id"), "title": q.get("titel"), "source": q.get("quelle"),
                                               "link": q.get("link"), "date": q.get("datum")}
            if not art.get("id") and not art.get("link"):
                continue
            z = meldung_zeile(art, "these", heute, {"verweise": [tid]})
            dazu(z)
            mids.append(z["id"])
        w, mp = k.get("wahrscheinlichkeit") or {}, k.get("messpunkt") or {}
        thesen[tid] = {"id": tid, "typ": "these", "datum": k.get("datum") or heute, "kb_id": k["kb_id"],
                       "titel": k.get("titel"), "p": w.get("p"), "grundrate": w.get("grundrate"),
                       "sicherheit": k.get("sicherheit"), "messpunkt": {x: mp.get(x) for x in ("sym", "richtung", "tage", "startwert")} if mp else None,
                       "experten": w.get("experten") or {}, "haeuser": (k.get("signal") or {}).get("haeuser"),
                       "zusammengefasst": [z.get("kb_id") for z in (k.get("zusammengefasst") or [])],
                       "verweise": mids[:30] + (["K-markt-" + mp["sym"]] if mp.get("sym") else [])}

    # d) Kennzahlen: amtliche Statistik je Beobachtung
    kennzahlen = {}
    pfad_k = os.path.join(ORDNER, "kennzahlen.jsonl")
    if os.path.exists(pfad_k):
        for z in open(pfad_k, encoding="utf-8"):
            try:
                r = json.loads(z)
                kennzahlen[r["id"]] = r
            except (ValueError, KeyError):
                pass
    for schl, s in (_laden("statistik_cache.json", {}) or {}).items():
        for p in s.get("reihe") or []:
            periode, wert = (p[0], p[1]) if isinstance(p, (list, tuple)) and len(p) >= 2 else (p.get("periode"), p.get("wert")) if isinstance(p, dict) else (None, None)
            if periode is None or not isinstance(wert, (int, float)):
                continue
            kid = f"K-{schl}-{periode}"
            kennzahlen[kid] = {"id": kid, "typ": "kennzahl", "reihe": schl, "name": s.get("name"), "quelle": s.get("quelle"),
                               "periode": str(periode), "wert": wert, "abgerufen": s.get("datum")}

    # e) Verweise Meldung → Ereignis (Ereignistabelle)
    try:
        import ereignisse as ev
        for r in ev.bestand().values():
            for mid in r.get("meldung_ids") or []:
                z = meldungen.get("M-" + str(mid))
                if z and r["id"] not in (z.get("verweise") or []):
                    z["verweise"] = sorted(set(z.get("verweise") or []) | {"E-" + r["id"]})
                    z["kategorie"] = r.get("kategorie")
    except Exception:
        pass
    return meldungen, deepdives, thesen, kennzahlen, neu_m, nach_id


# ── 2. Begriffshäufigkeit über alle Meldungen ────────────────────────
def begriffstage_laden():
    tage = {}
    d = os.path.join(ORDNER, "begriffe")
    if os.path.isdir(d):
        for f in sorted(os.listdir(d)):
            if f.endswith(".json"):
                tage.update((_laden(os.path.join(d, f), {}) or {}).get("tage") or {})
    return tage


def begriffstage_schreiben(tage):
    nach = defaultdict(dict)
    for t, v in tage.items():
        nach[t[:7]][t] = v
    for monat, inhalt in nach.items():
        _schreiben(os.path.join(ORDNER, "begriffe", f"{monat}.json"), {"tage": dict(sorted(inhalt.items()))})


def zaehlen(artikel):
    df, n = Counter(), 0
    for a in artikel:
        df.update(begriffe((a.get("title") or "") + " " + (a.get("desc") or "")[:300]))
        n += 1
    gekappt = {w: c for w, c in df.most_common(MAX_BEGRIFFE_TAG) if c >= 2}
    return {"n": n, "df": gekappt}


def begriffshaeufigkeit(roh_artikel, heute):
    tage = begriffstage_laden()
    nach_tag = defaultdict(list)
    for a in roh_artikel:
        t = tag_von(a.get("date"))
        if t and t <= heute:
            nach_tag[t].append(a)
    if nach_tag:
        aeltester = min(nach_tag)
        for t, liste in nach_tag.items():
            if t == aeltester and len(nach_tag) > 1:
                continue                     # erster Tag nur angeschnitten
            neu = zaehlen(liste)
            if neu["n"] >= (tage.get(t) or {}).get("n", 0):
                tage[t] = neu
    # Nachtrag aus dem Archiv (fehlende Tage, neueste zuerst)
    nachgetragen = 0
    try:
        import vorausschau as vsm
        arch = vsm.archiv_laden()
    except Exception:
        arch = []
    if arch:
        arch_tag = defaultdict(list)
        for a in arch:
            t = tag_von(a.get("date"))
            if t and t < heute:
                arch_tag[t].append(a)
        fehlend = sorted((t for t in arch_tag if t not in tage), reverse=True)[:NACHTRAG_TAGE]
        for t in fehlend:
            tage[t] = zaehlen(arch_tag[t])
            nachgetragen += 1
    begriffstage_schreiben(tage)
    return tage, nachgetragen


def auffaellige_begriffe(tage, stichtag, fenster=28):
    """Aktuelles Fenster = Stichtag und Vortag zusammen (robust gegen den
    angebrochenen Tag); Vergleich mit den 28 Tagen DAVOR, damit der Beginn
    einer Welle nicht schon in der Vergleichsbasis steckt."""
    if stichtag not in tage:
        return []
    s0 = date.fromisoformat(stichtag)
    akt_tage = [t for t in (stichtag, (s0 - timedelta(days=1)).isoformat()) if t in tage]
    df_akt, n_akt = Counter(), 0
    for t in akt_tage:
        df_akt.update(tage[t]["df"])
        n_akt += tage[t]["n"]
    vortage = [tage[(s0 - timedelta(days=k)).isoformat()] for k in range(2, fenster + 2)
               if (s0 - timedelta(days=k)).isoformat() in tage]
    if len(vortage) < 7 or not n_akt:
        return []
    sum_n = sum(v["n"] for v in vortage)
    out = []
    for w, c in df_akt.items():
        if c < 5:
            continue
        serie = [(v["df"].get(w, 0), v["n"]) for v in vortage]
        p0 = (sum(x for x, _ in serie) + 0.5) / (sum_n + 1)
        lam = n_akt * p0
        chi = sum((x - nn * p0) ** 2 / max(1e-9, nn * p0) for x, nn in serie)
        phi = max(1.0, chi / max(1, len(serie) - 1))
        z = (c - lam) / math.sqrt(phi * lam)
        verh = (c / n_akt) / p0
        if z >= 3 and verh >= 1.5:
            out.append({"begriff": w, "df": c, "erwartet": round(lam, 1), "z": round(z, 2), "faktor": round(verh, 1),
                        "neu": sum(x for x, _ in serie) == 0, "fenster": akt_tage})
    return sorted(out, key=lambda x: -x["z"])[:40]


# ── 3. Verknüpfungen ─────────────────────────────────────────────────
def kandidat_paare(meldungen, heute):
    grenze = (date.fromisoformat(heute) - timedelta(days=FENSTER_PAARE)).isoformat()
    zeilen = [z for z in meldungen.values() if (z.get("datum") or "") >= grenze and z.get("begriffe")]
    N = len(zeilen)
    if N < 30:
        return []
    einzel, paare = Counter(), Counter()
    tage, haeuser, belege = defaultdict(set), defaultdict(set), defaultdict(list)
    for z in zeilen:
        b = sorted(set(z["begriffe"]))
        einzel.update(b)
        for i in range(len(b)):
            for j in range(i + 1, len(b)):
                p = (b[i], b[j])
                paare[p] += 1
                tage[p].add(z["datum"])
                haeuser[p].add(z.get("haus"))
                if len(belege[p]) < 10:
                    belege[p].append(z["id"])
    tests = []
    for (a, b), n_ab in paare.items():
        if n_ab < 4 or len(tage[(a, b)]) < 2 or len(haeuser[(a, b)]) < 2:
            continue
        if a.startswith(b) or b.startswith(a) or KOEPFE.get(a) == b or KOEPFE.get(b) == a:
            continue
        lift = n_ab * N / (einzel[a] * einzel[b])
        if lift < 2:
            continue
        tests.append({"a": a, "b": b, "n_ab": n_ab, "n_a": einzel[a], "n_b": einzel[b], "N": N, "lift": round(lift, 2),
                      "p": hypergeom_oben(n_ab, N, einzel[a], einzel[b]), "tage": len(tage[(a, b)]),
                      "haeuser": len(haeuser[(a, b)]), "belege": belege[(a, b)]})
    if not tests:
        return []
    for t, q in zip(tests, bh([t["p"] for t in tests])):
        t["q"] = q
    return [t for t in tests if t["q"] <= 0.05]


def begriff_markt(tage, heute):
    try:
        import prognose_modell as pm
    except Exception:
        return []
    out = []
    tage_sortiert = sorted(t for t in tage if t >= (date.fromisoformat(heute) - timedelta(days=180)).isoformat())
    for begriff, syms in SYMBOL.items():
        reihe = next((r for r in (pm.kursreihe(s) for s in syms) if len(r) >= 60), None)
        if not reihe:
            continue
        sym = next(s for s in syms if pm.kursreihe(s) is reihe)
        kurs = {d.isoformat(): v for d, v in reihe}
        kd = sorted(kurs)
        idx = {d: i for i, d in enumerate(kd)}
        x, y0, y5 = [], [], []
        for t in tage_sortiert:
            v = tage[t]
            if not v.get("n"):
                continue
            nachher = [d for d in kd if d >= t][:6]
            vorher = [d for d in kd if d < t][-1:]
            if len(nachher) < 6 or not vorher:
                continue
            p0 = kurs[vorher[0]]
            x.append(v["df"].get(begriff, 0) / v["n"])
            y0.append(abs(math.log(kurs[nachher[0]] / p0)))
            y5.append(sum(abs(math.log(kurs[nachher[k]] / kurs[nachher[k - 1]])) for k in range(1, 6)) / 5)
        if len(x) < 60 or not any(x):
            continue
        r0, p_0 = spearman(x, y0)
        r5, p_5 = spearman(x, y5)
        bester = min(((r0, p_0, "gleicher Tag"), (r5, p_5, "5 Folgetage")), key=lambda t: t[1])
        out.append({"a": begriff, "sym": sym, "r": bester[0], "p": bester[1], "zeitraum": bester[2], "n": len(x),
                    "r_gleich": r0, "r_folge": r5})
    return out


def these_deepdive(thesen, deepdives, heute):
    grenze = (date.fromisoformat(heute) - timedelta(days=14)).isoformat()
    out = []
    for t in thesen.values():
        if (t.get("datum") or "") < grenze:
            continue
        tm = {v for v in t.get("verweise") or [] if v.startswith("M-")}
        for d in deepdives.values():
            if (d.get("datum") or "") < grenze:
                continue
            gem = tm & set(d.get("verweise") or [])
            if len(gem) >= 2:
                out.append({"these": t["kb_id"], "t_id": t["id"], "d_id": d["id"], "thema": d.get("titel"), "gemeinsam": sorted(gem)[:10]})
    return out


def verknuepfungen_pflegen(alt, beobachtet, heute):
    """Gedächtnisstärke: S ← S·e^(−Δt/τ) + 1 je Bestätigung; < 0,2 → Archiv."""
    zeilen = dict(alt)
    t_heute = date.fromisoformat(heute)
    bestaetigt = set()
    for b in beobachtet:
        xid = "X-" + hashlib.sha1(f"{b['art']}|{b['von']}|{b['nach']}".encode()).hexdigest()[:12]
        bestaetigt.add(xid)
        z = zeilen.get(xid)
        if z:
            dt = (t_heute - date.fromisoformat(z["zuletzt"])).days
            if dt == 0:
                z.update({"statistik": b["statistik"], "belege": b.get("belege") or z.get("belege")})
                continue
            z["staerke"] = round(z["staerke"] * math.exp(-dt / TAU) + 1, 3)
            z["beobachtungen"] += 1
            z.update({"zuletzt": heute, "statistik": b["statistik"], "belege": b.get("belege") or z.get("belege"),
                      "status": "aktiv"})
        else:
            zeilen[xid] = {"id": xid, "typ": "verknuepfung", "art": b["art"], "von": b["von"], "nach": b["nach"],
                           "titel": b["titel"], "statistik": b["statistik"], "belege": b.get("belege") or [],
                           "erstmals": heute, "zuletzt": heute, "beobachtungen": 1, "staerke": 1.0, "status": "neu",
                           "verweise": [b["von"], b["nach"]]}
    vergessen = []
    for xid, z in list(zeilen.items()):
        if xid in bestaetigt:
            continue
        dt = (t_heute - date.fromisoformat(z["zuletzt"])).days
        s = z["staerke"] * math.exp(-dt / TAU)
        z["staerke_aktuell"] = round(s, 3)
        z["status"] = "ruhend"
        if s < 0.2:
            vergessen.append(zeilen.pop(xid))
    for z in zeilen.values():
        if z["id"] in bestaetigt:
            z["staerke_aktuell"] = z["staerke"]
            if z["status"] == "neu" and z["beobachtungen"] >= 3:
                z["status"] = "aktiv"
    return zeilen, vergessen


# ── 4. Begriffsobjekte (Langzeit) ────────────────────────────────────
def begriffsobjekte(alt, tage, auff, heute):
    obj = dict(alt)
    letzte = sorted(tage)[-365:]
    n365 = sum(tage[t]["n"] for t in letzte) or 1
    letzte28 = sorted(tage)[-28:]
    n28 = sum(tage[t]["n"] for t in letzte28) or 1
    for a in auff:
        w = a["begriff"]
        o = obj.setdefault(w, {"id": "B-" + w, "typ": "begriff", "begriff": w, "erstmals_auffaellig": heute,
                               "auffaellig_tage": [], "staerke": 0.0, "zuletzt": heute})
        if heute not in o["auffaellig_tage"]:
            dt = (date.fromisoformat(heute) - date.fromisoformat(o["zuletzt"])).days
            o["staerke"] = round(o["staerke"] * math.exp(-dt / TAU) + 1, 3)
            o["auffaellig_tage"] = (o["auffaellig_tage"] + [heute])[-120:]
        o.update({"zuletzt": heute, "letzter_z": a["z"]})
    for w, o in obj.items():
        o["rate_365"] = round(sum(tage[t]["df"].get(w, 0) for t in letzte) / n365, 5)
        o["rate_28"] = round(sum(tage[t]["df"].get(w, 0) for t in letzte28) / n28, 5)
        o["trend"] = round(o["rate_28"] / o["rate_365"], 2) if o["rate_365"] else None
        o["symbol"] = (SYMBOL.get(w) or [None])[0]
    return obj


def main():
    heute = datetime.now(BERLIN).date().isoformat()
    os.makedirs(ORDNER, exist_ok=True)
    meldungen, deepdives, thesen, kennzahlen, neu_m, nach_id = sammeln(heute)
    roh = list(nach_id.values()) or ((_laden("articles.json", {}) or {}).get("articles") or [])
    tage, nachgetragen = begriffshaeufigkeit(roh, heute)
    stichtag = max((t for t in tage if t <= heute), default=None)
    auff = auffaellige_begriffe(tage, stichtag) if stichtag else []

    beobachtet = []
    for p in kandidat_paare(meldungen, heute):
        beobachtet.append({"art": "begriff_begriff", "von": "B-" + p["a"], "nach": "B-" + p["b"], "titel": f"{p['a']} ↔ {p['b']}",
                           "statistik": {k: (round(v, 6) if isinstance(v, float) else v) for k, v in p.items() if k not in ("a", "b", "belege")},
                           "belege": p["belege"]})
    for m in begriff_markt(tage, heute):
        if m["p"] < 0.01 and m["r"] is not None:
            beobachtet.append({"art": "begriff_markt", "von": "B-" + m["a"], "nach": "K-markt-" + m["sym"],
                               "titel": f"{m['a']} ↔ {m['sym']} ({m['zeitraum']})",
                               "statistik": {k: (round(v, 6) if isinstance(v, float) else v) for k, v in m.items() if k != "a"}})
    for b in these_deepdive(thesen, deepdives, heute):
        beobachtet.append({"art": "these_deepdive", "von": "T-" + b["these"], "nach": b["d_id"],
                           "titel": f"{b['these']} ↔ {b['thema']}", "statistik": {"gemeinsame_meldungen": len(b["gemeinsam"])},
                           "belege": b["gemeinsam"]})
    vk_alt = (_laden(os.path.join(ORDNER, "verknuepfungen.json"), {}) or {}).get("zeilen") or {}
    vk, vergessen = verknuepfungen_pflegen(vk_alt, beobachtet, heute)
    bo = begriffsobjekte((_laden(os.path.join(ORDNER, "begriffe.json"), {}) or {}).get("zeilen") or {}, tage, auff, heute)

    # Speichern
    jsonl_schreiben(os.path.join(ORDNER, "meldungen"), meldungen)
    jsonl_schreiben(os.path.join(ORDNER, "deepdives"), deepdives)
    jsonl_schreiben(os.path.join(ORDNER, "thesen"), thesen)
    with open(os.path.join(ORDNER, "kennzahlen.jsonl"), "w", encoding="utf-8") as fh:
        for r in sorted(kennzahlen.values(), key=lambda r: r["id"]):
            fh.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")
    _schreiben(os.path.join(ORDNER, "verknuepfungen.json"), {"stand": heute, "zeilen": vk})
    if vergessen:
        with open(os.path.join(ORDNER, "verknuepfungen_archiv.jsonl"), "a", encoding="utf-8") as fh:
            for z in vergessen:
                fh.write(json.dumps(dict(z, vergessen_am=heute), ensure_ascii=False, separators=(",", ":")) + "\n")
    _schreiben(os.path.join(ORDNER, "begriffe.json"), {"stand": heute, "zeilen": bo})

    # Belege für auffällige Begriffe: vorsortierte Meldungen zuerst
    for a in auff:
        a["belege"] = [z["id"] for z in meldungen.values() if z.get("datum") in a["fenster"] and a["begriff"] in (z.get("begriffe") or [])][:6]
    lagebild = {
        "stand": datetime.now(BERLIN).isoformat(timespec="minutes"), "stichtag": stichtag,
        "auffaellige_begriffe": auff[:25],
        "neue_verknuepfungen": sorted((z for z in vk.values() if z["erstmals"] == heute), key=lambda z: -(z["statistik"].get("lift") or abs(z["statistik"].get("r") or 0)))[:20],
        "staerkste_verknuepfungen": sorted(vk.values(), key=lambda z: -(z.get("staerke_aktuell") or z["staerke"]))[:25],
        "thesen_heute": [{"kb_id": t["kb_id"], "titel": t["titel"], "p": t.get("p"), "grundrate": t.get("grundrate")}
                         for t in thesen.values() if t["datum"] == heute],
        "deepdive_themen": sorted({d["titel"] for d in deepdives.values() if d["datum"] >= (date.fromisoformat(heute) - timedelta(days=7)).isoformat()}),
        "umfang": {"meldungen": len(meldungen), "deepdives": len(deepdives), "thesen": len(thesen), "kennzahlen": len(kennzahlen),
                   "begriffstage": len(tage), "begriffe": len(bo), "verknuepfungen": len(vk), "vergessen_heute": len(vergessen)}}
    _schreiben(os.path.join(ORDNER, "lagebild.json"), lagebild, kompakt=False)
    _schreiben(os.path.join(ORDNER, "schema.json"), {
        "typen": {"M": "Meldung", "D": "Deep Dive", "T": "These (Tagesstand)", "K": "Kennzahl / Marktsymbol",
                  "B": "Begriff", "X": "Verknüpfung", "E": "Ereignis (ereignisse/)"},
        "verknuepfungsarten": ["begriff_begriff", "begriff_markt", "these_deepdive"],
        "gedaechtnis": {"tau_tage": TAU, "vergessen_unter": 0.2},
        "auffaellig": "quasi-Poisson z ≥ 3 gegen 28 Vortage, df ≥ 5, Faktor ≥ 1,5",
        "paare": "hypergeometrisch exakt, Lift ≥ 2, ≥ 4 Meldungen, ≥ 2 Tage, ≥ 2 Häuser, BH q ≤ 0,05, Fenster 90 Tage"}, kompakt=False)
    u = lagebild["umfang"]
    print(f"→ Gedächtnis: {u['meldungen']} Meldungen (+{neu_m}), {u['deepdives']} Deep Dives, {u['thesen']} Thesenstände, "
          f"{u['kennzahlen']} Kennzahlen, {u['begriffstage']} Begriffstage (+{nachgetragen} nachgetragen), "
          f"{u['verknuepfungen']} Verknüpfungen ({len(lagebild['neue_verknuepfungen'])} neu, {len(vergessen)} vergessen)")
    if auff:
        print("  Auffällig am " + str(stichtag) + ": " + ", ".join(f"{a['begriff']} ×{a['faktor']} (z {a['z']})" for a in auff[:8]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
