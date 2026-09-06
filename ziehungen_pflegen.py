"""Holt die Ziehungen der letzten 12 Monate (6aus49, Eurojackpot, BINGO!,
Spiel 77, SUPER 6, Superchance) und schreibt sie validiert nach
Veroeffentlichen/ziehungen.json. Zeitrechnung immer in Europe/Berlin,
unabhaengig von der Uhr des ausfuehrenden Rechners."""
import datetime
import json
import pathlib
import sys
import urllib.request

FENSTER_TAGE = 365
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
ZIEL = pathlib.Path(__file__).parent / "Veroeffentlichen" / "ziehungen.json"

REGELN = {
    "lotto6aus49": {"haupt": (6, 1, 49), "zusatz": (1, 0, 9), "mindestens": 90},
    "eurojackpot": {"haupt": (5, 1, 50), "zusatz": (2, 1, 12), "mindestens": 90},
    "bingo": {"haupt": (22, 1, 75), "zusatz": (0, 0, 0), "mindestens": 45},
}
ZIFFERN = {"spiel77": (7, 90), "super6": (6, 90)}


def _lade(url, daten=None, kopfzeilen=None):
    anfrage = urllib.request.Request(
        url, data=daten, headers={"User-Agent": UA, **(kopfzeilen or {})})
    with urllib.request.urlopen(anfrage, timeout=30) as antwort:
        return json.load(antwort)


def _letzter_sonntag(jahr, monat):
    tag = datetime.date(jahr, monat + 1, 1) - datetime.timedelta(days=1)
    return tag - datetime.timedelta(days=(tag.weekday() + 1) % 7)


def _berlin(zeitpunkt_utc):
    """Deutsche Ortszeit ohne fremde Zeitzonendaten — Sommerzeit vom letzten
    Maerz- bis zum letzten Oktobersonntag, jeweils 01:00 UTC."""
    jahr = zeitpunkt_utc.year
    beginn = datetime.datetime(
        *_letzter_sonntag(jahr, 3).timetuple()[:3], 1,
        tzinfo=datetime.timezone.utc)
    ende = datetime.datetime(
        *_letzter_sonntag(jahr, 10).timetuple()[:3], 1,
        tzinfo=datetime.timezone.utc)
    stunden = 2 if beginn <= zeitpunkt_utc < ende else 1
    return zeitpunkt_utc + datetime.timedelta(hours=stunden)


def _heute():
    return _berlin(datetime.datetime.now(datetime.timezone.utc)).date()


def _ms(tag):
    mitternacht = datetime.datetime(tag.year, tag.month, tag.day,
                                    tzinfo=datetime.timezone.utc)
    versatz = _berlin(mitternacht) - mitternacht
    return int((mitternacht - versatz).timestamp() * 1000)


def _tag_aus_ms(millisekunden):
    roh = datetime.datetime.fromtimestamp(
        millisekunden / 1000, datetime.timezone.utc)
    return _berlin(roh).date()


def hole_6aus49(grenze):
    roh = _lade("https://johannesfriedrich.github.io/LottoNumberArchive/"
                "Lottonumbers_complete.json")
    ziehungen = []
    for e in roh["data"]:
        d = datetime.datetime.strptime(e["date"], "%d.%m.%Y").date()
        if d >= grenze and "Superzahl" in e:
            ziehungen.append({"spiel": "lotto6aus49", "datum": d.isoformat(),
                              "hauptzahlen": sorted(e["Lottozahl"]),
                              "zusatzzahlen": [e["Superzahl"]]})
    return ziehungen


