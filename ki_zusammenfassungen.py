#!/usr/bin/env python3
"""
ki_zusammenfassungen.py  –  schreibt ki_meldungen.json

Die Schlüssel liegen als GitHub-Secrets und bleiben dort: Die
Zusammenfassungen entstehen im Actions-Lauf, nicht im Browser. Die Seite
liest nur die fertige Datei – kein Schlüssel, kein Proxy, keine Wartezeit.

Reihenfolge wie bei fetch_directory.py: OpenRouter hat Vorrang (OPEN_ROUTER_API
+ OR_MODEL), Groq ist die Rückfallebene (GROQ_API_KEY). Ohne beide Schlüssel
endet das Skript ohne Fehler und ohne Datei.

Die Datei heißt bewusst NICHT ai.json – die schreibt schon fetch_directory.py
für die DIP-Vorgänge. Hier geht es um Meldungen.

WORKFLOW-SCHRITT (fetch.yml), nach "Feeds holen":

    - name: KI-Zusammenfassungen (Meldungen)
      continue-on-error: true
      timeout-minutes: 4
      env:
        OPEN_ROUTER_API: ${{ secrets.OPEN_ROUTER_API }}
        OR_MODEL: 'meta-llama/llama-3.3-70b-instruct:free'
        GROQ_API_KEY: ${{ secrets.GROQ_API_KEY }}
        KI_MAX: '15'
      run: python ki_zusammenfassungen.py

und ki_meldungen.json in die Commit-Liste aufnehmen.

WAS ES TUT
  1. liest articles.json, eu_articles.json, bundestag_articles.json,
     laender_articles.json, us_articles.json,
  2. wählt die Meldungen aus, die eine Zusammenfassung verdienen: mehrere
     Quellen (cluster), hohe Relevanz (boost), Gerichtsentscheidungen,
     Studien – höchstens KI_MAX je Lauf,
  3. überspringt alles, was in der alten ai.json schon steht (kein zweites
     Bezahlen für dieselbe Meldung),
  4. schreibt ki_meldungen.json:
        {"updated": …, "items": {"<id>": {"ki": "…", "ts": …}},
         "lage": {"bullets": ["…", …], "stand": "18.09.2026, 12:45 Uhr", "ts": …}}
  5. wirft Einträge weg, deren Meldung älter als KI_KEEP_DAYS ist,
  6. schreibt für jede Region (Alle, DE, EU, USA, China, Welt) die zehn
     wichtigsten Meldungen der letzten 24 Stunden als Schlagzeile mit einem
     Satz – in der Sprache der Quellen, also englisch, wenn die Meldungen
     englisch sind. Das erscheint direkt in den Top-Meldungen, nicht in
     einem eigenen Kasten. Neu geschrieben wird eine Region nur, wenn ihr
     Block älter als KI_SCHLAG_MIN Minuten ist (Standard: 6 Stunden) oder
     sich die Auswahl deutlich geändert hat; je Lauf höchstens
     KI_SCHLAG_REGIONEN Regionen, damit das Kontingent reicht.

MODELLE: OpenRouter nimmt Modelle regelmäßig aus dem kostenlosen Kontingent
("This model is unavailable for free"). Das Skript wechselt dann selbst:
erst auf den Nachfolger, den die Fehlermeldung nennt, dann auf ein anderes
freies Modell aus der Modell-Liste von OpenRouter, zuletzt auf Groq.

STAPEL: Die Meldungen gehen zu KI_BATCH Stück in eine Anfrage (Antwort als
JSON mit Nummern). Bei gleichem Kontingent werden so etwa achtmal so viele
Meldungen zusammengefasst wie früher mit einer Anfrage je Meldung.

KOSTEN: rund 400 Eingabe- und 120 Ausgabe-Token je Meldung. Bei 25 Meldungen
alle 25 Minuten bleibt das im Rahmen des kostenlosen Groq-Kontingents; wer
sparen will, setzt KI_MAX kleiner oder ruft das Skript nur stündlich auf.
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError

OR_URL = "https://openrouter.ai/api/v1/chat/completions"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
OR_KEY = os.environ.get("OPEN_ROUTER_API", "").strip()
OR_MODEL = os.environ.get("OR_MODEL", "meta-llama/llama-3.3-70b-instruct:free")
GROQ_KEY = os.environ.get("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.environ.get("KI_MODELL", "llama-3.3-70b-versatile")
MAX_NEU = int(os.environ.get("KI_MAX", "48"))      # Meldungen je Lauf
BATCH = int(os.environ.get("KI_BATCH", "8"))        # Meldungen je Anfrage
SCHLAG_N = int(os.environ.get("KI_SCHLAG_N", "10"))            # Ereignisse je Region
SCHLAG_KANDIDATEN = int(os.environ.get("KI_SCHLAG_KANDIDATEN", "30"))  # Gruppen, die das Modell sieht
SCHLAG_MIN = int(os.environ.get("KI_SCHLAG_MIN", "120"))       # Minuten bis zur Neufassung
SCHLAG_REGIONEN = int(os.environ.get("KI_SCHLAG_REGIONEN", "3"))  # Regionen je Lauf
KEEP_DAYS = int(os.environ.get("KI_KEEP_DAYS", "7"))
TIMEOUT = int(os.environ.get("KI_TIMEOUT", "30"))
OUT = "ki_meldungen.json"

# Anbieter in Reihenfolge. Listen statt Tupel, weil das Modell im Lauf
# gewechselt werden kann: [Name, URL, Schlüssel, Modell]
ANBIETER = [a for a in [["openrouter", OR_URL, OR_KEY, OR_MODEL],
                        ["groq", GROQ_URL, GROQ_KEY, GROQ_MODEL]] if a[2]]
OR_MODELS_URL = "https://openrouter.ai/api/v1/models"
# Reihenfolge der Vorlieben, wenn ein freies Modell gesucht werden muss.
OR_VORLIEBE = ("llama-3.3", "llama-3.1", "qwen", "gemma", "mistral", "deepseek", "phi")
_or_gesperrt = set()          # Modelle, die in diesem Lauf nicht funktioniert haben
QUELLEN = ["articles.json", "eu_articles.json", "bundestag_articles.json",
           "laender_articles.json", "us_articles.json"]

SYSTEM = ("Du bist ein nüchterner deutscher Politikredakteur. Du fasst zusammen, was dasteht, "
          "und erfindest nichts hinzu. Kein Vorspann, keine Wertung, keine Floskeln.")
AUFGABE = ("Fasse die Meldung in höchstens drei kurzen Sätzen zusammen und nenne im letzten Satz "
           "die wichtigste Folge für Deutschland oder die EU. Wenn der Text dafür zu dünn ist, "
           "schreibe nur, was gesichert dasteht.\n\n")


def or_freie_modelle():
    """Welche Modelle sind bei OpenRouter gerade kostenlos? Die Liste ist
    öffentlich; ohne sie müsste man Slugs raten."""
    try:
        with urlopen(Request(OR_MODELS_URL, headers={"Accept": "application/json"}),
                     timeout=TIMEOUT) as r:
            daten = json.loads(r.read().decode("utf-8", "replace")).get("data") or []
    except Exception as e:
        print(f"  Modell-Liste nicht erreichbar: {type(e).__name__}")
        return []
    frei = []
    for m in daten:
        preis = m.get("pricing") or {}
        try:
            if float(preis.get("prompt", 1)) == 0 and float(preis.get("completion", 1)) == 0:
                frei.append(m.get("id", ""))
        except (TypeError, ValueError):
            continue
    def rang(mid):
        low = mid.lower()
        for i, v in enumerate(OR_VORLIEBE):
            if v in low:
                return i
        return len(OR_VORLIEBE)
    frei = [m for m in frei if m and m not in _or_gesperrt]
    frei.sort(key=lambda m: (rang(m), len(m)))
    return frei


def or_ersatzmodell(fehlertext, aktuell):
    """Erst den Nachfolger nehmen, den OpenRouter in der Fehlermeldung
    nennt ('use this slug instead: …'), sonst ein anderes freies Modell."""
    _or_gesperrt.add(aktuell)
    m = re.search(r"use this slug instead:\s*([A-Za-z0-9._\-/:]+)", fehlertext or "")
    if m and m.group(1) not in _or_gesperrt:
        return m.group(1).rstrip(".,")
    for kandidat in or_freie_modelle():
        if kandidat != aktuell:
            return kandidat
    return None


def lade(pfad):
    try:
        with open(pfad, encoding="utf-8") as fh:
            return json.load(fh).get("articles", [])
    except FileNotFoundError:
        return []
    except Exception as e:
        print(f"  {pfad}: {type(e).__name__}: {e}")
        return []


def alter_stunden(iso):
    try:
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds() / 3600
    except Exception:
        return 1e9


def wichtig(a):
    """Punktzahl: je höher, desto eher lohnt eine Zusammenfassung."""
    p = 0
    p += 3 * max(0, int(a.get("cluster") or 1) - 1)
    p += int(a.get("boost") or 0)
    if a.get("herkunft") == "gericht":
        p += 4
    if a.get("herkunft") == "think":
        p += 2
    if a.get("priority") == "eil":
        p += 4
    # Ohne Teaser kann das Modell nichts zusammenfassen, was nicht schon im Titel steht.
    if len(a.get("desc") or "") < 180:
        p -= 4
    return p


def frage(anbieter, a):
    name, url, key, modell = anbieter
    text = (f"Quelle: {a.get('source','')}\n"
            f"Titel: {a.get('title','')}\n"
            f"Text: {(a.get('desc') or '')[:1400]}")
    if a.get("ls"):
        text += f"\nLeitsatz: {a['ls']}"
    if a.get("az"):
        text += f"\nAktenzeichen: {a['az']}"
    koerper = json.dumps({
        "model": modell,
        "temperature": 0.2,
        "max_tokens": 220,
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": AUFGABE + text}],
    }).encode("utf-8")
    kopf = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
    if name == "openrouter":
        kopf["HTTP-Referer"] = "https://presseschau.example"   # OpenRouter möchte einen Absender sehen
        kopf["X-Title"] = "Presseschau"
    req = Request(url, data=koerper, headers=kopf)
    with urlopen(req, timeout=TIMEOUT) as r:
        j = json.loads(r.read().decode("utf-8", "replace"))
    return (j.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()


# ─────────────────────────────────────────────────────────────
# SCHLAGZEILEN JE REGION
#
# Die Feed-Überschriften sind, was sie sind: "Auslegungssache 168", "dazu
# Matthias Deiß, ARD Berlin", "Spritpreise: Hoppermann schlägt vor". Für die
# Startseite wird daraus eine Schlagzeile plus ein Satz – in derselben
# Sprache wie die Quellen. Die Regeln stammen aus dem Newsletter-Skript:
# nur schreiben, was dasteht; im Zweifel die Institution statt der Person;
# keine Meta-Kommentare, keine leeren Phrasen.
# ─────────────────────────────────────────────────────────────
REGION_RE = {
    "de": re.compile(r"\b(deutschland|deutsche[rsnm]?|germany|german[sy]?|bundesregierung|bundeskanzler\w*|"
                     r"kanzler(in|amt)?|bundestag|bundesrat|bundesministe\w*|bundeswehr|bundesbank|"
                     r"bundesverfassungsgericht|bundesgerichtshof|karlsruhe|merz|klingbeil|spd|cdu|csu|afd|"
                     r"grünen|fdp|linkspartei|koalition\w*|dax|berlin|münchen|hamburg|köln|frankfurt|"
                     r"nordrhein-westfalen|bayern|baden-württemberg|niedersachsen|hessen|sachsen(-anhalt)?|"
                     r"thüringen|brandenburg|rheinland-pfalz|schleswig-holstein|mecklenburg-vorpommern|"
                     r"saarland|bremen|landtag\w*|ministerpräsident\w*)\b", re.I),
    "eu": re.compile(r"\b(eu|eu-\w+|europäische[nrs]? union|european union|eu-kommission|europäische kommission|"
                     r"european commission|europaparlament|eu-parlament|european parliament|von der leyen|brüssel|"
                     r"brussels|straßburg|ezb|europäische zentralbank|european central bank|lagarde|eurozone|"
                     r"binnenmarkt|schengen|frankreich|france|french|macron|italien|italy|meloni|spanien|spain|"
                     r"polen|poland|niederlande|netherlands|belgien|belgium|österreich|austria|schweden|sweden|"
                     r"finnland|finland|dänemark|denmark|irland|ireland|portugal|griechenland|greece|tschechien|"
                     r"czech|ungarn|hungary|orban|slowakei|rumänien|romania|bulgarien|kroatien|slowenien)\b", re.I),
    "us": re.compile(r"\b(usa|u\.s\.a?\.?|us-\w+|vereinigte staaten|united states|amerikanisch\w*|american[s]?|"
                     r"amerika|washington|weißes haus|white house|trump|vance|rubio|congress|senate|"
                     r"house of representatives|supreme court|pentagon|wall street|fed|federal reserve|powell|"
                     r"new york|kalifornien|california|texas|florida|fbi|cia|nasa|republicans?|democrats?)\b", re.I),
    "china": re.compile(r"\b(china|chinas|chinesisch\w*|chinese|peking|beijing|xi jinping|volksrepublik|taiwan|"
                        r"hongkong|hong kong|huawei|tsmc|alibaba|tencent|byd|shenzhen|shanghai|yuan|renminbi)\b", re.I),
    "welt": re.compile(r"\b(ukraine|ukrainisch\w*|ukrainian|kiew|kyiv|selenskyj|zelensky[y]?|russland|russisch\w*|"
                       r"russia|russian|moskau|moscow|putin|kreml|kremlin|israel|israeli|gaza|hamas|netanjahu|"
                       r"netanyahu|iran|teheran|syrien|syria|libanon|lebanon|jemen|saudi-arabien|saudi arabia|"
                       r"katar|qatar|türkei|turkey|turkish|erdogan|indien|india|indian|modi|pakistan|japan|tokio|"
                       r"tokyo|südkorea|south korea|nordkorea|north korea|australien|australia|kanada|canada|"
                       r"canadian|carney|brasilien|brazil|mexiko|mexico|argentinien|venezuela|afrika|africa\w*|"
                       r"nigeria|südafrika|south africa|ägypten|egypt|sudan|äthiopien|großbritannien|britain|"
                       r"british|united kingdom|london|starmer|schweiz|switzerland|norwegen|norway|serbien|serbia|"
                       r"kosovo|bosnien|albanien|georgien|armenien|kasachstan|belarus|moldau|nato|"
                       r"vereinte nationen|united nations|\buno?\b|who|iwf|\bimf\b|weltbank|world bank|\bwto\b|"
                       r"opec|g7|g20|klimakonferenz|den haag|the hague|internationaler strafgerichtshof)\b", re.I),
}
REGIONEN = ["all", "de", "eu", "us", "china", "welt"]
REGION_NAME = {"all": "alle Regionen", "de": "Deutschland", "eu": "Europäische Union",
               "us": "USA", "china": "China", "welt": "international"}
DEUTSCH_RE = re.compile(r"[äöüß]|\b(der|die|das|und|nicht|für|mit|von|über|nach|beim|wird|werden|gegen|mehr|"
                        r"aber|sich|dass|auch|keine?|ist|sind|will|soll|im|bei)\b", re.I)
# Was in einer Schlagzeilenliste nichts verloren hat – dieselben Regeln wie
# im Frontend: Werbung, Produkttests, Podcastfolgen, Lokaltermine.
RAUSCH_RE = re.compile(
    r"^\s*(anzeige|werbung|sponsored|advertorial|promotion|in eigener sache)\b|\[anzeige\]|"
    r"\b(im test|praxistest|testbericht|vergleichstest|hands[- ]?on|kaufberatung|bestenliste|testsieger|"
    r"schnäppchen|gutschein|prime day|black friday|ix-workshop|heise-workshop|webinar|"
    r"erntedankfest|stadtfest|volksfest|weihnachtsmarkt|vollsperrung|straßensperrung|"
    r"das müssen \w+ wissen|so geht'?s|folge \d+|episode \d+|podcast)\b|"
    r"^[a-zäöüß][\w äöüß'’-]{4,}\s+\d{2,4}\s*:", re.I)


def region_von(a):
    """Dieselbe Einteilung wie im Frontend: verschiedene Treffer zählen,
    Schlagzeile dreifach, Eigennamen doppelt, Vorspann einfach."""
    titel = a.get("title") or ""
    vor = (a.get("desc") or "")[:300]
    ents = " ".join(a.get("ents") or [])
    punkte = {}
    for k, muster in REGION_RE.items():
        t = len({m.group(0).lower() for m in muster.finditer(titel)})
        e = len({m.group(0).lower() for m in muster.finditer(ents)})
        d = len({m.group(0).lower() for m in muster.finditer(vor)})
        punkte[k] = t * 3 + e * 2 + d
    best = max(punkte, key=punkte.get)
    if punkte[best] >= 3:
        return best
    return "de" if DEUTSCH_RE.search(titel) else "welt"


def _worte(t):
    return {w for w in re.findall(r"[a-zäöüß]{4,}", (t or "").lower())}


def _aehnlich(a, b):
    """Jaccard über die Titelwörter – gegen zweimal dieselbe Meldung."""
    A, B = _worte(a), _worte(b)
    if not A or not B:
        return 0.0
    return len(A & B) / len(A | B)


def auswahl_fuer(artikel, region, anzahl):
    """Wie in der Tageslage: erst vorsortieren und grob buendeln, dann dem
    Modell die Gruppen zeigen. Liefert eine Liste von Gruppen (je eine Liste
    von Meldungen), die wichtigste zuerst."""
    kandidaten = []
    for a in artikel:
        if alter_stunden(a.get("date", "")) > 24:
            continue
        titel = a.get("title") or ""
        if not titel or RAUSCH_RE.search(titel):
            continue
        if region != "all" and a.get("_region") != region:
            continue
        kandidaten.append(a)
    kandidaten.sort(key=lambda a: (wichtig(a), a.get("date", "")), reverse=True)
    gruppen = []
    for a in kandidaten:
        # Schwelle wie im Newsletter fuer themengleiche Meldungen (0.38)
        ziel = next((g for g in gruppen if _aehnlich(a.get("title"), g[0].get("title")) >= 0.38), None)
        if ziel is not None:
            ziel.append(a)
        else:
            gruppen.append([a])
        if len(gruppen) >= anzahl:
            break
    return gruppen


SCHLAG_REGELN_DE = """REGELN (in dieser Reihenfolge, sie ueberschreiben alles andere):
- Verwende ausschliesslich Namen, Zahlen, Daten und Orte, die woertlich in den Meldungen unten stehen.
- Steht ein Amt ohne Namen da, schreibe die Institution: "Wirtschaftsminister kuendigt an" wird
  "Das Wirtschaftsministerium kuendigt an". Erfinde nie eine Behoerde, die nicht dasteht.
