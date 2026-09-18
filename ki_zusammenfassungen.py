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
MAX_NEU = int(os.environ.get("KI_MAX", "15"))
SCHLAG_N = int(os.environ.get("KI_SCHLAG_N", "10"))            # Meldungen je Region
SCHLAG_MIN = int(os.environ.get("KI_SCHLAG_MIN", "360"))       # Minuten: alle sechs Stunden
SCHLAG_REGIONEN = int(os.environ.get("KI_SCHLAG_REGIONEN", "2"))  # Regionen je Lauf
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
    raus = []
    for a in kandidaten:
        if any(_aehnlich(a.get("title"), b.get("title")) >= 0.45 for b in raus):
            continue
        raus.append(a)
        if len(raus) >= anzahl:
            break
    return raus


SCHLAG_REGELN_DE = """REGELN (in dieser Reihenfolge, sie überschreiben alles andere):
- Verwende ausschliesslich Namen, Zahlen, Daten und Orte, die woertlich in den Meldungen unten stehen.
- Steht ein Amt ohne Namen da, schreibe die Institution: "Wirtschaftsminister kuendigt an" wird
  "Das Wirtschaftsministerium kuendigt an". Erfinde nie eine Behoerde, die nicht dasteht.
- Schlagzeile: ein Hauptsatz im Praesens, hoechstens 75 Zeichen, ohne Doppelpunkt-Konstruktion
  am Anfang, ohne Quellenname, ohne Anfuehrungszeichen, ohne Punkt am Ende.
  Schlecht: "Spritpreise: Hoppermann schlaegt bis zu 25 Cent Entlastung pro Liter vor"
  Gut:      "Koalition erwaegt 25 Cent Entlastung beim Sprit"
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
- Headline: one main clause in present tense, at most 75 characters, no source name,
  no quotation marks, no trailing period.
- Sentence: one complete statement, at most 180 characters, giving the substance and,
  where stated, the consequence. Do not restate the headline.
- No meta comments ("according to the source"), no empty phrases ("experts warn",
  "far-reaching consequences"), no opinion."""


def schlag_frage(anbieter, meldungen, sprache, region):
    liste = "\n".join(
        f"[{i+1}] ({a.get('source','')}) {a.get('title','')}"
        + (f" – {(a.get('desc') or '')[:260]}" if a.get("desc") else "")
        for i, a in enumerate(meldungen))
    heute = datetime.now(timezone.utc).strftime("%d.%m.%Y")
    if sprache == "en":
        system = ("You are a sober news editor. You rewrite wire headlines into clean news "
                  "headlines and one factual sentence. You never add facts.")
        auftrag = (f"Today is {heute}; your training data is outdated.\n\n{SCHLAG_REGELN_EN}\n\n"
                   f"Items ({REGION_NAME.get(region, region)}):\n{liste}\n\n"
                   'Answer with JSON only, no prose, no code fence: '
                   '[{"nr":1,"schlagzeile":"…","text":"…"}] – one object per item, same order, English.')
    else:
        system = ("Du bist ein nuechterner Nachrichtenredakteur. Du machst aus Feed-Ueberschriften "
                  "saubere Schlagzeilen und einen sachlichen Satz. Du erfindest nichts hinzu.")
        auftrag = (f"Heute ist der {heute}, deine Trainingsdaten sind veraltet.\n\n{SCHLAG_REGELN_DE}\n\n"
                   f"Meldungen ({REGION_NAME.get(region, region)}):\n{liste}\n\n"
                   'Antworte nur mit JSON, ohne Vorspann, ohne Code-Zaun: '
                   '[{"nr":1,"schlagzeile":"…","text":"…"}] – ein Objekt je Meldung, gleiche '
                   'Reihenfolge, auf Deutsch.')
    name, url, key, modell = anbieter
    koerper = json.dumps({
        "model": modell, "temperature": 0.2, "max_tokens": 1200,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": auftrag}],
    }).encode("utf-8")
    kopf = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
    if name == "openrouter":
        kopf["HTTP-Referer"] = "https://presseschau.example"
        kopf["X-Title"] = "Presseschau"
    with urlopen(Request(url, data=koerper, headers=kopf), timeout=TIMEOUT) as r:
        j = json.loads(r.read().decode("utf-8", "replace"))
    text = (j.get("choices") or [{}])[0].get("message", {}).get("content", "") or ""
    return _schlag_lesen(text, meldungen)