def hole_eurojackpot(grenze):
    heute = _heute()
    wochen, d = [], grenze
    while d <= heute:
        jahr, woche, _ = d.isocalendar()
        if (jahr, woche) not in wochen:
            wochen.append((jahr, woche))
        d += datetime.timedelta(days=7)
    ziehungen = []
    for jahr, woche in wochen:
        roh = _lade("https://www.veikkaus.fi/api/draw-results/v1/games/"
                    f"EJACKPOT/draws/by-week/{jahr}-W{woche:02d}")
        for z in roh:
            if not z.get("results"):
                continue
            d = datetime.datetime.fromtimestamp(
                z["drawTime"] / 1000, tz=datetime.timezone.utc).date()
            if d < grenze:
                continue
            ziehungen.append({
                "spiel": "eurojackpot", "datum": d.isoformat(),
                "hauptzahlen": sorted(int(x) for x in z["results"][0]["primary"]),
                "zusatzzahlen": sorted(int(x) for x in z["results"][0]["secondary"]),
            })
    return ziehungen


def hole_bingo(grenze):
    heute = _heute()
    d = heute - datetime.timedelta(days=(heute.weekday() - 6) % 7)
    sonntage = []
    while d >= grenze:
        sonntage.append(d.isoformat())
        d -= datetime.timedelta(days=7)
    teile = [f'a{i}: latestDraws(lotteries:$l,drawDate:"{s}")'
             '{drawDateTime drawNumbers bingoSuperChances{serialNumber ticket}}'
             for i, s in enumerate(sonntage)]
    rumpf = json.dumps({"query": "query($l:[GameScalar!]!){" + " ".join(teile) + "}",
                        "variables": {"l": ["bingo"]}}).encode()
    antwort = _lade("https://www.lotto-niedersachsen.de/api/graphql",
                    daten=rumpf, kopfzeilen={"Content-Type": "application/json"})
    ziehungen = []
    for i, s in enumerate(sonntage):
        for z in antwort["data"][f"a{i}"] or []:
            if z["drawDateTime"][:10] != s:
                continue
            ziehungen.append({"spiel": "bingo", "datum": s,
                              "hauptzahlen": sorted(z["drawNumbers"]),
                              "zusatzzahlen": []})
            gewinne = [{"serie": str(g["serialNumber"]), "los": str(g["ticket"])}
                       for g in z.get("bingoSuperChances") or []]
            if gewinne:
                ziehungen.append({"spiel": "superchance", "datum": s,
                                  "gewinne": gewinne})
    return ziehungen


def hole_lotto_de(grenze):
    """Ein Abruf liefert 6aus49 samt Spiel 77 und SUPER 6."""
    heute = _heute()
    roh = _lade("https://www.lotto.de/api/stats/entities.lotto/draws/"
                f"{_ms(grenze)}/{_ms(heute)}")
    haupt, zusatz = [], []
    for z in roh:
        datum = _tag_aus_ms(z["drawDate"]).isoformat()
        zahlen = [n["drawNumber"]
                  for n in (z.get("drawNumbersCollection") or [])]
        superzahl = z.get("superNumber")
        if len(zahlen) == 6 and superzahl is not None:
            haupt.append({"spiel": "lotto6aus49", "datum": datum,
                          "hauptzahlen": sorted(zahlen),
                          "zusatzzahlen": [superzahl]})
        for spiel, feld in (("spiel77", "game77"), ("super6", "super6")):
            ziffern = (z.get(feld) or {}).get("numbers")
            if ziffern is not None:
                zusatz.append({"spiel": spiel, "datum": datum,
                               "ziffern": str(ziffern)})
    return haupt, zusatz


def _ergaenze(basis, nachschub):
    """Nimmt aus dem Nachschub nur Ziehungstage, die noch fehlen."""
    bekannt = {(z["spiel"], z["datum"]) for z in basis}
    return basis + [z for z in nachschub
                    if (z["spiel"], z["datum"]) not in bekannt]


ZIEHUNGSTAGE = {
    "lotto6aus49": (2, 5),
    "eurojackpot": (1, 4),
    "bingo": (6,),
    "spiel77": (2, 5),
    "super6": (2, 5),
    "superchance": (6,),
}


def _fehlende_tage(spiel, vorhanden):
    """Ziehungstage der letzten zwei Wochen, die in der Liste fehlen."""
    heute = _heute()
    fehlt = []
    for rueckwaerts in range(1, 15):
        tag = heute - datetime.timedelta(days=rueckwaerts)
        if tag.weekday() in ZIEHUNGSTAGE[spiel] and tag.isoformat() not in vorhanden:
            fehlt.append(tag.isoformat())
    return fehlt