- Schlagzeile: ein Hauptsatz im Praesens, hoechstens 75 Zeichen, nennt WER und WAS konkret.
  Ohne Quellenname, ohne Anfuehrungszeichen, ohne Punkt am Ende.
  VERBOTEN sind Schlagzeilen ohne Gegenstand oder ohne Akteur:
    schlecht: "Buerger muessen mit Konsequenzen leben"   (wer? welche Konsequenzen?)
    schlecht: "Daenemark und USA einigen sich"            (worauf?)
    schlecht: "Brandenburg tankt teuer"                   (Stimmung statt Nachricht)
    gut:      "Daenemark und USA einigen sich im Groenland-Streit"
    gut:      "Mineraloelbranche warnt vor Spritpreisdeckel"
- Satz: eine ganze Aussage, hoechstens 180 Zeichen, nennt das Wesentliche und, wenn es dasteht,
  die Folge. Keine Wiederholung der Schlagzeile mit anderen Worten.
- Keine Meta-Kommentare ("laut Quelltext", "unklar bleibt"), keine leeren Phrasen
  ("Experten warnen", "weitreichende Folgen"), keine Wertung.
- Kurze Laenderbezeichnungen: USA, EU, UK, China, Russland. Englische Begriffe uebersetzen
  ("lawmakers" = Abgeordnete, "bill" = Gesetzentwurf, "billion" = Milliarde)."""

SCHLAG_REGELN_EN = """RULES (in this order, they override everything else):
- Use only names, numbers, dates and places that appear verbatim in the items below.
- If an office is mentioned without a name, write the institution instead of a person.
  Never invent an agency that is not in the text.
