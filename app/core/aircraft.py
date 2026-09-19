"""ICAO aircraft type designators -> the name a person would say.

Designators are public facts (ICAO Doc 8643). Unknown types fall back to the
designator itself, which is still what flight trackers show.
"""

TYPES = {
    # Airbus
    "A19N": "A319NEO", "A20N": "A320NEO", "A21N": "A321NEO", "A318": "A318", "A319": "A319",
    "A320": "A320", "A321": "A321", "A332": "A330-200", "A333": "A330-300", "A338": "A330-800",
    "A339": "A330-900", "A343": "A340-300", "A346": "A340-600", "A359": "A350-900", "A35K": "A350-1000",
    "A388": "A380", "BCS1": "A220-100", "BCS3": "A220-300",
    # Boeing
    "B712": "717", "B737": "737-700", "B738": "737-800", "B739": "737-900", "B37M": "737 MAX 7",
    "B38M": "737 MAX 8", "B39M": "737 MAX 9", "B3XM": "737 MAX 10", "B752": "757-200",
    "B753": "757-300", "B762": "767-200", "B763": "767-300", "B764": "767-400", "B772": "777-200",
    "B77L": "777-200LR", "B77W": "777-300ER", "B778": "777-8", "B779": "777-9", "B788": "787-8",
    "B789": "787-9", "B78X": "787-10", "B744": "747-400", "B748": "747-8",
    # Regional
    "E170": "EMBRAER 170", "E75L": "EMBRAER 175", "E75S": "EMBRAER 175", "E190": "EMBRAER 190",
    "E195": "EMBRAER 195", "E290": "E190-E2", "E295": "E195-E2", "CRJ2": "CRJ-200", "CRJ7": "CRJ-700",
    "CRJ9": "CRJ-900", "CRJX": "CRJ-1000", "DH8D": "DASH 8 Q400", "AT72": "ATR 72", "AT76": "ATR 72-600",
    # Business and general aviation
    "C172": "CESSNA 172", "C182": "CESSNA 182", "C152": "CESSNA 152", "C208": "CARAVAN", "C210": "CESSNA 210",
    "C25A": "CITATION CJ2", "C25B": "CITATION CJ3", "C56X": "CITATION XLS", "C68A": "CITATION LATITUDE",
    "C700": "CITATION LONGITUDE", "C750": "CITATION X", "C510": "CITATION MUSTANG",
    "E55P": "PHENOM 300", "E50P": "PHENOM 100", "PC12": "PILATUS PC-12", "PC24": "PILATUS PC-24",
    "SR22": "CIRRUS SR22", "SR20": "CIRRUS SR20", "SF50": "CIRRUS JET", "P28A": "PIPER CHEROKEE",
    "PA46": "PIPER MALIBU", "BE20": "KING AIR 200", "BE35": "BONANZA", "BE36": "BONANZA", "BE9L": "KING AIR 90",
    "GLF4": "GULFSTREAM IV", "GLF5": "GULFSTREAM V", "GLF6": "GULFSTREAM G650", "GL5T": "GLOBAL 5000",
    "GLEX": "GLOBAL EXPRESS", "GL7T": "GLOBAL 7500", "CL35": "CHALLENGER 350", "CL60": "CHALLENGER 600",
    "LJ45": "LEARJET 45", "LJ75": "LEARJET 75", "HDJT": "HONDAJET", "TBM9": "TBM 900", "TBM7": "TBM 700",
    "DA40": "DIAMOND DA40", "DA42": "DIAMOND DA42", "F2TH": "FALCON 2000", "FA7X": "FALCON 7X",
    # Helicopters and military regulars over Southern California
    "EC35": "H135 HELICOPTER", "EC30": "H130 HELICOPTER", "AS50": "A-STAR HELICOPTER",
    "R44": "ROBINSON R44", "R22": "ROBINSON R22", "R66": "ROBINSON R66", "B06": "JET RANGER",
    "B407": "BELL 407", "S76": "SIKORSKY S-76", "H60": "BLACK HAWK", "V22": "OSPREY",
    "C130": "HERCULES", "C17": "C-17 GLOBEMASTER", "K35R": "KC-135 TANKER", "F18S": "F/A-18 SUPER HORNET",
}

MAKERS = (("A3", "AIRBUS "), ("A2", "AIRBUS "), ("A1", "AIRBUS "), ("BCS", "AIRBUS "), ("B7", "BOEING "))


def type_name(code, long=True):
    """"B38M" -> "BOEING 737 MAX 8" (or "737 MAX 8" when space is short)."""
    code = (code or "").strip().upper()
    name = TYPES.get(code)
    if not name:
        return code
    if long:
        for prefix, maker in MAKERS:
            if code.startswith(prefix) and not name.startswith(maker.strip()):
                return maker + name
    return name


KEEP = {"MAX", "CRJ", "ATR", "XLS", "CJ2", "CJ3", "TBM", "KC-135", "F/A-18", "S-76", "C-17", "H135", "H130",
        "G650", "Q400", "PC-12", "PC-24", "DA40", "DA42", "SR20", "SR22", "R22", "R44", "R66", "A-STAR"}


def mixed(name):
    """"BOEING 737 MAX 8" -> "Boeing 737 MAX 8", "AIRBUS A321NEO" -> "Airbus A321neo"."""
    words = []
    for word in name.split():
        if word in KEEP or any(c.isdigit() for c in word) and not word.endswith("NEO"):
            words.append(word)
        elif word.endswith("NEO") and any(c.isdigit() for c in word):
            words.append(word[:-3] + "neo")
        else:
            words.append(word.capitalize())
    return " ".join(words)
