#!/usr/bin/env python3
"""
fetch_plenum.py  –  schreibt plenum_votes.json

Nicht-namentliche Abstimmungen des Bundestags. Die meisten Beschlüsse
fallen per Handzeichen oder Aufstehen; das Ergebnis steht dann nur als
Text im Plenarprotokoll:

    "Ich bitte diejenigen, die dem Gesetzentwurf zustimmen wollen, sich zu
     erheben. – Das sind die Fraktionen A und B. Wer stimmt dagegen? –
     Das ist die Fraktion C. Wer enthält sich? – Das ist die Fraktion D.
     Damit ist der Gesetzentwurf angenommen."

oder in einem Satz:

    "Die Beschlussempfehlung ist mit den Stimmen der Koalitionsfraktionen
     gegen die Stimmen der Fraktion C bei Enthaltung der Fraktion D
     angenommen."

Dieses Skript holt die Protokolltexte über die DIP-Schnittstelle
(plenarprotokoll-text, derselbe DIP_KEY wie im Verzeichnis), liest daraus
jede Abstimmung mit Ergebnis und Fraktionsverhalten und schreibt:

    {"updated": …, "wahlperioden": [21, 20],
     "abstimmungen": [{"id": "pl-21-45-3", "datum": "2026-07-10",
        "sitzung": "21/45", "wahlperiode": 21, "stufe": "Schlussabstimmung",
        "gegenstand": "Gesetzentwurf … zur Modernisierung des Bundespolizeigesetzes",
        "drucksachen": ["21/3051", "21/6990"], "ergebnis": "angenommen",
        "dafuer": ["…", "…"], "dagegen": ["…"], "enthalten": [],
        "namentlich": false, "text": "…"}]}

Dazu kommen die Reden: wer zu welchem Tagesordnungspunkt gesprochen hat,
mit Fraktion oder Amt und einem Auszug. Jede Abstimmung kennt ihre Debatte
("redner"), jede Rede die Abstimmungen, die darauf folgten.

    "reden": [{"id": "rd-21-45-7", "datum": "2026-07-10", "sitzung": "21/45",
               "name": "Erika Mustermann", "fraktion": "…", "rolle": "",
               "top": "TOP 5", "betreff": "Modernisierung des Bundespolizeigesetzes",
               "auszug": "…", "zeichen": 5230, "abstimmungen": ["pl-21-45-3"]}]

Reden werden REDEN_TAGE Tage aufbewahrt (Standard 120), damit die Datei
nicht unbegrenzt wächst.

Stimmenzahlen gibt es bei diesen Abstimmungen nicht. Das Frontend rechnet
sie – wie die Democracy-App – aus den Fraktionsstärken hoch und schreibt
das dazu.

Je Lauf werden PLENUM_PER_RUN neue Protokolle gelesen; welche schon
ausgewertet sind, steht in plenum_cache.json.

ÄLTERE WAHLPERIODEN: WAHLPERIODEN nennt, welche gelesen werden, die
aktuelle zuerst (Standard "21,20,19"). Eine ältere kommt erst an die Reihe,
wenn die neuere vollständig ist – so verdrängt das Nachholen nie das
Aktuelle. Wer "die Koalition" war und welche Fraktionen es gab, steht je
Wahlperiode in WP_INFO; "die Koalitionsfraktionen" in einem Protokoll von
2023 sind andere als 2026. Reden älterer Wahlperioden werden nicht
aufbewahrt (REDEN_TAGE), ihre Abstimmungen schon. Namentliche Abstimmungen
überspringt das Skript – die kommen vollständig aus votes.json.

WORKFLOW-SCHRITT:

    - name: Nicht-namentliche Abstimmungen (Plenarprotokolle)
      continue-on-error: true
      timeout-minutes: 4
      env:
        DIP_KEY: ${{ secrets.DIP_KEY }}
        WAHLPERIODE: '21'
        PLENUM_PER_RUN: '6'
      run: python fetch_plenum.py

und plenum_votes.json sowie plenum_cache.json in die Commit-Liste.
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from urllib.parse import urlencode
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError

DIP = "https://search.dip.bundestag.de/api/v1/plenarprotokoll-text"
KEY = os.environ.get("DIP_KEY", "").strip()
WP = int(os.environ.get("WAHLPERIODE", "21"))
PRO_LAUF = int(os.environ.get("PLENUM_PER_RUN", "6"))
BUDGET = float(os.environ.get("PLENUM_BUDGET_MIN", "3")) * 60
TIMEOUT = int(os.environ.get("PLENUM_TIMEOUT", "40"))
OUT = "plenum_votes.json"
REDEN_TAGE = int(os.environ.get("REDEN_TAGE", "120"))
CACHE = "plenum_cache.json"
WAHLPERIODEN = [int(x) for x in os.environ.get("WAHLPERIODEN", str(WP) + ",20,19").split(",") if x.strip().isdigit()]
if WP not in WAHLPERIODEN:
    WAHLPERIODEN.insert(0, WP)

# Wer war "die Koalition", welche Fraktionen gab es? Je Wahlperiode, mit
# Stichtag – die Liste gilt ab dem genannten Datum. Alphabetisch, damit
# die Reihenfolge nichts gewichtet.
WP_INFO = {
    21: {"koalition": [("2025-05-06", ["CDU/CSU", "SPD"])],
         "fraktionen": [("2025-03-25", ["AfD", "CDU/CSU", "Grüne", "Linke", "SPD"])]},
    20: {"koalition": [("2021-12-08", ["FDP", "Grüne", "SPD"]),
                       ("2024-11-07", ["Grüne", "SPD"])],
         "fraktionen": [("2021-10-26", ["AfD", "CDU/CSU", "FDP", "Grüne", "Linke", "SPD"]),
                        # Ab Dezember 2023 zwei Gruppen statt der Linksfraktion
                        ("2023-12-06", ["AfD", "BSW", "CDU/CSU", "FDP", "Grüne", "Linke", "SPD"])]},
    19: {"koalition": [("2018-03-14", ["CDU/CSU", "SPD"])],
         "fraktionen": [("2017-10-24", ["AfD", "CDU/CSU", "FDP", "Grüne", "Linke", "SPD"])]},
}
# Für die laufende Wahlperiode lässt sich beides übersteuern, etwa nach
# einem Regierungswechsel – ohne Codeänderung.
if os.environ.get("KOALITION"):
    WP_INFO.setdefault(WP, {}).setdefault("koalition", []).append(
        ("0000-00-00", [x.strip() for x in os.environ["KOALITION"].split(",") if x.strip()]))
if os.environ.get("FRAKTIONEN"):
    WP_INFO.setdefault(WP, {}).setdefault("fraktionen", []).append(
        ("0000-00-00", [x.strip() for x in os.environ["FRAKTIONEN"].split(",") if x.strip()]))


def _gueltig(liste, datum):
    """Aus [(ab_datum, wert), …] den Wert, der am Datum galt. Übersteuerungen
    aus der Umgebung ("0000-00-00") gewinnen immer."""
    if not liste:
        return []
    uebersteuert = [w for d, w in liste if d == "0000-00-00"]
    if uebersteuert:
        return uebersteuert[-1]
    passend = [w for d, w in sorted(liste) if not datum or d <= datum]
    return passend[-1] if passend else sorted(liste)[0][1]


def koalition_fuer(wp, datum):
    return _gueltig((WP_INFO.get(wp) or {}).get("koalition"), datum)


def fraktionen_fuer(wp, datum):
    return _gueltig((WP_INFO.get(wp) or {}).get("fraktionen"), datum)

T0 = time.monotonic()


# ─────────────────────────────────────────────────────────────
# LESEN: Fraktionen in einem Satzstück erkennen
# ─────────────────────────────────────────────────────────────
PARTEI_MUSTER = [
    (re.compile(r"koalitionsfraktionen|fraktionen der koalition|der koalition\b", re.I), "KOALITION"),
    (re.compile(r"unionsfraktion|cdu\s*/\s*csu|\bcdu\b|\bcsu\b|\bunion\b", re.I), "CDU/CSU"),
    (re.compile(r"\bspd\b", re.I), "SPD"),
    (re.compile(r"\bafd\b", re.I), "AfD"),
    (re.compile(r"bündnis\s*90\s*/\s*die\s*grünen|\bgrünen\b|\bgrüne\b", re.I), "Grüne"),
    (re.compile(r"die\s+linke|\blinken\b|\blinke\b", re.I), "Linke"),
    (re.compile(r"\bbsw\b", re.I), "BSW"),
    (re.compile(r"\bfdp\b", re.I), "FDP"),
    (re.compile(r"\bssw\b", re.I), "SSW"),
    (re.compile(r"fraktionslose\w*|fraktionslos\w*", re.I), "Fraktionslos"),
]
REST_RE = re.compile(r"übrigen\s+fraktionen|aller\s+übrigen|des\s+(ganzen|gesamten|übrigen)\s+hauses|"
                     r"aller\s+fraktionen|restlichen\s+fraktionen|übrigen\s+hauses", re.I)
NIEMAND_RE = re.compile(r"^\W*(niemand|keine[rn]?|keine gegenstimmen|keine enthaltungen)\b", re.I)


def parteien(stueck, koalition=None):
    """Fraktionen in einem Satzstück, in Reihenfolge des Auftretens."""
    koalition = koalition if koalition is not None else koalition_fuer(WP, "")
    if not stueck or NIEMAND_RE.search(stueck):
        return []
    treffer = []
    for muster, name in PARTEI_MUSTER:
        for m in muster.finditer(stueck):
            treffer.append((m.start(), name))
    treffer.sort()
    out = []
    for _, name in treffer:
        namen = koalition if name == "KOALITION" else [name]
        for n in namen:
            if n not in out:
                out.append(n)
    if REST_RE.search(stueck):
        out.append("REST")
    return out


def rest_aufloesen(dafuer, dagegen, enthalten, fraktionen=None):
    """"die übrigen Fraktionen" = alle, die sonst nirgends stehen."""
    seiten = {"dafuer": dafuer, "dagegen": dagegen, "enthalten": enthalten}
    genannt = {p for liste in seiten.values() for p in liste if p != "REST"}
    fraktionen = fraktionen if fraktionen is not None else fraktionen_fuer(WP, "")
    rest = [f for f in fraktionen if f not in genannt]
    for k, liste in seiten.items():
        if "REST" in liste:
            seiten[k] = [p for p in liste if p != "REST"] + rest
    return seiten["dafuer"], seiten["dagegen"], seiten["enthalten"]


# ─────────────────────────────────────────────────────────────
# LESEN: Abstimmungsblöcke im Protokolltext
# ─────────────────────────────────────────────────────────────
ERGEBNIS_RE = re.compile(r"\b(angenommen|abgelehnt)\b", re.I)
ABSTIMM_SIGNAL = re.compile(r"stimm|enthält|enthaltung|handzeichen|erheben|gegenprobe", re.I)
SATZ_RE = re.compile(r"(?<=[.?!])\s+(?=[A-ZÄÖÜ„–-])")
DRS_RE = re.compile(r"Drucksachen?\s+((?:\d{2}/\d{1,5}(?:\s*,\s*|\s+und\s+|\s*\(neu\)\s*)?)+)", re.I)
GEGENSTAND_RE = re.compile(
    r"(?:Abstimmung\s+über|abstimmen\s+über|Beratung\s+über|zur\s+Abstimmung\s+über)\s+"
    r"((?:den|die|das)\s+(?:von\s+der\s+Bundesregierung\s+eingebrachten\s+)?"
    r"(?:Gesetzentwurf|Antrag|Entschließungsantrag|Beschlussempfehlung|Änderungsantrag|Verordnung)[^.]{0,220})", re.I)
NAMENTLICH_RE = re.compile(r"namentlich", re.I)


def _stufe(block):
    if re.search(r"dritte[rn]?\s+beratung|schlussabstimmung", block, re.I):
        return "Schlussabstimmung"
    if re.search(r"zweite[rn]?\s+beratung", block, re.I):
        return "Zweite Beratung"
    if re.search(r"beschlussempfehlung", block, re.I):
        return "Beschlussempfehlung"
    if re.search(r"entschließungsantrag", block, re.I):
        return "Entschließungsantrag"
    return ""


def _frage_antwort(block):
    """Frage-Antwort-Muster, in beiden Formen des Protokolls:
       "Wer stimmt dagegen? – Fraktion C, Fraktion D."
       "Wer stimmt für die Beschlussempfehlung? Das sind die Koalitionsfraktionen."
       "… zustimmen wollen, sich zu erheben. – Das sind die Unionsfraktion …"
    Die Antwort endet am Satzende oder an der nächsten Frage. Beginnt sie
    selbst mit "Wer", gab es keine Antwort (nur Handzeichen, nichts gesagt)."""
    def antwort_nach(frage_re):
        m = re.search(frage_re + r"\s*[–-]?\s*(?!Wer\b|Ich\b|Damit\b)([^?–]{0,220}?)(?=\.\s|\s+Wer\b|\s*[–-]\s|$)",
                      block, re.I)
        return m.group(1) if m else ""
    dafuer = antwort_nach(r"(?:zustimmen\s+(?:wollen|möchten)[^.?]{0,140}[.?]|stimmt\s+(?:für|dafür)[^?]{0,220}\?|dafür\s+ist\??)")
    dagegen = antwort_nach(r"(?:stimmt\s+dagegen\??|gegenprobe[^.?]{0,40}[.?]|gegenstimmen\??)")
    enth = antwort_nach(r"(?:enthält\s+sich\??|enthaltungen\??)")
    return dafuer, dagegen, enth


def _stimmen_satz(satz):
    """Ein-Satz-Muster: "mit den Stimmen X gegen die Stimmen Y bei Enthaltung Z"."""
    dafuer = dagegen = enth = ""
    m = re.search(r"mit\s+(?:den\s+)?stimmen\s+(.*?)(?=\s+gegen\s+(?:die\s+)?stimmen|\s+bei\s+(?:stimm)?enthaltung|"
                  r"\s+(?:angenommen|abgelehnt)|$)", satz, re.I)
    if m:
        dafuer = m.group(1)
    m = re.search(r"gegen\s+(?:die\s+)?stimmen\s+(.*?)(?=\s+bei\s+(?:stimm)?enthaltung|\s+(?:angenommen|abgelehnt)|"
                  r"\s+mit\s+(?:den\s+)?stimmen|$)", satz, re.I)
    if m:
        dagegen = m.group(1)
    m = re.search(r"bei\s+(?:stimm)?enthaltung(?:en)?\s+(.*?)(?=\s+(?:angenommen|abgelehnt)|\s+gegen\s+(?:die\s+)?stimmen|$)",
                  satz, re.I)
    if m:
        enth = m.group(1)
    einstimmig = bool(re.search(r"einstimmig|mit\s+den\s+stimmen\s+(des\s+ganzen|aller|des\s+gesamten)", satz, re.I))
    return dafuer, dagegen, enth, einstimmig


def _wp_aus_sitzung(sitzung):
    m = re.match(r"^(\d{2})/", str(sitzung or ""))
    return int(m.group(1)) if m else None


def abstimmungen_aus_text(text, datum="", sitzung="", wp=None):
    wp = wp or _wp_aus_sitzung(sitzung) or WP
    koalition, fraktionen = koalition_fuer(wp, datum), fraktionen_fuer(wp, datum)
    # Zwischenrufe und Beifall in Klammern stehen oft MITTEN in der Abstimmung
    # – "(Max Mustermann [Fraktion]: Die ganze Opposition dagegen!)" – und
    # enthalten Fraktionsnamen. Sie müssen vorher weg.
    text = re.sub(r"\s+", " ", text or "").strip()
    for _ in range(2):
        text = re.sub(r"\([^()]{0,500}\)", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    saetze = SATZ_RE.split(text)
    out, puffer = [], []
    for satz in saetze:
        puffer.append(satz)
        if len(puffer) > 14:
            puffer.pop(0)
        if not ERGEBNIS_RE.search(satz):
            continue
        block = " ".join(puffer)
        if not ABSTIMM_SIGNAL.search(block):
            continue
        # Beginn des Blocks: ab der letzten Abstimmungsankündigung
        start = max(block.rfind("Wir kommen zur Abstimmung"), block.rfind("Abstimmung über"),
                    block.rfind("Dritte Beratung"), block.rfind("zweiter Beratung"), 0)
        kern = block[start:] if start > 0 else block
        if NAMENTLICH_RE.search(kern) and re.search(r"abgegebene\s+stimmen|ja-stimmen|mit\s+ja", kern, re.I):
            puffer = []
            continue                      # namentlich – steht in votes.json
        ergebnis = ERGEBNIS_RE.findall(satz)[-1].lower()
        fa_dafuer, fa_dagegen, fa_enth = _frage_antwort(kern)
        s_dafuer, s_dagegen, s_enth, einstimmig = _stimmen_satz(satz)
        dafuer = parteien(s_dafuer or fa_dafuer, koalition)
        dagegen = parteien(s_dagegen or fa_dagegen, koalition)
        enthalten = parteien(s_enth or fa_enth, koalition)
        if einstimmig and not dafuer:
            dafuer = list(fraktionen)
        if not (dafuer or dagegen or enthalten):
            puffer = []
            continue                      # Ergebnis ohne Fraktionsangabe – nicht verwertbar
        dafuer, dagegen, enthalten = rest_aufloesen(dafuer, dagegen, enthalten, fraktionen)
        # Eine Fraktion steht nur auf einer Seite; bei Widerspruch gewinnt die erste Nennung
        gesehen = set()
        for liste in (dafuer, dagegen, enthalten):
            liste[:] = [p for p in liste if not (p in gesehen or gesehen.add(p))]
        gm = GEGENSTAND_RE.search(block)
        gegenstand = re.sub(r"\s+", " ", gm.group(1)).strip(" ,;") if gm else ""
        drs = []
        for m in DRS_RE.finditer(block):
            for n in re.findall(r"\d{2}/\d{1,5}", m.group(1)):
                if n not in drs:
                    drs.append(n)
        # Die dritte Beratung folgt unmittelbar auf die zweite und nennt den
        # Gegenstand nicht noch einmal: dann gilt der der vorigen Abstimmung.
        if (not gegenstand or not drs) and out and _stufe(kern) == "Schlussabstimmung":
            gegenstand = gegenstand or out[-1]["gegenstand"]
            drs = drs or list(out[-1]["drucksachen"])
        out.append({
            "id": f"pl-{sitzung.replace('/', '-')}-{len(out)+1}" if sitzung else f"pl-{len(out)+1}",
            "datum": datum, "sitzung": sitzung, "wahlperiode": wp, "stufe": _stufe(kern),
            "gegenstand": gegenstand[:260], "drucksachen": drs[:6],
            "ergebnis": ergebnis, "dafuer": dafuer, "dagegen": dagegen, "enthalten": enthalten,
            "namentlich": False, "text": kern[-700:],
        })
        puffer = []
    return out


# ─────────────────────────────────────────────────────────────
# LESEN: Tagesordnungspunkte und Reden
# ─────────────────────────────────────────────────────────────
# "Ich rufe den Tagesordnungspunkt 5 auf:", "Ich rufe die Tagesordnungs-
# punkte 12 a bis 12 c auf:", "Zusatzpunkt 3"
TOP_RE = re.compile(r"(?:Ich\s+rufe|rufe\s+ich)\s+(?:jetzt\s+|nun\s+|nunmehr\s+|noch\s+)?(?:den\s+|die\s+)?"
                    r"(Tagesordnungspunkte?|Zusatzpunkte?)\s+([0-9]+\s*[a-z]?(?:\s*(?:bis|und)\s*[0-9]*\s*[a-z]?)?)"
                    r"\s+(?:sowie\s+[^:]{0,120}?\s+)?auf\s*:?", re.I)
# Kopfzeile einer Rede, allein auf der Zeile:
#   "Dr. Erika Mustermann (SPD):"  "Max Mustermann, Bundesminister der Finanzen:"
#   "Britta Haßelmann (BÜNDNIS 90/DIE GRÜNEN):"
# "Stefan Müller (Erlangen) (CDU/CSU):" – der Wahlkreis steht in einer
# eigenen Klammer davor. "Christine Lambrecht, Bundesministerin der Justiz
# ⏎ und für Verbraucherschutz:" – das Amt läuft über zwei Zeilen. Damit ein
# gewöhnlicher Satz mit Doppelpunkt nicht als Rednerkopf gilt, muss nach dem
# Komma eine Amtsbezeichnung stehen.
AMT_WORT = r"(?:[Bb]undes|[Ss]taats|[Pp]arl\.|[Pp]arlamentarische)?(?:minister|ministerin|kanzler|kanzlerin|staatssekretär|staatssekretärin|staatsminister|staatsministerin|präsident|präsidentin|beauftragte|beauftragter|senator|senatorin|ministerpräsident|ministerpräsidentin)"
REDNER_RE = re.compile(
    r"^[ \t]*((?:(?:Dr|Prof)\.\s+)*[A-ZÄÖÜ][A-Za-zÄÖÜäöüßéèáçñ.\-]+(?:\s+(?:von\s+|van\s+|de\s+)?[A-ZÄÖÜ][A-Za-zÄÖÜäöüßéèáçñ.\-]+){1,3})"
    r"\s*(?:(?:\([^)\n]{2,40}\)\s*)?\(([^)\n]{2,40})\)"
    r"|,\s*((?:[^:\n!?]{0,60}?)" + AMT_WORT + r"[^:\n!?]{0,80}(?:\n[^:\n!?]{1,60})?))\s*:[ \t]*$", re.M | re.I)
LEITUNG_RE = re.compile(r"^[ \t]*(?:Alters)?(?:Vize)?[Pp]räsident(?:in)?\s+[^\n:]{3,60}:[ \t]*$", re.M)
ANREDE_RE = re.compile(r"^((?:sehr\s+)?(?:geehrte[rn]?\s+)?(?:frau|herr)\s+(?:vize)?präsident(?:in)?[!,.]\s*|"
                       r"(?:liebe|meine\s+sehr\s+geehrten|sehr\s+geehrte|meine\s+(?:sehr\s+)?(?:verehrten\s+)?damen)\s+[^!.]{0,80}[!.]\s*|"
                       r"guten\s+(?:morgen|tag|abend)[^!.]{0,60}[!.]\s*|"
                       r"(?:vielen|herzlichen)\s+dank[^!.–-]{0,30}[.!]?\s*[–-]?\s*)+", re.I)


def _betreff_kurz(t):
    """"Zweite und dritte Beratung des von der Bundesregierung eingebrachten
    Entwurfs eines Gesetzes zur Modernisierung …" → "Gesetz zur Modernisierung …"."""
    t = re.sub(r"^(?:(?:erste|zweite|dritte)(?:\s+und\s+(?:zweite|dritte))?\s+)?beratung\s+(?:des|der|über\s+(?:den|die|das))\s+",
               "", t or "", flags=re.I)
    t = re.sub(r"^von\s+der\s+bundesregierung\s+eingebrachten\s+|^von\s+den\s+fraktionen\s+[^e]{0,80}?eingebrachten\s+",
               "", t, flags=re.I)
    t = re.sub(r"^entwurfs\s+eines\s+gesetzes\b", "Gesetz", t, flags=re.I)
    t = re.sub(r"^entwurfs\s+einer\s+verordnung\b", "Verordnung", t, flags=re.I)
    t = re.sub(r"^antrags\s+der\s+(?:abgeordneten\s+[^,]{0,80},\s+)?", "Antrag der ", t, flags=re.I)
    t = t.strip(" :–-")
    return t[:1].upper() + t[1:] if t else t