- Headline: one main clause in present tense, at most 75 characters, naming WHO does WHAT.
  No source name, no quotation marks, no trailing period. Vague headlines without an
  actor or subject ("Citizens must live with consequences") are forbidden.
- Sentence: one complete statement, at most 180 characters, giving the substance and,
  where stated, the consequence. Do not restate the headline.
- No meta comments, no empty phrases ("experts warn"), no opinion."""

AUSWAHL_DE = """AUSWAHL (wie in der Tageslage):
1. Mehrere Eintraege koennen DASSELBE Ereignis beschreiben – auch in verschiedenen Sprachen
   ("Daenemark und USA einigen sich" = "Denmark hails Greenland deal"). Fasse sie zu EINER
   Meldung zusammen und nenne alle ihre Nummern.
2. Waehle danach die {n} wichtigsten Ereignisse nach Nachrichtenwert: Tragweite, Zahl der
   Betroffenen, politische und wirtschaftliche Bedeutung, Zahl der Quellen.
   Sport, Vermischtes, Service und Lokales nur, wenn sie ausnahmsweise herausragen.
3. Zwei Aspekte desselben Themas (etwa zwei Meldungen zu den Spritpreisen) sind EIN Eintrag,
   ausser sie berichten wirklich verschiedene Ereignisse."""

AUSWAHL_EN = """SELECTION (like a daily briefing):
1. Several items may describe the SAME event, also across languages. Merge them into ONE entry
   and list all their numbers.
