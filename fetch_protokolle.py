#!/usr/bin/env python3
"""Protokoll-Index für die laufende Wahlperiode.

WOZU
────
Die Plenarprotokolle des Bundestags sind durchsuchbar – aber nicht in einem
GitHub-Repository: Eine Wahlperiode umfasst rund 250 Sitzungen zu je etwa
800 KB Text, zusammen also gut 200 MB. Das kann eine Seite nicht bei jedem
Aufruf laden.

Gespeichert wird deshalb nicht der Text, sondern ein INDEX: je Redebeitrag eine
Zeile mit Datum, Sprecher, Fraktion, Tagesordnungspunkt und Fundstelle. Der
Wortlaut bleibt beim Bundestag und wird bei Bedarf dort geöffnet.

FORMAT
──────
Textdatei mit Tabulatoren statt JSON – das spart rund die Hälfte, weil keine
Feldnamen wiederholt werden:

    datum⇥sprecher⇥fraktion⇥protokoll⇥seite⇥top

Gerechnet: rund 25.000 Reden je Wahlperiode × etwa 90 Zeichen ≈ 2,2 MB roh.
GitHub Pages liefert komprimiert aus, beim Nutzer kommen also etwa 400 KB an –
und auch das nur, wenn jemand die Protokollsuche tatsächlich öffnet.

SCHNELL BLEIBEN
───────────────
Der Abruf läuft alle 25 Minuten und darf nicht blockieren. Deshalb:
  · je Lauf nur wenige neue Sitzungen (PROTO_PER_RUN, Standard 5)
  · was schon im Index steht, wird nie erneut geholt
  · ein hartes Zeitlimit; bei Überschreitung bricht der Lauf sauber ab
Beim ersten Mal füllt sich der Index also über mehrere Stunden, danach kommt
je Sitzungswoche nur noch wenig dazu.

AUFRUF
──────
    DIP_KEY=... python3 fetch_protokolle.py            # laufende Wahlperiode
    WAHLPERIODE=20 python3 fetch_protokolle.py         # eine bestimmte
    PROTO_PER_RUN=20 python3 fetch_protokolle.py       # Erstbefüllung beschleunigen
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen

DIP_KEY = os.environ.get("DIP_KEY") or os.environ.get("DIP_API_KEY") or ""
DIP_BASE = "https://search.dip.bundestag.de/api/v1"
WAHLPERIODE = int(os.environ.get("WAHLPERIODE", "21"))
PRO_LAUF = int(os.environ.get("PROTO_PER_RUN", "5"))
ZEITLIMIT = float(os.environ.get("PROTO_BUDGET_MIN", "4")) * 60
ORDNER = "protokolle"
INDEX = f"{ORDNER}/wp{WAHLPERIODE}.tsv"
META = f"{ORDNER}/wp{WAHLPERIODE}.meta.json"

_START = time.monotonic()


def zeit_aus():
    return (time.monotonic() - _START) > ZEITLIMIT


def hole(pfad, params, versuche=2):
    p = dict(params)
    p.update({"format": "json", "apikey": DIP_KEY})
    url = f"{DIP_BASE}/{pfad}?{urlencode(p)}"
    for i in range(versuche):
        try:
            req = Request(url, headers={"User-Agent": "Presseschau/1.0", "Accept": "application/json"})
            with urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except Exception as e:
            if i == versuche - 1:
                print(f"  Abruf fehlgeschlagen: {type(e).__name__}")
            time.sleep(1)
    return None


# ── Redebeiträge aus dem Protokolltext lösen ─────────────────────────────
#
# Der Bundestag schreibt jeden Wortbeitrag nach demselben Muster an:
#   "Dr. Anna Muster (SPD):"      "Präsidentin Bärbel Bas:"
#   "Karl Mustermann (CDU/CSU):"  "Staatsminister Klaus Beispiel:"
# Darauf lässt sich zuverlässig aufsetzen, ohne den Text zu speichern.
SPRECHER_RE = re.compile(
    r"^\s*((?:(?:Dr\.|Prof\.|Präsident(?:in)?|Vizepräsident(?:in)?|"
    r"Bundesminister(?:in)?|Staatsminister(?:in)?|Staatssekretär(?:in)?|"
    r"Parl\.\s+Staatssekretär(?:in)?)\s+)*"
    r"[A-ZÄÖÜ][\wäöüß.\-]+(?:\s+[A-ZÄÖÜ][\wäöüß.\-]+){0,3})"
    r"\s*(?:\(([^)]{2,40})\))?\s*:",
    re.M)

# Tagesordnungspunkte: "Tagesordnungspunkt 7:" / "Zusatzpunkt 3:"
TOP_RE = re.compile(r"^\s*(Tagesordnungspunkt|Zusatzpunkt)\s+(\d+[a-z]?)\s*:?", re.M | re.I)

FRAKTIONEN = {"spd", "cdu/csu", "cdu", "csu", "afd", "die linke", "linke",
              "bündnis 90/die grünen", "grüne", "fdp", "bsw", "fraktionslos"}


def sauber(t):
    return re.sub(r"\s+", " ", (t or "")).strip()


def reden_aus_text(text, protokoll, datum):
    """Zerlegt den Protokolltext in Redebeiträge – gibt nur Metadaten zurück."""
    zeilen = []
    top_aktuell = ""
    # Position der Tagesordnungspunkte merken, um jeder Rede den richtigen zuzuordnen
    def top_titel(pos):
        # Marke ("Tagesordnungspunkt 7:") plus die erste inhaltliche Folgezeile –
        # sonst zieht der Titel den ganzen folgenden Absatz mit.
        # Der Treffer beginnt am Zeilenumbruch – führende Leerzeichen und
        # Zeilenenden abschneiden, sonst ist die erste "Zeile" leer.
        block = text[pos:pos + 300].lstrip("\r\n \t").split("\n")
        marke = sauber(block[0]).rstrip(":")
        rest = next((sauber(z) for z in block[1:] if len(sauber(z)) > 8), "")
        rest = re.split(r"(?<=[a-zäöüß])\s+(?=[A-ZÄÖÜ][a-zäöüß]+\s*[:(])", rest)[0]
        return (f"{marke}: {rest}" if rest else marke)[:90]
    tops = [(m.start(), top_titel(m.start())) for m in TOP_RE.finditer(text)]

    for m in SPRECHER_RE.finditer(text):
        name = sauber(m.group(1))
        fraktion = sauber(m.group(2) or "")
        # Nur echte Wortbeiträge: Der Name muss plausibel sein und entweder eine
        # bekannte Fraktion oder ein Amt tragen. Sonst würden Zwischenrufe und
        # Quellenangaben mitgezählt.
        if len(name) < 6 or len(name) > 60:
            continue
        if fraktion and fraktion.lower() not in FRAKTIONEN and not re.search(r"(CDU|CSU|SPD|AfD|Grün|FDP|Linke|BSW)", fraktion, re.I):
            continue
        if not fraktion and not re.match(r"(Präsident|Vizepräsident|Bundesminister|Staatsminister|Staatssekretär|Parl\.)", name):
            continue
        # zugehörigen Tagesordnungspunkt suchen (letzter vor dieser Stelle)
        for pos, titel in tops:
            if pos <= m.start():
                top_aktuell = titel
            else:
                break
        zeilen.append("\t".join([
            datum, name, fraktion, protokoll, str(m.start() // 3000 + 1), top_aktuell[:90]
        ]))
    return zeilen


def lade_meta():
    try:
        with open(META, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"wahlperiode": WAHLPERIODE, "erledigt": [], "reden": 0, "updated": ""}


def main():
    if not DIP_KEY:
        print("DIP_KEY fehlt – Protokoll-Index übersprungen.")
        return 0

    os.makedirs(ORDNER, exist_ok=True)
    meta = lade_meta()
    erledigt = set(meta.get("erledigt", []))

    print(f"── Protokoll-Index WP {WAHLPERIODE} "
          f"({len(erledigt)} Sitzungen im Index, {meta.get('reden', 0)} Reden) ──")

    d = hole("plenarprotokoll", {"f.wahlperiode": WAHLPERIODE, "f.zuordnung": "BT"})
    if not d or not d.get("documents"):
        print("  Keine Protokolle gefunden.")
        return 0

    offen = [p for p in d["documents"] if str(p.get("id")) not in erledigt]
    offen.sort(key=lambda p: p.get("datum", ""), reverse=True)   # neueste zuerst
    if not offen:
        print("  Alles auf Stand.")
        return 0

    neue_zeilen, verarbeitet = [], 0
    for p in offen[:PRO_LAUF]:
        if zeit_aus():
            print("  Zeitlimit erreicht – Rest beim nächsten Lauf.")
            break
        pid = str(p.get("id"))
        nummer = p.get("dokumentnummer") or pid
        datum = (p.get("datum") or "")[:10]
        voll = hole(f"plenarprotokoll-text/{pid}", {})
        text = (voll or {}).get("text") or ""
        if not text:
            print(f"  {nummer}: kein Text")
            erledigt.add(pid)        # nicht endlos erneut versuchen
            continue
        zeilen = reden_aus_text(text, nummer, datum)
        neue_zeilen += zeilen
        erledigt.add(pid)
        verarbeitet += 1
        print(f"  {nummer} ({datum}): {len(zeilen)} Redebeiträge")

    if neue_zeilen:
        with open(INDEX, "a", encoding="utf-8") as f:
            f.write("\n".join(neue_zeilen) + "\n")

    meta.update({
        "wahlperiode": WAHLPERIODE,
        "erledigt": sorted(erledigt),
        "reden": meta.get("reden", 0) + len(neue_zeilen),
        "sitzungen_gesamt": len(d["documents"]),
        "updated": datetime.now(timezone.utc).isoformat(),
    })
    with open(META, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)

    groesse = os.path.getsize(INDEX) / 1024 if os.path.exists(INDEX) else 0
    print(f"  {verarbeitet} Sitzungen verarbeitet, {len(neue_zeilen)} Reden neu · "
          f"Index {groesse:.0f} KB · {len(erledigt)}/{len(d['documents'])} Sitzungen")
    return 0


if __name__ == "__main__":
    sys.exit(main())
