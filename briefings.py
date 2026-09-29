#!/usr/bin/env python3
"""
briefings.py  –  schreibt briefings.json

RESSORT-BRIEFINGS (nach Art von Tagesspiegel Background)
Sieben Ressorts: Energie & Klima, Technik & Digitales, Cyber & KI,
Handel & Wirtschaft, Inneres & Sicherheit, Weltpolitik, Verteidigung.

Je Ressort:
  – ein bis zwei Sätze Lage von der KI (einmal am Morgen, sonst bleibt der
    Satz vom Morgen stehen),
  – die vier aktivsten Unterkategorien, darin je bis zu vier Meldungen mit
    Schlagzeile, Quelle, Link und kurzem Teaser (KI-Zusammenfassung, sonst
    der Vorspann),
  – bis zu zwei Dokumente oder Urteile dazu (Drucksachen, Gerichts-
    entscheidungen, gelesene Dokumente der letzten sieben Tage).

Auswahl ohne KI, bei jedem Lauf neu: Meldungen der letzten 30 Stunden, ohne
Rauschen (Liveticker, Podcasts, Anzeigen), dieselbe Nachricht aus mehreren
Häusern nur einmal – mit der Zahl der Häuser als Gewicht. Innerhalb eines
Briefings steht jede Meldung nur in einer Unterkategorie.
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from urllib.error import HTTPError, URLError

import ki_zusammenfassungen as ki

OUT = "briefings.json"
FENSTER_STD = int(os.environ.get("BRIEF_FENSTER_STD", "30"))
MORGENLAUF = os.environ.get("MORGENLAUF") == "1"
DATEIEN = ["articles.json", "eu_articles.json", "bundestag_articles.json", "laender_articles.json", "us_articles.json"]

R = lambda *m: re.compile("|".join(m), re.I)
RESSORTS = [
    {"id": "energie", "name": "Energie & Klima", "unter": [
        ("Strom & Netze", R(r"\bstrom\w*", r"netzausbau", r"übertragungsnetz", r"stromnetz", r"power grid", r"electricity", r"netzentgelt", r"kraftwerk")),
        ("Gas, Öl & Wasserstoff", R(r"\bgas\b", r"gaspreis", r"erdgas", r"\blng\b", r"ölpreis", r"rohöl", r"\bopec", r"wasserstoff", r"hydrogen", r"\boil\b", r"crude", r"pipeline")),
        ("Erneuerbare", R(r"solar\w*", r"photovoltaik", r"windkraft", r"windrad", r"windpark", r"offshore-wind", r"erneuerbar\w*", r"renewable", r"wärmepumpe", r"geothermie")),
        ("Klima & CO₂-Preis", R(r"klima\w*", r"co2", r"co₂", r"emission\w*", r"emissionshandel", r"klimaschutz", r"klimaziel", r"climate", r"cop\d\d")),
        ("Energiepolitik", R(r"energiepolitik", r"energiewende", r"energiepreis\w*", r"bundesnetzagentur", r"heizungsgesetz", r"gebäudeenergie", r"energy policy")),
    ]},
    {"id": "technik", "name": "Technik & Digitales", "unter": [
        ("Chips & Hardware", R(r"\bchip\w*", r"halbleiter", r"semiconductor", r"nvidia", r"\btsmc\b", r"intel\b", r"infineon", r"\basml\b")),
        ("Plattformen & Regulierung", R(r"digital services act", r"\bdsa\b", r"\bdma\b", r"plattform\w*", r"meta\b", r"google", r"apple", r"amazon", r"tiktok", r"x\.com|\btwitter\b", r"kartell\w* .*digital")),
        ("Telekom & Netze", R(r"telekom\w*", r"glasfaser", r"\b5g\b", r"mobilfunk", r"breitband", r"vodafone", r"telefónica|telefonica", r"funkloch")),
        ("Raumfahrt & Mobilität", R(r"raumfahrt", r"satellit\w*", r"rakete\w*", r"spacex", r"starship", r"esa\b", r"autonom\w* fahr\w*", r"e-auto\w*", r"elektroauto\w*", r"robotaxi")),
        ("Digitalpolitik & Verwaltung", R(r"digitalministerium", r"digitalisierung", r"e-government", r"verwaltungsdigital", r"onlinezugangsgesetz", r"digitalpolitik", r"deutschland-stack")),
    ]},
    {"id": "cyber-ki", "name": "Cyber & KI", "unter": [
        ("Künstliche Intelligenz", R(r"künstliche intelligenz", r"\bki\b", r"\bai\b", r"openai", r"chatgpt", r"anthropic", r"gemini", r"sprachmodell", r"llm", r"deepmind", r"mistral")),
        ("KI-Regulierung", R(r"ai act", r"ki-verordnung", r"ki-gesetz", r"ki-regulierung", r"ai regulation", r"ai safety")),
        ("Cyberangriffe", R(r"cyber\w*", r"hacker\w*", r"ransomware", r"datenleck", r"data breach", r"\bddos\b", r"phishing", r"schadsoftware", r"malware", r"\bbsi\b")),
        ("Daten & Überwachung", R(r"datenschutz", r"\bdsgvo\b", r"gdpr", r"überwachung\w*", r"surveillance", r"vorratsdaten\w*", r"chatkontrolle", r"gesichtserkennung", r"spyware")),
    ]},
    {"id": "handel", "name": "Handel & Wirtschaft", "unter": [
        ("Zölle & Handelskonflikte", R(r"\bzoll\w*", r"zölle", r"tariff\w*", r"handelsstreit", r"handelskonflikt", r"trade war", r"handelsabkommen", r"freihandel", r"mercosur", r"\bwto\b")),
        ("Lieferketten & Industrie", R(r"lieferkette\w*", r"supply chain", r"industrie\w*", r"produktion", r"autoindustrie", r"stahl\w*", r"chemie\w*", r"werk\w* schlie", r"stellenabbau")),
        ("Konjunktur & Märkte", R(r"konjunktur\w*", r"\bifo\b", r"wachstum", r"rezession", r"inflation", r"\bdax\b", r"börse\w*", r"leitzins", r"\bezb\b", r"\bfed\b", r"arbeitsmarkt", r"arbeitslos\w*")),
        ("Unternehmen", R(r"übernahme", r"fusion", r"quartalszahlen", r"umsatz", r"gewinnwarnung", r"insolvenz\w*", r"börsengang", r"merger", r"acquisition", r"earnings")),
        ("Wirtschaftspolitik", R(r"wirtschaftsminister\w*", r"subvention\w*", r"standort\w*", r"bürokratieabbau", r"industriestrompreis", r"schuldenbremse", r"haushalt\w*")),
    ]},
    {"id": "inneres", "name": "Inneres & Sicherheit", "unter": [
        ("Migration & Asyl", R(r"migration\w*", r"asyl\w*", r"flüchtling\w*", r"abschiebung\w*", r"grenzkontroll\w*", r"zurückweisung\w*", r"einbürgerung", r"geflüchtete")),
        ("Polizei & Kriminalität", R(r"polizei\w*", r"kriminalität", r"festnahme", r"razzia", r"anschlag\w*", r"terror\w*", r"messerangriff\w*", r"clan\w*", r"verfassungsschutz", r"extremis\w*")),
        ("Justiz & Verfassung", R(r"bundesverfassungsgericht", r"\bbverfg\b", r"karlsruhe", r"bundesgerichtshof", r"\bbgh\b", r"urteil\w*", r"grundgesetz", r"justizminister\w*", r"strafrecht")),
        ("Bund & Länder", R(r"ministerpräsident\w*", r"landtag\w*", r"landesregierung", r"bundesrat", r"föderal\w*", r"kommunen", r"länderfinanz\w*", r"landtagswahl\w*")),
        ("Parteien & Koalition", R(r"koalition\w*", r"\bcdu\b", r"\bspd\b", r"\bafd\b", r"grüne", r"\bcsu\b", r"linke", r"\bbsw\b", r"parteitag", r"umfrage\w*")),
    ]},
    {"id": "welt", "name": "Weltpolitik", "unter": [
        ("Ukraine & Russland", R(r"ukrain\w*", r"russ\w*", r"kreml", r"putin", r"selenskyj|selensky|zelensky", r"kyiv|kiew", r"moskau|moscow")),
        ("Nahost", R(r"israel\w*", r"gaza", r"hamas", r"hisbollah|hezbollah", r"iran\w*", r"libanon|lebanon", r"westjordanland", r"syrien|syria", r"jemen|yemen|huthi|houthi")),
        ("USA", R(r"\busa\b", r"\bus-", r"washington", r"weißes haus|white house", r"trump", r"kongress|congress", r"senat\b|senate", r"supreme court", r"pentagon")),
        ("China & Asien", R(r"china\w*", r"chines\w*", r"peking|beijing", r"\bxi\b", r"taiwan", r"japan\w*", r"indien|india", r"korea\w*", r"asean")),
        ("Europa & EU", R(r"\beu\b", r"europäische union", r"brüssel|brussels", r"kommission|commission", r"von der leyen", r"europaparlament|european parliament", r"frankreich|france", r"polen|poland")),
    ]},
    {"id": "verteidigung", "name": "Verteidigung", "unter": [
        ("Bundeswehr & Rüstung", R(r"bundeswehr", r"rüstung\w*", r"rheinmetall", r"beschaffung\w*", r"wehrdienst\w*", r"wehrpflicht", r"verteidigungsminister\w*", r"sondervermögen", r"pistorius")),
        ("NATO & Bündnis", R(r"\bnato\b", r"bündnis\w*", r"ostflanke", r"abschreckung", r"alliance", r"article 5|artikel 5")),
        ("Hybride Bedrohungen", R(r"drohne\w*", r"\bdrone\w*", r"sabotage", r"desinformation\w*", r"spionage", r"hybrid\w*", r"gps-stör\w*", r"jamming")),
        ("Waffen & Konflikte", R(r"waffenlieferung\w*", r"raketenabwehr", r"luftverteidigung", r"patriot", r"taurus", r"marschflugkörper", r"militär\w*", r"military")),
    ]},
]
RAUSCH = re.compile(r"liveblog|liveticker|live-ticker|\+\+|newsblog|podcast|anzeige:|werbung|gewinnspiel|horoskop|"
                    r"rätsel|quiz|bilder des tages|wetter\b|tv-tipp|bundesliga|champions league|oktoberfest|wiesn", re.I)
GERICHT = re.compile(r"bundesgerichtshof|bundesverfassungsgericht|bundesfinanzhof|bundesarbeitsgericht|bundessozialgericht|"
                     r"bverwg|curia|\bBGH\b|\bBVerfG\b|\bEuGH\b", re.I)


def laden(pfad, leer):
    try:
        return json.load(open(pfad, encoding="utf-8"))
    except (OSError, ValueError):
        return leer


def zeit(a):
    try:
        return datetime.fromisoformat(str(a.get("date", "")).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def worte(t):
    return {w for w in re.findall(r"[a-zäöüß]{4,}", (t or "").lower())}


def buendeln(arts):
    """Dieselbe Nachricht aus mehreren Häusern: eine Gruppe, Leitmeldung ist
    die mit dem längsten Vorspann; Gewicht = Zahl der Häuser."""
    gruppen = []
    for a in sorted(arts, key=zeit, reverse=True):
        w = worte(a.get("title"))
        for g in gruppen:
            gem = len(w & g["worte"])
            if gem >= 3 or (gem >= 2 and gem / max(1, min(len(w), len(g["worte"]))) >= 0.5):
                g["arts"].append(a)
                g["haeuser"].add(a.get("haus") or a.get("source"))
                g["worte"] |= w
                break
        else:
            gruppen.append({"arts": [a], "worte": set(w), "haeuser": {a.get("haus") or a.get("source")}})
    for g in gruppen:
        g["leit"] = max(g["arts"], key=lambda a: (len(a.get("desc") or ""), zeit(a)))
        g["gewicht"] = len(g["haeuser"]) * 2 + max(int(a.get("cluster") or 1) for a in g["arts"]) + \
            (1 if time.time() - zeit(g["leit"]) < 8 * 3600 else 0)
    return sorted(gruppen, key=lambda g: g["gewicht"], reverse=True)


def teaser(a, ki_items):
    k = (ki_items.get(a.get("id")) or {})
    t = k.get("ki") or k.get("text") or a.get("desc") or ""
    t = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t)).strip()
    return (t[:180].rsplit(" ", 1)[0] + " …") if len(t) > 190 else t


def ki_lage(anbieter, name, zeilen):
    auftrag = (f"Ressort: {name}\nWichtigste Meldungen der letzten 24 Stunden:\n" + "\n".join(zeilen[:12]) +
               "\n\nSchreibe die Lage in diesem Ressort in ein bis zwei Sätzen (zusammen höchstens 260 Zeichen), "
               "sachlich, auf Deutsch, nur aus diesen Meldungen, ohne Wertung und ohne Einleitung wie "
               "\"Heute\" oder \"Die Lage\". Antworte nur mit dem Text.")
    for _ in range(6):
        if not anbieter:
            return None
        n, url, key, modell = anbieter[0]
        koerper = json.dumps({"model": modell, "temperature": 0.2, "max_tokens": 200,
                              "messages": [{"role": "system", "content": ki.SYSTEM},
                                           {"role": "user", "content": auftrag}]}).encode("utf-8")
        kopf = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
        try:
            with ki.ki_urlopen(n, url, koerper, kopf, 60) as r:
                j = json.loads(r.read().decode("utf-8", "replace"))
            t = ((j.get("choices") or [{}])[0].get("message", {}).get("content", "") or "").strip()
            t = re.sub(r"^[\"„“]|[\"“”]$", "", t).strip()
            return t[:320] if len(t) >= 30 else None
        except HTTPError as e:
            print(f"  ---  {n} HTTP {e.code} – nächster Anbieter")
            anbieter.pop(0)
        except (URLError, ValueError, KeyError, TimeoutError, OSError) as e:
            print(f"  ---  {n} {type(e).__name__} – nächster Anbieter")
            anbieter.pop(0)
    return None


def main():
    jetzt = time.time()
    grenze = jetzt - FENSTER_STD * 3600
    arts = []
    for d in DATEIEN:
        for a in (laden(d, {}).get("articles") or []):
            if zeit(a) >= grenze and a.get("title") and not RAUSCH.search(a.get("title") or ""):
                arts.append(a)
    ki_items = (laden("ki_meldungen.json", {}).get("items") or {})
    # Dokumente und Urteile der letzten sieben Tage
    dgrenze = jetzt - 7 * 86400
    doks = [{"titel": d.get("title", ""), "text": (d.get("title") or "") + " " + (d.get("desc") or ""), "quelle": d.get("source", ""),
             "link": d.get("link", ""), "datum": str(d.get("date", ""))[:10], "art": "Dokument"}
            for d in (laden("documents.json", {}).get("documents") or []) if zeit(d) >= dgrenze]
    dt = laden("dokumente_text.json", {}).get("dokumente") or {}
    for a in arts + [x for f in DATEIEN[:1] for x in (laden(f, {}).get("articles") or []) if zeit(x) >= dgrenze]:
        if GERICHT.search((a.get("source") or "") + " " + (a.get("link") or "")):
            v = dt.get(a.get("id")) or {}
            doks.append({"titel": v.get("titel") or a.get("title", ""), "text": (a.get("title") or "") + " " + (v.get("kurz") or a.get("desc") or ""),
                         "quelle": a.get("source", ""), "link": a.get("link", ""), "datum": str(a.get("date", ""))[:10],
                         "art": "Urteil", "kurz": v.get("kurz") or ""})
    alt = laden(OUT, {})
    alt_ki = {r["id"]: (r.get("ki"), r.get("ki_datum")) for r in alt.get("ressorts") or []}
    heute = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    anbieter = [list(a) for a in ki.ANBIETER]
    ki_jetzt = MORGENLAUF or os.environ.get("BRIEF_KI_FORCE") == "1"
    out = []
    for r in RESSORTS:
        vergeben = set()
        unter_out = []
        alle_muster = R(*[m.pattern for _, m in r["unter"]])
        kandidaten = [a for a in arts if alle_muster.search((a.get("title") or "") + " " + (a.get("desc") or "")[:200])]
        gruppen = buendeln(kandidaten)
        for name, m in r["unter"]:
            eintraege = []
            for g in gruppen:
                a = g["leit"]
                if id(g) in vergeben or not m.search((a.get("title") or "") + " " + (a.get("desc") or "")[:200]):
                    continue
                vergeben.add(id(g))
                eintraege.append({"id": a.get("id"), "titel": a.get("title", ""), "quelle": a.get("source", ""),
                                  "link": a.get("link", ""), "teaser": teaser(a, ki_items), "haeuser": len(g["haeuser"]),
                                  "datum": a.get("date", "")})
                if len(eintraege) >= 4:
                    break
            if eintraege:
                unter_out.append({"name": name, "artikel": eintraege,
                                  "gewicht": sum(e["haeuser"] for e in eintraege)})
        unter_out.sort(key=lambda u: u["gewicht"], reverse=True)
        unter_out = unter_out[:4]
        doks_r = [d for d in doks if alle_muster.search(d["text"])]
        doks_r.sort(key=lambda d: d["datum"], reverse=True)
        eintrag = {"id": r["id"], "name": r["name"], "unterkategorien": unter_out,
                   "dokumente": [{k: d.get(k, "") for k in ("titel", "quelle", "link", "datum", "art", "kurz")} for d in doks_r[:2]],
                   "meldungen": sum(len(u["artikel"]) for u in unter_out)}
        ki_alt, ki_datum = alt_ki.get(r["id"], (None, None))
        if ki_jetzt or not ki_alt or ki_datum != heute:
            if anbieter and (ki_jetzt or ki_datum != heute) and unter_out:
                zeilen = [f"- ({e['quelle']}, {e['haeuser']} Häuser) {e['titel']}" for u in unter_out for e in u["artikel"]]
                t = ki_lage(anbieter, r["name"], zeilen)
                if t:
                    ki_alt, ki_datum = t, heute
        eintrag["ki"], eintrag["ki_datum"] = ki_alt, ki_datum
        out.append(eintrag)
        print(f"  {r['name']:<22} {eintrag['meldungen']:>2} Meldungen in {len(unter_out)} Unterkategorien, "
              f"{len(eintrag['dokumente'])} Dokumente{', KI-Lage' if eintrag.get('ki') else ''}")
    daten = {"stand": datetime.now(timezone.utc).isoformat(), "fenster_std": FENSTER_STD, "ressorts": out}
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(daten, fh, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, OUT)
    print(f"→ {OUT}: {len(out)} Ressort-Briefings")
    return 0


if __name__ == "__main__":
    sys.exit(main())
