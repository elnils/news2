#!/usr/bin/env python3
"""
ep_abstimmungen.py  –  schreibt ep_votes.json

NAMENTLICHE ABSTIMMUNGEN IM EUROPÄISCHEN PARLAMENT
Quelle ist HowTheyVote.eu – eine freie, gemeinnützige Aufbereitung der
offiziellen Abstimmungsprotokolle des EP (Daten unter offener Lizenz).
Je Abstimmung: Datum, Titel, Ergebnis, Stimmen gesamt, Ergebnis je
Fraktion (EVP, S&D, PfE, EKR, Renew, Grüne/EFA, Linke, ESN, fraktionslos)
und je Land – dazu die Einzelstimme JEDES Abgeordneten.

Die Einzelstimmen liegen kompakt in ep_stimmen/JJJJ-MM.json
({Abstimmung: [[Abgeordneter, "J"/"N"/"E"], …]}), die Namen, Länder und
Fraktionen einmal in ep_abgeordnete.json. Die App lädt beides erst, wenn
man "Alle Abgeordneten" aufklappt.

Je Lauf werden neue Abstimmungen geholt (Standard bis zu 40), bereits
bekannte bleiben gespeichert. Bestand: die letzten 365 Tage.
Ist die Schnittstelle anders aufgebaut als erwartet, schreibt das Skript
die gefundenen Felder ins Protokoll – dann lässt es sich leicht anpassen.
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.request import urlopen, Request

API = os.environ.get("EP_API", "https://howtheyvote.eu/api")
OUT = "ep_votes.json"
PRO_LAUF = int(os.environ.get("EP_PRO_LAUF", "40"))
UA = {"User-Agent": "Mozilla/5.0 (compatible; Presseschau/1.0)", "Accept": "application/json"}
KURZ = [(r"people|\bepp\b|evp", "EVP"), (r"socialist|s&d|s-d", "S&D"), (r"patriot|\bpfe\b", "PfE"),
        (r"conservatives|\becr\b|ekr", "EKR"), (r"renew", "Renew"), (r"green|efa", "Grüne/EFA"),
        (r"\bleft\b|gue|linke", "Linke"), (r"sovereign|\besn\b", "ESN"), (r"non-attached|\bni\b|fraktionslos", "fraktionslos")]


def holen(pfad):
    with urlopen(Request(API.rstrip("/") + "/" + pfad.lstrip("/"), headers=UA), timeout=30) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def kurz(name):
    for m, k in KURZ:
        if re.search(m, name or "", re.I):
            return k
    return name or "?"


def zahl(d, *keys):
    for k in keys:
        if isinstance(d, dict) and isinstance(d.get(k), (int, float)):
            return int(d[k])
    return 0


def stimmen(st):
    """{"FOR": n, "AGAINST": n, "ABSTENTION": n} in verschiedenen Schreibweisen."""
    st = st or {}
    return {"ja": zahl(st, "FOR", "for", "yes"), "nein": zahl(st, "AGAINST", "against", "no"),
            "enth": zahl(st, "ABSTENTION", "abstention", "abstentions")}


def liste_holen(seite):
    for pfad in (f"votes?page={seite}&page_size=50", f"votes?page={seite}"):
        try:
            d = holen(pfad)
        except (HTTPError, URLError, ValueError, TimeoutError, OSError) as e:
            print(f"  Liste {pfad}: {type(e).__name__}")
            continue
        eintraege = d.get("results") or d.get("data") or d.get("votes") or (d if isinstance(d, list) else [])
        if eintraege:
            return eintraege
        print(f"  Liste {pfad}: keine Einträge – Felder: {list(d)[:10] if isinstance(d, dict) else type(d).__name__}")
    return []


def einzeln(vid):
    try:
        return holen(f"votes/{vid}")
    except (HTTPError, URLError, ValueError, TimeoutError, OSError) as e:
        print(f"  Abstimmung {vid}: {type(e).__name__}")
        return None


def aufbereiten(roh, detail):
    d = detail or roh
    titel = d.get("display_title") or d.get("title") or d.get("description") or roh.get("display_title") or ""
    zeit = str(d.get("timestamp") or d.get("date") or roh.get("timestamp") or "")[:10]
    stats = d.get("stats") or {}
    gesamt = stimmen(stats.get("total") or stats)
    ergebnis = str(d.get("result") or roh.get("result") or "").upper()
    if not ergebnis and (gesamt["ja"] or gesamt["nein"]):
        ergebnis = "ADOPTED" if gesamt["ja"] > gesamt["nein"] else "REJECTED"
    fraktionen = []
    for g in stats.get("by_group") or d.get("group_stats") or []:
        gr = g.get("group") or {}
        name = gr.get("short_label") or gr.get("code") or gr.get("label") or g.get("group_code") or ""
        s = stimmen(g.get("stats") or g)
        if name and (s["ja"] or s["nein"] or s["enth"]):
            fraktionen.append(dict(s, name=kurz(name)))
    # Einzelstimmen aller Abgeordneten, Ergebnis je Land
    de = {"ja": 0, "nein": 0, "enth": 0}
    laender, einzel = {}, []
    for mv in d.get("member_votes") or []:
        m = mv.get("member") or {}
        land = ((m.get("country") or {}).get("code") or m.get("country_code") or "").upper()
        k = {"FOR": "ja", "AGAINST": "nein", "ABSTENTION": "enth"}.get(str(mv.get("position") or "").upper())
        mid = str(m["id"] if m.get("id") is not None else (m.get("mep_id") if m.get("mep_id") is not None else ""))
        gr = (mv.get("group") or m.get("group") or {})
        grname = kurz(gr.get("short_label") or gr.get("code") or gr.get("label") or "") if isinstance(gr, dict) else kurz(str(gr))
        if mid:
            ABGEORDNETE[mid] = {"name": (" ".join(x for x in (m.get("first_name"), m.get("last_name")) if x) or m.get("name") or "").strip(),
                                "land": land, "fraktion": grname}
            einzel.append([mid, {"ja": "J", "nein": "N", "enth": "E"}.get(k, "-")])
        if k and land:
            laender.setdefault(land, {"ja": 0, "nein": 0, "enth": 0})[k] += 1
        if k and land in ("DEU", "DE"):
            de[k] += 1
    return {"id": f"ep-{roh.get('id')}", "datum": zeit, "titel": titel.strip(),
            "referenz": d.get("reference") or roh.get("reference") or "",
            "ergebnis": "angenommen" if ergebnis.startswith("ADOPT") else "abgelehnt" if ergebnis.startswith("REJECT") else ergebnis.lower(),
            "ja": gesamt["ja"], "nein": gesamt["nein"], "enth": gesamt["enth"],
            "fraktionen": sorted(fraktionen, key=lambda f: -(f["ja"] + f["nein"] + f["enth"])),
            "deutschland": de if sum(de.values()) else None,
            "laender": laender, "_einzel": einzel,
            "link": f"https://howtheyvote.eu/votes/{roh.get('id')}"}


ABGEORDNETE = {}


def main():
    try:
        bestand = json.load(open(OUT, encoding="utf-8"))
    except (OSError, ValueError):
        bestand = {"abstimmungen": []}
    bekannt = {a["id"] for a in bestand.get("abstimmungen") or []}
    neu = []
    for seite in (1, 2, 3):
        if len(neu) >= PRO_LAUF:
            break
        liste = liste_holen(seite)
        if not liste:
            break
        for roh in liste:
            if not isinstance(roh, dict) or f"ep-{roh.get('id')}" in bekannt:
                continue
            detail = einzeln(roh.get("id"))
            a = aufbereiten(roh, detail)
            if a["titel"] and a["datum"]:
                neu.append(a)
                bekannt.add(a["id"])
            time.sleep(0.4)
            if len(neu) >= PRO_LAUF:
                break
    # Einzelstimmen in Monatsdateien, Verzeichnis der Abgeordneten ergänzen
    os.makedirs("ep_stimmen", exist_ok=True)
    nach_monat = {}
    for a in neu:
        nach_monat.setdefault(a["datum"][:7], {})[a["id"]] = a.pop("_einzel", [])
    for monat, stimmen_m in nach_monat.items():
        pfad = os.path.join("ep_stimmen", f"{monat}.json")
        try:
            alt_m = json.load(open(pfad, encoding="utf-8"))
        except (OSError, ValueError):
            alt_m = {}
        alt_m.update(stimmen_m)
        with open(pfad, "w", encoding="utf-8") as fh:
            json.dump(alt_m, fh, ensure_ascii=False, separators=(",", ":"))
    try:
        verz = json.load(open("ep_abgeordnete.json", encoding="utf-8"))
    except (OSError, ValueError):
        verz = {}
    verz.update(ABGEORDNETE)
    with open("ep_abgeordnete.json", "w", encoding="utf-8") as fh:
        json.dump(verz, fh, ensure_ascii=False, separators=(",", ":"))
    grenze = (datetime.now(timezone.utc) - timedelta(days=365)).strftime("%Y-%m-%d")
    alle = [a for a in (bestand.get("abstimmungen") or []) + neu if a.get("datum", "") >= grenze]
    alle.sort(key=lambda a: (a.get("datum", ""), a.get("id", "")), reverse=True)
    daten = {"stand": datetime.now(timezone.utc).isoformat(), "quelle": "HowTheyVote.eu (Daten des Europäischen Parlaments)",
             "abstimmungen": alle}
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(daten, fh, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, OUT)
    print(f"→ {OUT}: {len(neu)} neue Abstimmungen, {len(alle)} im Bestand")
    if neu:
        b = neu[0]
        print(f"  Beispiel: {b['datum']} {b['titel'][:70]} – {b['ergebnis']} ({b['ja']}:{b['nein']}:{b['enth']}), "
              f"{len(b['fraktionen'])} Fraktionen{', deutsche Abgeordnete ' + str(b['deutschland']) if b['deutschland'] else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
