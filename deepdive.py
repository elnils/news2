#!/usr/bin/env python3
"""
deepdive.py  –  schreibt deepdive.json

DEEP DIVES ZU AKTUELLEN DEBATTEN
Einmal am Tag ab 12 Uhr (dann haben sich die Debatten des Tages entfaltet)
drei bis vier Themen. Gewählt wird aus einem Katalog von über 40 Debatten
(deutsch und englisch) und aus neuen Debatten, die das Skript selbst aus
den Trends (Sachbegriffe vieler Häuser) und den KI-Top-Schlagzeilen erkennt.
Deep Dives der Vortage bleiben sieben Tage lesbar. Für jedes Thema wird
zusammengetragen:

  Meldungen      die wichtigsten der letzten 48 Stunden (viele Häuser zuerst)
  Zahlen         Sätze mit Zahlen aus diesen Meldungen – jede mit Quelle
  Verlauf        wie oft im Archiv darüber berichtet wurde, Woche für Woche
                 über ein halbes Jahr: neue Debatte oder Dauerthema?
  Parlament      Reden im Bundestag (wer, welche Fraktion), Abstimmungen,
                 Drucksachen und Urteile dazu
  Statistik      amtliche Zahlen über freie Schnittstellen ohne Schlüssel:
                 Eurostat (Inflation, Arbeitslosigkeit, Wachstum, Schulden,
                 Hauspreise, Asylanträge, Strompreise), EZB (Leitzins)
  Märkte         passende Kurse und ihr Jahresverlauf aus markets_archive
  Welt           internationale Berichterstattung und Tonlage (GDELT)

Daraus schreibt die KI eine Analyse: Kern der Debatte, Auswirkungen auf
Gesellschaft, Wirtschaft und Forschung/Bildung, wer profitiert und wer
verliert (Unternehmen, Gruppen, Parteien), Positionen der Parteien, die
Konfliktlinie nach der Cleavage-Theorie (Lipset/Rokkan und neuere Linien
wie Globalisierungsgewinner/-verlierer, GAL/TAN) und die Triggerpunkte nach
Mau, Lux und Westheuser (Arenen Oben/Unten, Innen/Außen, Wir/Sie,
Heute/Morgen; Auslöser Ungleichbehandlung, Normalitätsverstöße,
Entgrenzungsbefürchtungen, Verhaltenszumutungen).

OHNE FEHLER
Jede Zahl in der Analyse wird gegen die Belege geprüft (Meldungen,
Statistik, Kurse). Ein Satz mit einer Zahl, die in keinem Beleg steht, wird
gestrichen. Parteipositionen nur aus Reden und Meldungen – oder
ausdrücklich als "laut Wahlprogramm 2025" gekennzeichnet.
"""

import json
import math
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen, Request
from zoneinfo import ZoneInfo

import ki_zusammenfassungen as ki

OUT = "deepdive.json"
BERLIN = ZoneInfo("Europe/Berlin")
ANZAHL = int(os.environ.get("DD_ANZAHL", "4"))
FORCE = os.environ.get("DD_FORCE") == "1"
STUNDE = int(os.environ.get("DD_STUNDE", "12"))      # frühestens ab dieser Stunde (deutsche Zeit)
TAGE_HISTORIE = int(os.environ.get("DD_TAGE", "7"))  # so lange bleiben Deep Dives der Vortage lesbar
UA = {"User-Agent": "Mozilla/5.0 (compatible; Presseschau/1.0)"}
DATEIEN = ["articles.json", "eu_articles.json", "bundestag_articles.json", "laender_articles.json", "us_articles.json"]

# Debattenthemen: Erkennungsmuster, passende Statistik und Kurse.
def _t(i, name, muster, statistik=(), kurse=()):
    return {"id": i, "name": name, "muster": muster, "statistik": list(statistik), "kurse": list(kurse)}


