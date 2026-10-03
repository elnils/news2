#!/usr/bin/env python3
"""
ereignisse.py  –  Ereignistabelle im Backend (Feature Store)

Eine Zeile je Ereignis, eine Spalte je Merkmal. Die Tabelle ist die
Rechengrundlage für das gelernte Modell der Vorausschau (prognose_modell.py)
und für spätere Auswertungen (Zeitreihen, Abhängigkeiten, Stresstests,
Abgleich mit Handelsdaten). In der App wird sie nicht angezeigt.

WAS EIN EREIGNIS IST
Ein Auslöser der Wissensbasis, über den an einem Tag mindestens zwei Häuser
berichten ("Angriff auf Ölinfrastruktur am 30.09., 5 Häuser"), plus die
Schlagzeilen der KI-Top-Auswahl, die keinem Auslöser zugeordnet sind.

SPALTEN (feste Wertelisten, damit gerechnet werden kann)
  id, datum, these, quelle_art (these|top), titel, haeuser, sprachen
  kategorie   konflikt | sanktion | handel_zoll | lieferstoerung | produktion_kapazitaet |
              preis_markt | technologie | regulierung | geld_fiskal | naturereignis |
              infrastruktur_cyber | unternehmen | politik | sonstiges
  status      geschehen | angekuendigt | erwogen | dementiert
  schwere     1–5 (1 gering … 5 systemisch)
  umfang      lokal | national | regional | global
  dauer       tage | wochen | monate | dauerhaft | unklar
  angebot, nachfrage, preis   Wirkung auf das betroffene Gut: −1 | 0 | +1
  laender     ISO-3166-Codes (DEU, USA, CHN …)
  route       hormus | rotes_meer | suez | malakka | panama | taiwanstrasse | bosporus |
              ostsee | rhein | keine
  branchen    NACE-Abschnitte (B Bergbau, C Industrie, D Energie, H Verkehr, J IT, K Finanzen …)
  gut         betroffenes Gut in Worten, hs: HS-Kapitel (zweistellig, z. B. 27 Öl, 85 Elektronik)
  akteure     bis zu fünf Objekte {name, typ, rolle}:
              typ  staat | unternehmen | organisation | person | gruppe
              rolle verursacher | betroffen | reagiert
  menge, einheit   nur wenn wörtlich im Text belegt (sonst leer);
              menge_basis/einheit_basis: umgerechnet (USD, EUR, Barrel/Tag, Tonnen, MW, %)

LOKAL BERECHNET (ohne KI, nachprüfbar)
  haeuser_liste, laender_quellen (Herkunft der Häuser über die Domain-Endung),
  erstmeldung, ausbreitung_std (Stunden bis zum dritten Haus),
  unsicherheit (Anteil der Meldungen mit vager Sprache: soll, angeblich, reportedly …),
  laender_quellen: Länder der Häuser über die Domain-Endung, INT = .com/.net/.org,
  bestaetigt (offiziell bestätigt / amtliche Quelle)

Nachträglich ergänzt von ontologie.py: Kette/Vorgänger, Wirkung am Markt
(Rendite und standardisierte Bewegung nach 1/5/20 Handelstagen), Nachhall.
  neuheit     neu | fortsetzung
  konkret, eskalation, groesse   Textmerkmale wie im Modell

AUSFÜHRUNG
Je Lauf (Volllauf): erst die aktuellen Ereignisse, dann ein Stück Nachtrag
aus dem Archiv – zuerst die Tage, aus denen das Modell lernt. Die KI codiert
sechs Ereignisse je Anfrage. Ungültige Werte werden verworfen, Mengen nur
übernommen, wenn die Zahl im Text steht.

Ablage: ereignisse/JJJJ-MM.jsonl (eine Zeile je Ereignis) und daneben
dieselbe Tabelle als CSV für Auswertungen (Excel, pandas, R).
"""

import csv
import hashlib
import io
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import ki_zusammenfassungen as ki
import prognose_modell as pm
import vorausschau as vs

ORDNER = "ereignisse"
BERLIN = ZoneInfo("Europe/Berlin")
AKTUELL_MAX = int(os.environ.get("EREIGNIS_AKTUELL", "36"))
NACHTRAG_MAX = int(os.environ.get("EREIGNIS_NACHTRAG", "36"))
JE_ANFRAGE = 6

