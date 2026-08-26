#!/usr/bin/env python3
"""
Presseschau – Verzeichnis-Fetch (Ergänzung zu fetch_news.py)

Erzeugt (additiv, bestehende Dateien bleiben unberührt):
  people.json       Personen + Ausschüsse (Bundestag, Bundesrat, 16 Landtage,
                    Europäisches Parlament, US-Kongress)
                    inkl. Kontakt, Ausschuss-Mitgliedschaften, letzte Reden
  dip.json          Gesetzgebung aus der DIP-API: Vorgänge mit Stand, Drucksachen,
                    Plenarprotokolle, Aktivitäten (= Politicos "Procedures/Bills")
  calendar.ics      Abonnierbarer Kalender (Google / Apple / Outlook) aus documents.json
  newsletters.json  Externe Newsletter-/Briefing-Feeds (optional)

Quellen (alle kostenlos):
  Bundestag  – abgeordnetenwatch.de API v2  (Mandate, Fraktionen, Ausschüsse, Mitgliedschaften)
             – DIP-API des Bundestags        (Reden/Aktivitäten, benötigt DIP_API_KEY)
  EP         – EP Open Data Portal API v2    (MEPs, Ausschüsse, Kontakt)

Umgebungsvariablen:
  DIP_API_KEY   API-Key von https://dip.bundestag.de/über-dip/hilfe/api  (kostenlos)
  DIR_LIMIT     nur N Personen je Parlament (zum Testen)

Nur Standardbibliothek. Läuft in GitHub Actions wie fetch_news.py.
Alle Endpunkte sind in ENDPOINTS gebündelt – dort zuerst nachsehen, wenn eine Quelle 404/403 liefert.
"""
import json, os, re, sys, time, hashlib
from datetime import datetime, timezone, timedelta, date
from urllib.request import urlopen, Request
from urllib.parse import urlencode, quote
from urllib.error import URLError, HTTPError
import traceback
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

