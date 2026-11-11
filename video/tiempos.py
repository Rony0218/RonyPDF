#!/usr/bin/env python3
"""Arma la línea de tiempo del video a partir de la voz: escenas.js.

Uso, desde video/:   python tiempos.py

El ritmo lo marca la voz. Cada línea de guion.json empieza después de su «pausa» y dura lo que dura su toma
(voz/lineas.json). Con las marcas de tiempo de Whisper se calculan:
  - los subtítulos, palabra por palabra, en trozos cortos que caben en dos renglones;
  - el tiempo de cada palabra «clave» de la línea (los golpes de la animación);
  - las escenas: cada una empieza un poco antes de su primera línea y termina donde empieza la siguiente;
  - los efectos de sonido: un «whoosh» en cada corte, el golpe de la revelación y un «pop» en cada clave.
index.html y musica.py leen escenas.js; no hay que tocar tiempos a mano.
"""
import json
import os
import re

from texto import alinear, normal

AQUI = os.path.dirname(os.path.abspath(__file__))
ADELANTO = 0.18      # la escena entra un poco antes de que empiece a hablar
COLA = 2.2           # lo que queda después de la última palabra (el cierre respira con la música)
DEBILES = {"de", "los", "las", "la", "el", "una", "un", "con", "ni", "y", "o", "se", "en", "a", "que", "para",
           "sin", "como", "tu", "tus", "lo", "le", "mi", "es", "al", "del", "te", "si"}


def pantalla(ln):
    s = ln.get("pantalla") or ln["texto"]
    return s.replace("...", "…").split()


def trozos(palabras, maximo=24):
    """Agrupa las palabras de una línea en subtítulos cortos: hasta ~24 letras, cortando en la puntuación
    cuando se puede y sin dejar una palabra débil («de», «la», «y»…) al final de un trozo."""
    grupos, act = [], []
    largo = lambda idx: len(" ".join(palabras[j] for j in idx))
    for i, w in enumerate(palabras):
        if act and largo(act + [i]) > maximo and largo(act) >= 6:
            mover = []
            while len(act) > 1 and palabras[act[-1]].lower() in DEBILES:
                mover.insert(0, act.pop())
            grupos.append(act)
            act = mover
        act.append(i)
        if w[-1] in ".,:;?!…" and largo(act) >= 8:
            grupos.append(act)
            act = []
    if act:
        if grupos and largo(act) < 7 and largo(grupos[-1] + act) <= maximo + 6:
            grupos[-1] += act
        else:
            grupos.append(act)
    return grupos


def main():
    g = json.load(open(os.path.join(AQUI, "guion.json"), encoding="utf-8"))
    voz = json.load(open(os.path.join(AQUI, "voz", "lineas.json"), encoding="utf-8"))
    if [v["texto"] for v in voz] != [ln["texto"] for ln in g["lineas"]]:
        raise SystemExit("voz/lineas.json no corresponde a guion.json: corre primero voz.py")

    t = 0.0
    lineas, subs, efectos = [], [], {"whoosh": [], "golpe": [], "pop": []}
    for k, (ln, v) in enumerate(zip(g["lineas"], voz)):
        t += ln["pausa"]
        desde, hasta = t, t + v["dur"]
        ws = pantalla(ln)
        tiempos = alinear(ws, v["palabras"])
        pal = [{"w": w, "a": round(desde + a, 3), "b": round(desde + min(b, v["dur"]), 3)} for w, (a, b) in zip(ws, tiempos)]
        claves = {}
        for c in ln.get("claves", []):
            nc = "".join(normal(c))
            hit = next((p for p in pal if "".join(normal(p["w"])) == nc), None)
            if hit is None:
                raise SystemExit(f"línea {k + 1}: la clave «{c}» no está en el texto")
            claves[c] = hit["a"]
        lineas.append({"k": k + 1, "escena": ln["escena"], "desde": round(desde, 3), "hasta": round(hasta, 3),
                       "texto": " ".join(ws), "palabras": pal, "claves": claves})
        t = hasta
    dur = round(t + COLA, 2)

    # subtítulos
    for i, L in enumerate(lineas):
        sig = lineas[i + 1]["desde"] if i + 1 < len(lineas) else dur
        grupos = trozos([p["w"] for p in L["palabras"]])
        for j, gr in enumerate(grupos):
            ps = [L["palabras"][x] for x in gr]
            a = ps[0]["a"] - 0.08
            b = (L["palabras"][grupos[j + 1][0]]["a"] - 0.08) if j + 1 < len(grupos) else min(L["hasta"] + 0.45, sig - 0.1)
            subs.append({"desde": round(max(a, L["desde"] - 0.1), 3), "hasta": round(b, 3), "linea": L["k"],
                         "palabras": ps})

    # escenas
    escenas = []
    for L in lineas:
        if not escenas or escenas[-1]["id"] != L["escena"]:
            escenas.append({"id": L["escena"], "desde": 0.0 if not escenas else round(L["desde"] - ADELANTO, 3)})
    for i, e in enumerate(escenas):
        e["hasta"] = escenas[i + 1]["desde"] if i + 1 < len(escenas) else dur
    ids = [e["id"] for e in escenas]
    if len(set(ids)) != len(ids):
        raise SystemExit("una escena aparece en dos tramos separados: " + ", ".join(ids))

    # efectos
    efectos["whoosh"] = [e["desde"] for e in escenas[1:]]
    revela = lineas[5]["claves"].get("RonyPDF")
    efectos["golpe"] = [revela] if revela else []
    for L in lineas:
        for c, tc in L["claves"].items():
            if tc != revela and L["escena"] not in ("nube", "pirata", "era", "tokens"):
                efectos["pop"].append(tc)
    efectos["pop"].sort()

    p_datos = os.path.join(AQUI, "capturas", "datos.json")       # tamaños reales de la compresión, etc.
    datos = json.load(open(p_datos, encoding="utf-8")) if os.path.exists(p_datos) else {}
    out = {"duracion": dur, "revelacion": revela, "lineas": lineas, "subtitulos": subs, "escenas": escenas,
           "efectos": efectos, "datos": datos}
    js = ("// Generado por tiempos.py a partir de guion.json y voz/lineas.json. No lo edites a mano.\n"
          "window.ESCENAS = " + json.dumps(out, ensure_ascii=False, indent=1) + ";\n")
    open(os.path.join(AQUI, "escenas.js"), "w", encoding="utf-8").write(js)
    print(f"escenas.js: {dur:.2f} s, {len(lineas)} líneas, {len(subs)} subtítulos, {len(escenas)} escenas")
    for e in escenas:
        print(f"  {e['id']:10s} {e['desde']:6.2f} → {e['hasta']:6.2f}")


if __name__ == "__main__":
    main()