FRAKTION_NAMEN = {
    "CDU/CSU": "CDU/CSU", "SPD": "SPD", "AFD": "AfD", "BÜNDNIS 90/DIE GRÜNEN": "Grüne",
    "DIE LINKE": "Linke", "BSW": "BSW", "FDP": "FDP", "FRAKTIONSLOS": "fraktionslos",
}


def _fraktion(roh):
    r = (roh or "").strip().upper()
    for k, v in FRAKTION_NAMEN.items():
        if k in r:
            return v
    return roh.strip() if roh else ""


def _auszug(text, n=300):
    t = re.sub(r"\([^)]{0,200}\)", " ", text)       # Zwischenrufe und Beifall
    t = re.sub(r"\s+", " ", t).strip()
    t = ANREDE_RE.sub("", t).strip()
    if len(t) <= n:
        return t
    schnitt = t.rfind(". ", 0, n)
    return (t[:schnitt + 1] if schnitt > n * 0.5 else t[:n].rsplit(" ", 1)[0] + " …").strip()


def _top_label(m):
    art = "ZP" if "zusatz" in m.group(1).lower() else "TOP"
    return art + " " + re.sub(r"\s+", " ", m.group(2)).strip()


SEITENKOPF_RE = re.compile(r"^[ \t]*(?:\d+\s+)?Deutscher Bundestag\s+[–-]\s+\d+\.\s+Wahlperiode\s+[–-].*$|^[ \t]*\([A-D]\)[ \t]*$", re.M)


