/* ────────────────────────────────────────────────────────────────
   Presseschau – öffentliche Konfiguration
   Diese Datei liegt öffentlich im Repository. Alles hier drin kann
   jeder Besucher lesen. Deshalb NUR Dinge eintragen, deren
   Offenlegung vertretbar ist.
   ────────────────────────────────────────────────────────────────

   HINWEIS: Ein Schlüssel wird hier normalerweise NICHT gebraucht.
   Die Zusammenfassungen ("Die Lage", Ressort-Überblicke, Vorgangs-
   Zusammenfassungen) entstehen beim nächtlichen Lauf in GitHub Actions
   und werden als ai.json mitgeliefert – für alle Besucher, ohne Schlüssel.

   Einen Schlüssel hier einzutragen lohnt nur, wenn die KI-Suche zusätzlich
   frei formulierte Antworten auf beliebige Fragen geben soll. Er ist dann
   für jeden Besucher lesbar.

   Vorher unbedingt in den OpenRouter-Einstellungen absichern:
     1. Einen EIGENEN Schlüssel nur für diese Seite anlegen
        (nicht den, der in GitHub Actions steckt).
     2. Unter "Credit limit" auf 0 setzen – dann sind ausschließlich
        die kostenlosen Modelle nutzbar und es kann nichts abgerechnet
        werden, egal wer den Schlüssel findet.
     3. Nur ein Modell mit ":free" eintragen.
     4. Bei Missbrauch: Schlüssel in OpenRouter löschen, hier neuen
        eintragen, fertig – index.html bleibt unberührt.
────────────────────────────────────────────────────────────────── */
window.PS_KI_CONFIG = {
  /* ── Nutzungsmessung (optional) ──────────────────────────────────────
     Leer lassen = keinerlei Messung, nichts wird geladen.

     Gemessen werden nur Seitenaufrufe je Bereich (Dashboard, Monitor, …).
     Keine Namen, keine Suchbegriffe, keine Artikelinhalte.

     Drei Möglichkeiten, alle ohne Cookie-Banner-Pflicht:

     1. GoatCounter  – kostenlos, sehr datensparsam, Konto auf goatcounter.com
        anbieter: "goatcounter", code: "deinname"     (dein Konto-Name)

     2. Cloudflare Web Analytics – kostenlos, wenn die Seite ohnehin über
        Cloudflare läuft
        anbieter: "cloudflare",  code: "dein-token"

     3. Plausible – kostenpflichtig, dafür sehr übersichtlich
        anbieter: "plausible",   code: "deine-domain.de"
  ─────────────────────────────────────────────────────────────────────── */
  /* Welche Parteien die Regierungskoalition bilden. Steuert, ob ein Vorgang
     unter "Koalition" oder "Opposition" einsortiert wird. Nach einer
     Regierungsbildung hier anpassen – sonst nirgends nötig.
     Mögliche Werte: cdu, csu, spd, gruene, fdp, linke, afd, bsw          */
  koalition: ["cdu", "csu", "spd"],

  analytics: {
    anbieter: "",     // "goatcounter" | "cloudflare" | "plausible"
    code: ""          // Konto-Name, Token oder Domain – je nach Anbieter
  },

  // Schlüssel hier eintragen, z. B. "sk-or-v1-…"
  key: "",

  // Kostenloses Modell. Weitere unter https://openrouter.ai/models?q=free
  model: "meta-llama/llama-3.3-70b-instruct:free",

  // Bremse gegen Missbrauch: Anfragen je Stunde und Browser.
  // Mit eigenem Schlüssel (im KI-Fenster hinterlegt) entfällt sie.
  maxPerHour: 12
};