WERTE = {
    "kategorie": ["konflikt", "sanktion", "handel_zoll", "lieferstoerung", "produktion_kapazitaet", "preis_markt",
                  "technologie", "regulierung", "geld_fiskal", "naturereignis", "infrastruktur_cyber",
                  "unternehmen", "politik", "sonstiges"],
    "status": ["geschehen", "angekuendigt", "erwogen", "dementiert"],
    "umfang": ["lokal", "national", "regional", "global"],
    "dauer": ["tage", "wochen", "monate", "dauerhaft", "unklar"],
    "route": ["hormus", "rotes_meer", "suez", "malakka", "panama", "taiwanstrasse", "bosporus", "ostsee", "rhein", "keine"],
    "neuheit": ["neu", "fortsetzung"],
}
NACE = set("ABCDEFGHIJKLMNOPQRSTU")
SPALTEN = ["id", "datum", "these", "quelle_art", "titel", "haeuser", "sprachen", "kategorie", "status", "schwere",
           "umfang", "dauer", "angebot", "nachfrage", "preis", "laender", "route", "branchen", "gut", "hs", "akteure",
           "menge", "einheit", "menge_basis", "einheit_basis", "neuheit", "konkret", "eskalation", "groesse",
           "unsicherheit", "bestaetigt", "haeuser_liste", "laender_quellen", "erstmeldung", "ausbreitung_std",
           "vorgaenger", "kette", "tage_seit_vorgaenger", "wirkung_sym", "r1", "r5", "r20", "z1", "z5", "z20",
           "abnormal5", "nachhall7", "codiert_am", "modell"]
AKTEUR_TYP = ["staat", "unternehmen", "organisation", "person", "gruppe"]
AKTEUR_ROLLE = ["verursacher", "betroffen", "reagiert"]

# ── Lokale Merkmale (ohne KI) ────────────────────────────────────────
VAGE = re.compile(r"\b(soll(?:en|te|ten)?|angeblich|möglicherweise|vermutlich|offenbar|berichten zufolge|insidern?|"
                  r"unbestätigt|gerüchte?|könnte|dürfte|erwägt|prüft|reportedly|allegedly|could|may|might|"
                  r"sources? say|unconfirmed|rumou?rs?|considering|weighs)\b", re.I)
AMTLICH_TEXT = re.compile(r"\b(offiziell|bestätigt(?:e|en)?|teilte .{0,30}mit|laut (?:ministerium|regierung|behörde)|"
                          r"confirmed|officially|said in a statement|announced)\b", re.I)
AMTLICH_HAUS = re.compile(r"(\.gov(\.|$)|\.gv\.|bund\.de$|europa\.eu$|ecb\.|bundesbank|destatis|un\.org$|who\.int$|imf\.org$|"
                          r"bundesregierung|bundestag)", re.I)
TLD_LAND = {"de": "DEU", "at": "AUT", "ch": "CHE", "fr": "FRA", "uk": "GBR", "it": "ITA", "es": "ESP", "nl": "NLD",
            "pl": "POL", "jp": "JPN", "cn": "CHN", "in": "IND", "ru": "RUS", "ua": "UKR", "il": "ISR", "tr": "TUR",
            "ca": "CAN", "au": "AUS", "br": "BRA", "se": "SWE", "dk": "DNK", "no": "NOR", "be": "BEL", "eu": "EUR"}


