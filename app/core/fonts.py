"""Original RackTicker bitmap alphabets, AGPL-3.0-only with this project.

No system fonts, font downloads, subpixel positioning, or antialiasing.
Main: 5×7 at 1× (5×7), 2× (10×14) or 3× (15×21). `smooth=True` at 2× uses the
Scale2x pixel-art algorithm, which rounds diagonals while staying strictly
binary, so headlines read like a real LED sign instead of chunky blocks.
Tiny: 3×5 (M, N, W wider) for dense tables (sportsbook lines, tape labels).
Text is uppercase unless mixed=True asks for lowercase. Unsupported glyphs are dropped.
"""
from functools import lru_cache
import unicodedata

from PIL import Image, ImageDraw

# Seven rows per glyph, most-significant bit at the left of its five columns.
GLYPHS = {
    "A": [14,17,17,31,17,17,17], "B": [30,17,17,30,17,17,30],
    "C": [14,17,16,16,16,17,14], "D": [30,17,17,17,17,17,30],
    "E": [31,16,16,30,16,16,31], "F": [31,16,16,30,16,16,16],
    "G": [14,17,16,23,17,17,15], "H": [17,17,17,31,17,17,17],
    "I": [31,4,4,4,4,4,31], "J": [7,2,2,2,18,18,12],
    "K": [17,18,20,24,20,18,17], "L": [16,16,16,16,16,16,31],
    "M": [17,27,21,21,17,17,17], "N": [17,25,21,19,17,17,17],
    "O": [14,17,17,17,17,17,14], "P": [30,17,17,30,16,16,16],
    "Q": [14,17,17,17,21,18,13], "R": [30,17,17,30,20,18,17],
    "S": [15,16,16,14,1,1,30], "T": [31,4,4,4,4,4,4],
    "U": [17,17,17,17,17,17,14], "V": [17,17,17,17,17,10,4],
    "W": [17,17,17,21,21,21,10], "X": [17,17,10,4,10,17,17],
    "Y": [17,17,10,4,4,4,4], "Z": [31,1,2,4,8,16,31],
    "0": [14,17,19,21,25,17,14], "1": [4,12,4,4,4,4,14],
    "2": [14,17,1,2,4,8,31], "3": [30,1,1,14,1,1,30],
    "4": [2,6,10,18,31,2,2], "5": [31,16,16,30,1,1,30],
    "6": [14,16,16,30,17,17,14], "7": [31,1,2,4,8,8,8],
    "8": [14,17,17,14,17,17,14], "9": [14,17,17,15,1,1,14],
    " ": [0]*7, ":": [0,4,4,0,4,4,0], ".": [0,0,0,0,0,4,4],
    ",": [0,0,0,0,0,4,8], "-": [0,0,0,31,0,0,0],
    "+": [0,4,4,31,4,4,0], "/": [1,2,2,4,8,8,16],
    "!": [4,4,4,4,4,0,4], "?": [14,17,1,2,4,0,4], '"': [10,10,0,0,0,0,0], ";": [0,4,0,0,4,4,8],
    ">": [16,8,4,2,4,8,16], "<": [1,2,4,8,4,2,1],
    "↑": [4,14,21,4,4,4,4], "↓": [4,4,4,4,21,14,4],
    "→": [0,4,2,31,2,4,0], "✓": [0,0,1,2,20,8,0],
    "×": [0,17,10,4,10,17,0], "•": [0,0,14,14,14,0,0],
    "°": [6,9,9,6,0,0,0], "'": [4,4,8,0,0,0,0],
    "&": [12,18,20,8,21,18,13], "=": [0,31,0,31,0,0,0],
    "(": [2,4,8,8,8,4,2], ")": [8,4,2,2,2,4,8],
    "%": [24,25,2,4,8,19,3], "_": [0,0,0,0,0,0,31],
    "#": [10,10,31,10,31,10,10], "@": [14,17,23,21,23,16,14],
    "$": [4,15,20,14,5,30,4], "▲": [0,0,4,14,31,0,0], "▼": [0,0,31,14,4,0,0],
    "*": [0,21,14,31,14,21,0], "|": [4,4,4,4,4,4,4],
}

