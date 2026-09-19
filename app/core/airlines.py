"""Airline identity from ADS-B callsigns: ICAO prefix -> IATA code, short name and
a representative livery colour.

Codes and colours are facts. No logo artwork is bundled: the ADS-B plugin fetches
a small mark at runtime from a public CDN, and screens fall back to generated
code tiles when none is available.
"""

AIRLINES = {
    "AAL": ("AA", "AMERICAN", (0, 120, 210)), "DAL": ("DL", "DELTA", (200, 16, 46)),
    "UAL": ("UA", "UNITED", (0, 51, 160)), "SWA": ("WN", "SOUTHWEST", (48, 76, 178)),
    "ASA": ("AS", "ALASKA", (1, 90, 140)), "JBU": ("B6", "JETBLUE", (0, 56, 150)),
    "NKS": ("NK", "SPIRIT", (255, 222, 0)), "FFT": ("F9", "FRONTIER", (0, 122, 83)),
    "HAL": ("HA", "HAWAIIAN", (106, 40, 140)), "AAY": ("G4", "ALLEGIANT", (245, 130, 32)),
    "SCX": ("SY", "SUN COUNTRY", (242, 101, 34)), "MXY": ("MX", "BREEZE", (0, 150, 200)),
    "SKW": ("OO", "SKYWEST", (0, 70, 130)), "RPA": ("YX", "REPUBLIC", (0, 80, 150)),
    "ENY": ("MQ", "ENVOY", (0, 120, 210)), "EDV": ("9E", "ENDEAVOR", (200, 16, 46)),
    "QXE": ("QX", "HORIZON", (1, 90, 140)), "GJS": ("G7", "GOJET", (0, 51, 160)),
    "ASH": ("YV", "MESA", (0, 51, 160)), "PDT": ("PT", "PIEDMONT", (0, 120, 210)),
    "JIA": ("OH", "PSA", (0, 120, 210)), "CPZ": ("C5", "COMMUTAIR", (0, 51, 160)),
    "FDX": ("FX", "FEDEX", (77, 20, 140)), "UPS": ("5X", "UPS", (120, 80, 30)),
    "GTI": ("5Y", "ATLAS", (0, 70, 140)), "EJA": ("1I", "NETJETS", (110, 110, 120)),
    "ACA": ("AC", "AIR CANADA", (216, 34, 42)), "WJA": ("WS", "WESTJET", (0, 150, 170)),
    "AMX": ("AM", "AEROMEXICO", (0, 60, 130)), "VOI": ("Y4", "VOLARIS", (160, 30, 120)),
    "VIV": ("VB", "VIVA", (0, 160, 70)), "BAW": ("BA", "BRITISH", (20, 60, 140)),
    "VIR": ("VS", "VIRGIN", (218, 30, 40)), "AFR": ("AF", "AIR FRANCE", (0, 50, 160)),
    "KLM": ("KL", "KLM", (0, 161, 222)), "DLH": ("LH", "LUFTHANSA", (20, 40, 110)),
    "UAE": ("EK", "EMIRATES", (210, 25, 40)), "QTR": ("QR", "QATAR", (120, 30, 80)),
    "ETD": ("EY", "ETIHAD", (189, 139, 19)), "SIA": ("SQ", "SINGAPORE", (240, 170, 0)),
    "CPA": ("CX", "CATHAY", (0, 110, 100)), "JAL": ("JL", "JAPAN AIR", (200, 0, 30)),
    "ANA": ("NH", "ANA", (19, 68, 152)), "KAL": ("KE", "KOREAN AIR", (0, 150, 210)),
    "AAR": ("OZ", "ASIANA", (180, 30, 40)), "EVA": ("BR", "EVA AIR", (0, 110, 70)),
    "CAL": ("CI", "CHINA AIR", (220, 110, 150)), "CES": ("MU", "CHINA EAST", (0, 60, 140)),
    "CSN": ("CZ", "CHINA SOUTH", (0, 110, 180)), "CCA": ("CA", "AIR CHINA", (200, 20, 30)),
    "QFA": ("QF", "QANTAS", (230, 0, 0)), "ANZ": ("NZ", "AIR NZ", (0, 140, 150)),
    "THY": ("TK", "TURKISH", (200, 16, 46)), "IBE": ("IB", "IBERIA", (210, 20, 40)),
    "AIC": ("AI", "AIR INDIA", (200, 30, 40)), "PAL": ("PR", "PHILIPPINE", (0, 50, 150)),
    "FJI": ("FJ", "FIJI", (0, 120, 130)), "ICE": ("FI", "ICELANDAIR", (0, 70, 140)),
    "TAP": ("TP", "TAP", (0, 160, 80)), "LAN": ("LA", "LATAM", (60, 40, 140)),
    "AVA": ("AV", "AVIANCA", (220, 20, 30)), "CMP": ("CM", "COPA", (0, 60, 150)),
}


def airline(callsign):
    """(IATA, name, colour) for an airline callsign like UAL1234, else None."""
    callsign = str(callsign or "").strip().upper()
    prefix = callsign[:3]
    if len(callsign) > 3 and prefix.isalpha() and callsign[3].isdigit():
        return AIRLINES.get(prefix)
    return None


SPELLING = {"JETBLUE": "JetBlue", "SKYWEST": "SkyWest", "WESTJET": "WestJet", "FEDEX": "FedEx", "NETJETS": "NetJets",
            "GOJET": "GoJet", "COMMUTAIR": "CommutAir", "EVA AIR": "EVA Air", "AIR NZ": "Air NZ", "PSA": "PSA",
            "KLM": "KLM", "ANA": "ANA", "UPS": "UPS", "TAP": "TAP", "LATAM": "LATAM", "ICELANDAIR": "Icelandair"}


def display_name(name):
    """Airline names as they write them: "JETBLUE" -> "JetBlue", "DELTA" -> "Delta"."""
    return SPELLING.get(name) or " ".join(word.capitalize() for word in name.split())