def _atomic_dump(obj, path):
    """Erst in eine Nebendatei schreiben, dann umbenennen. Bricht der Lauf mittendrin
    ab (Timeout, Netzfehler), bleibt die alte Datei vollstaendig erhalten - statt einer
    halb geschriebenen, die im Browser als "kein gueltiges JSON" ankommt."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, separators=(",", ":"))
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)



UA = "Presseschau/3.0 (+github actions; kontakt siehe repo)"
# Biografie-Seiten werden inkrementell angereichert: pro Lauf höchstens ENRICH_PER_RUN
# Personen, danach erst wieder nach ENRICH_AFTER_DAYS. So sind nach wenigen Läufen alle
# Profile mit Foto und Kontakt gefüllt, ohne die Server zu belasten.
# ── ZEITBUDGET ───────────────────────────────────────────────
# Ohne Begrenzung läuft dieses Skript sehr lange: Bundestag + 16 Landtage über
# abgeordnetenwatch sind mehrere tausend Einzelabrufe. Deshalb bekommt jeder Lauf
# ein hartes Budget. Was nicht mehr hineinpasst, macht der nächste Lauf – der
# Fortschritt steht im Cache, es geht nichts verloren.
TIME_BUDGET_MIN = int(os.environ.get("TIME_BUDGET_MIN", "8"))
_DEADLINE = time.monotonic() + TIME_BUDGET_MIN * 60
def time_left():   return _DEADLINE - time.monotonic()
def out_of_time(): return time_left() <= 0
def budget_note(what):
    print(f"  ⏱ Zeitbudget aufgebraucht – {what} wird im nächsten Lauf fortgesetzt.")

# Landtage rotieren: pro Lauf nur LANDTAGE_PER_RUN Stück, der Rest folgt.
# 16 Landtage / 4 pro Lauf = 4 Läufe; 720 MEP-Detailprofile / 150 = 5 Läufe.
LANDTAGE_PER_RUN = int(os.environ.get("LANDTAGE_PER_RUN", "4"))
ENRICH_PER_RUN   = int(os.environ.get("ENRICH_PER_RUN", "40"))
ENRICH_AFTER_DAYS = int(os.environ.get("ENRICH_AFTER_DAYS", "90"))
CACHE_FILE = "people_cache.json"
LIMIT = int(os.environ.get("DIR_LIMIT", "0") or 0)
# DIP-API-Key: Der Bundestag veröffentlicht auf
#   https://dip.bundestag.de/über-dip/hilfe/api
# einen öffentlichen Key, der aktuell bis Ende Mai 2027 gültig ist. Trage ihn hier ein
# (oder besser als Secret DIP_API_KEY, dann muss er beim Ablauf nur an einer Stelle getauscht werden).
# Bewusst NICHT fest einkodiert: der genaue String ändert sich mit jeder Verlängerung.
# Öffentlicher Key des Bundestags, gültig bis Ende Mai 2027
# Quelle: https://dip.bundestag.de/über-dip/hilfe/api · Doku: informationsblatt_zur_dip_api.pdf
DIP_PUBLIC_KEY = "R2BZaee.DjdCyihKZMf8AOjtScubP2EVydegzjmBIQ"
# Secret-Name egal: DIP_KEY, DIP_API_KEY oder der eingebaute öffentliche Key
DIP_KEY = (os.environ.get("DIP_KEY") or os.environ.get("DIP_API_KEY") or DIP_PUBLIC_KEY).strip()
TZ = ZoneInfo("Europe/Berlin")

ENDPOINTS = {
    # abgeordnetenwatch.de – dokumentiert unter https://www.abgeordnetenwatch.de/api
    "aw": "https://www.abgeordnetenwatch.de/api/v2",
    # DIP – dokumentiert unter https://search.dip.bundestag.de/api/v1/swagger-ui/
    "dip": "https://search.dip.bundestag.de/api/v1",
    # EP Open Data – dokumentiert unter https://data.europarl.europa.eu/en/developer-corner/opendata-api
    "ep": "https://data.europarl.europa.eu/api/v2",
    # unitedstates/congress-legislators – gemeinfrei (CC0), JSON auf dem gh-pages-Branch
    "us": "https://raw.githubusercontent.com/unitedstates/congress-legislators/gh-pages",
    # Wikipedia REST – Kurzbiografie + Bild, kein Key nötig
    "wiki": "https://de.wikipedia.org/api/rest_v1/page/summary",
}

# Externe Newsletter/Briefing-Feeds → newsletters.json  (url, name, thema, pro)
# Hinweis: Paywall-Feeds liefern meist nur Teaser – das reicht für die Briefing-Kachel.
NEWSLETTER_FEEDS = [
    # ── Eigener Newsletter „Die Tageslage" ────────────────────
    # RSS-URL der eigenen Seite hier eintragen; der Name muss „Tageslage" enthalten,
    # damit das Frontend die Ausgabe als eigene Karte oben anzeigt.
    (os.environ.get("TAGESLAGE_FEED", "https://example.invalid/tageslage/feed"), "Die Tageslage", "politik", False),
    ("https://news.google.com/rss/search?q=site:tagesspiegel.de/background&hl=de&gl=DE&ceid=DE:de", "Tagesspiegel Background", "netzpolitik", True),
    ("https://news.google.com/rss/search?q=site:table.media&hl=de&gl=DE&ceid=DE:de",                "Table.Media",             "politik",     True),
    ("https://netzpolitik.org/feed/",                                                              "netzpolitik.org",         "netzpolitik", False),
    ("https://www.euractiv.de/feed/",                                                              "Euractiv DE",             "eu",          True),
]

# ─────────────────────────────────────────────────────────────
# HTTP
# ─────────────────────────────────────────────────────────────
def get(url, params=None, accept="application/json", retries=1, timeout=20):
    if params:
        url = url + ("&" if "?" in url else "?") + urlencode(params)
    for i in range(retries + 1):
        try:
            req = Request(url, headers={"User-Agent": UA, "Accept": accept})
            with urlopen(req, timeout=timeout) as r:
                data = r.read()
                return data
        except HTTPError as e:
            if e.code in (429, 503) and i < retries and not out_of_time():
                time.sleep(3 * (i + 1)); continue
            print(f"    HTTP {e.code} {url[:90]}"); return None
        except (URLError, TimeoutError, OSError) as e:
            if i < retries: time.sleep(2); continue
            print(f"    ERR {e} {url[:90]}"); return None

def get_json(url, params=None):
    d = get(url, params)
    if not d: return None
    txt = d.decode("utf-8", "replace").lstrip()
    if not txt or txt[0] not in "[{":
        print(f"    kein JSON ({txt[:40]!r})"); return None
    try: return json.loads(txt)
    except Exception as e:
        print(f"    JSON ERR {e}"); return None

def uid(*parts):
    return hashlib.md5("|".join(str(p) for p in parts).encode()).hexdigest()[:12]

def norm_name(s):
    s = re.sub(r"\b(Dr|Prof|h\.c|Freiherr|Graf|von|van|de|der|zu)\b\.?", " ", s or "", flags=re.I)
    return re.sub(r"[^a-zäöüß ]", "", s.lower()).strip()

def email_pattern(first, last, domain):
    """Adressmuster der Parlamente – als 'unverifiziert' gekennzeichnet."""
    t = lambda x: re.sub(r"[^a-z\-]", "", (x or "").lower().replace("ä","ae").replace("ö","oe").replace("ü","ue").replace("ß","ss").replace(" ", "-"))
    if not first or not last: return ""
    return f"{t(first)}.{t(last)}@{domain}"

# ─────────────────────────────────────────────────────────────
# ANREICHERUNG – Biografie-Seiten (Foto, E-Mail, Telefon, Raum)
# ─────────────────────────────────────────────────────────────
def load_cache():
    try: return json.load(open(CACHE_FILE, encoding="utf-8"))
    except Exception: return {}

def save_cache(c):
    json.dump(c, open(CACHE_FILE, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))

def html_get(url):
    d = get(url, accept="text/html,application/xhtml+xml")
    return d.decode("utf-8", "replace") if d else ""

def strip_tags(x):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", x or "")).strip()

def abs_url(base, u):
    if not u: return ""
    if u.startswith("http"): return u
    if u.startswith("//"): return "https:" + u
    return base.rstrip("/") + "/" + u.lstrip("/")

def scrape_profile(url, base):
    """Liest Foto, E-Mail, Telefon und Adresse aus einer Abgeordneten-/Mitgliedsseite.
    Bewusst tolerant: unbekanntes Markup führt zu leeren Feldern, nie zu einem Abbruch."""
    html = html_get(url)
    if not html: return {}
    out = {}
    # Foto: og:image bevorzugt, sonst erstes Bild mit sprechendem Pfad
    m = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)', html, re.I)
    if not m:
        m = re.search(r'<img[^>]+src=["\']([^"\']*(?:portrait|profil|mdb|person|mitglied|foto)[^"\']*\.(?:jpg|jpeg|png|webp))', html, re.I)
    if m: out["photo"] = abs_url(base, m.group(1))
    m = re.search(r'mailto:([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})', html)
    if m: out["email"] = m.group(1); out["email_verified"] = True
    m = re.search(r'(?:Telefon|Tel\.?|Phone)[^0-9+]{0,20}((?:\+?\d[\d\s/()\-]{7,}\d))', strip_tags(html))
    if m: out["phone"] = re.sub(r"\s{2,}", " ", m.group(1)).strip()
    m = re.search(r'(Platz der Republik[^<]{0,60}|Leipziger Stra(?:ß|ss)e[^<]{0,40}|Rue Wiertz[^<]{0,40})', html)
    if m: out["address"] = strip_tags(m.group(1))
    return out

def enrich_people(people, base_of):
    """Reichert höchstens ENRICH_PER_RUN Profile an, älteste zuerst."""
    cache = load_cache()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=ENRICH_AFTER_DAYS)).isoformat()
    todo = [p for p in people if p.get("link", "").startswith("http")
            and cache.get(p["id"], {}).get("ts", "") < cutoff]
    todo.sort(key=lambda p: cache.get(p["id"], {}).get("ts", ""))
    todo = todo[:ENRICH_PER_RUN]
    # bereits Bekanntes aus dem Cache sofort einspielen
    for p in people:
        c = cache.get(p["id"], {}).get("data") or {}
        for k, v in c.items():
            if v: p[k] = v
    if not todo:
        print(f"  Anreicherung: nichts fällig (alle < {ENRICH_AFTER_DAYS} Tage alt)"); return
    print(f"  Anreicherung: {len(todo)} Profile (von {len(people)})")
    done = 0
    for p in todo:
        if out_of_time(): budget_note("Profil-Anreicherung"); break
        data = scrape_profile(p["link"], base_of(p))
        if data:
            for k, v in data.items():
                if v: p[k] = v
            done += 1
        cache[p["id"]] = {"ts": datetime.now(timezone.utc).isoformat(), "data": data}
        time.sleep(0.6)      # freundlich zu den Servern
    save_cache(cache)
    print(f"  → {done}/{len(todo)} Profile ergänzt (Rest folgt im nächsten Lauf)")

# ─────────────────────────────────────────────────────────────
# BUNDESRAT – Mitglieder (Länderregierungen)
# ─────────────────────────────────────────────────────────────
BR_LIST = "https://www.bundesrat.de/DE/bundesrat/mitglieder/mitglieder-node.html"
BR_BASE = "https://www.bundesrat.de"
BR_LAND = {"bw":"Baden-Württemberg","by":"Bayern","be":"Berlin","bb":"Brandenburg","hb":"Bremen","hh":"Hamburg",
           "he":"Hessen","mv":"Mecklenburg-Vorpommern","ni":"Niedersachsen","nw":"Nordrhein-Westfalen",
           "rp":"Rheinland-Pfalz","sl":"Saarland","sn":"Sachsen","st":"Sachsen-Anhalt","sh":"Schleswig-Holstein","th":"Thüringen"}

def fetch_bundesrat():
    print("── Bundesrat (Mitglieder) ──")
    html = html_get(BR_LIST)
    if not html:
        print("  Mitgliederseite nicht erreichbar – übersprungen"); return [], []
    # Profillinks: /SharedDocs/personen/DE/laender/<land>/<name>.html
    links = {}
    for m in re.finditer(r'href="([^"]*?/SharedDocs/personen/DE/laender/([a-z]{2})/([^"/]+?)\.html[^"]*)"', html, re.I):
        url = abs_url(BR_BASE, m.group(1).split("?")[0])
        links[url] = m.group(2)
    print(f"  {len(links)} Mitgliederprofile gefunden")
    people = []
    for url, land in links.items():
        slug = url.rsplit("/", 1)[-1].replace(".html", "")
        parts = slug.split("-")
        last = parts[0].replace("_", " ").title()
        first = " ".join(parts[1:]).replace("_", " ").title() if len(parts) > 1 else ""
        people.append({
            "id": "br-" + land + "-" + slug, "parliament": "br", "name": (first + " " + last).strip(),
            "first_name": first, "last_name": last, "party": "", "constituency": BR_LAND.get(land, land),
            "mandate_type": "Mitglied des Bundesrates", "email": "", "email_verified": False, "phone": "",
            "address": "Bundesrat, Leipziger Straße 3-4, 10117 Berlin", "link": url, "photo": "",
            "committees": [], "speeches": [], "land": land,
        })
    comm = [{"id": "br-l-" + k, "parliament": "br", "name": "Landesvertretung " + v, "topics": [], "link": "",
             "members": [{"id": p["id"], "role": "Mitglied"} for p in people if p.get("land") == k]}
            for k, v in BR_LAND.items()]
    comm = [c for c in comm if c["members"]]
    for c in comm:
        for m in c["members"]:
            next(p for p in people if p["id"] == m["id"])["committees"].append({"id": c["id"], "name": c["name"], "role": "Mitglied"})
    return people, comm

# ─────────────────────────────────────────────────────────────
# BUNDESTAG – abgeordnetenwatch
# ─────────────────────────────────────────────────────────────
def aw_all(path, **params):
    """Paginiert über die abgeordnetenwatch-API (range_start/range_end)."""
    out, start, page = [], 0, 500
    while not out_of_time():
        p = dict(params); p.update({"range_start": start, "range_end": page})
        d = get_json(f"{ENDPOINTS['aw']}/{path}", p)
        if not d or "data" not in d: break
        out.extend(d["data"])
        total = (d.get("meta", {}).get("result", {}) or {}).get("total", 0)
        start += page
        if start >= total or not d["data"]: break
        time.sleep(0.25)
    return out

LANDTAG_LABELS = {"bw":"Baden-Württemberg","by":"Bayern","be":"Berlin","bb":"Brandenburg","hb":"Bremen",
    "hh":"Hamburg","he":"Hessen","mv":"Mecklenburg-Vorpommern","ni":"Niedersachsen","nw":"Nordrhein-Westfalen",
    "rp":"Rheinland-Pfalz","sl":"Saarland","sn":"Sachsen","st":"Sachsen-Anhalt","sh":"Schleswig-Holstein","th":"Thüringen"}

def fetch_landtage():
    """Landtage über dieselbe abgeordnetenwatch-Struktur wie der Bundestag.
    Pro Lauf nur LANDTAGE_PER_RUN Stück (rotierend) – nach wenigen Läufen sind alle drin.
    Bereits geholte Landtage kommen aus landtage_cache.json, damit people.json vollständig bleibt."""
    print("── Landtage (abgeordnetenwatch) ──")
    cache = {}
    try: cache = json.load(open("landtage_cache.json", encoding="utf-8"))
    except Exception: pass
    codes = list(LANDTAG_LABELS)
    start = int(cache.get("_next", 0)) % len(codes)
    todo = [codes[(start + i) % len(codes)] for i in range(LANDTAGE_PER_RUN)]
    print(f"  Diesmal: {', '.join(LANDTAG_LABELS[c] for c in todo)}")
    parls = aw_all("parliaments") if not out_of_time() else []
    people, comms = [], []
    for code in todo:
        if out_of_time(): budget_note("Landtage"); break
        label = LANDTAG_LABELS[code]
        pl = next((p for p in parls if p.get("label", "").strip().lower() == label.lower()), None)
        if not pl:
            print(f"  {label}: nicht gefunden"); continue
        ppl, cms = fetch_parliament(pl, prefix="lt-" + code, parliament_id="lt-" + code,
                                    label=label, address=f"Landtag {label}", memberships=False)
        print(f"  {label}: {len(ppl)} Abgeordnete")
        if ppl: cache[code] = {"people": ppl, "comms": cms, "ts": datetime.now(timezone.utc).isoformat()}
        time.sleep(0.3)
    cache["_next"] = (start + LANDTAGE_PER_RUN) % len(codes)
    _atomic_dump(cache, "landtage_cache.json")
    for code in codes:                                  # alle bekannten Landtage einspielen
        entry = cache.get(code)
        if isinstance(entry, dict):
            people += entry.get("people", []); comms += entry.get("comms", [])
    print(f"  Gesamt aus Cache + Lauf: {len(people)} Abgeordnete aus "
          f"{sum(1 for c in codes if isinstance(cache.get(c), dict))}/16 Landtagen")
    return people, comms

def fetch_parliament(parliament, prefix, parliament_id, label, address, memberships=True):
    """Gemeinsame Logik für Bundestag und Landtage."""
    periods = aw_all("parliament-periods", parliament=parliament["id"], type="legislature",
                     sort_by="id", sort_direction="desc")
    if not periods: return [], []
    period = periods[0]
    mandates = aw_all("candidacies-mandates", parliament_period=period["id"], type="mandate")
    people = {}
    for m in mandates:
        pol = m.get("politician") or {}
        if not pol.get("id"): continue
        name = pol.get("label", "")
        parts = name.split(" ")
        first, last = (parts[0], " ".join(parts[1:])) if len(parts) > 1 else ("", name)
        frac = ((m.get("fraction_membership") or [{}])[0].get("fraction") or {}).get("label", "")
        frac = re.sub(r"\s*\(.*?\)\s*$", "", frac)
        ed = m.get("electoral_data") or {}
        pid = f"{prefix}-{pol['id']}"
        people[pid] = {
            "id": pid, "parliament": parliament_id, "parliament_label": label, "name": name,
            "first_name": first, "last_name": last, "party": frac,
            "constituency": (ed.get("constituency") or {}).get("label", ""),
            "mandate_type": ed.get("mandate_won", ""),
            "email": "", "email_verified": False, "phone": "", "address": address,
            "link": pol.get("abgeordnetenwatch_url") or f"https://www.abgeordnetenwatch.de/profile/{pol['id']}",
            "photo": "", "committees": [], "speeches": [],
            "_mandate_id": m.get("id"), "_norm": norm_name(name),
        }
    comms = []
    if memberships:
        raw = aw_all("committees", legislature=period["id"])
        m2p = {v["_mandate_id"]: k for k, v in people.items()}
        for c in raw:
            cid = f"{prefix}-c{c['id']}"
            comm = {"id": cid, "parliament": parliament_id, "name": c.get("label", ""),
                    "topics": [t.get("label") for t in (c.get("field_topics") or [])],
                    "link": c.get("abgeordnetenwatch_url", ""), "members": []}
            if out_of_time():
                budget_note("Ausschuss-Mitgliedschaften"); break
            for mm in aw_all("committee-memberships", committee=c["id"]):
                pid = m2p.get((mm.get("candidacy_mandate") or {}).get("id"))
                if not pid: continue
                role = mm.get("committee_role", "")
                comm["members"].append({"id": pid, "role": role})
                people[pid]["committees"].append({"id": cid, "name": comm["name"], "role": role})
            comms.append(comm)
            time.sleep(0.2)
    return list(people.values()), comms

# ─────────────────────────────────────────────────────────────
# US-KONGRESS – unitedstates/congress-legislators (CC0)
# ─────────────────────────────────────────────────────────────
US_STATES = {"AL":"Alabama","AK":"Alaska","AZ":"Arizona","AR":"Arkansas","CA":"California","CO":"Colorado",
 "CT":"Connecticut","DE":"Delaware","FL":"Florida","GA":"Georgia","HI":"Hawaii","ID":"Idaho","IL":"Illinois",
 "IN":"Indiana","IA":"Iowa","KS":"Kansas","KY":"Kentucky","LA":"Louisiana","ME":"Maine","MD":"Maryland",
 "MA":"Massachusetts","MI":"Michigan","MN":"Minnesota","MS":"Mississippi","MO":"Missouri","MT":"Montana",
 "NE":"Nebraska","NV":"Nevada","NH":"New Hampshire","NJ":"New Jersey","NM":"New Mexico","NY":"New York",
 "NC":"North Carolina","ND":"North Dakota","OH":"Ohio","OK":"Oklahoma","OR":"Oregon","PA":"Pennsylvania",
 "RI":"Rhode Island","SC":"South Carolina","SD":"South Dakota","TN":"Tennessee","TX":"Texas","UT":"Utah",
 "VT":"Vermont","VA":"Virginia","WA":"Washington","WV":"West Virginia","WI":"Wisconsin","WY":"Wyoming",
 "DC":"District of Columbia","PR":"Puerto Rico","GU":"Guam","VI":"Virgin Islands","AS":"American Samoa","MP":"Northern Mariana Islands"}

def fetch_us_congress():
    print("── US-Kongress (congress-legislators) ──")
    legs = get_json(f"{ENDPOINTS['us']}/legislators-current.json") or []
    if not legs:
        print("  keine Daten"); return [], []
    comms_raw = get_json(f"{ENDPOINTS['us']}/committees-current.json") or []
    memb = get_json(f"{ENDPOINTS['us']}/committee-membership-current.json") or {}
    print(f"  {len(legs)} Abgeordnete, {len(comms_raw)} Ausschüsse")

    comm_by_id, comms = {}, []
    for c in comms_raw:
        cid = "us-c" + str(c.get("thomas_id", ""))
        comm = {"id": cid, "parliament": "us", "name": c.get("name", ""),
                "topics": [], "link": c.get("url", ""), "members": [],
                "chamber": c.get("type", ""),
                "subcommittees": [sc.get("name", "") for sc in (c.get("subcommittees") or [])]}
        comm_by_id[c.get("thomas_id", "")] = comm
        comms.append(comm)

    by_bioguide = {}
    people = []
    for L in legs:
        t = (L.get("terms") or [{}])[-1]
        bio = L.get("id", {}).get("bioguide", "")
        nm = L.get("name", {})
        name = nm.get("official_full") or f"{nm.get('first','')} {nm.get('last','')}".strip()
        chamber = "us-senate" if t.get("type") == "sen" else "us-house"
        state = t.get("state", "")
        district = t.get("district")
        p = {"id": "us-" + bio, "parliament": "us", "parliament_label": "US-Kongress",
             "chamber": chamber, "name": name,
             "first_name": nm.get("first", ""), "last_name": nm.get("last", ""),
             "party": t.get("party", ""),
             "constituency": US_STATES.get(state, state) + (f" – District {district}" if district is not None else ""),
             "state": state,
             "mandate_type": "Senator" if t.get("type") == "sen" else "Representative",
             "email": "", "email_verified": False, "phone": t.get("phone", "") or "",
             "address": t.get("address", "") or t.get("office", "") or "US Capitol, Washington DC",
             "link": t.get("url", "") or f"https://bioguide.congress.gov/search/bio/{bio}",
             "contact_form": t.get("contact_form", ""),
             "photo": f"https://unitedstates.github.io/images/congress/225x275/{bio}.jpg",
             "committees": [], "speeches": [], "wikipedia": L.get("id", {}).get("wikipedia", "")}
        by_bioguide[bio] = p
        people.append(p)

    for tid, members in (memb or {}).items():
        base = tid[:4]
        comm = comm_by_id.get(tid) or comm_by_id.get(base)
        if not comm: continue
        for m in members:
            p = by_bioguide.get(m.get("bioguide", ""))
            if not p: continue
            role = m.get("title", "") or ("Vorsitz" if m.get("rank") == 1 and m.get("party") == "majority" else "Mitglied")
            comm["members"].append({"id": p["id"], "role": role})
            if not any(c["id"] == comm["id"] for c in p["committees"]):
                p["committees"].append({"id": comm["id"], "name": comm["name"], "role": role})
    print(f"  {sum(len(c['members']) for c in comms)} Mitgliedschaften")
    return people, comms

# ─────────────────────────────────────────────────────────────
# ÄNDERUNGSERKENNUNG
# Vergleicht den neuen Stand mit der letzten people.json: Wer ist neu,
# wer fehlt, wer hat Fraktion oder Ausschüsse gewechselt.
# ─────────────────────────────────────────────────────────────
def diff_people(new_people):
    try:
        old = json.load(open("people.json", encoding="utf-8")).get("people", [])
    except Exception:
        print("  (kein Vorstand zum Vergleichen – erster Lauf)")
        return {"added": [], "removed": [], "changed": [], "checked": datetime.now(timezone.utc).isoformat()}
    o = {p["id"]: p for p in old}
    n = {p["id"]: p for p in new_people}
    # Parlamente, die in diesem Lauf gar nicht geholt wurden, nicht als "weg" melden
    live_parls = {p["parliament"] for p in new_people}
    added   = [{"id": i, "name": n[i]["name"], "parliament": n[i]["parliament"],
                "party": n[i].get("party", "")} for i in n if i not in o]
    removed = [{"id": i, "name": o[i]["name"], "parliament": o[i]["parliament"],
                "party": o[i].get("party", "")} for i in o
               if i not in n and o[i].get("parliament") in live_parls]
    changed = []
    for i in n:
        if i not in o: continue
        a, b = o[i], n[i]
        fields = []
        if (a.get("party") or "") != (b.get("party") or "") and b.get("party"):
            fields.append({"feld": "Fraktion", "vorher": a.get("party", ""), "jetzt": b["party"]})
        ca = {c["name"] for c in a.get("committees") or []}
        cb = {c["name"] for c in b.get("committees") or []}
        if cb and ca != cb:
            if cb - ca: fields.append({"feld": "Ausschuss neu", "jetzt": ", ".join(sorted(cb - ca))})
            if ca - cb: fields.append({"feld": "Ausschuss weg", "vorher": ", ".join(sorted(ca - cb))})
        if (a.get("constituency") or "") != (b.get("constituency") or "") and b.get("constituency"):
            fields.append({"feld": "Wahlkreis", "vorher": a.get("constituency", ""), "jetzt": b["constituency"]})
        if fields:
            changed.append({"id": i, "name": b["name"], "parliament": b["parliament"], "aenderungen": fields})
    print(f"── Änderungen ggü. letztem Stand ──")
    print(f"  neu: {len(added)} · nicht mehr enthalten: {len(removed)} · verändert: {len(changed)}")
    for x in added[:8]:   print(f"    + {x['name']} ({x['parliament']}, {x['party']})")
    for x in removed[:8]: print(f"    − {x['name']} ({x['parliament']})")
    for x in changed[:8]:
        first = x["aenderungen"][0]
        print(f"    ~ {x['name']}: {first['feld']} {first.get('vorher','')} → {first.get('jetzt','')}".rstrip())
    if len(added) + len(removed) + len(changed) > 24:
        print("    … weitere in people.json unter \"changes\"")
    return {"added": added, "removed": removed, "changed": changed,
            "checked": datetime.now(timezone.utc).isoformat()}

# ─────────────────────────────────────────────────────────────
# WIKIPEDIA – Kurzbiografie als Hintergrund (inkrementell wie die Profile)
# ─────────────────────────────────────────────────────────────
def wiki_summary(title):
    d = get_json(f"{ENDPOINTS['wiki']}/{quote(title.replace(' ', '_'))}")
    if not d or d.get("type") == "disambiguation": return {}
    return {"wiki_extract": (d.get("extract") or "")[:600],
            "wiki_url": (d.get("content_urls", {}).get("desktop", {}) or {}).get("page", ""),
            "wiki_thumb": (d.get("thumbnail") or {}).get("source", "")}

def enrich_wikipedia(people, per_run=None):
    """Ergänzt Kurzbiografien. Nutzt denselben Cache-Rhythmus wie die Profil-Anreicherung."""
    per_run = per_run or ENRICH_PER_RUN
    cache = load_cache()
    todo = [p for p in people if not cache.get("w:" + p["id"])][:per_run]
    for p in people:
        c = cache.get("w:" + p["id"], {}).get("data") or {}
        for k, v in c.items():
            if v: p[k] = v
    if not todo:
        print("  Wikipedia: nichts fällig"); return
    print(f"  Wikipedia: {len(todo)} Kurzbiografien")
    for p in todo:
        if out_of_time(): budget_note("Wikipedia"); break
        data = wiki_summary(p.get("wikipedia") or p["name"])
        for k, v in data.items():
            if v: p[k] = v
        cache["w:" + p["id"]] = {"ts": datetime.now(timezone.utc).isoformat(), "data": data}
        time.sleep(0.4)
    save_cache(cache)

def fetch_bundestag():
    print("── Bundestag (abgeordnetenwatch) ──")
    parls = aw_all("parliaments")
    bt = next((p for p in parls if "Bundestag" in p.get("label", "")), None)
    if not bt:
        print("  Parlament 'Bundestag' nicht gefunden – ENDPOINTS['aw'] prüfen"); return [], []
    periods = aw_all("parliament-periods", parliament=bt["id"], type="legislature", sort_by="id", sort_direction="desc")
    if not periods:
        print("  keine Wahlperiode"); return [], []
    period = periods[0]
    print(f"  Wahlperiode: {period.get('label')}")

    mandates = aw_all("candidacies-mandates", parliament_period=period["id"], type="mandate")
    print(f"  {len(mandates)} Mandate")
    committees_raw = aw_all("committees", legislature=period["id"])
    print(f"  {len(committees_raw)} Ausschüsse")

    people = {}
    for m in mandates:
        pol = m.get("politician") or {}
        if not pol.get("id"): continue
        label = pol.get("label", "")
        parts = label.split(" ")
        first, last = (parts[0], " ".join(parts[1:])) if len(parts) > 1 else ("", label)
        frac = ((m.get("fraction_membership") or [{}])[0].get("fraction") or {}).get("label", "")
        frac = re.sub(r"\s*\(.*?\)\s*$", "", frac)  # "SPD (Bundestag 2025 - 2029)" → "SPD"
        ed = m.get("electoral_data") or {}
        const = (ed.get("constituency") or {}).get("label", "")
        pid = f"bt-{pol['id']}"
        people[pid] = {
            "id": pid, "parliament": "bt", "name": label, "first_name": first, "last_name": last,
            "party": frac, "constituency": const, "mandate_type": ed.get("mandate_won", ""),
            "email": email_pattern(first, last, "bundestag.de"), "email_verified": False,
            "phone": "", "address": "Deutscher Bundestag, Platz der Republik 1, 11011 Berlin",
            "link": pol.get("abgeordnetenwatch_url") or f"https://www.abgeordnetenwatch.de/profile/{pol['id']}",
            "bundestag_link": "", "photo": "",
            "committees": [], "speeches": [], "_mandate_id": m.get("id"), "_norm": norm_name(label),
        }

    committees = []
    mandate_to_pid = {v["_mandate_id"]: k for k, v in people.items()}
    for c in committees_raw:
        cid = f"bt-c{c['id']}"
        comm = {"id": cid, "parliament": "bt", "name": c.get("label", ""), "topics": [t.get("label") for t in (c.get("field_topics") or [])],
                "link": c.get("abgeordnetenwatch_url", ""), "members": []}
        mem = aw_all("committee-memberships", committee=c["id"])
        for mm in mem:
            mid = (mm.get("candidacy_mandate") or {}).get("id")
            pid = mandate_to_pid.get(mid)
            if not pid: continue
            role = mm.get("committee_role", "")
            comm["members"].append({"id": pid, "role": role})
            people[pid]["committees"].append({"id": cid, "name": comm["name"], "role": role})
        committees.append(comm)
        time.sleep(0.3)
    print(f"  {sum(len(c['members']) for c in committees)} Mitgliedschaften")
    return list(people.values()), committees

# ─────────────────────────────────────────────────────────────
# BUNDESTAG – Reden aus DIP
# ─────────────────────────────────────────────────────────────
def fetch_speeches_dip(people, days=60):
    if not DIP_KEY:
        print("── Reden (DIP): kein DIP_API_KEY gesetzt – übersprungen ──"); return
    print("── Reden (DIP) ──")
    start = (date.today() - timedelta(days=days)).isoformat()
    by_norm = {p["_norm"]: p for p in people if p["parliament"] == "bt"}
    cursor, n = None, 0
    while True:
        params = {"f.aktivitaetsart": "Rede", "f.datum.start": start, "format": "json", "apikey": DIP_KEY}
        if cursor: params["cursor"] = cursor
        d = get_json(f"{ENDPOINTS['dip']}/aktivitaet", params)
        if not d or not d.get("documents"): break
        for a in d["documents"]:
            # titel: "Max Mustermann, SPD" – Name vor dem Komma
            name = norm_name((a.get("titel") or "").split(",")[0])
            p = by_norm.get(name)
            if not p:
                # Nachname-Fallback
                cand = [v for k, v in by_norm.items() if k.split()[-1:] == name.split()[-1:]] if name else []
                p = cand[0] if len(cand) == 1 else None
            if not p: continue
            fs = a.get("fundstelle") or {}
            topic = ", ".join(v.get("titel", "") for v in (a.get("vorgangsbezug") or [])[:1])
            p["speeches"].append({
                "date": a.get("datum", ""), "title": topic or a.get("titel", ""),
                "protocol": fs.get("dokumentnummer", ""), "link": fs.get("pdf_url") or "",
                "page": fs.get("seite", ""),
            })
            n += 1
        cursor = d.get("cursor")
        if not cursor or n > 8000: break
        time.sleep(0.5)
    for p in people:
        p["speeches"].sort(key=lambda s: s["date"], reverse=True)
        p["speeches"] = p["speeches"][:15]
    print(f"  {n} Reden zugeordnet")

# ─────────────────────────────────────────────────────────────
# EUROPÄISCHES PARLAMENT – Open Data Portal
# ─────────────────────────────────────────────────────────────
def ep_ld(path, **params):
    params.setdefault("format", "application/ld+json")
    d = get_json(f"{ENDPOINTS['ep']}/{path}", params)
    if not d: return []
    return d.get("data") or d.get("@graph") or []

# Alle 27 Mitgliedstaaten – Fallback-Scrape über die erweiterte MEP-Suche,
# falls das Open-Data-Portal nichts liefert.
EU_COUNTRIES = ["AT","BE","BG","HR","CY","CZ","DK","EE","FI","FR","DE","GR","HU","IE","IT",
                "LV","LT","LU","MT","NL","PL","PT","RO","SK","SI","ES","SE"]
EP_SEARCH = "https://www.europarl.europa.eu/meps/de/search/advanced?countryCode={}"

def fetch_ep_scrape():
    """Fallback: MEP-IDs je Land aus der Suchseite ziehen."""
    print("  Fallback: MEP-Suche je Land")
    people, seen = [], set()
    for cc in EU_COUNTRIES:
        if out_of_time(): budget_note("MEP-Suche"); break
        html = html_get(EP_SEARCH.format(cc))
        found = 0
        for m in re.finditer(r'/meps/de/(\d{4,7})/([A-ZÀ-Ýa-zà-ÿ_\-\+\.\'%]+)/home', html or ""):
            mid, slug = m.group(1), m.group(2)
            if mid in seen: continue
            seen.add(mid)
            name = re.sub(r"\s+", " ", slug.replace("+", " ").replace("_", " ")).strip().title()
            parts = name.split(" ")
            people.append({"id": "ep-" + mid, "parliament": "ep", "name": name,
                           "first_name": parts[0] if parts else "", "last_name": " ".join(parts[1:]),
                           "party": "", "constituency": cc, "mandate_type": "MEP",
                           "email": "", "email_verified": False, "phone": "",
                           "address": "European Parliament, Rue Wiertz 60, B-1047 Brussels",
                           "link": f"https://www.europarl.europa.eu/meps/de/{mid}",
                           "photo": f"https://www.europarl.europa.eu/mepphoto/{mid}.jpg",
                           "committees": [], "speeches": [], "_norm": norm_name(name)})
            found += 1
        print(f"    {cc}: {found}")
        time.sleep(0.4)
    return people, []

def fetch_ep():
    print("── Europäisches Parlament (Open Data) ──")
    meps = ep_ld("meps/show-current")
    if not meps:
        print("  Open-Data-Portal ohne Ergebnis")
        return fetch_ep_scrape()
    print(f"  {len(meps)} MEPs")
    bodies = ep_ld("corporate-bodies", **{"body-classification": "COMMITTEE_PARLIAMENTARY_STANDING"}) or ep_ld("corporate-bodies")
    comm_map = {}
    for b in bodies:
        bid = str(b.get("id") or b.get("identifier") or "")
        label = b.get("label") or b.get("prefLabel") or ""
        if isinstance(label, dict): label = label.get("en") or next(iter(label.values()), "")
        if bid and label and re.search(r"committee|ausschuss", str(label), re.I):
            comm_map[bid] = {"id": f"ep-c{bid}", "parliament": "ep", "name": str(label), "topics": [], "link": "", "members": []}
    print(f"  {len(comm_map)} Ausschüsse")

    people = []
    detail_budget = int(os.environ.get("EP_DETAILS_PER_RUN", "150"))
    details_done = 0
    # Detailprofile rotieren: wer schon dran war, steht im Cache und wird übersprungen.
    dcache = load_cache()
    def has_detail(mid): return bool(dcache.get("ep:" + str(mid)))
    for i, m in enumerate(meps):
        if LIMIT and i >= LIMIT: break
        mid = str(m.get("id") or m.get("identifier") or "").split("/")[-1]
        given, family = m.get("givenName", ""), m.get("familyName", "")
        name = m.get("label") or f"{given} {family}".strip()
        p = {"id": f"ep-{mid}", "parliament": "ep", "name": name, "first_name": given, "last_name": family,
             "party": "", "constituency": m.get("citizenship", "").split("/")[-1] if isinstance(m.get("citizenship"), str) else "",
             "mandate_type": "", "email": email_pattern(given, family, "europarl.europa.eu"), "email_verified": False,
             "phone": "", "address": "European Parliament, Rue Wiertz 60, B-1047 Brussels",
             "link": f"https://www.europarl.europa.eu/meps/de/{mid}",
             # EP-Portraits liegen unter einer festen URL je MEP-ID
             "photo": f"https://www.europarl.europa.eu/mepphoto/{mid}.jpg",
             "committees": [], "speeches": [], "_norm": norm_name(name)}
        # Detail (Fraktion, Ausschüsse, Kontakt) nur für einen Teil pro Lauf –
        # Grunddaten aller MEPs stehen bereits oben, die Details wachsen über die Läufe.
        det = {}
        cached = dcache.get("ep:" + str(mid), {}).get("data") or {}
        if cached:
            p.update({k: v for k, v in cached.items() if v})
        elif details_done < detail_budget and not out_of_time():
            d = ep_ld(f"meps/{mid}")
            det = d[0] if d else {}
            details_done += 1
        for ms in det.get("hasMembership") or []:
            org = str(ms.get("organization") or ms.get("membershipClassification") or "")
            oid = org.split("/")[-1]
            role = str(ms.get("role") or "").split("/")[-1]
            if oid in comm_map and not ms.get("memberDuring", {}).get("endDate"):
                comm_map[oid]["members"].append({"id": p["id"], "role": role})
                p["committees"].append({"id": comm_map[oid]["id"], "name": comm_map[oid]["name"], "role": role})
            elif "EU_POLITICAL_GROUP" in org.upper() or "POLITICAL_GROUP" in str(ms.get("membershipClassification", "")).upper():
                p["party"] = str(ms.get("organizationLabel") or oid)
        for cp in det.get("contactPoint") or []:
            em = cp.get("email") or cp.get("hasEmail")
            if em: p["email"], p["email_verified"] = str(em).replace("mailto:", ""), True
            if cp.get("telephone"): p["phone"] = str(cp["telephone"])
        if det.get("img") or det.get("image"): p["photo"] = str(det.get("img") or det.get("image"))
        if det:
            dcache["ep:" + str(mid)] = {"ts": datetime.now(timezone.utc).isoformat(),
                                        "data": {"party": p["party"], "email": p["email"],
                                                 "email_verified": p["email_verified"], "phone": p["phone"],
                                                 "committees": p["committees"]}}
        people.append(p)
        if det: time.sleep(0.2)
    save_cache(dcache)
    have = sum(1 for m in meps if has_detail(str(m.get("id") or m.get("identifier") or "").split("/")[-1]))
    print(f"  {len(people)} MEPs · {details_done} neue Detailprofile · "
          f"{have}/{len(meps)} vollständig ({max(0,-(-(len(meps)-have)//max(1,detail_budget)))} Läufe bis komplett)")
    return people, list(comm_map.values())

# ─────────────────────────────────────────────────────────────
# DIP – Gesetzgebung: Vorgänge, Drucksachen, Plenarprotokolle → dip.json
# API-Doku: https://dip.bundestag.de/über-dip/hilfe/api
# ─────────────────────────────────────────────────────────────
def dip_pages(resource, params, max_items=2000):
    """Blättert per cursor über eine DIP-Ressource."""
    out, cursor, guard = [], None, 0
    while guard < 40 and not out_of_time():
        guard += 1
        p = dict(params); p.update({"format": "json", "apikey": DIP_KEY})
        if cursor: p["cursor"] = cursor
        d = get_json(f"{ENDPOINTS['dip']}/{resource}", p)
        if not d or not d.get("documents"): break
        out.extend(d["documents"])
        new_cursor = d.get("cursor")
        if not new_cursor or new_cursor == cursor or len(out) >= max_items: break
        cursor = new_cursor
        time.sleep(0.4)
    return out[:max_items]

# Vorgangstyp → Thema (gleiche Schlüssel wie TOPIC_KW im Frontend)
DIP_TOPIC = {
    "gesundheit": ["gesundheit","kranken","pflege","arznei","klinik"],
    "digitales": ["digital","daten","cyber","telekommunikation","künstliche intelligenz","glasfaser","breitband","netzausbau","funk"],
    "wirtschaft": ["wirtschaft","industrie","handel","unternehmen","mittelstand"],
    "haushalt": ["haushalt","steuer","finanz","etat"],
    "verteidigung": ["verteidigung","bundeswehr","soldat","wehr"],
    "energie_klima": ["energie","klima","strom","wasserstoff","emission"],
    "aussenpolitik": ["auswärtig","außenpolitik","abkommen","völkerrecht"],
    "migration": ["migration","asyl","aufenthalt","staatsangehörigkeit"],
    "soziales": ["sozial","rente","arbeit","familie","wohngeld","bürgergeld"],
    "rechtsstaat": ["straf","zivil","gericht","grundgesetz","justiz","recht"],
    "umwelt": ["umwelt","natur","landwirtschaft","tier","wasser"],
    "mobilitaet": ["verkehr","bahn","straße","luftfahrt","schiff"],
}
# Gesetzgebungs-Pipeline: grobe Stufe für die Fortschrittsanzeige im Frontend
STAGES = ["eingebracht", "ausschuss", "bundestag", "bundesrat", "verkuendet", "abgeschlossen"]
def legislative_stage(beratungsstand, stationen):
    txt = (beratungsstand or "") + " " + " ".join(s.get("title", "") + " " + s.get("type", "") for s in stationen)
    t = txt.lower()
    if re.search(r"verkündet|inkrafttreten|gesetz(esbe)?schluss|abgeschlossen", t): return "verkuendet"
    if re.search(r"bundesrat|zustimmung des bundesrates|2\. durchgang", t): return "bundesrat"
    if re.search(r"3\. beratung|schlussabstimmung|angenommen|verabschiedet", t): return "bundestag"
    if re.search(r"ausschuss|beschlussempfehlung|anhörung", t): return "ausschuss"
    if re.search(r"eingebracht|zugeleitet|1\. beratung|überwiesen", t): return "eingebracht"
    return "eingebracht"

def dip_topic(text):
    t = (text or "").lower()
    best, score = "sonstig", 0
    for k, kws in DIP_TOPIC.items():
        sc = sum(1 for w in kws if w in t)
        if sc > score: best, score = k, sc
    return best

def fetch_dip(days=90):
    if not DIP_KEY:
        print("── DIP: kein Key (DIP_API_KEY oder DIP_PUBLIC_KEY setzen) – übersprungen ──"); return
    print("── DIP (Vorgänge, Drucksachen, Protokolle) ──")
    since = (date.today() - timedelta(days=days)).isoformat()

    vorgaenge = dip_pages("vorgang", {"f.aktualisiert.start": since}, max_items=3000)
    print(f"  {len(vorgaenge)} Vorgänge")
    procs = []
    for v in vorgaenge:
        titel = v.get("titel", "")
        verlauf = v.get("vorgangsverlauf") or []
        stand = verlauf[-1] if verlauf else {}
        stationen = [{"title": x.get("titel", ""), "date": x.get("datum", ""),
                      "body": x.get("zuordnung", ""), "type": x.get("vorgangspositionstyp", "")}
                     for x in verlauf][-12:]
        procs.append({
            "id": "v" + str(v.get("id", "")),
            "title": titel,
            "type": v.get("vorgangstyp", ""),
            "status": v.get("beratungsstand", "") or stand.get("titel", ""),
            "initiative": ", ".join(v.get("initiative", []) or [])[:200],
            "wp": v.get("wahlperiode", ""),
            "updated": v.get("aktualisiert", ""),
            "date": v.get("datum", ""),
            "sachgebiet": (v.get("sachgebiet") or [])[:3],
            "stage": legislative_stage(v.get("beratungsstand", ""), stationen),
            "stations": stationen,
            "gesta": v.get("gesta", ""),
            "zustimmungsbeduerftig": v.get("zustimmungsbeduerftigkeit", []),
            "topic": dip_topic(titel + " " + " ".join(v.get("sachgebiet") or [])),
            "link": f"https://dip.bundestag.de/vorgang/-/{v.get('id','')}",
            "kind": "procedure",
        })

    drucks = dip_pages("drucksache", {"f.datum.start": since, "f.zuordnung": "BT"}, max_items=1500)
    print(f"  {len(drucks)} Drucksachen")
    docs = [{
        "id": "d" + str(x.get("id", "")),
        "title": x.get("titel", ""),
        "number": x.get("dokumentnummer", ""),
        "type": x.get("drucksachetyp", "") or "Drucksache",
        "date": x.get("datum", ""),
        "wp": x.get("wahlperiode", ""),
        "authors": ", ".join(a.get("titel", "") for a in (x.get("urheber") or [])[:3]),
        "topic": dip_topic(x.get("titel", "")),
        "link": x.get("fundstelle", {}).get("pdf_url", "") or f"https://dip.bundestag.de/drucksache/-/{x.get('id','')}",
        "kind": "document",
    } for x in drucks]

    prot = dip_pages("plenarprotokoll", {"f.datum.start": since, "f.zuordnung": "BT"}, max_items=300)
    print(f"  {len(prot)} Plenarprotokolle")
    protocols = [{
        "id": "p" + str(x.get("id", "")),
        "title": x.get("titel", ""),
        "number": x.get("dokumentnummer", ""),
        "date": x.get("datum", ""),
        "wp": x.get("wahlperiode", ""),
        "link": x.get("fundstelle", {}).get("pdf_url", "") or f"https://dip.bundestag.de/plenarprotokoll/-/{x.get('id','')}",
        "kind": "transcript",
    } for x in prot]

    by_topic, by_stage = {}, {}
    for pr in procs:
        by_topic[pr["topic"]] = by_topic.get(pr["topic"], 0) + 1
        by_stage[pr["stage"]] = by_stage.get(pr["stage"], 0) + 1
    out = {
        "updated": datetime.now(timezone.utc).isoformat(),
        "source": "DIP – Dokumentations- und Informationssystem für Parlamentsmaterialien",
        "counts": {"procedures": len(procs), "documents": len(docs), "protocols": len(protocols)},
        "topics": by_topic, "stages": by_stage, "stage_order": STAGES,
        "procedures": procs, "documents": docs, "protocols": protocols,
    }
    if not (procs or docs or protocols):
        print("  ⚠ DIP lieferte nichts – dip.json bleibt unverändert."); return
    _atomic_dump(out, "dip.json")
    print(f"  → dip.json: {len(procs)} Vorgänge, {len(docs)} Drucksachen, {len(protocols)} Protokolle, {os.path.getsize('dip.json')//1024} KB")

# ─────────────────────────────────────────────────────────────
# KALENDER – calendar.ics aus documents.json (abonnierbar)
# ─────────────────────────────────────────────────────────────
MON = {"januar":1,"februar":2,"märz":3,"maerz":3,"april":4,"mai":5,"juni":6,"juli":7,"august":8,"september":9,"oktober":10,"november":11,"dezember":12}
def parse_event(doc):
    t = doc.get("title", "") + " " + doc.get("desc", "")
    d = None
    m = re.search(r"(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})", t)
    if m: d = date(int(m[3]), int(m[2]), int(m[1]))
    else:
        m = re.search(r"(\d{1,2})\.\s*(januar|februar|märz|maerz|april|mai|juni|juli|august|september|oktober|november|dezember)\s*(\d{4})?", t, re.I)
        if m: d = date(int(m[3]) if m[3] else date.today().year, MON[m[2].lower()], int(m[1]))
    if not d and doc.get("date"):
        try: d = datetime.fromisoformat(doc["date"]).astimezone(TZ).date()
        except Exception: pass
    if not d: return None
    tm = re.search(r"(\d{1,2})[:.](\d{2})\s*uhr|(\d{1,2})\s*uhr", t, re.I)
    start = None
    if tm:
        h, mi = (int(tm[1]), int(tm[2])) if tm[1] else (int(tm[3]), 0)
        if 0 <= h < 24: start = datetime(d.year, d.month, d.day, h, mi, tzinfo=TZ)
    return {"date": d, "start": start, "title": doc["title"], "link": doc.get("link", ""),
            "type": doc.get("type", ""), "source": doc.get("source", ""), "id": doc["id"]}

def ics_escape(s):
    return str(s).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")

def build_calendar_ics(docs_file="documents.json", out="calendar.ics"):
    print("── Kalender (calendar.ics) ──")
    try: docs = json.load(open(docs_file, encoding="utf-8")).get("documents", [])
    except Exception: print("  documents.json fehlt"); return
    events = [e for e in (parse_event(d) for d in docs if re.search(r"tagesordnung|sitzung|plenarprotokoll|ausschusstermin", d.get("type","")+d.get("title",""), re.I)) if e]
    if not events:
        print("  keine Termine gefunden – calendar.ics bleibt unverändert."); return
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Presseschau//Kalender//DE", "CALSCALE:GREGORIAN",
             "METHOD:PUBLISH", "X-WR-CALNAME:Presseschau – Sitzungen & Termine", "X-WR-TIMEZONE:Europe/Berlin"]
    for e in events:
        lines += ["BEGIN:VEVENT", f"UID:{e['id']}@presseschau", f"DTSTAMP:{now}"]
        if e["start"]:
            s = e["start"].astimezone(timezone.utc); en = s + timedelta(hours=2)
            lines += [f"DTSTART:{s.strftime('%Y%m%dT%H%M%SZ')}", f"DTEND:{en.strftime('%Y%m%dT%H%M%SZ')}"]
        else:
            lines += [f"DTSTART;VALUE=DATE:{e['date'].strftime('%Y%m%d')}", f"DTEND;VALUE=DATE:{(e['date']+timedelta(days=1)).strftime('%Y%m%d')}"]
        lines += [f"SUMMARY:{ics_escape(e['title'])}", f"DESCRIPTION:{ics_escape(e['source']+' · '+e['type']+'\n'+e['link'])}",
                  f"URL:{e['link']}", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    with open(out, "w", encoding="utf-8", newline="") as f:
        f.write("\r\n".join(lines) + "\r\n")
    print(f"  → {out}: {len(events)} Termine (Google/Apple: als URL abonnieren)")

# ─────────────────────────────────────────────────────────────
# NEWSLETTER-FEEDS → newsletters.json
# ─────────────────────────────────────────────────────────────
def clean_html(t):
    t = re.sub(r"<[^>]+>", " ", t or "")
    for a, b in (("&amp;","&"),("&lt;","<"),("&gt;",">"),("&quot;",'"'),("&#39;","'"),("&nbsp;"," ")): t = t.replace(a, b)
    return re.sub(r"\s+", " ", t).strip()

def fetch_newsletters():
    print("── Newsletter-Feeds ──")
    items = []
    for url, name, topic, pro in NEWSLETTER_FEEDS:
        if out_of_time(): budget_note("Newsletter"); break
        raw = get(url, accept="application/rss+xml, application/xml, text/xml")
        if not raw: print(f"  {name}: FAIL"); continue
        try: root = ET.fromstring(re.sub(rb"[^\x09\x0A\x0D\x20-\xFF]", b"", raw))
        except ET.ParseError: print(f"  {name}: PARSE ERR"); continue
        n = 0
        for it in (root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry"))[:20]:
            gt = lambda tag: (it.findtext(tag) or it.findtext("{http://www.w3.org/2005/Atom}"+tag) or "").strip()
            title, link = gt("title"), gt("link") or (it.find("{http://www.w3.org/2005/Atom}link").get("href", "") if it.find("{http://www.w3.org/2005/Atom}link") is not None else "")
            if not title: continue
            desc = clean_html(gt("description") or gt("summary") or it.findtext("{http://purl.org/rss/1.0/modules/content/}encoded") or "")[:1500]
            pub = gt("pubDate") or gt("published") or gt("updated")
            try:
                from email.utils import parsedate_to_datetime
                iso = parsedate_to_datetime(pub).astimezone(timezone.utc).isoformat() if pub and "," in pub else (datetime.fromisoformat(pub.replace("Z","+00:00")).isoformat() if pub else "")
            except Exception: iso = ""
            items.append({"id": uid(name, title), "source": name, "title": title, "link": link, "desc": desc,
                          "date": iso, "topic": topic, "pro": pro, "kind": "newsletter"})
            n += 1
        print(f"  {name}: {n}"); time.sleep(0.3)
    items.sort(key=lambda x: x["date"], reverse=True)
    if not items:
        print("  ⚠ Keine Newsletter-Einträge – newsletters.json bleibt unverändert."); return
    json.dump({"updated": datetime.now(timezone.utc).isoformat(), "count": len(items), "items": items},
              open("newsletters.json", "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print(f"  → newsletters.json: {len(items)}")

# ─────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────
# ABSTURZSICHERUNG
# Jede Quelle läuft gekapselt: fällt eine aus, wird das protokolliert und
# der Lauf geht weiter. So entsteht immer eine vollständige people.json –
# notfalls mit weniger Parlamenten, aber nie ein Abbruch.
# ─────────────────────────────────────────────────────────────
STAGE_ERRORS = []
def safe(label, fn, default):
    try:
        return fn()
    except Exception as e:
        STAGE_ERRORS.append((label, f"{type(e).__name__}: {e}"))
        print(f"  ⚠ {label} übersprungen – {type(e).__name__}: {e}")
        traceback.print_exc(limit=3)
        return default

def main():
    t0 = time.monotonic()
    print(f"[{datetime.now().isoformat()}] Presseschau Verzeichnis-Fetch "
          f"(Zeitbudget {TIME_BUDGET_MIN} Min)")
    bt_people, bt_comm = safe("Bundestag", fetch_bundestag, ([], []))
    safe("Reden (DIP)", lambda: fetch_speeches_dip(bt_people), None)
    br_people, br_comm = safe("Bundesrat", fetch_bundesrat, ([], []))
    lt_people, lt_comm = safe("Landtage", fetch_landtage, ([], []))
    ep_people, ep_comm = safe("Europäisches Parlament", fetch_ep, ([], []))
    us_people, us_comm = safe("US-Kongress", fetch_us_congress, ([], []))
    people = bt_people + br_people + lt_people + ep_people + us_people
    # Fotos/Kontakt inkrementell von den Biografie-Seiten nachladen
    base_of = lambda p: {"bt": "https://www.bundestag.de", "br": BR_BASE,
                         "ep": "https://www.europarl.europa.eu"}.get(p["parliament"], "")
    print("── Profil-Anreicherung (Foto, E-Mail, Telefon) ──")
    safe("Profil-Anreicherung",
         lambda: enrich_people([p for p in people if p["parliament"] in ("bt", "br", "ep")], base_of), None)
    safe("Wikipedia", lambda: enrich_wikipedia(people), None)
    for p in people:
        p.pop("_mandate_id", None); p.pop("_norm", None); p.pop("land", None)
    changes = safe("Änderungsvergleich", lambda: diff_people(people),
                   {"added": [], "removed": [], "changed": []})
    out = {"updated": datetime.now(timezone.utc).isoformat(),
           "changes": changes,
           "parliaments": [{"id": "bt", "label": "Bundestag", "count": len(bt_people)},
                           {"id": "br", "label": "Bundesrat", "count": len(br_people)},
                           {"id": "lt", "label": "Landtage", "count": len(lt_people)},
                           {"id": "ep", "label": "Europäisches Parlament", "count": len(ep_people)},
                           {"id": "us", "label": "US-Kongress", "count": len(us_people)}],
           "committees": bt_comm + br_comm + lt_comm + ep_comm + us_comm, "people": people,
           "note": "E-Mail-Adressen ohne email_verified=true folgen dem Adressmuster des Parlaments und sind unverifiziert."}
    # Bestehende people.json nie durch eine leere ersetzen
    if not people:
        print("⚠ Keine Personen geladen – vorhandene people.json bleibt unverändert.")
    else:
        prev = 0
        try: prev = len(json.load(open("people.json", encoding="utf-8")).get("people", []))
        except Exception: pass
        if prev and len(people) < prev * 0.5:
            print(f"⚠ Nur {len(people)} statt zuvor {prev} Personen – sieht nach Ausfall aus, "
                  f"people.json wird NICHT überschrieben.")
        else:
            _atomic_dump(out, "people.json")
    size = os.path.getsize("people.json") // 1024 if os.path.exists("people.json") else 0
    print(f"→ people.json: {len(people)} Personen, {len(out['committees'])} Ausschüsse, {size} KB")
    c = out.get("changes", {})
    if c.get("added") or c.get("removed") or c.get("changed"):
        print(f"   Änderungen: +{len(c['added'])} / -{len(c['removed'])} / ~{len(c['changed'])}")
    safe("DIP", fetch_dip, None)
    safe("Kalender", build_calendar_ics, None)
    safe("Newsletter", fetch_newsletters, None)

    print(f"\n⏱ Laufzeit: {(time.monotonic()-t0)/60:.1f} Min von {TIME_BUDGET_MIN} Min Budget")
    if STAGE_ERRORS:
        print(f"\n── {len(STAGE_ERRORS)} Bausteine mit Fehler (Lauf trotzdem abgeschlossen) ──")
        for label, err in STAGE_ERRORS:
            print(f"  {label}: {err}")
    else:
        print("\n✓ Alle Bausteine ohne Fehler.")

if __name__ == "__main__":
    main()
