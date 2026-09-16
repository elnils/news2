#!/usr/bin/env python3
"""
nachtragen.py  –  Anreicherung über mehrere Läufe verteilt

Neue Meldungen bekommen herkunft, Gerichtsangaben und Personen schon beim
Abruf (fetch_news.py). Für alles, was vorher entstanden ist, wäre ein
einmaliger Durchlauf über Monate von Archivdateien zu teuer – deshalb
arbeitet dieses Skript in Portionen: Jeder Lauf nimmt sich ein Zeitbudget
und so viele Meldungen, wie hineinpassen, und merkt sich in der Datei
selbst, was erledigt ist. Nach ein paar Stunden ist der Bestand vollständig,
ohne dass ein einziger Lauf ins Zeitlimit gerät.

WORKFLOW-SCHRITT (fetch.yml), nach "Feeds holen":

    - name: Anreicherung nachtragen (Archiv und Bestand)
      continue-on-error: true
      timeout-minutes: 4
      env:
        NACHTRAG_BUDGET_MIN: '2.5'    # Zeitbudget je Lauf
        NACHTRAG_DATEIEN: '3'         # Archivmonate je Lauf
      run: python nachtragen.py

und archive/ ist ohnehin schon in der Commit-Liste.

WAS ES TUT
  1. Bestand (articles.json und die vier Geschwisterdateien): ergänzt, was
     beim Abruf übersprungen wurde – etwa weil dort die Notbremse griff.
  2. Archiv (archive/YYYY-MM.json): arbeitet die Monate von neu nach alt ab,
     je Lauf so viele wie das Budget erlaubt. Fertige Monate tragen
     "angereichert": "v10" und werden künftig übersprungen.
  3. Schreibt jede Datei atomar und nur, wenn sich wirklich etwas geändert
     hat – sonst gäbe es bei jedem Lauf einen leeren Commit.

Die Anreicherung selbst kommt aus fetch_news.py; dieses Skript importiert sie
und definiert nichts doppelt.
"""

import json
import os
import sys
import time
from datetime import datetime, timezone

MARKE = "v10"                     # Stand der Anreicherung; ändert sich die Logik, hochzählen
BUDGET = float(os.environ.get("NACHTRAG_BUDGET_MIN", "2.5")) * 60
MAX_DATEIEN = int(os.environ.get("NACHTRAG_DATEIEN", "3"))
ARCHIV = "archive"
BESTAND = ["articles.json", "eu_articles.json", "bundestag_articles.json",
           "laender_articles.json", "us_articles.json"]
START = time.monotonic()


def zeit_übrig():
    return BUDGET - (time.monotonic() - START)


# Die Anreicherung liegt in fetch_news.py. Der Import führt dort nur
# Definitionen aus; main() startet erst unter __main__.
try:
    from fetch_news import anreichern, lade_personen
except ImportError as e:
    print(f"fetch_news.py nicht importierbar ({e}) – nichts zu tun.")
    sys.exit(0)


def atomar(pfad, obj):
    tmp = pfad + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, separators=(",", ":"))
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, pfad)


def fehlt(a):
    """Was noch keine Herkunft hat, ist noch nicht angereichert."""
    return not a.get("herkunft")


def datei_nachtragen(pfad, personen, alle=False):
    """Ergänzt eine Datei. alle=True: bis zum Ende, sonst bis das Budget endet.
       Gibt (geändert, offen) zurück."""
    try:
        with open(pfad, encoding="utf-8") as fh:
            daten = json.load(fh)
    except Exception as e:
        print(f"  {pfad}: {type(e).__name__} – übersprungen")
        return 0, 0
    artikel = daten.get("articles") or []
    offen = [a for a in artikel if fehlt(a)]
    if not offen:
        if daten.get("angereichert") != MARKE:
            daten["angereichert"] = MARKE
            atomar(pfad, daten)
        return 0, 0

    getan = 0
    for a in offen:
        if not alle and zeit_übrig() <= 5:
            break
        anreichern(a, personen)
        getan += 1
    rest = len(offen) - getan
    if getan:
        if not rest:
            daten["angereichert"] = MARKE
        daten["updated_anreicherung"] = datetime.now(timezone.utc).isoformat()
        atomar(pfad, daten)
    print(f"  {pfad}: +{getan} ergänzt, {rest} offen")
    return getan, rest


def main():
    personen = lade_personen()
    print(f"[{datetime.now().isoformat(timespec='seconds')}] Nachtragen "
          f"(Budget {BUDGET/60:.1f} Min, {len(personen)} Personen im Verzeichnis)")

    # 1. Bestand – klein und schnell, läuft immer ganz durch
    print("── Bestand ──")
    gesamt = 0
    for f in BESTAND:
        if os.path.exists(f):
            g, _ = datei_nachtragen(f, personen, alle=True)
            gesamt += g

    # 2. Archiv – portionsweise, neueste Monate zuerst
    print("── Archiv ──")
    if not os.path.isdir(ARCHIV):
        print("  kein archive/ – nichts zu tun")
    else:
        monate = sorted([n for n in os.listdir(ARCHIV)
                         if n.endswith(".json") and n != "index.json"], reverse=True)
        bearbeitet = 0
        offen_gesamt = 0
        for name in monate:
            if bearbeitet >= MAX_DATEIEN or zeit_übrig() <= 5:
                break
            pfad = os.path.join(ARCHIV, name)
            try:
                with open(pfad, encoding="utf-8") as fh:
                    kopf = json.load(fh)
            except Exception:
                continue
            if kopf.get("angereichert") == MARKE:
                continue                      # Monat ist fertig
            g, rest = datei_nachtragen(pfad, personen)
            gesamt += g
            offen_gesamt += rest
            bearbeitet += 1
        noch = [n for n in monate
                if _offen(os.path.join(ARCHIV, n))]
        print(f"  {bearbeitet} Monatsdateien bearbeitet, {len(noch)} Monate noch offen")

    print(f"\n✓ {gesamt} Meldungen ergänzt, {zeit_übrig():.0f}s Budget übrig")
    return 0


def _offen(pfad):
    try:
        with open(pfad, encoding="utf-8") as fh:
            return json.load(fh).get("angereichert") != MARKE
    except Exception:
        return False


if __name__ == "__main__":
    sys.exit(main())
