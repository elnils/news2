#!/usr/bin/env python3
"""
energie_holen.py  –  schreibt energie.json

Holt die Daten deines Energie-Dashboards. Die Adresse steht NICHT im Code,
sondern im Secret ENERGIE_API (Basisadresse der Seite). GitHub schwärzt den
Wert von Secrets in den Protokollen; in energie.json steht nur, welche
Datendateien gefunden wurden (relative Pfade wie data/history/spot_price.jsonl),
nie die Adresse.

ABLAUF
  1. Seite laden, die eingebundenen Skripte lesen und darin die Pfade der
     Datendateien finden (data/….json, .jsonl, .csv).
  2. Jede Datei laden (höchstens 8 MB, höchstens 80 Dateien) und
     zusammenfassen: letzter Eintrag, eine kurze Zeitreihe der letzten 120
     Einträge, die Zahlenfelder.
  2b. Für Reihen mit Datum zusätzlich den VOLLEN Verlauf (bis 2.000 Punkte)
     in energie_reihen.json sichern – Grundlage für die historische
     Einordnung und die Weitergabe-Analyse (energie_analyse.py).
  3. Für bekannte Reihen (Strompreis Day-Ahead, Kraftstoffe, Heizöl,
     Gasspeicher, TTF, CO₂, Strommix, Verbraucherpreise Energie) einen
     lesbaren Namen setzen. Was das Skript nicht kennt, steht unter dem
     Dateinamen und lässt sich später benennen.

Ohne Secret passiert nichts.
"""

import csv
import io
import json
import os
import re
import sys
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import urlopen, Request

BASIS = os.environ.get("ENERGIE_API", "").strip()
OUT = "energie.json"
MAX_BYTES = 8 * 1024 * 1024
MAX_DATEIEN = 80
UA = {"User-Agent": "Mozilla/5.0 (compatible; Presseschau/1.0)"}
PFAD_RE = re.compile(r"""['"`]((?:\./)?data/[\w\-./]+?\.(?:json|jsonl|csv))['"`]""")
NAMEN = [  # Muster im Dateipfad → lesbarer Name
    (r"spot|day.?ahead|dayahead", "Strompreis Day-Ahead"), (r"smard|mix|erzeugung|generation", "Strommix"),
    (r"tank|fuel|kraftstoff|e5|e10|diesel|benzin", "Kraftstoffpreise"), (r"heiz|heating.?oil|tecson", "Heizöl"),
    (r"agsi|speicher|storage", "Gasspeicher"), (r"ttf", "TTF Erdgas"), (r"co2|ets|emission", "CO₂"),
    (r"vpi|cpi|inflation|verbraucherpreis", "Verbraucherpreise Energie"), (r"jet|kerosin", "Jet-Fuel"),
    (r"fx|wechselkurs|exchange", "Wechselkurse"), (r"weather|wetter|meteo|wind|solar", "Wetter"),
    (r"entso|flow|fluss", "Stromflüsse"), (r"brent|oil|öl", "Rohöl"), (r"gas", "Gas"), (r"news", "News"),
]


def holen(url, text=True):
    with urlopen(Request(url, headers=UA), timeout=30) as r:
        daten = r.read(MAX_BYTES + 1)
    if len(daten) > MAX_BYTES:
        raise ValueError("zu groß")
    return daten.decode("utf-8", "replace") if text else daten


def pfade_finden(basis):
    html = holen(basis)
    pfade = set(PFAD_RE.findall(html))
    skripte = re.findall(r"""<script[^>]+src=["']([^"']+)["']""", html, re.I)
    for src in skripte[:20]:
        if src.startswith("http") and not src.startswith(basis):
            continue                                    # fremde Bibliotheken (CDN) überspringen
        try:
            pfade |= set(PFAD_RE.findall(holen(urljoin(basis, src))))
        except (HTTPError, URLError, ValueError, TimeoutError, OSError):
            continue
    # eingebettete Skripte im HTML sind oben schon erfasst
    return sorted(p.lstrip("./") for p in pfade)


def lesen(pfad, roh):
    if pfad.endswith(".jsonl"):
        return [json.loads(z) for z in roh.splitlines() if z.strip().startswith(("{", "["))]
    if pfad.endswith(".csv"):
        return list(csv.DictReader(io.StringIO(roh)))
    return json.loads(roh)


def zahlenfelder(e):
    return {k: v for k, v in e.items() if isinstance(v, (int, float)) and not isinstance(v, bool)} if isinstance(e, dict) else {}