# Rednerkopf mitten im Fließtext. Runde Klammer mit Fraktion und direkt
# danach ein Doppelpunkt kommt im Protokoll nur im Kopf vor – Zwischenrufe
# stehen mit eckigen Klammern: "(Max Mustermann [CDU/CSU]: …)".
_NAME = r"(?:(?:Dr|Prof)\.\s+)*[A-ZÄÖÜ][A-Za-zÄÖÜäöüßéèáçñ.\-]+(?:\s+(?:von\s+|van\s+|de\s+)?[A-ZÄÖÜ][A-Za-zÄÖÜäöüßéèáçñ.\-]+){1,3}"
_FRAKTION_KOPF = r"(?:CDU/CSU|SPD|AfD|BÜNDNIS\s*90/DIE\s*GRÜNEN|DIE\s*LINKE|Die\s*Linke|BSW|FDP|fraktionslos)"
INLINE_KOPF_RE = re.compile(
    r"(?<=[.!?)»“\"])\s+(" + _NAME + r"(?:\s*\([^)]{2,40}\))?\s*\(" + _FRAKTION_KOPF + r"\)\s*:)\s+"
    r"|(?<=[.!?)»“\"])\s+(" + _NAME + r",\s*[^:.!?]{0,60}?" + AMT_WORT + r"[^:.!?]{0,90}:)\s+"
    r"|(?<=[.!?)»“\"])\s+((?:Alters)?(?:Vize)?[Pp]räsident(?:in)?\s+" + _NAME + r"\s*:)\s+", re.I)