# Debattenkatalog – deutsche und englische Begriffe. Neue Debatten, die hier
# fehlen, erkennt themen_waehlen() zusätzlich aus Trends und Top-Meldungen.
THEMEN = [
    # Soziales und Gesundheit
    _t("pflege", "Pflegereform", r"pflege(reform|versicherung|beitrag|kasse|bedürftig|heim|kosten|eigenanteil)\w*|\bpflege\b|long-term care|nursing care"),
    _t("rente", "Rente", r"\brente\w*|rentenpaket|rentenniveau|rentenversicherung|haltelinie|aktivrente|frühstartrente|pension reform|pensions?\b"),
    _t("gesundheit", "Krankenkassen und Kliniken", r"krankenkass\w*|zusatzbeitrag|krankenhausreform|klinik(reform|sterben)|gesundheitsreform|\bgkv\b|health insurance|hospital reform"),
    _t("buergergeld", "Bürgergeld und Grundsicherung", r"bürgergeld|grundsicherung|sozialleistung\w*|totalverweiger\w*|welfare benefits?"),
    _t("mindestlohn", "Mindestlohn", r"mindestlohn\w*|minimum wage", ["arbeitslos"]),
    _t("familie", "Familie und Kinder", r"kindergeld|elterngeld|kindergrundsicherung|kita-\w*|kitaplätze|child benefit|parental leave"),
    _t("bildung", "Bildung und Schulen", r"pisa|schulreform|lehrermangel|bildungsgipfel|startchancen|schulen\b.*(krise|mangel)|education reform"),
    _t("wohnen", "Wohnen und Mieten", r"mietpreis\w*|mieten\b|mietpreisbremse|wohnungsbau|wohnungsnot|immobilienpreis\w*|baugenehmigung\w*|housing crisis|rent control", ["hauspreise"]),
    # Wirtschaft und Finanzen
    _t("inflation", "Inflation und Preise", r"inflation\w*|teuerung|verbraucherpreise|preissteigerung|consumer prices", ["hvpi", "leitzins"], ["DE10Y", "EURUSD=X"]),
    _t("zinsen", "Zinsen und Notenbanken", r"leitzins\w*|zinsentscheid\w*|zinssenkung\w*|zinserhöhung\w*|\bezb\b|\bfed\b|interest rates?|rate (cut|hike)", ["leitzins", "hvpi"], ["DE10Y", "^TNX", "EURUSD=X"]),
    _t("konjunktur", "Konjunktur und Wachstum", r"konjunktur\w*|rezession|wirtschaftswachstum|bruttoinlandsprodukt|\bbip\b|wachstumsprognose|recession|gdp growth", ["bip", "arbeitslos"], ["^GDAXI"]),
    _t("arbeit", "Arbeitsmarkt", r"arbeitslos\w*|arbeitsmarkt|stellenabbau|jobabbau|fachkräfte\w*|unemployment|layoffs?", ["arbeitslos"]),
    _t("industrie", "Industrie und Standort", r"deindustrialisierung|standort deutschland|industriestandort|werksschließung\w*|produktionsverlagerung|industriestrompreis|industrial policy", ["bip"], ["^GDAXI", "EXV7.DE"]),
    _t("auto", "Autoindustrie und Verbrenner-Aus", r"autoindustrie|autobauer|verbrenner(-?aus|verbot)|e-auto\w*|elektroauto\w*|automobilzulieferer|car industry|combustion engine ban", [], ["EXV5.DE", "VOW3.DE", "MBG.DE"]),
    _t("haushalt", "Haushalt und Schuldenbremse", r"schuldenbremse|bundeshaushalt|haushaltsloch|sondervermögen|haushaltsentwurf|neuverschuldung|debt brake", ["schulden"], ["DE10Y"]),
    _t("steuern", "Steuern und Abgaben", r"steuerreform|steuersenkung\w*|steuererhöhung\w*|erbschaftsteuer|vermögensteuer|einkommensteuer|stromsteuer|tax cuts?|wealth tax"),
    _t("buerokratie", "Bürokratieabbau", r"bürokratieabbau|bürokratie\w*|lieferkettengesetz|berichtspflicht\w*|red tape"),
    _t("zoelle", "Zölle und Handel", r"\bzoll\w*|zölle|handelsstreit|handelskonflikt|handelsabkommen|mercosur|tariffs?|trade war", [], ["^GDAXI", "EXV5.DE"]),
    _t("banken", "Banken und Finanzmärkte", r"bankenkrise|bankenfusion|übernahmeangebot|commerzbank|unicredit|kapitalmarktunion|banking crisis|stock market crash", [], ["^GDAXI", "EXX5.DE"]),
    # Energie, Klima, Verkehr
    _t("energie", "Energiepreise und Energiewende", r"strompreis\w*|gaspreis\w*|energiepreis\w*|netzentgelt\w*|energiewende|heizungsgesetz|gebäudeenergiegesetz|energy prices", ["strompreis"], ["TTF=F", "DEBASE27"]),
    _t("klima", "Klimaschutz", r"klimaschutz\w*|klimaziel\w*|co2-preis|emissionshandel|klimageld|klimawandel|climate (policy|target|change)|net zero", [], ["EUA"]),
    _t("verkehr", "Verkehr und Bahn", r"deutschlandticket|deutsche bahn|bahnchaos|schienennetz|tempolimit|autobahn\w*|lkw-maut|rail network"),
    _t("landwirtschaft", "Landwirtschaft", r"landwirt\w*|bauernproteste?|agrardiesel|agrarpolitik|düngeverordnung|farmers? protests?"),
    # Sicherheit und Außenpolitik
    _t("wehr", "Wehrdienst und Verteidigung", r"wehrdienst\w*|wehrpflicht|bundeswehr|verteidigungsausgaben|aufrüstung|nato-ziel|conscription|defen[cs]e spending", [], ["DFEN.DE", "RHM.DE"]),
    _t("ukraine", "Krieg in der Ukraine", r"ukraine-krieg|krieg in der ukraine|waffenlieferung\w*|taurus|waffenstillstand|friedensverhandlung\w*|ukraine war|ceasefire", [], ["DFEN.DE", "TTF=F"]),
    _t("nahost", "Nahost", r"gaza\w*|israel\w*|hamas|hisbollah|hezbollah|westjordanland|iran\w*", [], ["BZ=F", "GC=F"]),
    _t("usa", "US-Politik", r"trump-regierung|weißes haus|white house|us-regierung|kongress|congress|supreme court|shutdown|midterms?|zwischenwahlen", [], ["^GSPC", "EURUSD=X"]),
    _t("china", "China", r"china\w*|peking|beijing|taiwan|chinesisch\w*|chinese|xi jinping", [], ["HG=F", "EURCNY=X"]),
    _t("eu", "EU-Politik", r"eu-kommission|von der leyen|eu-gipfel|europäischer rat|eu-haushalt|eu-erweiterung|european commission|eu summit"),
    _t("hybrid", "Sabotage, Drohnen, Spionage", r"sabotage|drohnen\w*|drone sightings?|spionage|spionag\w*|desinformation\w*|hybride?\w* (angriff|bedrohung)"),
    # Inneres, Demokratie, Recht
    _t("migration", "Migration und Asyl", r"migration\w*|asyl\w*|abschiebung\w*|zurückweisung\w*|grenzkontroll\w*|geflüchtete\w*|deportation\w*|asylum", ["asyl"]),
    _t("sicherheit", "Innere Sicherheit", r"messerangriff\w*|anschlag\w*|terror\w*|kriminalitätsstatistik|polizeigesetz|clan\w*|attack|terrorism"),
    _t("extremismus", "Extremismus und AfD", r"afd-verbot|verfassungsschutz|rechtsextrem\w*|linksextrem\w*|islamis\w*|brandmauer|far-right|extremism"),
    _t("demokratie", "Demokratie und Wahlen", r"wahlrecht\w*|wahlrechtsreform|landtagswahl\w*|bundestagswahl|koalitionsvertrag|koalitionsstreit|misstrauensvotum|election"),
    _t("justiz", "Justiz und Verfassung", r"bundesverfassungsgericht|karlsruhe|verfassungsklage|grundgesetzänderung|richterwahl|constitutional court"),
    _t("gleichstellung", "Gleichstellung und Gesellschaft", r"gleichstellung|gender\w*|selbstbestimmungsgesetz|paragraf 218|abtreibung\w*|diskriminierung|abortion"),
    _t("drogen", "Cannabis und Drogen", r"cannabis\w*|kiffen|drogenpolitik|legalisierung"),
    # Technik
    _t("ki", "Künstliche Intelligenz und Regulierung", r"künstliche intelligenz|\bki-\w+|\bki\b|ai act|openai|chatgpt|sprachmodell|artificial intelligence|\bai (regulation|safety|model)", [], ["SMH", "NVDA"]),
    _t("digital", "Plattformen und Social Media", r"social-media-verbot|social media ban|digital services act|plattformregulierung|tiktok|meta\b|x\.com|elon musk|jugendschutz online"),
    _t("datenschutz", "Datenschutz und Überwachung", r"chatkontrolle|vorratsdatenspeicherung|überwachung\w*|datenschutz\w*|palantir|surveillance|data protection"),
    _t("cyber", "Cybersicherheit", r"cyberangriff\w*|hackerangriff\w*|ransomware|datenleck\w*|cyberattack|data breach", [], ["HACK"]),
    _t("chips", "Chips und Halbleiter", r"halbleiter\w*|chipfabrik|chipindustrie|intel-werk|tsmc|nvidia|semiconductor\w*|chips act", [], ["SMH", "NVDA", "IFX.DE"]),
]
EXTRA = os.environ.get("DD_THEMEN", "")      # z. B. "Wärmepumpe:wärmepumpe\\w*;Tempolimit:tempolimit"