def lokale_merkmale(meldungen, texte):
    text = " ".join(texte)
    # Anteil der Meldungen mit vager Sprache (0 = alle eindeutig, 1 = alle vage)
    lok = {"unsicherheit": round(sum(1 for t in texte if VAGE.search(t)) / max(1, len(texte)), 3),
           "bestaetigt": 1 if AMTLICH_TEXT.search(text) else 0,
           "haeuser_liste": [], "laender_quellen": [], "erstmeldung": None, "ausbreitung_std": None}
    if meldungen:
        haeuser = sorted({(m.get("haus") or "").lower() for m in meldungen if m.get("haus")})
        lok["haeuser_liste"] = haeuser[:15]
        if any(AMTLICH_HAUS.search(h) for h in haeuser):
            lok["bestaetigt"] = 1
        laender = set()
        for h in haeuser:
            tld = h.rsplit(".", 1)[-1]
            laender.add(TLD_LAND.get(tld, "INT" if tld in ("com", "org", "net", "info") else "?"))
        lok["laender_quellen"] = sorted(laender - {"?"})
        zeiten = []
        for m in meldungen:
            try:
                zeiten.append((datetime.fromisoformat(str(m.get("date")).replace("Z", "+00:00")), (m.get("haus") or "").lower()))
            except ValueError:
                pass
        zeiten = [(z if z.tzinfo else z.replace(tzinfo=timezone.utc), h) for z, h in zeiten]
        zeiten.sort()
        if zeiten:
            lok["erstmeldung"] = zeiten[0][0].isoformat(timespec="minutes")
            seen = []
            for z, h in zeiten:
                if h not in seen:
                    seen.append(h)
                if len(seen) == 3:
                    lok["ausbreitung_std"] = round((z - zeiten[0][0]).total_seconds() / 3600, 1)
                    break
    return lok


GROESSEN = [(r"billionen|\bbio\b|trillion", 1e12), (r"mrd|milliarde|billion", 1e9), (r"mio|million", 1e6),
            (r"tsd|tausend|thousand", 1e3)]


def normieren(menge, einheit):
    """Menge in Basiseinheit: USD, EUR, Barrel/Tag, Tonnen, MW, Prozent.
    Billionen/Bio./trillion = 10^12; Mrd./Milliarde/englisch billion = 10^9."""
    if menge is None:
        return None, ""
    e = (einheit or "").lower()
    basis = ("USD" if re.search(r"\$|usd|dollar", e) else "EUR" if re.search(r"€|eur", e) else
             "Barrel/Tag" if re.search(r"barrel|fass|bpd|b/d", e) else "Tonnen" if re.search(r"tonne|\bt\b", e) else
             "MW" if re.search(r"\b[mgk]w\b|watt", e) else "%" if re.search(r"%|prozent|percent", e) else "")
    if not basis:
        return None, ""
    f = 1.0
    if basis == "MW":
        f = 1000.0 if re.search(r"\bgw\b|giga", e) else 0.001 if re.search(r"\bkw\b", e) else 1.0
    elif basis != "%":
        f = next((fk for muster, fk in GROESSEN if re.search(muster, e)), 1.0)
    return round(float(menge) * f, 3), basis


def _id(these, datum, titel):
    norm = re.sub(r"\W+", " ", titel.lower()).strip()[:80]
    return hashlib.sha1(f"{these}|{datum}|{norm}".encode()).hexdigest()[:12]


def bestand():
    """id → Zeile über alle Monatsdateien."""
    zeilen = {}
    if os.path.isdir(ORDNER):
        for f in sorted(os.listdir(ORDNER)):
            if f.endswith(".jsonl"):
                for z in open(os.path.join(ORDNER, f), encoding="utf-8"):
                    if z.strip():
                        try:
                            r = json.loads(z)
                            zeilen[r["id"]] = r
                        except (ValueError, KeyError):
                            pass
    return zeilen


def _zelle(v):
    if isinstance(v, list):
        return "|".join(x.get("name", "") if isinstance(x, dict) else str(x) for x in v)
    return "" if v is None else v


def speichern(zeilen):
    os.makedirs(ORDNER, exist_ok=True)
    nach_monat = {}
    for r in zeilen.values():
        nach_monat.setdefault(r["datum"][:7], []).append(r)
    for monat, liste in nach_monat.items():
        liste.sort(key=lambda r: (r["datum"], r["id"]))
        with open(os.path.join(ORDNER, f"{monat}.jsonl"), "w", encoding="utf-8") as fh:
            for r in liste:
                fh.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";")
        w.writerow(SPALTEN)
        for r in liste:
            w.writerow([_zelle(r.get(k)) for k in SPALTEN])
        with open(os.path.join(ORDNER, f"{monat}.csv"), "w", encoding="utf-8") as fh:
            fh.write(buf.getvalue())
    with open(os.path.join(ORDNER, "schema.json"), "w", encoding="utf-8") as fh:
        json.dump({"spalten": SPALTEN, "werte": WERTE, "nace": sorted(NACE), "akteur_typ": AKTEUR_TYP, "akteur_rolle": AKTEUR_ROLLE,
                   "hinweis": "Eine Zeile je Ereignis. Codiert von einem Sprachmodell, Mengen nur mit wörtlichem Beleg."},
                  fh, ensure_ascii=False, indent=1)