def pruefe(ziehungen):
    befunde = []
    for spiel in ZIEHUNGSTAGE:
        vorhanden = {z["datum"] for z in ziehungen if z["spiel"] == spiel}
        fehlt = _fehlende_tage(spiel, vorhanden)
        if fehlt:
            befunde.append(f"{spiel}: Ziehungstage fehlen — {', '.join(fehlt)}.")
    for spiel, regel in REGELN.items():
        eintraege = [z for z in ziehungen if z["spiel"] == spiel]
        if len(eintraege) < regel["mindestens"]:
            befunde.append(f"{spiel}: nur {len(eintraege)} Ziehungen "
                           f"(mindestens {regel['mindestens']} erwartet).")
        if len({z["datum"] for z in eintraege}) != len(eintraege):
            befunde.append(f"{spiel}: doppelter Ziehungstag.")
        for z in eintraege:
            for feld, (soll, unten, oben) in (("hauptzahlen", regel["haupt"]),
                                              ("zusatzzahlen", regel["zusatz"])):
                zahlen = z[feld]
                if len(zahlen) != soll:
                    befunde.append(f"{spiel} {z['datum']}: {feld} "
                                   f"{len(zahlen)} statt {soll}.")
                if len(set(zahlen)) != len(zahlen):
                    befunde.append(f"{spiel} {z['datum']}: doppelte Zahl.")
                for wert in zahlen:
                    if not unten <= wert <= oben:
                        befunde.append(f"{spiel} {z['datum']}: {wert} "
                                       f"ausserhalb {unten}-{oben}.")
    for spiel, (stellen, mindestens) in ZIFFERN.items():
        eintraege = [z for z in ziehungen if z["spiel"] == spiel]
        if len(eintraege) < mindestens:
            befunde.append(f"{spiel}: nur {len(eintraege)} Ziehungen "
                           f"(mindestens {mindestens} erwartet).")
        for z in eintraege:
            if len(z["ziffern"]) != stellen or not z["ziffern"].isdigit():
                befunde.append(f"{spiel} {z['datum']}: Ziffernfolge "
                               f"'{z['ziffern']}' passt nicht.")
    superchance = [z for z in ziehungen if z["spiel"] == "superchance"]
    if len(superchance) < 45:
        befunde.append(f"superchance: nur {len(superchance)} Ziehungstage "
                       "(mindestens 45 erwartet).")
    for z in superchance:
        if not z["gewinne"]:
            befunde.append(f"superchance {z['datum']}: keine Gewinne.")
        for g in z["gewinne"]:
            if not (g["serie"].isdigit() and g["los"].isdigit()):
                befunde.append(f"superchance {z['datum']}: Gewinn {g} "
                               "ist keine Ziffernfolge.")
    return befunde


def main():
    grenze = _heute() - datetime.timedelta(days=FENSTER_TAGE)
    lotto_de, zusatz = hole_lotto_de(grenze)
    ziehungen = (_ergaenze(hole_6aus49(grenze), lotto_de)
                 + hole_eurojackpot(grenze) + hole_bingo(grenze) + zusatz)
    befunde = pruefe(ziehungen)
    if befunde:
        print("ABBRUCH — Befunde:")
        for b in befunde:
            print(" -", b)
        return 1
    ziehungen.sort(key=lambda z: (z["datum"], z["spiel"]))
    ZIEL.parent.mkdir(exist_ok=True)
    ZIEL.write_text(json.dumps(ziehungen, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    alle_spiele = list(REGELN) + list(ZIFFERN) + ["superchance"]
    je_spiel = {s: sum(1 for z in ziehungen if z["spiel"] == s)
                for s in alle_spiele}
    print(f"OK: {len(ziehungen)} Eintraege nach {ZIEL} geschrieben "
          f"({', '.join(f'{s} {n}' for s, n in je_spiel.items())}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
