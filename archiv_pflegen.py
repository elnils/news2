#!/usr/bin/env python3
"""
archiv_pflegen.py  –  läuft nach fetch_news.py und entdoppeln.py

WARUM
archive/2026-09.json war 62 MB groß. GitHub warnt ab 50 MB und lehnt ab
100 MB jeden Push ab – dann würde kein einziger Lauf mehr etwas speichern.
Außerdem wird die ganze Monatsdatei bei jedem Lauf neu geschrieben; jede
Megabyte darin vergrößert die Git-Geschichte.

WAS ES TUT – VERLUSTFREI
Keine Meldung, kein Feld, kein Zeichen fällt weg. Ist eine Monatsdatei
größer als ARCHIV_MAX_MB (Standard 40), wird sie in Teile zerlegt
(archive/2026-09.json, archive/2026-09-b.json, …), jeder Teil mit
vollständigen Meldungen. archive/index.json listet alle Teile; die App lädt
sie automatisch.
"""

import json
import os
import re
import sys


ORDNER = "archive"
MAX_BYTES = int(float(os.environ.get("ARCHIV_MAX_MB", "40")) * 1024 * 1024)
def schreiben(pfad, daten):
    tmp = pfad + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(daten, fh, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, pfad)
    return os.path.getsize(pfad)


def main():
    idx_pfad = os.path.join(ORDNER, "index.json")
    try:
        idx = json.load(open(idx_pfad, encoding="utf-8"))
    except (OSError, ValueError):
        print("Archiv: kein archive/index.json – nichts zu tun.")
        return 0
    monate = {}
    for m in idx.get("months") or []:
        monate.setdefault(m.get("month"), []).append(m.get("file") or f"{ORDNER}/{m.get('month')}.json")
    neue_liste = []
    for monat, dateien in sorted(monate.items()):
        if not monat:
            continue
        # alle Teile eines Monats zusammen lesen
        arts, rest = [], {}
        for f in dict.fromkeys(dateien):
            try:
                d = json.load(open(f, encoding="utf-8"))
            except (OSError, ValueError):
                continue
            teil = d.get("articles") if isinstance(d, dict) else d
            if isinstance(d, dict):
                rest.update({k: v for k, v in d.items() if k != "articles"})
            arts.extend(teil or [])
        vorher = sum(os.path.getsize(f) for f in dict.fromkeys(dateien) if os.path.exists(f))
        # Verlustfrei: jede Meldung unverändert, nur nach Datum geordnet
        schlanke = sorted(arts, key=lambda a: str(a.get("date", "")))
        # in Teile unter der Größengrenze zerlegen
        teile, aktuell, groesse = [], [], 0
        for a in schlanke:
            z = len(json.dumps(a, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) + 1
            if aktuell and groesse + z > MAX_BYTES:
                teile.append(aktuell)
                aktuell, groesse = [], 0
            aktuell.append(a)
            groesse += z
        if aktuell or not teile:
            teile.append(aktuell)
        geschrieben = []
        for i, teil in enumerate(teile):
            name = f"{ORDNER}/{monat}.json" if i == 0 else f"{ORDNER}/{monat}-{chr(ord('a') + i)}.json"
            schreiben(name, dict(rest, articles=teil))
            geschrieben.append(name)
            neue_liste.append({"month": monat, "file": name, "count": len(teil)})
        # übrig gebliebene alte Teile entfernen
        for f in dict.fromkeys(dateien):
            if f not in geschrieben and os.path.exists(f):
                os.remove(f)
        nachher = sum(os.path.getsize(f) for f in geschrieben)
        print(f"  {monat}: {len(schlanke)} Meldungen, {vorher/1e6:.1f} MB → {len(teile)} Datei(en), {nachher/1e6:.1f} MB"
              f"{f', {len(teile)} Teile' if len(teile) > 1 else ''}")
    idx["months"] = neue_liste
    schreiben(idx_pfad, idx)
    print(f"→ Archiv gepflegt: {len(neue_liste)} Dateien")
    return 0


if __name__ == "__main__":
    sys.exit(main())