2. Then choose the {n} most newsworthy events: reach, people affected, political and economic
   weight, number of sources. Sports, lifestyle, service and local news only if exceptional.
3. Two angles on the same topic count as ONE entry unless they report genuinely different events."""


def schlag_frage(anbieter, gruppen, sprache, region):
    zeilen = []
    for i, g in enumerate(gruppen):
        a = g[0]
        quellen = sorted({x.get("source", "") for x in g if x.get("source")})
        zusatz = "; ".join(x.get("title", "") for x in g[1:3])
        zeilen.append(f"[{i+1}] ({', '.join(quellen[:4])}{' +' + str(len(quellen)-4) if len(quellen) > 4 else ''}) "
                      f"{a.get('title','')}"
                      + (f" – {(a.get('desc') or '')[:240]}" if a.get("desc") else "")
                      + (f" | auch: {zusatz[:200]}" if zusatz else ""))
    liste = "\n".join(zeilen)
    heute = datetime.now(timezone.utc).strftime("%d.%m.%Y")
    if sprache == "en":
        system = ("You are a sober news editor who compiles the daily top stories. You group "
                  "duplicate reports, pick the most important events and write clean headlines. "
                  "You never add facts.")
        auftrag = (f"Today is {heute}; your training data is outdated.\n\n{AUSWAHL_EN.format(n=SCHLAG_N)}\n\n"
                   f"{SCHLAG_REGELN_EN}\n\nItems ({REGION_NAME.get(region, region)}):\n{liste}\n\n"
                   'Answer with JSON only, no prose, no code fence, most important first: '
                   '[{"nrs":[1,4],"schlagzeile":"…","text":"…"}] – English.')
    else:
        system = ("Du bist ein nuechterner Nachrichtenredakteur und stellst die wichtigsten Meldungen "
                  "des Tages zusammen. Du legst doppelte Berichte zusammen, waehlst die wichtigsten "
                  "Ereignisse und schreibst saubere Schlagzeilen. Du erfindest nichts hinzu.")
        auftrag = (f"Heute ist der {heute}, deine Trainingsdaten sind veraltet.\n\n{AUSWAHL_DE.format(n=SCHLAG_N)}\n\n"
                   f"{SCHLAG_REGELN_DE}\n\nMeldungen ({REGION_NAME.get(region, region)}):\n{liste}\n\n"
                   'Antworte nur mit JSON, ohne Vorspann, ohne Code-Zaun, das Wichtigste zuerst: '
                   '[{"nrs":[1,4],"schlagzeile":"…","text":"…"}] – auf Deutsch.')
    name, url, key, modell = anbieter
    koerper = json.dumps({
        "model": modell, "temperature": 0.2, "max_tokens": 1600,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": auftrag}],
    }).encode("utf-8")
    kopf = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
    if name == "openrouter":
        kopf["HTTP-Referer"] = "https://presseschau.example"
        kopf["X-Title"] = "Presseschau"
    with urlopen(Request(url, data=koerper, headers=kopf), timeout=max(TIMEOUT, 60)) as r:
        j = json.loads(r.read().decode("utf-8", "replace"))
    text = (j.get("choices") or [{}])[0].get("message", {}).get("content", "") or ""
    return _schlag_lesen(text, gruppen)


# Allgemeinplaetze, die trotz Anweisung durchrutschen: lieber die
# Feed-Ueberschrift behalten als eine leere Schlagzeile zeigen.
LEERE_SCHLAGZEILE = re.compile(
    r"^(buerger|bürger|menschen|verbraucher|citizens|people)\s+(muessen|müssen|must)\b|"
    r"\b(mit (den )?konsequenzen leben|live with (the )?consequences)\b|"
    r"^\S+\s+(und|and)\s+\S+\s+(einigen sich|agree)$", re.I)


def _schlag_lesen(text, gruppen):
    """Antwort auswerten: JSON mit nrs → Gruppen → Meldungs-IDs."""
    roh = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    daten = None
    m = re.search(r"\[.*\]", roh, re.S)
    if m:
        try:
            daten = json.loads(m.group(0))
        except Exception:
            daten = None
    out, vergeben = [], set()
    if not isinstance(daten, list):
        return out
    for eintrag in daten:
        if not isinstance(eintrag, dict):
            continue
        nrs = eintrag.get("nrs") or eintrag.get("nr") or []
        if isinstance(nrs, (int, str)):
            nrs = [nrs]
        nummern = []
        for n in nrs:
            try:
                n = int(n)
            except (TypeError, ValueError):
                continue
            if 1 <= n <= len(gruppen) and n not in vergeben:
                nummern.append(n)
        if not nummern:
            continue
        kopf = str(eintrag.get("schlagzeile") or eintrag.get("headline") or "").strip(" \"'.")
        satz = str(eintrag.get("text") or eintrag.get("sentence") or "").strip()
        if len(kopf) < 12 or LEERE_SCHLAGZEILE.search(kopf):
            kopf = ""                     # Frontend zeigt dann die Feed-Ueberschrift
        vergeben.update(nummern)
        meldungen = [x for n in nummern for x in gruppen[n - 1]]
        lead = gruppen[nummern[0] - 1][0]
        eintrag_out = {"id": lead.get("id"), "ids": [x.get("id") for x in meldungen if x.get("id")],
                       "quellen": sorted({x.get("source", "") for x in meldungen if x.get("source")})}
        if kopf:
            eintrag_out["schlagzeile"] = kopf[:120]
            eintrag_out["text"] = satz[:240]
        else:
            eintrag_out["schlagzeile"] = lead.get("title", "")[:120]
            eintrag_out["text"] = ""
        out.append(eintrag_out)
        if len(out) >= SCHLAG_N:
            break
    return out


def sprache_fuer(gruppen):
    """Englisch schreiben, wenn die Meldungen ueberwiegend englisch sind."""
    meldungen = [g[0] for g in gruppen] if gruppen and isinstance(gruppen[0], list) else gruppen
    if not meldungen:
        return "de"
    deutsch = sum(1 for a in meldungen if DEUTSCH_RE.search(a.get("title") or ""))
    return "de" if deutsch >= len(meldungen) * 0.4 else "en"


def schlagzeilen_bauen(artikel, anbieter, alt_block):
    """Je Region ein Block. Neu geschrieben wird nur, was alt ist oder sich
    geaendert hat – hoechstens SCHLAG_REGIONEN Regionen je Lauf."""
    for a in artikel:
        a["_region"] = region_von(a)
    alt = dict((alt_block or {}).get("regionen") or {})
    faellig = []
    for region in REGIONEN:
        auswahl = auswahl_fuer(artikel, region, SCHLAG_KANDIDATEN)
        if len(auswahl) < 3:
            continue
        ids = [g[0].get("id") for g in auswahl[:SCHLAG_N]]
        vorher = alt.get(region) or {}
        alt_ids = [x for i in (vorher.get("items") or []) for x in (i.get("ids") or [i.get("id")])]
        gleich = len(set(ids) & set(alt_ids)) >= max(1, len(ids) * 0.6)
        frisch = (vorher.get("ts") or 0) > time.time() - SCHLAG_MIN * 60
        if vorher and frisch and gleich:
            continue
        faellig.append((vorher.get("ts") or 0, region, auswahl))
    faellig.sort(key=lambda x: x[0])           # das Aelteste zuerst
    if not faellig:
        print("  Schlagzeilen: alle Regionen aktuell.")
        return alt_block
    for _, region, auswahl in faellig[:SCHLAG_REGIONEN]:
        if not anbieter:
            break
        sprache = sprache_fuer(auswahl)
        for _versuch in range(2):
            try:
                items = schlag_frage(anbieter[0], auswahl, sprache, region)
                if items:
                    alt[region] = {"sprache": sprache, "ts": int(time.time()),
                                   "stand": _stand_jetzt(), "via": anbieter[0][0],
                                   "modell": anbieter[0][3], "items": items}
                    gebuendelt = sum(1 for i in items if len(i.get("ids") or []) > 1)
                    print(f"  Schlagzeilen {region}: {len(items)} Ereignisse aus {len(auswahl)} Gruppen, "
                          f"{gebuendelt} gebuendelt ({sprache}, {anbieter[0][0]})")
                else:
                    print(f"  Schlagzeilen {region}: keine verwertbare Antwort")
                break
            except HTTPError as e:
                leib = ""
                try:
                    leib = json.loads(e.read().decode())["error"]["message"]
                except Exception:
                    pass
                print(f"  ---  Schlagzeilen {region}: {anbieter[0][0]} HTTP {e.code}: {leib[:140]}")
                if anbieter[0][0] == "openrouter" and (e.code in (400, 404) or "unavailable for free" in leib):
                    ersatz = or_ersatzmodell(leib, anbieter[0][3])
                    if ersatz:
                        print(f"     Modellwechsel: {anbieter[0][3]} → {ersatz}")
                        anbieter[0][3] = ersatz
                        continue
                anbieter.pop(0)
                break
            except (URLError, ValueError, KeyError) as e:
                print(f"  ---  Schlagzeilen {region}: {type(e).__name__}: {e}")
                break
        time.sleep(0.5)
    return {"stand": _stand_jetzt(), "ts": int(time.time()), "regionen": alt}


def _stand_jetzt():
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Europe/Berlin")).strftime("%d.%m.%Y, %H:%M Uhr")
    except Exception:
        return datetime.now(timezone.utc).strftime("%d.%m.%Y, %H:%M Uhr")


def frage_stapel(anbieter, meldungen):
    """Mehrere Meldungen in einer Anfrage zusammenfassen. Antwort als JSON
    mit Nummern – die Zuordnung zur Meldung macht das Skript, nicht das
    Modell (derselbe Kniff wie im Newsletter)."""
    heute = datetime.now(timezone.utc).strftime("%d.%m.%Y")
    zeilen = []
    for i, a in enumerate(meldungen):
        z = (f"[{i+1}] Quelle: {a.get('source','')}\nTitel: {a.get('title','')}\n"
             f"Text: {(a.get('desc') or '')[:900]}")
        if a.get("ls"):
            z += f"\nLeitsatz: {a['ls']}"
        zeilen.append(z)
    auftrag = (f"Heute ist der {heute}, deine Trainingsdaten sind veraltet.\n\n"
               + AUFGABE
               + "Regeln: nur Namen, Zahlen und Orte verwenden, die woertlich im Text stehen; "
                 "steht ein Amt ohne Namen da, die Institution nennen; keine Meta-Kommentare, "
                 "keine leeren Phrasen. Englische Meldungen auf Deutsch zusammenfassen.\n\n"
               + "\n\n".join(zeilen)
               + '\n\nAntworte nur mit JSON, ohne Vorspann und ohne Code-Zaun: '
                 '[{"nr":1,"ki":"…"}] – ein Objekt je Meldung.')
    name, url, key, modell = anbieter
    koerper = json.dumps({
        "model": modell, "temperature": 0.2, "max_tokens": 180 * len(meldungen) + 200,
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": auftrag}],
    }).encode("utf-8")
    kopf = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
    if name == "openrouter":
        kopf["HTTP-Referer"] = "https://presseschau.example"
        kopf["X-Title"] = "Presseschau"
    with urlopen(Request(url, data=koerper, headers=kopf), timeout=max(TIMEOUT, 60)) as r:
        j = json.loads(r.read().decode("utf-8", "replace"))
    text = (j.get("choices") or [{}])[0].get("message", {}).get("content", "") or ""
    roh = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    m = re.search(r"\[.*\]", roh, re.S)
    out = {}
    if not m:
        return out
    try:
        daten = json.loads(m.group(0))
    except Exception:
        return out
    for e in daten if isinstance(daten, list) else []:
        if not isinstance(e, dict):
            continue
        try:
            nr = int(e.get("nr"))
        except (TypeError, ValueError):
            continue
        t = str(e.get("ki") or e.get("text") or "").strip()
        if 1 <= nr <= len(meldungen) and len(t) >= 30 and meldungen[nr - 1].get("id"):
            out[meldungen[nr - 1]["id"]] = t[:600]
    return out


def main():
    if not ANBIETER:
        print("Weder OPEN_ROUTER_API noch GROQ_API_KEY gesetzt – ki_meldungen.json bleibt unverändert.")
        return 0

    artikel = []
    for p in QUELLEN:
        artikel += lade(p)
    if not artikel:
        print("Keine Artikel gefunden – nichts zu tun.")
        return 0

    try:
        with open(OUT, encoding="utf-8") as fh:
            alt = json.load(fh)
    except Exception:
        alt = {}
    items = dict(alt.get("items") or {})

    # Alte Einträge wegräumen
    bekannt = {a["id"]: a for a in artikel if a.get("id")}
    grenze = time.time() - KEEP_DAYS * 86400
    items = {k: v for k, v in items.items()
             if k in bekannt and (v.get("ts") or 0) > grenze}

    # Mehr Meldungen als frueher: Statt einer Anfrage je Meldung gehen
    # KI_BATCH Meldungen in eine Anfrage. Bei gleichem Kontingent werden so
    # rund achtmal so viele Meldungen zusammengefasst. Ausgelassen wird nur,
    # was keinen Teaser hat (dann steht nichts drin, was nicht schon der
    # Titel sagt) und was Werbung, Test oder Podcastfolge ist.
    offen = [a for a in artikel
             if a.get("id") and a["id"] not in items and alter_stunden(a.get("date", "")) <= 36
             and not RAUSCH_RE.search(a.get("title") or "")
             and len(a.get("desc") or "") >= 140]
    offen.sort(key=wichtig, reverse=True)
    offen = offen[:MAX_NEU]

    if not offen:
        print("Nichts Neues, das eine Zusammenfassung braucht.")
    neu = fehler = 0
    anbieter = list(ANBIETER)          # bei 401/403/429 fällt der erste weg
    stapel = [offen[k:k + BATCH] for k in range(0, len(offen), BATCH)]
    for teil in stapel:
        for _versuch in range(2):
            if not anbieter:
                break
            try:
                ergebnis = frage_stapel(anbieter[0], teil)
                for aid, text in ergebnis.items():
                    items[aid] = {"ki": text, "ts": int(time.time()), "via": anbieter[0][0]}
                    neu += 1
                print(f"  ok   {anbieter[0][0]:<10} {len(ergebnis)}/{len(teil)} Meldungen zusammengefasst")
                break
            except HTTPError as e:
                leib = ""
                try:
                    leib = json.loads(e.read().decode())["error"]["message"]
                except Exception:
                    pass
                print(f"  ---  {anbieter[0][0]} HTTP {e.code}: {leib[:140]}")
                fehler += 1
                # Modell weg (404) oder nicht mehr kostenlos: anderes Modell,
                # derselbe Stapel wird noch einmal versucht.
                if anbieter[0][0] == "openrouter" and (e.code in (400, 404) or "unavailable for free" in leib):
                    ersatz = or_ersatzmodell(leib, anbieter[0][3])
                    if ersatz:
                        print(f"     Modellwechsel: {anbieter[0][3]} → {ersatz}")
                        anbieter[0][3] = ersatz
                        continue
                    print("     Kein freies Modell gefunden – OpenRouter fällt weg.")
                    anbieter.pop(0)
                    continue
                if e.code in (401, 403, 429, 402):
                    print(f"     {anbieter[0][0]} fällt für diesen Lauf weg.")
                    anbieter.pop(0)
                    continue
                break
            except (URLError, ValueError, KeyError) as e:
                print(f"  ---  {type(e).__name__}: {e}")
                fehler += 1
                break
        if not anbieter:
            print("Kein Anbieter mehr verfügbar – Rest im nächsten Lauf.")
            break
        time.sleep(0.5)

    schlag = schlagzeilen_bauen(artikel, anbieter, (alt.get("schlagzeilen") or None))

    out = {"updated": datetime.now(timezone.utc).isoformat(),
           "anbieter": [x[0] + ":" + x[3] for x in ANBIETER],
           "hinweis": "Automatisch erzeugte Zusammenfassungen. Im Zweifel gilt die Originalmeldung.",
           "items": items,
           "schlagzeilen": schlag or {}}
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, separators=(",", ":"))
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, OUT)
    regionen = (schlag or {}).get("regionen") or {}
    print(f"\n→ {OUT}: {neu} neu, {len(items)} insgesamt, {fehler} Fehler, "
          f"Schlagzeilen: " + (", ".join(f"{k} {len(v.get('items') or [])}"
                                         for k, v in regionen.items()) or "keine"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
