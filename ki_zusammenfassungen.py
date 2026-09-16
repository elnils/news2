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
  4. schreibt ai.json:
        {"updated": …, "modell": …, "items": {"<id>": {"ki": "…", "ts": …}}}
  5. wirft Einträge weg, deren Meldung älter als KI_KEEP_DAYS ist.

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
KEEP_DAYS = int(os.environ.get("KI_KEEP_DAYS", "7"))
TIMEOUT = int(os.environ.get("KI_TIMEOUT", "30"))
OUT = "ki_meldungen.json"

# Anbieter in Reihenfolge: (Name, URL, Schlüssel, Modell)
ANBIETER = [a for a in [("openrouter", OR_URL, OR_KEY, OR_MODEL),
                        ("groq", GROQ_URL, GROQ_KEY, GROQ_MODEL)] if a[2]]
QUELLEN = ["articles.json", "eu_articles.json", "bundestag_articles.json",
           "laender_articles.json", "us_articles.json"]

SYSTEM = ("Du bist ein nüchterner deutscher Politikredakteur. Du fasst zusammen, was dasteht, "
          "und erfindest nichts hinzu. Kein Vorspann, keine Wertung, keine Floskeln.")
AUFGABE = ("Fasse die Meldung in höchstens drei kurzen Sätzen zusammen und nenne im letzten Satz "
           "die wichtigste Folge für Deutschland oder die EU. Wenn der Text dafür zu dünn ist, "
           "schreibe nur, was gesichert dasteht.\n\n")


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
            print(f"  ---  {anbieter[0][0]} HTTP {e.code}: {leib[:120]}")
            fehler += 1
            if e.code in (401, 403, 429, 402):
                print(f"     {anbieter[0][0]} fällt für diesen Lauf weg.")
                anbieter.pop(0)
        except (URLError, ValueError, KeyError) as e:
            print(f"  ---  {type(e).__name__}: {e}")
            fehler += 1
        time.sleep(0.5)

    out = {"updated": datetime.now(timezone.utc).isoformat(),
           "anbieter": [x[0] + ":" + x[3] for x in ANBIETER],
           "hinweis": "Automatisch erzeugte Zusammenfassungen. Im Zweifel gilt die Originalmeldung.",
           "items": items}
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, separators=(",", ":"))
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, OUT)
    print(f"\n→ {OUT}: {neu} neu, {len(items)} insgesamt, {fehler} Fehler")
    return 0


if __name__ == "__main__":
    sys.exit(main())
