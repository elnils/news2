#!/usr/bin/env python3
"""
entdoppeln.py  –  läuft direkt nach fetch_news.py

DASSELBE HAUS, DERSELBE ARTIKEL – EINMAL
Viele Häuser liefern denselben Artikel über mehrere Feeds: "Heise Telco"
und "Heise Security", "Reuters Tech", "Reuters Business" und "Reuters Top".
Bisher zählte jeder Feed als eigene Quelle – "2 Häuser", "3 Quellen",
dieselbe Schlagzeile dreimal unter "Zum Thema" oder "Unter Beobachtung".

Hier bekommt jede Meldung zwei feste Merkmale:

  haus  die Domain der Artikel-Adresse (heise.de, reuters.com, tagesschau.de)
        – egal, wie der Feed heißt. Nur wo die Adresse nichts verrät
        (Google-News-Umleitungen, Feed-Dienste), der Feedname ohne Ressort.
  uid   eine feste Kennung aus der bereinigten Adresse (ohne utm_- und
        andere Verfolgungsparameter), ersatzweise aus Haus und Schlagzeile.

Zwei Meldungen sind dieselbe, wenn sie dieselbe uid haben ODER dasselbe
Haus und dieselbe Schlagzeile innerhalb von zwölf Stunden. Dann bleibt die
erste; ihre weiteren Feeds stehen im Feld "feeds", die Ressorts werden
vereinigt. Die ID der ersten Meldung bleibt unverändert, damit KI-Gruppen,
Merklisten und Verweise weiter stimmen. Die Kennungen der entfernten Kopien
stehen in "kopien" an der verbleibenden Meldung.

Über alle Nachrichtendateien hinweg: articles.json zuerst, dann EU, Bund,
Länder, USA. Eine Meldung, die schon in einer früheren Datei steht, fällt
in der späteren weg.
"""

import hashlib
import json
import os
import re
import sys
from datetime import datetime
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

DATEIEN = ["articles.json", "eu_articles.json", "bundestag_articles.json",
           "laender_articles.json", "us_articles.json"]
FENSTER_S = 12 * 3600

# Adressen, die nichts über das Haus verraten
UMLEITUNG = re.compile(r"(^|\.)(news\.google\.com|feedproxy\.google\.com|feeds\.feedburner\.com|"
                       r"rss\.app|feedly\.com|flipboard\.com|msn\.com|t\.co|bit\.ly)$", re.I)
# Verfolgungs- und Feed-Parameter, die dieselbe Seite verschieden aussehen lassen
PARAM_WEG = re.compile(r"^(utm_\w+|wt_\w+|wt\.mc_id|at_\w+|ref|refsrc|src|source|rss|feed|feedid|ns_\w+|"
                       r"cmpid|cid|icid|ito|mc_cid|mc_eid|fbclid|gclid|ocid|smid|partner|xtor|piano_\w+|"
                       r"r|dicbo|em_pos|nlid|s_cid|campaign)$", re.I)
# Zweite Ebene unter Länderdomains: bbc.co.uk, abc.net.au
ZWEITE_EBENE = {"co", "com", "org", "net", "gov", "ac", "or", "gv", "edu"}
FEED_ZUSATZ = re.compile(r"\s+(eil|eilmeldung|breaking|politik|politics|wirtschaft|economy|inland|ausland|"
                         r"news|aktuell|digital|netz|tech|technology|technik|telco|telko|security|developer|ix|"
                         r"science|wissenschaft|wissen|health|gesundheit|sport|sports|kultur|culture|"
                         r"panorama|reise|auto|karriere|meinung|opinion|video|live|top|top ?news|"
                         r"schlagzeilen|rss|feed|world|international|europe|europa|asia|china|uk|business|"
                         r"markets|finance|us|eu)\s*$", re.I)


def domain(url):
    try:
        host = (urlsplit(url or "").hostname or "").lower()
    except ValueError:
        return ""
    if not host or UMLEITUNG.search(host):
        return ""
    host = re.sub(r"^(www\d?|m|mobil|mobile|amp|rss|feeds?|app)\.", "", host)
    teile = host.split(".")
    if len(teile) >= 3 and teile[-2] in ZWEITE_EBENE and len(teile[-1]) == 2:
        return ".".join(teile[-3:])
    return ".".join(teile[-2:])


def adresse_bereinigen(url):
    try:
        u = urlsplit((url or "").strip())
    except ValueError:
        return ""
    if not u.scheme or not u.netloc:
        return ""
    q = [(k, v) for k, v in parse_qsl(u.query, keep_blank_values=False) if not PARAM_WEG.match(k)]
    host = re.sub(r"^(www\d?|m|amp)\.", "", u.netloc.lower())
    pfad = re.sub(r"/(amp|index\.html?)?$", "", u.path) or "/"
    return urlunsplit(("https", host, pfad, urlencode(q), ""))