# ── Kandidaten ───────────────────────────────────────────────────────
def kandidaten_aktuell(basis, artikel, heute):
    out = []
    for e in basis:
        s = vs.signal_fuer(e, artikel)
        if not s or len(s["haeuser"]) < vs.MIN_HAEUSER:
            continue
        tr = s.get("treffer") or []
        out.append({"these": e["id"], "datum": heute, "titel": (tr[0].get("title") if tr else e["titel"]) or e["titel"],
                    "haeuser": len(s["haeuser"]), "texte": [(m.get("title") or "") + ". " + (m.get("desc") or "")[:300] for m in tr[:6]],
                    "sprachen": sorted({m.get("lang") or "?" for m in tr}), "quelle_art": "these", "ausloeser": e["ausloeser"],
                    "meldungen": tr})
    nach_id = {a.get("id"): a for a in artikel if a.get("id")}
    # KI-Top-Schlagzeilen, die keiner These zugeordnet sind
    top = (vs._laden("ki_meldungen.json", {}).get("schlagzeilen") or {}).get("regionen") or {}
    gesehen = set()
    for r in top.values():
        for it in (r.get("items") or []):
            t = (it.get("schlagzeile") or "").strip()
            if t and t not in gesehen:
                gesehen.add(t)
                ms = [nach_id[i] for i in (it.get("ids") or []) if i in nach_id]
                texte = [t + ". " + (it.get("text") or "")[:300]] + [(m.get("title") or "") + ". " + (m.get("desc") or "")[:250] for m in ms[:5]]
                out.append({"these": "", "datum": heute, "titel": t,
                            "haeuser": len({m.get("haus") for m in ms if m.get("haus")}) or len(it.get("ids") or []) or 1,
                            "texte": texte, "sprachen": sorted({m.get("lang") or "?" for m in ms}), "quelle_art": "top",
                            "ausloeser": "", "meldungen": ms})
    return out


def kandidaten_archiv(basis, archiv_art, heute, bekannt):
    """Tage mit Auslöser im Archiv (≥ 2 Häuser) – genau die Fälle, aus denen das Modell lernt. Neueste zuerst."""
    out = []
    for e in basis:
        for tag, t in (vs.treffer_tage(e, archiv_art) or {}).items():
            if tag >= heute or len(t["haeuser"]) < vs.MIN_HAEUSER:
                continue
            titel = t.get("titel") or e["titel"]
            if _id(e["id"], tag, titel) in bekannt:
                continue
            out.append({"these": e["id"], "datum": tag, "titel": titel, "haeuser": len(t["haeuser"]),
                        "texte": (t.get("texte") or [titel])[:6], "sprachen": [], "quelle_art": "these", "ausloeser": e["ausloeser"],
                        "meldungen": [], "haeuser_set": sorted(t["haeuser"])})
    out.sort(key=lambda k: k["datum"], reverse=True)
    return out


# ── Codierung ────────────────────────────────────────────────────────
SYSTEM = ("Du codierst Nachrichtenereignisse für eine Datenbank. Du antwortest nur mit JSON und nur mit den erlaubten Werten. "
          "Was die Texte nicht hergeben, bleibt leer oder 'unklar'. Keine Zahl, die nicht im Text steht.")