# Amtliche Statistik: freie Schnittstellen, JSON-stat (Eurostat) bzw. SDMX-JSON (EZB)
EUROSTAT = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/"
STATISTIK = {
    "hvpi": {"name": "Inflationsrate Deutschland (HVPI, Vorjahr in %)", "quelle": "Eurostat",
             "url": EUROSTAT + "prc_hicp_manr?" + urlencode({"geo": "DE", "coicop": "CP00", "unit": "RCH_A", "lastTimePeriod": 13})},
    "arbeitslos": {"name": "Arbeitslosenquote Deutschland (%)", "quelle": "Eurostat",
                   "url": EUROSTAT + "une_rt_m?" + urlencode({"geo": "DE", "s_adj": "SA", "age": "TOTAL", "sex": "T", "unit": "PC_ACT", "lastTimePeriod": 13})},
    "bip": {"name": "Wachstum Deutschland (BIP, Vorquartal in %)", "quelle": "Eurostat",
            "url": EUROSTAT + "namq_10_gdp?" + urlencode({"geo": "DE", "na_item": "B1GQ", "s_adj": "SCA", "unit": "CLV_PCH_PRE", "lastTimePeriod": 8})},
    "schulden": {"name": "Staatsschulden Deutschland (% des BIP)", "quelle": "Eurostat",
                 "url": EUROSTAT + "gov_10q_ggdebt?" + urlencode({"geo": "DE", "sector": "S13", "na_item": "GD", "unit": "PC_GDP", "lastTimePeriod": 8})},
    "hauspreise": {"name": "Hauspreisindex Deutschland (Vorjahr in %)", "quelle": "Eurostat",
                   "url": EUROSTAT + "prc_hpi_q?" + urlencode({"geo": "DE", "purchase": "TOTAL", "unit": "RCH_A", "lastTimePeriod": 8})},
    "asyl": {"name": "Asylerstanträge in Deutschland (Monat)", "quelle": "Eurostat",
             "url": EUROSTAT + "migr_asyappctzm?" + urlencode({"geo": "DE", "citizen": "TOTAL", "sex": "T", "age": "TOTAL", "asyl_app": "NASY_APP", "unit": "PER", "lastTimePeriod": 13})},
    "strompreis": {"name": "Strompreis Haushalte Deutschland (EUR/kWh, inkl. Steuern)", "quelle": "Eurostat",
                   "url": EUROSTAT + "nrg_pc_204?" + urlencode({"geo": "DE", "nrg_cons": "KWH2500-4999", "tax": "I_TAX", "currency": "EUR", "unit": "KWH", "lastTimePeriod": 6})},
    "leitzins": {"name": "EZB-Einlagensatz (%)", "quelle": "EZB",
                 "url": "https://data-api.ecb.europa.eu/service/data/FM/B.U2.EUR.4F.KR.DFR.LEV?format=jsondata&lastNObservations=8"},
}
CLEAVAGE = ("Cleavage-Theorie (Lipset/Rokkan): Kapital/Arbeit, Zentrum/Peripherie, Stadt/Land, Staat/Kirche; "
            "neuere Linien: Generationen, Globalisierungsgewinner/-verlierer, GAL/TAN (libertär-grün vs. traditionell-autoritär).")