# Lowercase for mixed-case text (airport names, headlines), drawn like station
# dot-matrix boards: a 5×7 body on the same baseline as capitals, and true
# descenders two rows below it. Mixed-case text is therefore 9 rows tall.
DESCENT = 2
LOWER = {
    "a": (".....", ".....", ".***.", "....*", ".****", "*...*", ".****"),
    "b": ("*....", "*....", "*.**.", "**..*", "*...*", "*...*", "****."),
    "c": (".....", ".....", ".***.", "*....", "*....", "*...*", ".***."),
    "d": ("....*", "....*", ".**.*", "*..**", "*...*", "*...*", ".****"),
    "e": (".....", ".....", ".***.", "*...*", "*****", "*....", ".***."),
    "f": ("..**.", ".*..*", ".*...", "***..", ".*...", ".*...", ".*..."),
    "g": (".....", ".....", ".****", "*...*", "*...*", "*...*", ".****", "....*", ".***."),
    "h": ("*....", "*....", "*.**.", "**..*", "*...*", "*...*", "*...*"),
    "i": ("..*..", ".....", ".**..", "..*..", "..*..", "..*..", ".***."),
    "j": ("...*.", ".....", "..**.", "...*.", "...*.", "...*.", "...*.", "*..*.", ".**.."),
    "k": ("*....", "*....", "*..*.", "*.*..", "**...", "*.*..", "*..*."),
    "l": (".**..", "..*..", "..*..", "..*..", "..*..", "..*..", ".***."),
    "m": (".....", ".....", "**.*.", "*.*.*", "*.*.*", "*...*", "*...*"),
    "n": (".....", ".....", "*.**.", "**..*", "*...*", "*...*", "*...*"),
    "o": (".....", ".....", ".***.", "*...*", "*...*", "*...*", ".***."),
    "p": (".....", ".....", "****.", "*...*", "*...*", "*...*", "****.", "*....", "*...."),
    "q": (".....", ".....", ".****", "*...*", "*...*", "*...*", ".****", "....*", "....*"),
    "r": (".....", ".....", "*.**.", "**..*", "*....", "*....", "*...."),
    "s": (".....", ".....", ".***.", "*....", ".***.", "....*", "****."),
    "t": (".*...", ".*...", "***..", ".*...", ".*...", ".*..*", "..**."),
    "u": (".....", ".....", "*...*", "*...*", "*...*", "*..**", ".**.*"),
    "v": (".....", ".....", "*...*", "*...*", "*...*", ".*.*.", "..*.."),
    "w": (".....", ".....", "*...*", "*...*", "*.*.*", "*.*.*", ".*.*."),
    "x": (".....", ".....", "*...*", ".*.*.", "..*..", ".*.*.", "*...*"),
    "y": (".....", ".....", "*...*", "*...*", "*...*", "*...*", ".****", "....*", ".***."),
    "z": (".....", ".....", "*****", "...*.", "..*..", ".*...", "*****"),
}
GLYPHS.update({char: [int(row.replace("*", "1").replace(".", "0"), 2) for row in rows]
               for char, rows in LOWER.items()})

# Accented lowercase for place names (Gödöllő, Zürich, Città): the mark sits in
# the two rows above the x-height, so the letter keeps its shape.
MARKS = {"\u0301": ("...*.", "..*.."), "\u0300": (".*...", "..*.."), "\u0308": ("*..*.", "....."),
         "\u030b": ("..*.*", ".*.*."), "\u0302": ("..*..", ".*.*."), "\u0303": (".*.*.", "*.*.."),
         "\u030c": (".*.*.", "..*.."), "\u030a": ("..*..", ".*.*.")}


def _accented():
    import unicodedata as ud
    glyphs = {}
    for base in "aeiouynczsr":
        for mark, (top, second) in MARKS.items():
            char = ud.normalize("NFC", base + mark)
            if len(char) != 1 or char == base:
                continue
            rows = list(LOWER[base])
            if base == "i":  # the dot makes way for the accent
                rows[0] = "....."
            rows[0], rows[1] = top, second if rows[1] == "....." else rows[1]
            glyphs[char] = [int(row.replace("*", "1").replace(".", "0"), 2) for row in rows]
    return glyphs


ACCENTED = _accented()
GLYPHS.update(ACCENTED)
NARROW_ACCENTED = {char for char in ACCENTED if unicodedata.normalize("NFD", char)[0] == "i"}