def zusammenfassen(daten):
    """Liste von Einträgen → letzter Eintrag + kurze Reihe; Objekt → seine Listen und Zahlen."""
    if isinstance(daten, list):
        eintraege = [e for e in daten if isinstance(e, dict)]
        if not eintraege:
            return {"art": "liste", "anzahl": len(daten)}
        letzter = eintraege[-1]
        felder = sorted(zahlenfelder(letzter))
        zeitfeld = next((k for k in letzter if re.search(r"^(date|datum|time|zeit|ts|timestamp|day|tag)$", k, re.I)), None)
        reihe = [{**({zeitfeld: e.get(zeitfeld)} if zeitfeld else {}), **{k: e.get(k) for k in felder[:6]}} for e in eintraege[-120:]]
        return {"art": "reihe", "anzahl": len(eintraege), "zeitfeld": zeitfeld, "felder": felder[:20],
                "letzter": {k: letzter.get(k) for k in ([zeitfeld] if zeitfeld else []) + felder[:20]}, "reihe": reihe}
    if isinstance(daten, dict):
        out = {"art": "objekt", "werte": zahlenfelder(daten), "teile": {}}
        for k, v in list(daten.items())[:40]:
            if isinstance(v, list) and v and isinstance(v[0], dict):
                out["teile"][k] = zusammenfassen(v)
            elif isinstance(v, dict) and zahlenfelder(v):
                out["teile"][k] = {"art": "objekt", "werte": dict(list(zahlenfelder(v).items())[:40])}
        for k in ("updated", "stand", "timestamp", "date", "generated"):
            if isinstance(daten.get(k), str):
                out["stand"] = daten[k]
        return out
    return {"art": type(daten).__name__}


def name_fuer(pfad):
    for muster, name in NAMEN:
        if re.search(muster, pfad, re.I):
            return name
    return os.path.splitext(os.path.basename(pfad))[0]


def main():
    if not BASIS:
        print("Energie: kein ENERGIE_API hinterlegt – nichts zu tun.")
        return 0
    basis = BASIS if BASIS.endswith("/") else BASIS + "/"
    try:
        pfade = pfade_finden(basis)
    except (HTTPError, URLError, ValueError, TimeoutError, OSError) as e:
        print(f"Energie: Seite nicht erreichbar ({type(e).__name__})")
        return 0
    print(f"  Energie: {len(pfade)} Datendateien gefunden")
    reihen, voll = {}, {}
    for pfad in pfade[:MAX_DATEIEN]:
        try:
            daten = lesen(pfad, holen(urljoin(basis, pfad)))
        except (HTTPError, URLError, ValueError, TimeoutError, OSError) as e:
            print(f"    – {pfad}: {type(e).__name__}")
            continue
        z = zusammenfassen(daten)
        z["name"] = name_fuer(pfad)
        reihen[pfad] = z
        # voller Verlauf für Reihen mit Datum (auch verschachtelt in Objekten)
        for unterpfad, teil in ([(pfad, daten)] if isinstance(daten, list) else
                                [(f"{pfad}#{k}", v) for k, v in daten.items() if isinstance(v, list)] if isinstance(daten, dict) else []):
            eintraege = [e for e in teil if isinstance(e, dict)]
            if len(eintraege) < 30:
                continue
            zf = next((k for k in eintraege[-1] if re.search(r"^(date|datum|time|zeit|ts|timestamp|day|tag|period|periode)$", k, re.I)), None)
            felder = sorted(zahlenfelder(eintraege[-1]))[:8]
            if zf and felder:
                voll[unterpfad] = {"name": name_fuer(unterpfad), "zeitfeld": zf, "felder": felder,
                                   "punkte": [[str(e.get(zf))[:10]] + [e.get(f) for f in felder] for e in eintraege[-2000:]]}
        print(f"    + {pfad}: {z['name']} ({z.get('art')}, {z.get('anzahl', len(z.get('werte', {})))} Einträge)")
    if not reihen:
        print("Energie: keine Daten gelesen – energie.json bleibt unverändert.")
        return 0
    with open("energie_reihen.json", "w", encoding="utf-8") as fh:
        json.dump({"stand": datetime.now(timezone.utc).isoformat(), "reihen": voll}, fh, ensure_ascii=False, separators=(",", ":"))
    print(f"  Verläufe gesichert: {len(voll)} Reihen, {sum(len(v['punkte']) for v in voll.values())} Punkte")
    out = {"stand": datetime.now(timezone.utc).isoformat(), "quelle": "eigenes Energie-Dashboard", "dateien": reihen}
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, OUT)
    print(f"→ {OUT}: {len(reihen)} Datendateien zusammengefasst")
    return 0


if __name__ == "__main__":
    sys.exit(main())