def koepfe_auf_zeilen(text):
    """Liefert DIP den Text ohne Zeilenumbrüche, stehen die Rednerköpfe
    mitten im Fließtext. Dann werden sie hier auf eigene Zeilen gesetzt,
    damit die zeilenweise Erkennung greift. Mit Umbrüchen bleibt alles, wie
    es ist."""
    zeilen = text.count("\n")
    if zeilen >= max(20, len(text) / 800):
        return text
    def auf_zeile(m):
        kopf = next(g for g in m.groups() if g)
        return "\n" + re.sub(r"\s+", " ", kopf).strip() + "\n"
    return INLINE_KOPF_RE.sub(auf_zeile, text)


def text_saeubern(text):
    """PDF-Reste entfernen: Seitenköpfe ("Deutscher Bundestag – 21. Wahl-
    periode – …"), Randmarken "(A)" bis "(D)" und die Silbentrennung am
    Zeilenende ("Vermittlungs-⏎ausschuss")."""
    t = (text or "").replace("\r", "")
    t = SEITENKOPF_RE.sub("", t)
    t = re.sub(r"(\w)-[ \t]*\n[ \t]*([a-zäöüß])", r"\1\2", t)
    return koepfe_auf_zeilen(t)


def sitzung_auswerten(text, datum="", sitzung=""):
    """Eine Sitzung in Tagesordnungspunkte zerlegen; je Punkt die Reden und
    die Abstimmungen finden und miteinander verbinden."""
    text = text_saeubern(text)
    starts = [(m.start(), _top_label(m), m.end()) for m in TOP_RE.finditer(text)]
    if not starts:
        starts = [(0, "", 0)]
    elif starts[0][0] > 0:
        starts.insert(0, (0, "", 0))
    abst, reden = [], []
    for i, (pos, top, kopf_ende) in enumerate(starts):
        ende = starts[i + 1][0] if i + 1 < len(starts) else len(text)
        teil = text[pos:ende]
        # Betreff: der erste Satz nach dem Aufruf
        nach = re.sub(r"\s+", " ", text[kopf_ende:kopf_ende + 600]).strip()
        betreff = _betreff_kurz(re.split(r"(?<=[a-zäöü)])\.\s|Drucksache", nach)[0][:220]) if top else ""
        # Abstimmungen dieses Punkts – IDs laufen über die ganze Sitzung
        funde = abstimmungen_aus_text(teil, datum, sitzung)
        for f in funde:
            f["id"] = f"pl-{sitzung.replace('/', '-')}-{len(abst) + 1}" if sitzung else f"pl-{len(abst) + 1}"
            f["top"], f["betreff"] = top, betreff
            # Der Betreff des Tagesordnungspunkts ist der amtliche Titel und sagt
            # mehr als "den Gesetzentwurf" oder "den Antrag der Fraktion …
            # auf Drucksache …". Nur ein ausdrücklich genannter Titel schlägt ihn.
            if betreff and "titel" not in (f.get("gegenstand") or "").lower():
                f["gegenstand"] = betreff
            abst.append(f)
        # Reden: von Kopfzeile zu Kopfzeile (Redner oder Sitzungsleitung)
        grenzen = sorted([(m.start(), m.end(), m) for m in REDNER_RE.finditer(teil)] +
                         [(m.start(), m.end(), None) for m in LEITUNG_RE.finditer(teil)])
        vorige = None                      # letzte Rede in diesem Punkt
        for k, (a, b, m) in enumerate(grenzen):
            if m is None:
                continue
            bis = grenzen[k + 1][0] if k + 1 < len(grenzen) else len(teil)
            koerper = teil[b:bis]
            name = re.sub(r"^(?:(?:Dr|Prof)\.\s+)+", "", m.group(1)).strip()
            # Unterbrochen von der Sitzungsleitung ("Herr Kollege, bitte …")
            # und weitergeredet: das ist dieselbe Rede, nicht eine zweite.
            if vorige is not None and vorige["name"] == name:
                vorige["zeichen"] += len(re.sub(r"\s+", " ", koerper))
                continue
            if len(koerper.strip()) < 200:
                continue                   # Zwischenfrage, Geschäftsordnung, Kurzbeitrag
            reden.append({
                "id": f"rd-{sitzung.replace('/', '-')}-{len(reden) + 1}" if sitzung else f"rd-{len(reden) + 1}",
                "datum": datum, "sitzung": sitzung, "name": name,
                "fraktion": _fraktion(m.group(2)) if m.group(2) else "",
                "rolle": re.sub(r"\s+", " ", m.group(3) or "").strip(),
                "top": top, "betreff": betreff, "auszug": _auszug(koerper),
                "zeichen": len(re.sub(r"\s+", " ", koerper)),
                "abstimmungen": [f["id"] for f in funde],
            })
            vorige = reden[-1]
        # Debatte an die Abstimmungen haengen
        redner = [{"name": r["name"], "fraktion": r["fraktion"] or r["rolle"], "id": r["id"]}
                  for r in reden if r["top"] == top and r["sitzung"] == sitzung and r["abstimmungen"] == [f["id"] for f in funde]]
        for f in funde:
            f["redner"] = redner
    return abst, reden