TRIGGER = ("Triggerpunkte (Mau/Lux/Westheuser 2023): Arenen Oben/Unten (Ungleichheit), Innen/Außen (Migration), "
           "Wir/Sie (Identität, Diversität), Heute/Morgen (Klima, Zukunft); Auslöser: Ungleichbehandlung, "
           "Normalitätsverstöße, Entgrenzungsbefürchtungen, Verhaltenszumutungen.")


def laden(pfad, leer):
    try:
        return json.load(open(pfad, encoding="utf-8"))
    except (OSError, ValueError):
        return leer


def zeit(a):
    try:
        return datetime.fromisoformat(str(a.get("date", "")).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def haus(a):
    return a.get("haus") or re.sub(r"\s+(eil|top|tech|business|world)$", "", (a.get("source") or "").lower())


# ── Statistik ────────────────────────────────────────────────────────
def jsonstat_reihe(d):
    """Zeitreihe aus Eurostat JSON-stat 2.0 (eine Reihe; alle anderen Dimensionen fest)."""
    zeit_kat = (((d.get("dimension") or {}).get("time") or {}).get("category") or {}).get("index") or {}
    werte = d.get("value") or {}
    reihe = []
    for per, idx in sorted(zeit_kat.items(), key=lambda kv: kv[1]):
        v = werte.get(str(idx)) if isinstance(werte, dict) else (werte[idx] if idx < len(werte) else None)
        if v is not None:
            reihe.append([per, v])
    return reihe


def sdmx_reihe(d):
    """Zeitreihe aus der SDMX-JSON-Antwort der EZB."""
    try:
        serie = next(iter(d["dataSets"][0]["series"].values()))
        zeiten = d["structure"]["dimensions"]["observation"][0]["values"]
        return [[zeiten[int(k)]["id"], v[0]] for k, v in sorted(serie["observations"].items(), key=lambda kv: int(kv[0]))]
    except (KeyError, IndexError, StopIteration, ValueError):
        return []


def statistik_holen(schluessel, cache):
    if schluessel in cache and cache[schluessel].get("datum") == datetime.now(BERLIN).strftime("%Y-%m-%d"):
        return cache[schluessel]
    s = STATISTIK[schluessel]
    try:
        with urlopen(Request(s["url"], headers=UA), timeout=30) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
        reihe = sdmx_reihe(d) if s["quelle"] == "EZB" else jsonstat_reihe(d)
    except (HTTPError, URLError, ValueError, TimeoutError, OSError) as e:
        print(f"  Statistik {schluessel}: nicht abrufbar ({type(e).__name__})")
        return cache.get(schluessel)
    if not reihe:
        return cache.get(schluessel)
    cache[schluessel] = {"name": s["name"], "quelle": s["quelle"], "reihe": reihe[-13:],
                         "datum": datetime.now(BERLIN).strftime("%Y-%m-%d")}
    return cache[schluessel]


# ── Themenwahl ───────────────────────────────────────────────────────
STOP = set("""regierung bundesregierung minister ministerin präsident kanzler partei parteien politik deutschland
berlin woche jahr jahre tag tage prozent milliarden millionen euro dollar menschen kritik streit debatte
nachrichten meldung live news update liveblog überblick bericht interview analyse kommentar""".split())


def dynamische_themen(arts, katalog):
    """Neue Debatten aus den Daten: Sachbegriffe der Meldungen (Feld "terms",
    wie im Trend-Reiter "Sachfragen") und die Schlagzeilen der KI-Top-Auswahl.
    Ein Begriff wird zur Debatte, wenn ihn mindestens fünf Häuser tragen und
    kein Katalogthema ihn schon abdeckt."""
    haeuser = {}
    for a in arts:
        for t in set((a.get("terms") or [])[:8]):
            k = str(t).strip()
            if 5 <= len(k) <= 40 and k.lower() not in STOP and not k.isdigit():
                haeuser.setdefault(k, set()).add(haus(a))
    # Schlagzeilen der KI-Top-Auswahl: Begriffe darin zählen doppelt
    top = laden("ki_meldungen.json", {}).get("schlagzeilen") or {}
    top_text = " ".join(it.get("schlagzeile", "") for r in (top.get("regionen") or {}).values()
                        for it in (r.get("items") or []) if isinstance(it, dict))
    kat = [re.compile(t["muster"], re.I) for t in katalog]
    out = []
    for k, h in sorted(haeuser.items(), key=lambda kv: -len(kv[1])):
        n = len(h) + (3 if k.lower() in top_text.lower() else 0)
        if n < 5 or any(m.search(k) for m in kat):
            continue
        out.append(_t("dyn-" + re.sub(r"[^a-z0-9]+", "-", k.lower()).strip("-"), k, re.escape(k)))
        if len(out) >= 25:
            break
    return out


def themen_waehlen(arts, archiv):
    jetzt = time.time()
    themen = list(THEMEN)
    for teil in [x for x in EXTRA.split(";") if ":" in x]:
        name, m = teil.split(":", 1)
        themen.append(_t(re.sub(r"\W+", "-", name.lower()), name.strip(), m))
    dyn = dynamische_themen(arts, themen)
    themen += dyn
    top = laden("ki_meldungen.json", {}).get("schlagzeilen") or {}
    top_zeilen = [it.get("schlagzeile", "") for r in (top.get("regionen") or {}).values()
                  for it in (r.get("items") or []) if isinstance(it, dict)]
    bewertet = []
    for t in themen:
        rx = re.compile(t["muster"], re.I)
        treffer = [a for a in arts if rx.search((a.get("title") or "") + " " + (a.get("desc") or "")[:300])]
        haeuser = {haus(a) for a in treffer}
        if len(haeuser) < 3:
            continue
        grenze_a, grenze_b = jetzt - 32 * 86400, jetzt - 2 * 86400
        alt = [a for a in archiv if grenze_a <= a["_t"] < grenze_b and rx.search(a["title"])]
        pro_tag = len({(haus(a), a["date"][:10]) for a in alt}) / 30
        heute_pro_tag = len({(haus(a), str(a.get("date", ""))[:10]) for a in treffer}) / 2
        anstieg = (heute_pro_tag + 0.5) / (pro_tag + 0.5)
        in_top = sum(1 for z in top_zeilen if rx.search(z))
        # Wurzel aus der Häuserzahl: Dauerthemen mit vielen Häusern (China,
        # Nahost) sollen neue, wachsende Debatten nicht verdrängen.
        wert = math.sqrt(len(haeuser)) * (1 + 1.5 * math.log2(max(anstieg, 0.5))) + 3 * in_top
        bewertet.append((wert, t, treffer, round(pro_tag, 2), round(anstieg, 2)))
    bewertet.sort(key=lambda x: x[0], reverse=True)
    # Vielfalt: ein Thema, dessen Meldungen zu mehr als der Hälfte schon zu
    # einem gewählten gehören, ist dieselbe Debatte unter anderem Namen.
    gewaehlt, belegt = [], set()
    for b in bewertet:
        ids = {a.get("id") for a in b[2]}
        if ids and len(ids & belegt) / len(ids) > 0.5:
            continue
        gewaehlt.append(b)
        belegt |= ids
        if len(gewaehlt) >= ANZAHL:
            break
    print(f"  Themen: {len(THEMEN)} im Katalog, {len(dyn)} neue aus Trends und Top-Meldungen, "
          f"{len(bewertet)} mit mindestens drei Häusern → {', '.join(b[1]['name'] for b in gewaehlt)}")
    return gewaehlt


# ── Belege je Thema ──────────────────────────────────────────────────
ZAHL_RE = re.compile(r"\d[\d.,]*\s*(?:%|prozent|euro|€|milliard\w*|million\w*|mrd\.?|mio\.?|punkte|jahre?n?|menschen|personen)", re.I)


def zahlensaetze(meldungen):
    out = []
    for i, a in enumerate(meldungen, 1):
        text = (a.get("title") or "") + ". " + re.sub(r"<[^>]+>", " ", a.get("desc") or "")
        for satz in re.split(r"(?<=[.!?])\s+", text):
            if ZAHL_RE.search(satz) and 20 <= len(satz) <= 320:
                out.append({"nr": i, "satz": satz.strip(), "quelle": a.get("source", "")})
    return out[:30]


def verlauf(archiv, rx):
    """Häuser-Nennungen je Kalenderwoche, letzte 26 Wochen."""
    jetzt = datetime.now(timezone.utc)
    wochen = {}
    for a in archiv:
        if a["_t"] >= (jetzt - timedelta(weeks=26)).timestamp() and rx.search(a["title"]):
            w = datetime.fromtimestamp(a["_t"], timezone.utc).strftime("%G-W%V")
            wochen.setdefault(w, set()).add((haus(a), a["date"][:10]))
    alle = []
    for i in range(25, -1, -1):
        w = (jetzt - timedelta(weeks=i)).strftime("%G-W%V")
        alle.append([w, len(wochen.get(w, ()))])
    return alle


def kurse_fuer(syms):
    mk = laden("markets.json", {})
    alle = {i.get("sym"): i for g in mk.get("groups") or [] for i in g.get("items") or []}
    out = []
    for s in syms:
        w = alle.get(s)
        if not w or not isinstance(w.get("last"), (int, float)):
            continue
        r = [x for x in ((w.get("series") or {}).get("1j") or []) if isinstance(x, (int, float))]
        jahr = round((r[-1] / r[0] - 1) * 100, 1) if len(r) > 1 and r[0] else None
        out.append({"sym": s, "name": w.get("n", s), "wert": w["last"], "einheit": w.get("unit", ""), "jahr_pct": jahr})
    return out


# ── KI-Analyse ───────────────────────────────────────────────────────
def frage(anbieter, auftrag):
    system = ("Du bist Politik- und Wirtschaftsanalyst einer seriösen Presseschau. Du schreibst präzise, sachlich, "
              "auf Deutsch, ohne Wertung und ohne Floskeln. Zahlen, Namen und Daten nur aus den gelieferten Belegen.")
    for _ in range(6):
        if not anbieter:
            return None
        n, url, key, modell = anbieter[0]
        koerper = json.dumps({"model": modell, "temperature": 0.2, "max_tokens": 1800,
                              "messages": [{"role": "system", "content": system}, {"role": "user", "content": auftrag}]}).encode("utf-8")
        try:
            with ki.ki_urlopen(n, url, koerper, {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}, 90) as r:
                j = json.loads(r.read().decode("utf-8", "replace"))
            return (j.get("choices") or [{}])[0].get("message", {}).get("content", "") or ""
        except HTTPError as e:
            print(f"  ---  {n} HTTP {e.code} – nächster Anbieter")
            anbieter.pop(0)
        except (URLError, ValueError, KeyError, TimeoutError, OSError) as e:
            print(f"  ---  {n} {type(e).__name__} – nächster Anbieter")
            anbieter.pop(0)
    return None


def zahlen_pruefen(text, belegtext):
    """Sätze mit Zahlen, die in keinem Beleg vorkommen, werden gestrichen."""
    if not isinstance(text, str):
        return text, 0
    norm = lambda z: z.replace(".", "").replace(",", ".").rstrip(".")
    belegt = {norm(z) for z in re.findall(r"\d[\d.,]*", belegtext)}
    gestrichen, behalten = 0, []
    for satz in re.split(r"(?<=[.!?])\s+", text):
        zahlen = [norm(z) for z in re.findall(r"\d[\d.,]*", satz) if not re.fullmatch(r"(19|20)\d\d", z)]
        if any(z not in belegt for z in zahlen if len(z.replace(".", "")) >= 1 and z not in ("1", "2", "3", "4", "5")):
            gestrichen += 1
            continue
        behalten.append(satz)
    return " ".join(behalten).strip(), gestrichen


def analyse(anbieter, t, meldungen, zahlen, stat, kurse_l, reden, abst, doks, verl, gdelt):
    liste = "\n".join(f"[{i}] {a.get('source','')} · {str(a.get('date',''))[:10]} · {a.get('title','')}"
                      + (f" – {re.sub('<[^>]+>', ' ', a.get('desc') or '')[:300]}" if a.get("desc") else "")
                      for i, a in enumerate(meldungen, 1))
    stat_txt = "\n".join(f"- {s['name']} ({s['quelle']}): " + ", ".join(f"{p} {v}" for p, v in s["reihe"][-6:]) for s in stat) or "- keine"
    kurs_txt = "\n".join(f"- {k['name']}: {k['wert']} {k['einheit']}" + (f", {k['jahr_pct']:+} % in einem Jahr" if k["jahr_pct"] is not None else "") for k in kurse_l) or "- keine"
    reden_txt = "\n".join(f"- {r['datum']} {r['name']} ({r['fraktion'] or r.get('rolle','')}): {r.get('betreff','')} – {(r.get('auszug') or '')[:220]}" for r in reden[:10]) or "- keine"
    abst_txt = "\n".join(f"- {v.get('datum','')} {v.get('vorlage') or v.get('gegenstand','')}: {v.get('ergebnis','')} (dafür: {', '.join(v.get('dafuer') or [])}; dagegen: {', '.join(v.get('dagegen') or [])})" for v in abst[:6]) or "- keine"
    dok_txt = "\n".join(f"- {d['titel']} ({d['quelle']}, {d['datum']})" for d in doks[:6]) or "- keine"
    verl_txt = ", ".join(f"{w}: {n}" for w, n in verl[-12:])
    auftrag = f"""Heute ist der {datetime.now(BERLIN).strftime('%d.%m.%Y')}. Thema: {t['name']}.
Deine Trainingsdaten sind veraltet – maßgeblich sind die Belege unten.

MELDUNGEN (nummeriert):
{liste}

AMTLICHE STATISTIK:
{stat_txt}

KURSE:
{kurs_txt}

BUNDESTAG – REDEN:
{reden_txt}

BUNDESTAG – ABSTIMMUNGEN:
{abst_txt}

DOKUMENTE UND URTEILE:
{dok_txt}

VERLAUF DER BERICHTERSTATTUNG (Häuser-Nennungen je Woche, älteste zuerst): {verl_txt}
INTERNATIONAL (GDELT): {gdelt or 'keine Daten'}

ANALYSERAHMEN:
{CLEAVAGE}
{TRIGGER}

AUFGABE: Schreibe einen Deep Dive. Beispiel für den Ton des Kerns: "Die Reform der Pflegeversicherung soll das
Finanzloch der Pflegekassen stopfen und die steigenden Eigenanteile begrenzen. Während Beitragszahler und
Arbeitgeber durch steigende Beiträge belastet werden, profitieren Pflegebedürftige in Heimen …"

REGELN
- Jede Zahl muss wörtlich in den Belegen stehen (Meldungen, Statistik, Kurse). Keine Zahl aus eigenem Wissen.
- Parteipositionen aus Reden, Abstimmungen oder Meldungen. Nur wenn dort nichts steht und du dir sicher bist,
  aus den Wahlprogrammen zur Bundestagswahl 2025 – dann "quelle": "Wahlprogramm 2025". Sonst weglassen.
- Konfliktlinie und Triggerpunkte: nur nennen, wenn sie wirklich passen, mit einem Satz Begründung.
- Nichts erfinden; wenn etwas unklar ist, gehört es zu den offenen Fragen.

Antworte NUR mit JSON:
{{"titel": "prägnant, höchstens 80 Zeichen",
 "kern": "4–5 Sätze: worum es geht, warum jetzt, wer belastet, wer entlastet, was diskutiert wird",
 "stichpunkte": ["3–5 Stichsätze, je höchstens 120 Zeichen"],
 "zahlen": [{{"wert": "3,4 %", "bedeutung": "…", "beleg": 3}}],
 "auswirkungen": {{"gesellschaft": "1–2 Sätze", "wirtschaft": "1–2 Sätze", "forschung_bildung": "1 Satz oder leer"}},
 "gewinner": [{{"wer": "Gruppe, Branche oder Unternehmen", "warum": "…"}}],
 "verlierer": [{{"wer": "…", "warum": "…"}}],
 "parteien": [{{"partei": "…", "position": "…", "quelle": "Rede|Abstimmung|Meldung|Wahlprogramm 2025"}}],
 "konfliktlinie": {{"cleavage": "…", "triggerpunkte": "…"}},
 "verlauf": "1 Satz: neue Debatte, wiederkehrend oder Dauerthema (aus dem Verlauf)",
 "offene_fragen": ["…"]}}"""
    roh = frage(anbieter, auftrag) or ""
    m = re.search(r"\{.*\}", roh, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except ValueError:
        return None


def main():
    heute = datetime.now(BERLIN).strftime("%Y-%m-%d")
    alt = laden(OUT, {})
    if not FORCE and alt.get("datum") == heute and alt.get("themen"):
        print("Deep Dive: heute schon erstellt.")
        return 0
    if datetime.now(BERLIN).hour < STUNDE and not FORCE:
        print(f"Deep Dive: vor {STUNDE} Uhr – Debatten entfalten sich erst im Lauf des Tages.")
        return 0
    jetzt = time.time()
    arts = [a for d in DATEIEN for a in (laden(d, {}).get("articles") or []) if jetzt - zeit(a) <= 48 * 3600]
    archiv = []
    idx = laden("archive/index.json", {})
    for m in sorted(idx.get("months") or [], key=lambda x: x.get("month", ""))[-7:]:
        for a in (laden(m.get("file") or f"archive/{m['month']}.json", {}).get("articles") or []):
            if a.get("title") and a.get("date"):
                archiv.append({"title": a["title"], "date": str(a["date"]), "_t": zeit(a), "source": a.get("source", ""), "haus": a.get("haus")})
    print(f"  Deep Dive: {len(arts)} aktuelle Meldungen, {len(archiv)} im Archiv")
    gewaehlt = themen_waehlen(arts, archiv)
    plenum = laden("plenum_votes.json", {})
    doks_alle = [{"titel": d.get("title", ""), "quelle": d.get("source", ""), "link": d.get("link", ""), "datum": str(d.get("date", ""))[:10]}
                 for d in (laden("documents.json", {}).get("documents") or []) if jetzt - zeit(d) <= 60 * 86400]
    for v in (laden("dokumente_text.json", {}).get("dokumente") or {}).values():
        if v.get("titel"):
            doks_alle.append({"titel": v["titel"], "quelle": "Gericht" if v.get("gericht") else "Dokument", "link": v.get("link", ""),
                              "datum": str(v.get("stand", ""))[:10], "kurz": v.get("kurz", "")})
    stat_cache = laden("statistik_cache.json", {})
    try:
        import vorausschau as vs
        gdelt_fn = vs.gdelt_volumen
    except Exception:
        gdelt_fn = None
    anbieter = [list(a) for a in ki.ANBIETER]
    themen_out = []
    for wert, t, treffer, pro_tag, anstieg in gewaehlt:
        rx = re.compile(t["muster"], re.I)
        # Meldungen: je Haus eine, viele Häuser zuerst
        gesehen, meldungen = set(), []
        for a in sorted(treffer, key=lambda a: (int(a.get("cluster") or 1), zeit(a)), reverse=True):
            h = haus(a)
            if h in gesehen:
                continue
            gesehen.add(h)
            meldungen.append(a)
            if len(meldungen) >= 12:
                break
        zahlen = zahlensaetze(meldungen)
        stat = [x for x in (statistik_holen(k, stat_cache) for k in t["statistik"]) if x]
        kurse_l = kurse_fuer(t["kurse"])
        reden = [r for r in plenum.get("reden") or [] if rx.search((r.get("betreff") or "") + " " + (r.get("auszug") or ""))]
        reden.sort(key=lambda r: r.get("datum", ""), reverse=True)
        abst = [v for v in plenum.get("abstimmungen") or [] if rx.search((v.get("vorlage") or "") + " " + (v.get("gegenstand") or "") + " " + (v.get("text") or "")[:400])]
        abst.sort(key=lambda v: v.get("datum", ""), reverse=True)
        doks = [d for d in doks_alle if rx.search(d["titel"] + " " + d.get("kurz", ""))][:6]
        verl = verlauf(archiv, rx)
        gd = None
        if gdelt_fn:
            try:
                g = gdelt_fn(t["name"] if t["id"] not in ("ki",) else "artificial intelligence regulation")
                gd = f"Berichterstattung weltweit ×{g['faktor']} gegen die Vorwoche" if g and g.get("faktor") else None
            except Exception:
                gd = None
        a = analyse(anbieter, t, meldungen, zahlen, stat, kurse_l, reden, abst, doks, verl, gd) if anbieter else None
        # Zahlenprüfung gegen alle Belege
        belegtext = " ".join((m.get("title") or "") + " " + (m.get("desc") or "") for m in meldungen) + " " + \
            json.dumps(stat, ensure_ascii=False) + " " + json.dumps(kurse_l, ensure_ascii=False) + " " + \
            " ".join((r.get("auszug") or "") for r in reden[:10])
        gestrichen = 0
        if a:
            for feld in ("kern", "verlauf"):
                a[feld], n = zahlen_pruefen(a.get(feld, ""), belegtext)
                gestrichen += n
            a["stichpunkte"] = [p for p in (a.get("stichpunkte") or []) if zahlen_pruefen(p, belegtext)[1] == 0]
            for k in ("gesellschaft", "wirtschaft", "forschung_bildung"):
                v, n = zahlen_pruefen((a.get("auswirkungen") or {}).get(k, ""), belegtext)
                a.setdefault("auswirkungen", {})[k] = v
                gestrichen += n
            a["zahlen"] = [z for z in (a.get("zahlen") or []) if zahlen_pruefen(str(z.get("wert", "")), belegtext)[1] == 0]
        eintrag = {"id": t["id"], "thema": t["name"], "analyse": a, "gestrichen": gestrichen,
                   "signal": {"haeuser": len({haus(x) for x in treffer}), "meldungen": len(treffer), "pro_tag_sonst": pro_tag, "anstieg": anstieg},
                   "meldungen": [{"id": m.get("id"), "titel": m.get("title", ""), "quelle": m.get("source", ""), "link": m.get("link", ""),
                                  "datum": str(m.get("date", ""))[:16]} for m in meldungen],
                   "zahlen_belege": zahlen[:12], "statistik": stat, "kurse": kurse_l, "verlauf": verl, "gdelt": gd,
                   "reden": [{k: r.get(k) for k in ("datum", "name", "fraktion", "betreff", "sitzung")} for r in reden[:8]],
                   "abstimmungen": [{k: v.get(k) for k in ("datum", "vorlage", "gegenstand", "ergebnis", "dafuer", "dagegen")} for v in abst[:5]],
                   "dokumente": doks}
        themen_out.append(eintrag)
        print(f"  + {t['name']}: {eintrag['signal']['haeuser']} Häuser, Anstieg ×{anstieg}, {len(reden)} Reden, "
              f"{len(stat)} Statistiken, {'Analyse' if a else 'ohne Analyse'}{f', {gestrichen} Sätze gestrichen' if gestrichen else ''}")
    json.dump(stat_cache, open("statistik_cache.json", "w", encoding="utf-8"), ensure_ascii=False)
    fertig = [x for x in themen_out if x["analyse"]]
    if themen_out and not fertig:
        print("  Kein KI-Anbieter hat geantwortet – Deep Dive bleibt beim alten Stand; nächster Volllauf versucht es erneut.")
        return 0
    # Vortage: der bisherige Stand wandert in die Historie (7 Tage), kompakt
    historie = [h for h in (alt.get("historie") or []) if h.get("datum") != heute]
    if alt.get("datum") and alt.get("datum") != heute and alt.get("themen"):
        historie.insert(0, {"datum": alt["datum"], "themen": [
            {k: x.get(k) for k in ("id", "thema", "analyse", "signal", "meldungen", "statistik", "verlauf", "reden", "dokumente")}
            for x in alt["themen"] if x.get("analyse")]})
    grenze_h = (datetime.now(BERLIN) - timedelta(days=TAGE_HISTORIE)).strftime("%Y-%m-%d")
    historie = [h for h in historie if h.get("datum", "") >= grenze_h][:TAGE_HISTORIE]
    # Fortlaufende Debatten: an wie vielen Tagen stand das Thema schon da?
    for x in themen_out:
        x["tage_im_deepdive"] = 1 + sum(1 for h in historie if any(y.get("id") == x["id"] for y in h.get("themen") or []))
    daten = {"datum": heute, "stand": datetime.now(timezone.utc).isoformat(), "themen": themen_out, "historie": historie,
             "rahmen": {"cleavage": CLEAVAGE, "triggerpunkte": TRIGGER}}
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(daten, fh, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, OUT)
    print(f"→ {OUT}: {len(themen_out)} Themen")
    return 0


if __name__ == "__main__":
    sys.exit(main())