def auftrag(stapel):
    teile = []
    for i, k in enumerate(stapel, 1):
        teile.append(f"[{i}] Datum {k['datum']}, {k['haeuser']} Häuser" + (f", Auslöser: {k['ausloeser']}" if k["ausloeser"] else "")
                     + "\n" + "\n".join(f"  - {t[:350]}" for t in k["texte"]))
    return f"""Codiere jedes Ereignis als eine Tabellenzeile.

{chr(10).join(teile)}

Erlaubte Werte:
- kategorie: {", ".join(WERTE["kategorie"])}
- status: {", ".join(WERTE["status"])} (geschehen = ist passiert; angekuendigt = fest beschlossen, kommt noch; erwogen = nur im Gespräch, gedroht, geprüft)
- schwere: 1–5 (1 gering, 3 spürbar für eine Branche, 5 systemisch für Weltwirtschaft oder Sicherheit)
- umfang: {", ".join(WERTE["umfang"])}
- dauer: {", ".join(WERTE["dauer"])}
- angebot, nachfrage, preis: −1, 0 oder 1 – Wirkung auf das betroffene Gut (z. B. Angriff auf Ölanlage: angebot −1, preis 1)
- laender: ISO-3166-Alpha-3-Codes (DEU, USA, CHN, IRN, RUS, TWN …)
- route: {", ".join(WERTE["route"])}
- branchen: NACE-Abschnitte als Buchstaben (B Bergbau, C Industrie, D Energie, H Verkehr, J IT/Telekom, K Finanzen, O Staat, A Landwirtschaft)
- gut: betroffenes Gut in 1–3 Wörtern (z. B. "Rohöl", "Speicherchips", "Weizen"); hs: HS-Kapitel zweistellig (27 Öl/Gas, 85 Elektronik, 10 Getreide, 87 Fahrzeuge, 30 Pharma) oder leer
- akteure: bis zu 5 Objekte {{"name": "…", "typ": "{"|".join(AKTEUR_TYP)}", "rolle": "{"|".join(AKTEUR_ROLLE)}"}}
  (verursacher = löst aus; betroffen = trägt die Folgen; reagiert = antwortet darauf). Volle übliche Namen, keine Abkürzungen außer USA/EU/NATO/OPEC.
- menge, einheit: eine Zahl mit Einheit NUR wenn wörtlich im Text (z. B. 2 und "Mio. Barrel/Tag"), sonst null
- neuheit: neu oder fortsetzung (Fortsetzung einer bekannten Lage)

Antworte NUR mit JSON: {{"zeilen": [{{"nr": 1, "kategorie": "…", "status": "…", "schwere": 3, "umfang": "…", "dauer": "…",
"angebot": 0, "nachfrage": 0, "preis": 0, "laender": ["…"], "route": "keine", "branchen": ["…"], "gut": "…", "hs": "",
"akteure": [{{"name": "…", "typ": "…", "rolle": "…"}}], "menge": null, "einheit": "", "neuheit": "neu"}}]}}"""


def _int(v, lo, hi, leer=None):
    try:
        v = int(round(float(v)))
        return v if lo <= v <= hi else leer
    except (TypeError, ValueError):
        return leer


def pruefen(roh, k):
    """Werte gegen die erlaubten Listen prüfen; Menge nur mit Beleg im Text."""
    z = {}
    for feld, erlaubt in WERTE.items():
        v = str(roh.get(feld) or "").strip().lower()
        z[feld] = v if v in erlaubt else ("unklar" if feld == "dauer" else "keine" if feld == "route" else None)
    z["schwere"] = _int(roh.get("schwere"), 1, 5)
    for f in ("angebot", "nachfrage", "preis"):
        z[f] = _int(roh.get(f), -1, 1, 0)
    z["laender"] = sorted({str(x).upper() for x in (roh.get("laender") or []) if re.fullmatch(r"[A-Za-z]{3}", str(x))})[:8]
    z["branchen"] = sorted({str(x).upper()[:1] for x in (roh.get("branchen") or []) if str(x)[:1].upper() in NACE})
    z["gut"] = str(roh.get("gut") or "")[:40]
    z["hs"] = str(roh.get("hs") or "") if re.fullmatch(r"\d{2}", str(roh.get("hs") or "")) else ""
    akt = []
    for x in (roh.get("akteure") or [])[:5]:
        if isinstance(x, dict) and str(x.get("name") or "").strip():
            akt.append({"name": str(x["name"]).strip()[:60],
                        "typ": x.get("typ") if x.get("typ") in AKTEUR_TYP else None,
                        "rolle": x.get("rolle") if x.get("rolle") in AKTEUR_ROLLE else None})
        elif isinstance(x, str) and x.strip():
            akt.append({"name": x.strip()[:60], "typ": None, "rolle": None})
    z["akteure"] = akt
    menge, text = roh.get("menge"), " ".join(k["texte"])
    z["menge"], z["einheit"] = None, ""
    if isinstance(menge, (int, float)) and not isinstance(menge, bool):
        roh_zahl = re.sub(r"\.0$", "", str(menge))
        if roh_zahl in text or roh_zahl.replace(".", ",") in text:
            z["menge"], z["einheit"] = menge, str(roh.get("einheit") or "")[:30]
    z["menge_basis"], z["einheit_basis"] = normieren(z["menge"], z["einheit"])
    return z


