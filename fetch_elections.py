#!/usr/bin/env python3
"""
Presseschau – Wahlen (Termine, Umfragen, Ergebnisse)

Erzeugt elections.json:
  {
    "updated": ISO,
    "elections": [ {id, level, region, title, date, days, status, url, note} ],
    "polls":     { region_id: {institute, date, url, parties:{"CDU/CSU":29.0,...}} },
    "results":   { election_id: {date, url, parties:{...}, kind:"hochrechnung"|"ergebnis"} },
    "sources":   [ {name, url, ok} ]
  }

Quellen (alle frei zugänglich, HTML):
  wahlrecht.de/termine.htm                     – Wahltermine Bund, Länder, Europa
  wahlrecht.de/umfragen/                       – Sonntagsfrage Bund (mehrere Institute)
  wahlrecht.de/umfragen/politbarometer.htm     – Politbarometer im Detail
  wahlrecht.de/umfragen/landtage/<land>.htm    – Landtagsumfragen
  wahlrecht.de/news/<jahr>/<wahl>.html         – Prognosen, Hochrechnungen, Ergebnisse

Der Parser ist bewusst tolerant: Ändert wahlrecht.de das Markup, bleiben Felder
leer und der Lauf geht weiter. Alles, was nicht geholt werden konnte, steht am
Ende im Log und in "sources".

Ohne Netz oder bei Parser-Problemen bleibt eine vorhandene elections.json unverändert.
"""
import json, os, re, sys, time, html
from datetime import datetime, timezone, timedelta, date
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

UA = "Presseschau/3.0 (+github actions)"
BASE = "https://www.wahlrecht.de"
TIME_BUDGET_MIN = int(os.environ.get("ELECTION_BUDGET_MIN", "4"))
_DEADLINE = time.monotonic() + TIME_BUDGET_MIN * 60
def out_of_time(): return time.monotonic() > _DEADLINE

SOURCES = []

LAND_NAMES = {
    "bw": "Baden-Württemberg", "by": "Bayern", "be": "Berlin", "bb": "Brandenburg",
    "hb": "Bremen", "hh": "Hamburg", "he": "Hessen", "mv": "Mecklenburg-Vorpommern",
    "ni": "Niedersachsen", "nw": "Nordrhein-Westfalen", "rp": "Rheinland-Pfalz",
    "sl": "Saarland", "sn": "Sachsen", "st": "Sachsen-Anhalt",
    "sh": "Schleswig-Holstein", "th": "Thüringen",
}
# Dateinamen der Landtagsumfragen auf wahlrecht.de
LAND_SLUG = {
    "bw": "baden-wuerttemberg", "by": "bayern", "be": "berlin", "bb": "brandenburg",
    "hb": "bremen", "hh": "hamburg", "he": "hessen", "mv": "mecklenburg-vorpommern",
    "ni": "niedersachsen", "nw": "nrw", "rp": "rheinland-pfalz", "sl": "saarland",
    "sn": "sachsen", "st": "sachsen-anhalt", "sh": "schleswig-holstein", "th": "thueringen",
}
PARTIES = ["CDU/CSU", "CDU", "CSU", "SPD", "GRÜNE", "FDP", "LINKE", "AfD", "BSW",
           "FW", "SSW", "PIRATEN", "Sonstige"]

# ─────────────────────────────────────────────────────────────
def get(url, timeout=20):
    try:
        req = Request(url, headers={"User-Agent": UA, "Accept": "text/html"})
        with urlopen(req, timeout=timeout) as r:
            raw = r.read()
        for enc in ("utf-8", "iso-8859-1", "cp1252"):
            try: return raw.decode(enc)
            except UnicodeDecodeError: continue
        return raw.decode("utf-8", "replace")
    except (HTTPError, URLError, TimeoutError, OSError) as e:
        print(f"    FAIL {type(e).__name__} {url}")
        return ""

def strip(x):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", x or ""))).strip()

def to_num(x):
    """'29,5 %' → 29.5 ; '–' → None"""
    x = (x or "").replace("%", "").replace("\xa0", " ").strip()
    m = re.search(r"(\d{1,2})[,.](\d)", x) or re.search(r"\b(\d{1,2})\b", x)
    if not m: return None
    return float(m.group(0).replace(",", ".")) if m.lastindex else float(m.group(1))

