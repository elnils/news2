#!/usr/bin/env python3
"""
maerkte_archivieren.py  –  schreibt markets_archive/JJJJ-MM.json

Für spätere Auswertungen (Ereignisstudien, Zusammenhänge mit Meldungen,
eigene Zeitreihen über Jahre): jeder Wert aus markets.json – Indizes,
Zinsen, Devisen, Energie, Rohstoffe, Agrar, Branchen, Pegel, Aktien – mit
seinem Tagesschluss. Bei jedem Lauf wird der Wert des heutigen Tages
überschrieben; der letzte Lauf des Tages bleibt stehen.

Beim ersten Mal je Wert wird das zurückliegende Jahr aus der Tagesreihe
von markets.json nachgetragen. Die Daten dafür sind aus Handelstagen
zurückgerechnet (Wochenenden ausgelassen, Feiertage nicht bekannt) und
tragen die Markierung "rueckgerechnet".

Aufbau je Monatsdatei:
  {"werte": {"BZ=F": {"name": "Brent", "einheit": "USD/Barrel",
                      "tage": {"2026-09-30": 64.2, …}, "rueckgerechnet": ["2026-09-01", …]}}}
Klein: rund 130 Werte × 22 Handelstage ≈ 50 KB je Monat.
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ORDNER = "markets_archive"
BERLIN = ZoneInfo("Europe/Berlin")


def laden(pfad, leer):
    try:
        return json.load(open(pfad, encoding="utf-8"))
    except (OSError, ValueError):
        return leer


def speichern(pfad, daten):
    tmp = pfad + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(daten, fh, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, pfad)


def handelstage_zurueck(heute, n):
    """Die letzten n Handelstage bis heute (ohne Wochenenden), ältester zuerst."""
    tage, d = [], heute
    while len(tage) < n:
        if d.weekday() < 5:
            tage.append(d.strftime("%Y-%m-%d"))
        d -= timedelta(days=1)
    return list(reversed(tage))


def main():
    mk = laden("markets.json", {})
    heute = datetime.now(BERLIN).date()
    tag = heute.strftime("%Y-%m-%d")
    os.makedirs(ORDNER, exist_ok=True)
    dateien = {}

    def datei(monat):
        if monat not in dateien:
            dateien[monat] = laden(os.path.join(ORDNER, f"{monat}.json"), {"werte": {}})
        return dateien[monat]

    bekannt = set()
    for f in os.listdir(ORDNER):
        if f.endswith(".json"):
            bekannt |= set((laden(os.path.join(ORDNER, f), {}).get("werte") or {}).keys())
    neu_nachgetragen = aktualisiert = 0
    for gr in mk.get("groups") or []:
        for w in gr.get("items") or []:
            sym, last = w.get("sym"), w.get("last")
            if not sym or not isinstance(last, (int, float)):
                continue
            kopf = {"name": w.get("n") or sym, "einheit": w.get("unit") or "", "gruppe": gr.get("grp") or ""}
            # heute (an Wochenenden der letzte Handelstag – bleibt dann einfach gleich)
            e = datei(tag[:7])["werte"].setdefault(sym, dict(kopf, tage={}))
            e.update(kopf)
            e["tage"][tag] = round(float(last), 6)
            aktualisiert += 1
            # erstes Mal: Jahr zurück aus der Tagesreihe
            if sym not in bekannt:
                reihe = [x for x in ((w.get("series") or {}).get("1j") or []) if isinstance(x, (int, float))]
                if len(reihe) > 1:
                    for d, v in zip(handelstage_zurueck(heute, len(reihe)), reihe):
                        if d == tag:
                            continue
                        ed = datei(d[:7])["werte"].setdefault(sym, dict(kopf, tage={}))
                        if d not in ed["tage"]:
                            ed["tage"][d] = round(float(v), 6)
                            ed.setdefault("rueckgerechnet", []).append(d)
                    neu_nachgetragen += 1
    for monat, daten in dateien.items():
        daten["stand"] = datetime.now(timezone.utc).isoformat()
        speichern(os.path.join(ORDNER, f"{monat}.json"), daten)
    print(f"→ Marktarchiv: {aktualisiert} Werte für {tag}, {neu_nachgetragen} erstmals ein Jahr zurück nachgetragen, "
          f"{len(dateien)} Monatsdateien geschrieben")
    return 0


if __name__ == "__main__":
    sys.exit(main())