# ─────────────────────────────────────────────────────────────
# HOLEN
# ─────────────────────────────────────────────────────────────
def dip_seite(cursor=None, bis=None, wp=None):
    params = {"f.zuordnung": "BT", "f.wahlperiode": wp or WP, "format": "json", "apikey": KEY}
    if cursor:
        params["cursor"] = cursor
    if bis:
        params["f.datum.end"] = bis
    req = Request(DIP + "?" + urlencode(params), headers={"Accept": "application/json"})
    with urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def main():
    if not KEY:
        print("DIP_KEY fehlt – plenum_votes.json bleibt unverändert.")
        return 0
    try:
        cache = json.load(open(CACHE, encoding="utf-8"))
    except Exception:
        cache = {}
    fertig = set(cache.get("fertig") or [])
    try:
        alt = json.load(open(OUT, encoding="utf-8"))
    except Exception:
        alt = {}
    bestand = {a["id"]: a for a in (alt.get("abstimmungen") or []) if a.get("id")}
    reden_bestand = {r["id"]: r for r in (alt.get("reden") or []) if r.get("id")}

    neu_prot = neu_abst = 0
    # Stand je Wahlperiode; ältere Cache-Dateien kannten nur die aktuelle.
    stand = cache.get("wp") or {}
    if cache.get("aeltestes") and str(WP) not in stand:
        stand[str(WP)] = {"aeltestes": cache["aeltestes"], "komplett": False}

    def zeit_um():
        return neu_prot >= PRO_LAUF or time.monotonic() - T0 >= BUDGET

    def lese_seiten(wp, bis, nur_neue):
        """Blättert eine Durchsuchung durch. Gibt zurück, ob überhaupt noch
        unbekannte Protokolle auftauchten."""
        nonlocal neu_prot, neu_abst
        ws = stand.setdefault(str(wp), {"aeltestes": "", "komplett": False})
        cursor, seiten, neues_gesehen = None, 0, False
        while not zeit_um() and seiten < 4:
            try:
                daten = dip_seite(cursor, bis, wp)
            except (HTTPError, URLError, ValueError) as e:
                print(f"  DIP nicht erreichbar: {type(e).__name__}: {e}")
                return True                  # unklar – lieber nicht als komplett markieren
            seiten += 1
            doks = daten.get("documents") or []
            if not doks:
                break
            unbekannt = [d for d in doks if str(d.get("id") or d.get("dokumentnummer") or "") not in fertig]
            if unbekannt:
                neues_gesehen = True
            elif nur_neue:
                # Neue Protokolle stehen immer oben. Ist eine ganze Seite schon
                # bekannt, lohnt Weiterblättern nicht – jede Seite sind zehn
                # Volltexte, zusammen schnell zehn Megabyte.
                break
            for d in unbekannt:
                pid = str(d.get("id") or d.get("dokumentnummer") or "")
                text = d.get("text") or ""
                if len(text) < 2000:
                    continue                 # Protokoll noch nicht als Volltext da
                sitzung = str(d.get("dokumentnummer") or "")
                datum = str(d.get("datum") or "")[:10]
                funde, reden = sitzung_auswerten(text, datum, sitzung)
                for f in funde:
                    bestand[f["id"]] = f
                for r in reden:
                    reden_bestand[r["id"]] = r
                fertig.add(pid)
                if datum and (not ws["aeltestes"] or datum < ws["aeltestes"]):
                    ws["aeltestes"] = datum
                neu_prot += 1
                neu_abst += len(funde)
                print(f"  + {sitzung:<7} {datum}  {len(funde)} Abstimmungen, {len(reden)} Reden")
                if zeit_um():
                    break
            neuer = daten.get("cursor")
            if not neuer or neuer == cursor:
                break
            cursor = neuer
        return neues_gesehen

    for i, wp in enumerate(WAHLPERIODEN):
        if zeit_um():
            break
        # Eine ältere Wahlperiode erst, wenn alle neueren vollständig sind.
        if i > 0 and not all((stand.get(str(w)) or {}).get("komplett") for w in WAHLPERIODEN[:i]):
            break
        ws = stand.setdefault(str(wp), {"aeltestes": "", "komplett": False})
        if wp == WP or not ws["aeltestes"]:
            lese_seiten(wp, None, nur_neue=bool(ws["aeltestes"]))
        if zeit_um():
            break
        if ws["aeltestes"] and not ws["komplett"]:
            print(f"  Wahlperiode {wp}: nachholen vor {ws['aeltestes']}")
            if not lese_seiten(wp, ws["aeltestes"], nur_neue=False) and not zeit_um():
                ws["komplett"] = True
                print(f"  Wahlperiode {wp} vollständig gelesen.")
    aeltestes = (stand.get(str(WP)) or {}).get("aeltestes", "")

    liste = sorted(bestand.values(), key=lambda a: (a.get("datum", ""), a.get("id", "")), reverse=True)
    grenze = datetime.now(timezone.utc).date().toordinal() - REDEN_TAGE
    def jung(r):
        try:
            return datetime.fromisoformat(r.get("datum", "")[:10]).date().toordinal() >= grenze
        except ValueError:
            return True
    reden_liste = sorted((r for r in reden_bestand.values() if jung(r)),
                         key=lambda r: (r.get("datum", ""), r.get("id", "")), reverse=True)
    # Debatten älterer Wahlperioden sind nicht aufbewahrt – dann auch keine
    # Verweise darauf in den Abstimmungen.
    behalten = {r["id"] for r in reden_liste}
    for a in liste:
        if a.get("redner"):
            a["redner"] = [r for r in a["redner"] if r.get("id") in behalten]
    out = {"updated": datetime.now(timezone.utc).isoformat(), "wahlperiode": WP,
           "wahlperioden": sorted({a.get("wahlperiode") or WP for a in liste}, reverse=True),
           "quelle": "Deutscher Bundestag, Plenarprotokolle (DIP)",
           "hinweis": "Nicht-namentliche Abstimmungen, aus dem Protokolltext gelesen. "
                      "Stimmenzahlen gibt es dafür nicht.",
           "abstimmungen": liste,
           "reden": reden_liste}
    for pfad, inhalt in ((OUT, out), (CACHE, {"fertig": sorted(fertig), "aeltestes": aeltestes, "wp": stand})):
        tmp = pfad + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(inhalt, fh, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, pfad)
    print(f"→ {OUT}: {neu_prot} Protokolle neu gelesen, {neu_abst} Abstimmungen neu, "
          f"{len(liste)} Abstimmungen und {len(reden_liste)} Reden insgesamt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