def name_basis(quelle):
    q = re.sub(r"^\s*google\s*news\s*[:\-–]\s*", "", (quelle or "").strip(), flags=re.I)
    for _ in range(3):
        q = FEED_ZUSATZ.sub("", q).strip()
    return re.sub(r"^(die|der|das|the)\s+", "", q.lower())


NAME_ZU_DOMAIN = {}      # aus allen Meldungen mit echter Adresse gelernt: "tagesschau" → tagesschau.de


def haus_von(a):
    d = domain(a.get("link", ""))
    if d:
        return d
    # Umleitung (Google News): Domain aus dem Titelende " - tagesschau.de" …
    m = re.search(r"\s[-–|]\s*((?:[a-z0-9-]+\.)+[a-z]{2,})\s*$", (a.get("title") or "").lower())
    if m:
        return domain("https://" + m.group(1)) or m.group(1)
    # … oder aus dem Namen, den dasselbe Haus anderswo mit echter Adresse trägt
    n = name_basis(a.get("source"))
    return NAME_ZU_DOMAIN.get(n) or n or "unbekannt"


KUERZEL = re.compile(r"^\s*(ROUNDUP(\s*\d+)?|WDH|KORREKTUR|UPDATE(\s*\d+)?|EILMELDUNG|BREAKING|FLASH|"
                     r"IM FOKUS|AKTIE IM FOKUS|dpa-AFX-Überblick|ANALYSE-FLASH|INTERVIEW|PRESSESTIMME)\s*[:/]\s*", re.I)


def titel_schluessel(t):
    t = KUERZEL.sub("", t or "")                                      # "ROUNDUP:", "WDH:" am Anfang
    t = re.sub(r"\s+[-–|]\s+[^-–|]{2,40}$", "", t)                  # " - tagesschau.de" am Ende
    # Umschrift wie im Frontend: "Neutralitaet" = "Neutralität"
    t = t.lower().replace("ä", "a").replace("ö", "o").replace("ü", "u").replace("ae", "a").replace("oe", "o").replace("ue", "u")
    return re.sub(r"[^a-z0-9ß]+", " ", t).strip()


def zeit(a):
    try:
        return datetime.fromisoformat(str(a.get("date", "")).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def main():
    nach_uid, nach_titel = {}, {}
    gesamt_vorher = gesamt_nachher = 0
    # Erst lernen, welcher Feedname zu welcher Domain gehört
    for datei in DATEIEN:
        try:
            with open(datei, encoding="utf-8") as fh:
                for a in (json.load(fh).get("articles") or []):
                    d = domain(a.get("link", ""))
                    if d:
                        NAME_ZU_DOMAIN.setdefault(name_basis(a.get("source")), d)
        except (OSError, ValueError, AttributeError):
            continue
    for datei in DATEIEN:
        try:
            with open(datei, encoding="utf-8") as fh:
                daten = json.load(fh)
        except (OSError, ValueError):
            continue
        arts = daten.get("articles") if isinstance(daten, dict) else None
        if not isinstance(arts, list):
            continue
        gesamt_vorher += len(arts)
        # älteste zuerst: die erste Meldung bleibt, spätere Feeds hängen sich an
        arts.sort(key=zeit)
        bleiben = []
        for a in arts:
            haus = haus_von(a)
            sauber = adresse_bereinigen(a.get("link", ""))
            tkey = titel_schluessel(a.get("title", ""))
            uid = hashlib.sha1((sauber or (haus + "|" + tkey)).encode("utf-8")).hexdigest()[:14]
            a["haus"], a["uid"] = haus, uid
            erste = nach_uid.get(uid)
            if erste is None and tkey:
                kand = nach_titel.get((haus, tkey))
                if kand is not None and abs(zeit(a) - zeit(kand)) <= FENSTER_S:
                    erste = kand
            if erste is not None:
                feeds = erste.setdefault("feeds", [erste.get("source", "")])
                if a.get("source") and a["source"] not in feeds:
                    feeds.append(a["source"])
                erste.setdefault("kopien", []).append(a.get("id"))
                for t in a.get("topics") or []:
                    if t not in (erste.get("topics") or []):
                        erste.setdefault("topics", []).append(t)
                if len(a.get("desc") or "") > len(erste.get("desc") or ""):
                    erste["desc"] = a["desc"]
                continue
            nach_uid[uid] = a
            if tkey:
                nach_titel[(haus, tkey)] = a
            bleiben.append(a)
        bleiben.sort(key=zeit, reverse=True)
        daten["articles"] = bleiben
        daten["entdoppelt"] = {"vorher": len(arts), "nachher": len(bleiben)}
        gesamt_nachher += len(bleiben)
        tmp = datei + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(daten, fh, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, datei)
        print(f"  {datei}: {len(arts)} → {len(bleiben)}")
    print(f"→ entdoppelt: {gesamt_vorher} → {gesamt_nachher} Meldungen "
          f"({gesamt_vorher - gesamt_nachher} Kopien aus weiteren Feeds desselben Hauses)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