MONTHS = {"januar":1,"februar":2,"märz":3,"maerz":3,"april":4,"mai":5,"juni":6,"juli":7,
          "august":8,"september":9,"oktober":10,"november":11,"dezember":12}
def parse_date(text):
    t = (text or "").lower()
    m = re.search(r"(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})", t)
    if m: 
        try: return date(int(m[3]), int(m[2]), int(m[1]))
        except ValueError: return None
    m = re.search(r"(\d{1,2})\.\s*(" + "|".join(MONTHS) + r")\s*(\d{4})", t)
    if m:
        try: return date(int(m[3]), MONTHS[m[2]], int(m[1]))
        except ValueError: return None
    return None

def rows_of(table_html):
    """Zeilen einer HTML-Tabelle als Liste von Zellenlisten."""
    out = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", table_html, re.S | re.I):
        cells = [strip(c) for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", tr, re.S | re.I)]
        if cells: out.append(cells)
    return out

def tables_of(page):
    return re.findall(r"<table[^>]*>(.*?)</table>", page or "", re.S | re.I)

# ─────────────────────────────────────────────────────────────
# TERMINE
# ─────────────────────────────────────────────────────────────
def fetch_termine():
    url = BASE + "/termine.htm"
    print("── Wahltermine (wahlrecht.de/termine.htm) ──")
    page = get(url)
    SOURCES.append({"name": "Wahltermine", "url": url, "ok": bool(page)})
    if not page: return []
    today = date.today()
    elections, seen = [], set()
    for tbl in tables_of(page):
        for cells in rows_of(tbl):
          try:
            line = " | ".join(cells)
            d = parse_date(line)
            if not d: continue
            # Region und Art der Wahl aus der Zeile bestimmen
            region_id, region, level = None, "", "sonstige"
            for code, name in LAND_NAMES.items():
                if name.lower() in line.lower():
                    region_id, region, level = code, name, "land"; break
            if region_id is None:
                if re.search(r"bundestag", line, re.I):
                    region_id, region, level = "bund", "Deutschland", "bund"
                elif re.search(r"europa|europäisch", line, re.I):
                    region_id, region, level = "eu", "Europäische Union", "eu"
                elif re.search(r"bundespräsident|bundesversammlung", line, re.I):
                    region_id, region, level = "bund", "Deutschland", "bund"
                else:
                    continue
            kind = ("Landtagswahl" if level == "land" else
                    "Europawahl" if level == "eu" else
                    "Bundestagswahl" if re.search(r"bundestag", line, re.I) else "Wahl")
            # Sonderformen: ein einziger Treffer entscheidet, kein zweiter Suchlauf.
            # (Vorher wurde erst auf "kommunal" geprueft und dann auf "kommunalwahl" -
            #  stand in der Zeile nur "Kommunalwahlen", war das Ergebnis None.)
            if level == "land":
                # Auch ohne angehaengtes "-wahl" erkennen ("Buergerschaft Hamburg")
                SONDER = [(r"kommunalwahl\w*|kommunalwahlen", "Kommunalwahl"),
                          (r"b[üu]rgerschaft(swahl)?\w*", "Bürgerschaftswahl"),
                          (r"abgeordnetenhaus(wahl)?\w*", "Abgeordnetenhauswahl"),
                          (r"volksentscheid\w*", "Volksentscheid"),
                          (r"volksabstimmung\w*", "Volksabstimmung"),
                          (r"oberb[üu]rgermeisterwahl\w*", "Oberbürgermeisterwahl")]
                for pat, label in SONDER:
                    if re.search(pat, line, re.I):
                        kind = label; break
            eid = f"{region_id}-{d.isoformat()}"
            if eid in seen: continue
            seen.add(eid)
            elections.append({
                "id": eid, "level": level, "region_id": region_id, "region": region,
                "title": f"{kind} {region}".strip(), "kind": kind,
                "date": d.isoformat(), "days": (d - today).days,
                "status": "past" if d < today else ("today" if d == today else "upcoming"),
                "url": url, "note": "",
            })
          except Exception as e:
            print(f"    Zeile uebersprungen ({type(e).__name__})")
            continue
    elections.sort(key=lambda e: e["date"])
    up = [e for e in elections if e["status"] != "past"]
    print(f"  {len(elections)} Termine erkannt, davon {len(up)} bevorstehend")
    for e in up[:5]:
        print(f"    {e['date']}  {e['title']}  (in {e['days']} Tagen)")
    return elections

# ─────────────────────────────────────────────────────────────
# UMFRAGEN
# ─────────────────────────────────────────────────────────────
def parse_poll_table(page, url):
    """Nimmt die oberste Datenzeile einer wahlrecht-Umfragetabelle."""
    for tbl in tables_of(page):
        rows = rows_of(tbl)
        if len(rows) < 2: continue
        header = [h.upper().replace("Ü", "Ü") for h in rows[0]]
        # Spalten den Parteien zuordnen
        cols = {}
        for i, h in enumerate(header):
            hh = re.sub(r"[^A-ZÄÖÜ]", "", h)      # "CDU/CSU" → "CDUCSU"
            for party in PARTIES:                 # lange Namen zuerst, s. PARTIES-Reihenfolge
                p = re.sub(r"[^A-ZÄÖÜ]", "", party.upper())
                if p and (hh == p or hh.startswith(p)):
                    cols.setdefault(party, i); break
            if "GRÜN" in hh: cols.setdefault("GRÜNE", i)
            if "LINKE" in hh: cols.setdefault("LINKE", i)
            if "SONST" in hh: cols.setdefault("Sonstige", i)
        if len(cols) < 3: continue
        for row in rows[1:]:
            d = parse_date(" ".join(row[:2]))
            vals = {}
            for party, idx in cols.items():
                if idx < len(row):
                    v = to_num(row[idx])
                    if v is not None and 0 <= v <= 100: vals[party] = v
            if len(vals) >= 3 and sum(vals.values()) > 50:
                return {"date": d.isoformat() if d else "", "parties": vals, "url": url}
    return None

def fetch_polls_bund():
    print("── Sonntagsfrage Bund ──")
    polls = {}
    for slug, name in [("politbarometer", "Politbarometer (FGW)"),
                       ("allensbach", "Allensbach"),
                       ("forsa", "Forsa"),
                       ("insa", "INSA"),
                       ("emnid", "Verian/Emnid")]:
        if out_of_time(): print("  ⏱ Budget – Rest folgt"); break
        url = f"{BASE}/umfragen/{slug}.htm"
        page = get(url)
        SOURCES.append({"name": f"Umfrage {name}", "url": url, "ok": bool(page)})
        if not page: continue
        p = parse_poll_table(page, url)
        if p:
            p["institute"] = name
            polls[slug] = p
            print(f"  {name}: {p['date']} " + " ".join(f"{k} {v}" for k, v in list(p['parties'].items())[:5]))
        else:
            print(f"  {name}: Tabelle nicht lesbar")
        time.sleep(0.5)
    if polls:
        # Mittelwert über alle Institute = einfache „Poll of Polls"
        agg, cnt = {}, {}
        for p in polls.values():
            for k, v in p["parties"].items():
                agg[k] = agg.get(k, 0) + v; cnt[k] = cnt.get(k, 0) + 1
        polls["bund"] = {"institute": f"Mittelwert aus {len(polls)} Instituten",
                         "date": max((p.get("date") or "") for p in polls.values()),
                         "url": BASE + "/umfragen/",
                         "parties": {k: round(agg[k] / cnt[k], 1) for k in agg}}
        print("  Mittelwert: " + " ".join(f"{k} {v}" for k, v in list(polls['bund']['parties'].items())[:6]))
    return polls

def fetch_polls_laender(regions):
    print("── Landtagsumfragen ──")
    polls = {}
    for code in regions:
        if out_of_time(): print("  ⏱ Budget – Rest folgt"); break
        slug = LAND_SLUG.get(code)
        if not slug: continue
        url = f"{BASE}/umfragen/landtage/{slug}.htm"
        page = get(url)
        SOURCES.append({"name": f"Umfrage {LAND_NAMES[code]}", "url": url, "ok": bool(page)})
        if not page: continue
        p = parse_poll_table(page, url)
        if p:
            p["institute"] = "verschiedene (wahlrecht.de)"
            polls[code] = p
            print(f"  {LAND_NAMES[code]}: {p['date']} " + " ".join(f"{k} {v}" for k, v in list(p['parties'].items())[:5]))
        time.sleep(0.5)
    return polls

# ─────────────────────────────────────────────────────────────
# ERGEBNISSE / HOCHRECHNUNGEN am Wahlabend
# ─────────────────────────────────────────────────────────────
def fetch_result(election):
    """Sucht die News-Seite zur Wahl und liest Prognose/Hochrechnung/Ergebnis."""
    d = election["date"]; year = d[:4]
    code = election["region_id"]
    slug = LAND_SLUG.get(code, code)
    kind = election["kind"].lower().replace("wahl", "wahl")
    candidates = [
        f"{BASE}/news/{year}/{kind}-{slug}-{year}.html",
        f"{BASE}/news/{year}/landtagswahl-{slug}-{year}.html",
        f"{BASE}/news/{year}/{slug}-{year}.html",
    ]
    for url in candidates:
        page = get(url)
        if not page: continue
        SOURCES.append({"name": f"Ergebnis {election['title']}", "url": url, "ok": True})
        p = parse_poll_table(page, url)
        if p:
            low = page.lower()
            kindr = ("ergebnis" if "endgültiges ergebnis" in low or "amtliches endergebnis" in low
                     else "hochrechnung" if "hochrechnung" in low else "prognose")
            p["kind"] = kindr
            print(f"  {election['title']}: {kindr} gelesen")
            return p
    print(f"  {election['title']}: keine Ergebnisseite gefunden")
    return None

# ─────────────────────────────────────────────────────────────
def main():
    print(f"[{datetime.now().isoformat()}] Presseschau – Wahlen (Budget {TIME_BUDGET_MIN} Min)")
    def safe(label, fn, default):
        try:
            return fn()
        except Exception as e:
            import traceback
            print(f"  ⚠ {label}: {type(e).__name__}: {e}")
            traceback.print_exc(limit=2)
            return default

    elections = safe("Termine", fetch_termine, [])
    today = date.today()

    # Umfragen: Bund immer, Länder nur für die nächsten anstehenden Wahlen
    polls = safe("Umfragen Bund", fetch_polls_bund, {})
    next_lands = [e["region_id"] for e in elections
                  if e["level"] == "land" and e["status"] != "past"][:6]
    polls.update(safe("Landtagsumfragen", lambda: fetch_polls_laender(next_lands), {}))

    # Ergebnisse: für Wahlen von heute und den letzten 2 Tagen
    print("── Ergebnisse (Wahltag + 48 Stunden) ──")
    results = {}
    fresh = [e for e in elections
             if 0 <= (today - date.fromisoformat(e["date"])).days <= 2]
    if not fresh:
        print("  keine Wahl in den letzten 48 Stunden")
    for e in fresh:
        if out_of_time(): break
        r = safe(f"Ergebnis {e['title']}", lambda: fetch_result(e), None)
        if r: results[e["id"]] = r

    out = {
        "updated": datetime.now(timezone.utc).isoformat(),
        "elections": elections, "polls": polls, "results": results,
        "sources": SOURCES,
        "note": "Termine und Umfragen von wahlrecht.de. Umfragen sind keine Prognosen.",
    }
    if not elections and not polls:
        print("⚠ Nichts geladen – bestehende elections.json bleibt unverändert.")
        return
    json.dump(out, open("elections.json", "w", encoding="utf-8"),
              ensure_ascii=False, separators=(",", ":"))
    ok = sum(1 for s in SOURCES if s["ok"])
    print(f"→ elections.json: {len(elections)} Termine, {len(polls)} Umfragen, "
          f"{len(results)} Ergebnisse ({ok}/{len(SOURCES)} Quellen erreichbar)")

if __name__ == "__main__":
    # Ein Fehler in einer Quelle darf den Workflow nicht rot faerben:
    # lieber unvollstaendige Daten als abgebrochener Lauf.
    try:
        main()
    except Exception as e:
        import traceback
        print(f"\n⚠ Wahlen-Lauf abgebrochen: {type(e).__name__}: {e}")
        traceback.print_exc()
        print("Vorhandene elections.json bleibt unveraendert. Der naechste Lauf versucht es erneut.")
        sys.exit(0)