# Five rows of three columns. Narrow punctuation uses only the centre column.
TINY = {
    "A": [2,5,7,5,5], "B": [6,5,6,5,6], "C": [3,4,4,4,3], "D": [6,5,5,5,6],
    "E": [7,4,6,4,7], "F": [7,4,6,4,4], "G": [3,4,5,5,3], "H": [5,5,7,5,5],
    "I": [7,2,2,2,7], "J": [1,1,1,5,2], "K": [5,6,4,6,5], "L": [4,4,4,4,7],
    "M": [17,27,21,17,17], "N": [9,13,11,9,9], "O": [2,5,5,5,2], "P": [6,5,6,4,4],
    "Q": [2,5,5,6,3], "R": [6,5,6,5,5], "S": [3,4,2,1,6], "T": [7,2,2,2,2],
    "U": [5,5,5,5,7], "V": [5,5,5,5,2], "W": [17,17,21,27,17], "X": [5,5,2,5,5],
    "Y": [5,5,2,2,2], "Z": [7,1,2,4,7],
    "0": [7,5,5,5,7], "1": [2,6,2,2,7], "2": [6,1,2,4,7], "3": [6,1,2,1,6],
    "4": [5,5,7,1,1], "5": [7,4,6,1,6], "6": [3,4,7,5,7], "7": [7,1,1,2,2],
    "8": [7,5,7,5,7], "9": [7,5,7,1,6],
    " ": [0,0,0,0,0], ".": [0,0,0,0,2], ":": [0,2,0,2,0], "!": [2,2,2,0,2],
    "'": [2,2,0,0,0], ",": [0,0,0,2,2], "|": [2,2,2,2,2],
    "-": [0,0,7,0,0], "+": [0,2,7,2,0], "/": [1,1,2,4,4], "%": [5,1,2,4,5],
    "$": [3,6,2,3,6], "?": [6,1,2,0,2], '"': [5,5,0,0,0], ";": [0,2,0,2,4], "(": [1,2,2,2,1], ")": [4,2,2,2,4],
    "#": [5,7,5,7,5], "&": [2,5,2,5,3], "@": [7,5,7,4,3], "°": [2,5,2,0,0],
    "=": [0,7,0,7,0], "<": [1,2,4,2,1], ">": [4,2,1,2,4], "_": [0,0,0,0,7],
    "×": [0,5,2,5,0], "*": [0,5,2,5,0], "•": [0,0,2,0,0],
    "↑": [2,7,2,2,2], "↓": [2,2,2,7,2], "▲": [0,2,7,0,0], "▼": [0,7,2,0,0],
}
TINY_NARROW = set(".:!',|")
# M, N and W need their diagonals: at three columns M read as H and N as R.
TINY_WIDE = {"M": 5, "W": 5, "N": 4}


def _tiny_advance(char):
    return 1 if char in TINY_NARROW else TINY_WIDE.get(char, 3)


# Typographic punctuation from feeds, folded to what the LED fonts can draw.
PUNCTUATION = str.maketrans({
    "‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'", "`": "'", "´": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"', "″": '"', "«": '"', "»": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "―": "-", "−": "-", "~": "-",
    "…": "...", "·": "•", "[": "(", "]": ")", "{": "(", "}": ")",
    "ß": "SS", "Ø": "O", "Æ": "AE", "Œ": "OE", "Ł": "L", "Đ": "D", "Þ": "TH",
})


def normalize(text, glyphs=GLYPHS, mixed=False):
    text = str(text).translate(PUNCTUATION)
    if mixed and glyphs is GLYPHS:
        # Keep accented lowercase the font can draw; fold the rest to plain letters.
        text = unicodedata.normalize("NFC", text)
        text = "".join(c if c in ACCENTED else "".join(p for p in unicodedata.normalize("NFD", c)
                                                        if not unicodedata.combining(p)) for c in text)
    else:
        text = "".join(c for c in unicodedata.normalize("NFD", text.upper()) if not unicodedata.combining(c))
    # Anything still unknown (emoji, other scripts) is dropped rather than shown
    # as "?", which reads as a broken headline.
    return "".join(c for c in text if c in glyphs)


def glyph_width(char):
    return 3 if char in " :.,'|il" or char in NARROW_ACCENTED else 5


def text_width(text, scale=1, mixed=False):
    text = normalize(text, mixed=mixed)
    return max(0, sum(glyph_width(c) + 1 for c in text) - 1) * scale


def wrap_text(text, width, scale=1, mixed=False):
    """Lines of whole words, each at most `width` pixels wide. A word that is wider
    than a line on its own (a URL, a long compound) is split where it has to be."""
    lines, line = [], ""
    for word in normalize(text, mixed=mixed).split():
        joined = f"{line} {word}" if line else word
        if text_width(joined, scale, mixed) <= width:
            line = joined
            continue
        if line:
            lines.append(line)
        line = word
        while text_width(line, scale, mixed) > width:
            cut = max(1, next((n for n in range(len(line) - 1, 0, -1)
                               if text_width(line[:n], scale, mixed) <= width), 1))
            lines.append(line[:cut])
            line = line[cut:]
    if line:
        lines.append(line)
    return lines