def _schlag_lesen(text, meldungen):
    """Antwort auswerten: bevorzugt JSON, sonst zeilenweise."""
    roh = text.strip()
    roh = re.sub(r"^```(?:json)?|```$", "", roh, flags=re.M).strip()
    daten = None
    m = re.search(r"\[.*\]", roh, re.S)
    if m:
        try:
            daten = json.loads(m.group(0))
        except Exception:
            daten = None
    out = []
    if isinstance(daten, list):
        for eintrag in daten:
            if not isinstance(eintrag, dict):
                continue
            try:
                nr = int(eintrag.get("nr") or eintrag.get("id") or 0)
            except (TypeError, ValueError):
                nr = 0
            if not 1 <= nr <= len(meldungen):
                continue
            kopf = str(eintrag.get("schlagzeile") or eintrag.get("headline") or "").strip(" \"'.")
            satz = str(eintrag.get("text") or eintrag.get("sentence") or "").strip()
            if len(kopf) < 12:
                continue
            out.append({"id": meldungen[nr - 1].get("id"), "schlagzeile": kopf[:120],
                        "text": satz[:240], "quelle": meldungen[nr - 1].get("source", "")})
    return out


def sprache_fuer(meldungen):
    """Englisch schreiben, wenn die Meldungen ueberwiegend englisch sind."""
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
        auswahl = auswahl_fuer(artikel, region, SCHLAG_N)
        if len(auswahl) < 3:
            continue
        ids = [a.get("id") for a in auswahl]
        vorher = alt.get(region) or {}
        alt_ids = [i.get("id") for i in (vorher.get("items") or [])]
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
                    print(f"  Schlagzeilen {region}: {len(items)} Stueck ({sprache}, {anbieter[0][0]})")
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

    offen = [a for a in artikel
             if a.get("id") and a["id"] not in items and alter_stunden(a.get("date", "")) <= 36]
    offen.sort(key=wichtig, reverse=True)
    offen = [a for a in offen if wichtig(a) >= 2][:MAX_NEU]

    if not offen:
        print("Nichts Neues, das eine Zusammenfassung braucht.")
    neu = fehler = 0
    anbieter = list(ANBIETER)          # bei 401/403/429 fällt der erste weg
    for a in offen:
        if not anbieter:
            print("Kein Anbieter mehr verfügbar – Rest im nächsten Lauf.")
            break
        try:
            t = frage(anbieter[0], a)
            if t:
                items[a["id"]] = {"ki": t, "ts": int(time.time()), "via": anbieter[0][0]}
                neu += 1
                print(f"  ok   {anbieter[0][0]:<10} {a.get('source','')[:16]:<16} {a.get('title','')[:56]}")
        except HTTPError as e:
            leib = ""
            try:
                leib = json.loads(e.read().decode())["error"]["message"]
            except Exception:
                pass
            print(f"  ---  {anbieter[0][0]} HTTP {e.code}: {leib[:140]}")
            fehler += 1
            # Modell weg (404) oder nicht mehr kostenlos: anderes Modell nehmen,
            # statt den ganzen Anbieter fallen zu lassen.
            if anbieter[0][0] == "openrouter" and e.code in (400, 404) or "unavailable for free" in leib:
                ersatz = or_ersatzmodell(leib, anbieter[0][3])
                if ersatz:
                    print(f"     Modellwechsel: {anbieter[0][3]} → {ersatz}")
                    anbieter[0][3] = ersatz
                    continue
                print("     Kein freies Modell gefunden – OpenRouter fällt weg.")
                anbieter.pop(0)
            elif e.code in (401, 403, 429, 402):
                print(f"     {anbieter[0][0]} fällt für diesen Lauf weg.")
                anbieter.pop(0)
        except (URLError, ValueError, KeyError) as e:
            print(f"  ---  {type(e).__name__}: {e}")
            fehler += 1
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
