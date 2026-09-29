#!/usr/bin/env python3
"""
umfragen.py  –  schreibt umfragen.json

DIE NEUESTE UMFRAGE JE PARLAMENT, MIT RICHTIGEN PARTEINAMEN
Quelle ist DAWUM (dawum.de), die frei zugängliche Sammlung deutscher
Wahlumfragen: Bundestag, alle Landtage, Europaparlament. Eine einzige
Abfrage liefert Parlamente, Institute, Parteien und Umfragen.

Warum eigene Datei: In den bisherigen Wahldaten fehlte bei der NRW-Wahl die
CDU, dafür stand "Sonstige" groß da. DAWUM führt die CDU in Landtags-
umfragen unter einer eigenen Kennung (CDU statt CDU/CSU); wer nur die
Bundeskennungen kennt, schlägt sie den Sonstigen zu. Hier wird jede
Kennung über die Parteitabelle von DAWUM in ihren Namen übersetzt.

Ergebnis je Parlament: Institut, Datum, Befragte und die Werte je Partei.
Lizenz der Daten: ODC-ODbL, Quelle DAWUM – steht in der Datei und in der App.
"""

import json
import os
import sys
from datetime import datetime, timezone
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError

URL = "https://api.dawum.de/"
OUT = "umfragen.json"


def main():
    try:
        with urlopen(Request(URL, headers={"User-Agent": "Mozilla/5.0 (Presseschau)"}), timeout=40) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
    except (HTTPError, URLError, ValueError, TimeoutError, OSError) as e:
        print(f"DAWUM nicht erreichbar ({type(e).__name__}) – umfragen.json bleibt unverändert.")
        return 0
    parlamente = d.get("Parliaments") or {}
    parteien = d.get("Parties") or {}
    institute = d.get("Institutes") or {}
    je_parlament = {}
    for sid, u in (d.get("Surveys") or {}).items():
        je_parlament.setdefault(str(u.get("Parliament_ID")), []).append(u)

    def werte_von(u):
        w = {}
        for partei_id, wert in (u.get("Results") or {}).items():
            name = (parteien.get(str(partei_id)) or {}).get("Shortcut") or f"Partei {partei_id}"
            w[name] = round(float(wert), 1)
        return w

    out = {}
    for pid, liste in je_parlament.items():
        liste.sort(key=lambda u: u.get("Date") or "", reverse=True)
        u = liste[0]
        # Vorige Umfrage zum Vergleich: bevorzugt dasselbe Institut – sonst
        # misst man Unterschiede zwischen Instituten statt einer Veränderung.
        vorige = next((x for x in liste[1:] if x.get("Institute_ID") == u.get("Institute_ID")), None) \
            or (liste[1] if len(liste) > 1 else None)
        p = parlamente.get(pid) or {}
        werte = werte_von(u)
        vorher = None
        if vorige:
            vorher = {"datum": vorige.get("Date") or "", "parteien": werte_von(vorige),
                      "institut": (institute.get(str(vorige.get("Institute_ID"))) or {}).get("Name") or ""}
        out[p.get("Name") or pid] = {
            "vorher": vorher,
            "parlament": p.get("Name") or "", "kurz": p.get("Shortcut") or "", "wahl": p.get("Election") or "",
            "institut": (institute.get(str(u.get("Institute_ID"))) or {}).get("Name") or "",
            "datum": u.get("Date") or "", "befragte": u.get("Surveyed_Persons"),
            "parteien": dict(sorted(werte.items(), key=lambda kv: -kv[1])),
        }
    daten = {"stand": datetime.now(timezone.utc).isoformat(), "quelle": "DAWUM (dawum.de), Lizenz ODC-ODbL",
             "parlamente": out}
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(daten, fh, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, OUT)
    print(f"→ {OUT}: neueste Umfrage für {len(out)} Parlamente")
    for name, e in out.items():
        top = ", ".join(f"{k} {v}" for k, v in list(e["parteien"].items())[:4])
        print(f"  {name:<24} {e['datum']}  {e['institut'][:18]:<18} {top}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