def glyph_spans(text, scale=1, mixed=False):
    """(char, x, width) for each normalized glyph, for per-letter animation."""
    spans, x = [], 0
    for char in normalize(text, mixed=mixed):
        width = glyph_width(char) * scale
        spans.append((char, x, width))
        x += width + scale
    return tuple(spans)


def _scale2x(mask):
    width, height = mask.size
    source = mask.load()
    out = Image.new("1", (width * 2, height * 2))
    target = out.load()

    def at(x, y):
        return source[x, y] if 0 <= x < width and 0 <= y < height else 0

    for y in range(height):
        for x in range(width):
            p, a, b, c, d = at(x, y), at(x, y - 1), at(x + 1, y), at(x - 1, y), at(x, y + 1)
            e1 = a if c == a and c != d and a != b else p
            e2 = b if a == b and a != c and b != d else p
            e3 = c if d == c and d != b and c != a else p
            e4 = d if b == d and b != a and d != c else p
            target[x * 2, y * 2], target[x * 2 + 1, y * 2] = e1, e2
            target[x * 2, y * 2 + 1], target[x * 2 + 1, y * 2 + 1] = e3, e4
    return out


def text_height(scale=1, mixed=False):
    """Rows a line of text occupies: 7, plus the descenders of mixed case."""
    return (7 + (DESCENT if mixed else 0)) * scale


@lru_cache(maxsize=512)
def glyph_mask(char, scale=1, smooth=False):
    # Glyphs are separated by a blank column, so Scale2x per glyph is identical
    # to Scale2x over a whole line, and far cheaper for long headlines.
    if smooth and scale == 2:
        mask = _scale2x(glyph_mask(char, 1))
        if char == "D":
            # Scale2x chamfers every outer corner, and a D with its square left side
            # rounded off is an O: "DL" on an airline badge read as "OL".
            for y in (0, mask.height - 1):
                mask.putpixel((0, y), 1)
        return mask
    width = glyph_width(char)
    mask = Image.new("1", (width * scale, len(GLYPHS[char]) * scale))
    draw = ImageDraw.Draw(mask)
    for y, bits in enumerate(GLYPHS[char]):
        for column in range(width):
            source_column = column + (1 if width == 3 else 0)
            if bits & (1 << (4 - source_column)):
                draw.rectangle((column * scale, y * scale,
                                (column + 1) * scale - 1, (y + 1) * scale - 1), fill=1)
    return mask


@lru_cache(maxsize=512)
def text_mask(text, scale=1, smooth=False, mixed=False):
    text = normalize(text, mixed=mixed)
    smooth = smooth and scale == 2
    mask = Image.new("1", (max(1, text_width(text, scale, mixed)), text_height(scale, mixed)))
    x = 0
    for char in text:
        if char != " ":
            mask.paste(glyph_mask(char, scale, smooth), (x, 0))
        x += (glyph_width(char) + 1) * scale
    return mask


def draw_text(frame, text, x, y, color=(245, 245, 235), scale=1, smooth=False, mixed=False):
    """`mixed=True` keeps lowercase, which reads faster in names and headlines."""
    mask = text_mask(str(text), scale, smooth, mixed)
    frame.paste(color, (int(x), int(y), int(x) + mask.width, int(y) + mask.height), mask)


def centered(frame, text, y, color=(245,245,235), scale=1, smooth=False, mixed=False):
    draw_text(frame, text, (frame.width - text_width(text, scale, mixed)) // 2, y, color, scale, smooth, mixed)


def tiny_width(text):
    text = normalize(text, TINY)
    return max(0, sum(_tiny_advance(c) + 1 for c in text) - 1)


@lru_cache(maxsize=512)
def tiny_mask(text):
    text = normalize(text, TINY)
    mask = Image.new("1", (max(1, tiny_width(text)), 5))
    pixels = mask.load()
    x = 0
    for char in text:
        narrow = char in TINY_NARROW
        width = 3 if narrow else _tiny_advance(char)
        for y, bits in enumerate(TINY[char]):
            for column in (1,) if narrow else range(width):
                if bits & (1 << (width - 1 - column)):
                    pixels[x + (0 if narrow else column), y] = 1
        x += _tiny_advance(char) + 1
    return mask


def draw_tiny(frame, text, x, y, color=(245, 245, 235)):
    mask = tiny_mask(str(text))
    frame.paste(color, (int(x), int(y), int(x) + mask.width, int(y) + mask.height), mask)