def codieren(anbieter, stapel):
    for _ in range(4):
        if not anbieter:
            return None
        n, url, key, modell = anbieter[0]
        koerper = json.dumps({"model": modell, "temperature": 0, "max_tokens": 1600,
                              "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": auftrag(stapel)}]}).encode()
        try:
            with ki.ki_urlopen(n, url, koerper, {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}, 90) as r:
                j = json.loads(r.read().decode("utf-8", "replace"))
            text = (j.get("choices") or [{}])[0].get("message", {}).get("content", "") or ""
            m = re.search(r"\{.*\}", text, re.S)
            return (json.loads(m.group(0)).get("zeilen") if m else None), modell
        except Exception as e:                       # Anbieter wechseln, nie den Lauf abbrechen
            print(f"  ---  {n}: {type(e).__name__} – nächster Anbieter")
            anbieter.pop(0)
    return None


def main():
    heute = datetime.now(BERLIN).strftime("%Y-%m-%d")
    zeilen = bestand()
    basis = (vs._laden(vs.BASIS, {}) or {}).get("eintraege") or vs.DEFAULT_BASIS
    artikel = vs.artikel_laden()
    archiv_art = vs.archiv_laden()
    akt = [k for k in kandidaten_aktuell(basis, artikel, heute) if _id(k["these"], k["datum"], k["titel"]) not in zeilen][:AKTUELL_MAX]
    nach = kandidaten_archiv(basis, archiv_art, heute, set(zeilen))[:NACHTRAG_MAX] if archiv_art else []
    print(f"  Ereignisse: {len(zeilen)} im Bestand, {len(akt)} neue aktuelle, {len(nach)} Nachtrag aus dem Archiv")
    anbieter = [list(a) for a in ki.ANBIETER]
    neu = 0
    alle = akt + nach
    for i in range(0, len(alle), JE_ANFRAGE):
        stapel = alle[i:i + JE_ANFRAGE]
        erg = codieren(anbieter, stapel)
        if not erg or not erg[0]:
            print("  Kein Anbieter mehr verfügbar – Rest im nächsten Lauf.")
            break
        roh_liste, modell = erg
        nach_nr = {int(r.get("nr", 0)): r for r in roh_liste if isinstance(r, dict) and str(r.get("nr", "")).isdigit()}
        for nr, k in enumerate(stapel, 1):
            r = nach_nr.get(nr)
            if not r:
                continue
            z = pruefen(r, k)
            tm = pm.textmerkmale(k["texte"])
            zeile = {"id": _id(k["these"], k["datum"], k["titel"]), "datum": k["datum"], "these": k["these"],
                     "quelle_art": k["quelle_art"], "titel": k["titel"][:200], "haeuser": k["haeuser"], "sprachen": k["sprachen"],
                     **z, "konkret": round(tm[0], 3), "eskalation": round(tm[1], 3), "groesse": round(tm[2], 3),
                     **lokale_merkmale(k.get("meldungen"), k["texte"]),
                     "meldung_ids": [m.get("id") for m in (k.get("meldungen") or []) if m.get("id")][:15],
                     "codiert_am": heute, "modell": modell}
            zeilen[zeile["id"]] = zeile
            neu += 1
        time.sleep(1)
    if neu:
        speichern(zeilen)
    kat = {}
    for r in zeilen.values():
        kat[r.get("kategorie") or "?"] = kat.get(r.get("kategorie") or "?", 0) + 1
    print(f"→ {ORDNER}/: {neu} Ereignisse codiert, {len(zeilen)} insgesamt · "
          + ", ".join(f"{k} {v}" for k, v in sorted(kat.items(), key=lambda x: -x[1])[:6]))
    return 0


def nachschlagen():
    """(these, datum) → Zeile – für das gelernte Modell."""
    return {(r["these"], r["datum"]): r for r in bestand().values() if r.get("these")}


if __name__ == "__main__":
    sys.exit(main())
