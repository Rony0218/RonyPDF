"""Cómo se comparan las palabras del guion con lo que oyó Whisper (lo usan voz.py y tiempos.py).

Solo usa la biblioteca estándar, para que tiempos.py corra con cualquier Python.
"""
import difflib
import re
import unicodedata

_U = ("cero uno dos tres cuatro cinco seis siete ocho nueve diez once doce trece catorce quince dieciseis "
      "diecisiete dieciocho diecinueve veinte").split()
_D = {3: "treinta", 4: "cuarenta", 5: "cincuenta", 6: "sesenta", 7: "setenta", 8: "ochenta", 9: "noventa"}
_C = {1: "ciento", 5: "quinientos", 7: "setecientos", 9: "novecientos"}


def num_es(n):
    """Entero -> palabras en español (sin tildes), para comparar los dígitos de Whisper con el guion."""
    if n <= 20:
        return _U[n]
    if n < 30:
        return "veinti" + _U[n - 20]
    if n < 100:
        return _D[n // 10] + (" y " + _U[n % 10] if n % 10 else "")
    if n < 1000:
        c = "cien" if n == 100 else _C.get(n // 100, _U[n // 100] + "cientos")
        return c + (" " + num_es(n % 100) if n % 100 else "")
    if n < 1_000_000:
        m = n // 1000
        return ("mil" if m == 1 else num_es(m) + " mil") + (" " + num_es(n % 1000) if n % 1000 else "")
    m = n // 1_000_000
    return ("un millon" if m == 1 else num_es(m) + " millones") + (" " + num_es(n % 1_000_000) if n % 1_000_000 else "")


# Nombres propios que Whisper escribe a su manera aunque la voz los diga bien: «Rony» suena «Roni» y Whisper lo
# escribe «Ronnie», «Ronny» o «Roni»; «Ctrl» es «Control». Solo nombres: una palabra mal dicha sigue sin pasar.
ALIAS = [(r"\bro(?:nnie|nny|ni|ney)(?=pdf\b|\b)", "rony"), (r"\bctrl\b", "control")]


def normal(s):
    """Palabras en minúscula, sin tildes ni puntuación, con los números escritos en letras."""
    s = unicodedata.normalize("NFD", s.lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    for a, b in ALIAS:
        s = re.sub(a, b, s)
    s = re.sub(r"(\d)[.,](\d{3})\b", r"\1\2", s)
    s = re.sub(r"(\d)[.,](\d{3})\b", r"\1\2", s)
    s = re.sub(r"(?<=[a-z])\.(?=[a-z])", " punto ", s)          # «ronyguerrero.com» = «ronyguerrero punto com»
    s = re.sub(r"\d+", lambda m: " " + num_es(int(m.group())) + " ", s)
    return re.sub(r"[^a-z0-9 ]+", " ", s).split()


def parecido(a, b):
    """1.0 solo cuando Whisper oyó exactamente las palabras del guion (los espacios no cuentan)."""
    return difflib.SequenceMatcher(None, "".join(normal(a)), "".join(normal(b)), autojunk=False).ratio()


def alinear(palabras_pantalla, palabras_oidas):
    """Le pone tiempo a cada palabra de los subtítulos con las marcas de Whisper.

    palabras_pantalla: ["¿Todavía", "le", …] (como se escriben en pantalla)
    palabras_oidas: [{"p": "Todavía", "a": 0.0, "b": 0.4}, …] (de voz/lineas.json)
    Compara letra por letra las dos versiones normalizadas, así «iLovePDF» en pantalla toma el tiempo de
    «I Love PDF» en la voz. Devuelve [(inicio, fin)] por palabra de pantalla.
    """
    def letras(ws):
        s, dueno = [], []
        for i, w in enumerate(ws):
            for c in "".join(normal(w)):
                s.append(c)
                dueno.append(i)
        return "".join(s), dueno

    sp, dp = letras(palabras_pantalla)
    so, do = letras([w["p"] for w in palabras_oidas])
    mapa = {}
    for blk in difflib.SequenceMatcher(None, sp, so, autojunk=False).get_matching_blocks():
        for k in range(blk.size):
            mapa[blk.a + k] = blk.b + k
    out = []
    for i in range(len(palabras_pantalla)):
        idx = [mapa[j] for j, d in enumerate(dp) if d == i and j in mapa]
        if idx:
            a = palabras_oidas[do[min(idx)]]["a"]
            b = palabras_oidas[do[max(idx)]]["b"]
            out.append([a, b])
        else:
            out.append(None)
    # las que no casaron (signos sueltos) toman el tiempo de sus vecinas
    for i, v in enumerate(out):
        if v is None:
            prev = next((out[j] for j in range(i - 1, -1, -1) if out[j]), None)
            nxt = next((out[j] for j in range(i + 1, len(out)) if out[j]), None)
            a = prev[1] if prev else (nxt[0] if nxt else 0.0)
            b = nxt[0] if nxt else a + 0.2
            out[i] = [a, max(a, b)]
    return out
