"""Música, efectos y mezcla con la voz del video de RonyPDF 2.0, hechos con numpy (sin muestras ni descargas).

Uso, desde video/:   python musica.py      → mezcla.wav (48 kHz, estéreo, −14 LUFS) y musica.wav (sin voz)

Lee escenas.js (tiempos.py) y la voz (voz/lNN.wav):
  - El gancho (hasta que aparece RonyPDF) va en La menor, oscuro y filtrado, y crece hasta un corte en seco.
  - En la revelación entra un golpe y el ritmo completo en Do mayor a 120 BPM, con la cuadrícula alineada a ese
    instante; en el cierre baja a un puente suave y el remate vuelve con todo hasta el acorde final.
  - Efectos en el instante exacto de la animación: whoosh en cada corte, pops en las palabras clave, teclas al
    escribir, monedas, falla de imagen, escáner, sello, campanita de «listo» y confeti.
  - Mezcla: cada línea de voz se nivela; la música va a ≈0,24 del nivel de la voz mientras se habla (sube a ≈0,55
    en las pausas largas) y los efectos, como en los videos anteriores, a 0,2 con la voz en 0,5 (pico 0,4 del de la
    voz). El total se normaliza a −14 LUFS (pico −1,5 dBTP) con loudnorm en dos pasadas.
Es determinista (semilla fija). Solo necesita numpy, scipy y ffmpeg.
"""

from __future__ import annotations

import json
import re
import subprocess
import wave
from pathlib import Path

import numpy as np
from scipy.io import wavfile
from scipy.signal import fftconvolve, resample_poly

HERE = Path(__file__).resolve().parent
ESC = json.loads(re.sub(r"^[\s\S]*?window\.ESCENAS\s*=\s*", "", (HERE / "escenas.js").read_text("utf-8")).rstrip().rstrip(";"))
SR = 48000
DUR = ESC["duracion"]
N = int(SR * DUR)
BPM = 120
BEAT = 60 / BPM
REV = ESC["revelacion"]
SC = {e["id"]: e for e in ESC["escenas"]}
LINES = ESC["lineas"]
rng = np.random.default_rng(20261110)


def K(k: int, w: str, d: float = 0.0) -> float:
    return LINES[k - 1]["claves"].get(w, LINES[k - 1]["desde"]) + d


def mtof(m: float) -> float:
    return 440.0 * 2 ** ((m - 69) / 12)


def tv(d: float) -> np.ndarray:
    return np.arange(int(d * SR)) / SR


def add(bus: np.ndarray, x: np.ndarray, t: float, gain: float = 1.0, pan: float = 0.0) -> None:
    i = int(round(t * SR))
    if i >= N or i + len(x) <= 0:
        return
    a, b = max(0, i), min(N, i + len(x))
    seg = x[a - i: b - i] * gain
    if seg.ndim == 1:
        bus[a:b, 0] += seg * np.sqrt((1 - pan) / 2) * 1.414
        bus[a:b, 1] += seg * np.sqrt((1 + pan) / 2) * 1.414
    else:
        bus[a:b] += seg


def spectral(x: np.ndarray, resp) -> np.ndarray:
    X = np.fft.rfft(x, axis=0)
    f = np.fft.rfftfreq(len(x), 1 / SR)
    g = resp(f)
    return np.fft.irfft(X * (g[:, None] if x.ndim == 2 else g), n=len(x), axis=0)


def lp(fc, order=4):
    return lambda f: 1 / np.sqrt(1 + (f / fc) ** (2 * order))


def hp(fc, order=4):
    return lambda f: 1 / np.sqrt(1 + (fc / np.maximum(f, 1e-3)) ** (2 * order))


def bp(fc, q=1.5):
    return lambda f: 1 / (1 + (q * (f / fc - fc / np.maximum(f, 1e-3))) ** 2)


def sweep(x: np.ndarray, f0: float, f1: float, kind: str = "bp", q: float = 2.0) -> np.ndarray:
    n, hop = 2048, 512
    out = np.zeros(len(x) + n)
    win = np.hanning(n)
    f = np.fft.rfftfreq(n, 1 / SR)
    xp = np.concatenate([x, np.zeros(n)])
    for k in range(0, len(x), hop):
        fc = f0 * (f1 / f0) ** (k / max(1, len(x)))
        g = bp(fc, q)(f) if kind == "bp" else (lp(fc)(f) if kind == "lp" else hp(fc)(f))
        out[k: k + n] += np.fft.irfft(np.fft.rfft(xp[k: k + n] * win) * g, n=n) * win
    return out[: len(x)] / 1.5


def env_adsr(n, a, d, s, r, hold):
    t = np.arange(n) / SR
    e = np.where(t < a, t / max(a, 1e-4), s + (1 - s) * np.exp(-(t - a) / max(d, 1e-4)))
    return e * (1 - np.clip((t - hold) / max(r, 1e-4), 0, 1))


# ---------------------------------------------------------------- instrumentos
def kick(d=0.45):
    t = tv(d)
    f = 46 + 95 * np.exp(-t / 0.032)
    body = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / 0.17)
    click = rng.normal(0, 1, len(t)) * np.exp(-t / 0.003) * 0.25
    return np.tanh((body + click) * 1.7)


def clap():
    t = tv(0.32)
    n = rng.normal(0, 1, len(t))
    e = sum(np.exp(-np.clip(t - d, 0, None) / 0.01) * (t >= d) for d in (0, 0.011, 0.023)) + np.exp(-t / 0.1) * 0.8
    return spectral(n * e, bp(1500, 0.9)) * 0.9


def snare():
    t = tv(0.25)
    tone = np.sin(2 * np.pi * 190 * t) * np.exp(-t / 0.05) * 0.5
    n = spectral(rng.normal(0, 1, len(t)), bp(3200, 0.7)) * np.exp(-t / 0.08)
    return tone + n


def hat(open_=False):
    t = tv(0.25 if open_ else 0.06)
    return spectral(rng.normal(0, 1, len(t)) * np.exp(-t / (0.08 if open_ else 0.014)), hp(7500)) * 0.7


def crash():
    t = tv(2.4)
    return spectral(rng.normal(0, 1, len(t)) * np.exp(-t / 0.7), hp(4500, 2)) * 0.5


def pluck(m, d=0.6, bright=1.0):
    t = tv(d)
    f = mtof(m)
    x = sum((0.55 ** k) * np.sin(2 * np.pi * f * (k + 1) * t) * np.exp(-t * (6 + 5 * k / bright)) for k in range(6))
    return x * np.minimum(1, t / 0.003)


def pad_note(m, d, bright=9):
    t = tv(d)
    x = np.zeros_like(t)
    for det in (-0.09, 0.0, 0.11):
        f = mtof(m + det)
        for k in range(1, bright + 1):
            x += np.sin(2 * np.pi * f * k * t + k * 0.7 + det * 9) / k
    return x * env_adsr(len(t), 0.35, 0.8, 0.8, 0.4, d - 0.4) / 9


def bass_note(m, d):
    t = tv(d)
    f = mtof(m)
    x = np.sin(2 * np.pi * f * t) + 0.35 * np.sin(4 * np.pi * f * t) + 0.12 * np.sin(6 * np.pi * f * t)
    return np.tanh(1.4 * x * env_adsr(len(t), 0.006, 0.12, 0.65, 0.04, d - 0.04)) * 0.8


def sub_pulse(m, d):
    t = tv(d)
    x = np.sin(2 * np.pi * mtof(m) * t) + 0.2 * np.sin(2 * np.pi * mtof(m) * 2 * t)
    return x * env_adsr(len(t), 0.01, 0.12, 0.5, 0.05, d - 0.05)


def pop(f0=900):
    t = tv(0.12)
    f = f0 * (1 + 0.6 * np.exp(-t / 0.02))
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / 0.035)


def bell(m=84, d=1.6):
    t = tv(d)
    return sum(a * np.sin(2 * np.pi * mtof(m) * r * t) * np.exp(-t / dd)
               for a, r, dd in ((1, 1, 0.9), (0.5, 2.76, 0.4), (0.3, 5.4, 0.2), (0.4, 2, 0.6))) / 2.2


def whoosh(d=0.55, up=True):
    n = rng.normal(0, 1, int(d * SR))
    t = np.arange(len(n)) / SR
    e = np.sin(np.pi * np.clip(t / d, 0, 1)) ** 2
    return sweep(n * e, 400 if up else 4000, 4000 if up else 400, "bp", 1.2)


def riser(d):
    n = rng.normal(0, 1, int(d * SR))
    t = np.arange(len(n)) / SR
    tone = np.sin(2 * np.pi * np.cumsum(220 * 2 ** (2 * t / d)) / SR) * 0.15
    return (sweep(n, 300, 9000, "bp", 1.0) + tone) * (t / d) ** 2.2


def impact():
    t = tv(2.2)
    boom = np.sin(2 * np.pi * np.cumsum(36 + 70 * np.exp(-t / 0.08)) / SR) * np.exp(-t / 0.6)
    noise = spectral(rng.normal(0, 1, len(t)) * np.exp(-t / 0.4), lp(2500)) * 0.5
    return np.tanh((boom + noise) * 1.3)


def tecla():
    t = tv(0.05)
    return spectral(rng.normal(0, 1, len(t)) * np.exp(-t / 0.006), bp(2600, 1.2)) + np.sin(2 * np.pi * 1800 * t) * np.exp(-t / 0.01) * 0.3


def moneda(i=0):
    t = tv(0.5)
    f = mtof(91 + (i % 3) * 2)
    return (np.sin(2 * np.pi * f * t) + 0.5 * np.sin(2 * np.pi * f * 2.4 * t)) * np.exp(-t / 0.12)


def falla(d=0.5):
    t = tv(d)
    sq = np.sign(np.sin(2 * np.pi * (180 + 400 * (rng.random(len(t)) > 0.97).cumsum() % 900) * t))
    gate = (np.floor(t * 40) % 3 != 0).astype(float)
    return spectral(sq * gate * 0.5 + rng.normal(0, 0.4, len(t)) * gate, bp(1800, 0.6)) * np.minimum(1, (d - t) / 0.05)


def scanner(d):
    t = tv(d)
    hum = np.sin(2 * np.pi * np.cumsum(300 + 500 * t / d) / SR) * 0.25
    n = sweep(rng.normal(0, 1, len(t)), 900, 5000, "bp", 3.0)
    return (hum + n * 0.8) * np.minimum(1, t / 0.08) * np.minimum(1, (d - t) / 0.15)


def thump():
    t = tv(0.35)
    return np.tanh(np.sin(2 * np.pi * np.cumsum(70 + 120 * np.exp(-t / 0.02)) / SR) * np.exp(-t / 0.08) * 2) \
        + spectral(rng.normal(0, 1, len(t)) * np.exp(-t / 0.02), bp(900, 1)) * 0.5


def reverb(x, seconds=1.8, mix=0.3):
    n = int(seconds * SR)
    t = np.arange(n) / SR
    ir = rng.normal(0, 1, (n, 2)) * np.exp(-t / (seconds / 5))[:, None]
    ir = spectral(ir, lp(5000, 2))
    ir /= np.sqrt((ir ** 2).sum(axis=0))
    wet = np.stack([fftconvolve(x[:, c], ir[:, c])[: len(x)] for c in range(2)], 1)
    return x + wet * mix


# ---------------------------------------------------------------- partitura
AM, F_, C_, G_, E7 = [57, 60, 64], [57, 60, 65], [55, 60, 64], [55, 59, 62], [56, 59, 64]
DO, SOL, LAM, FA = [60, 64, 67], [59, 62, 67], [57, 60, 64], [57, 60, 65]
RAIZ = lambda ch: {id(AM): 45, id(F_): 41, id(C_): 48, id(G_): 43, id(E7): 40, id(DO): 36, id(SOL): 43,
                   id(LAM): 45, id(FA): 41}[id(ch)]


def acorde(t: float):
    """Gancho en La menor (Am F C G … E al final para tensar); después, Do mayor (C G Am F)."""
    if t < REV:
        compas = int(np.floor((t - REV) / (4 * BEAT)))      # negativo: compases antes de la revelación
        if compas == -1:
            return E7
        return [AM, F_, C_, G_][compas % 4]
    return [DO, SOL, LAM, FA][int((t - REV) // (4 * BEAT)) % 4]


def main() -> None:
    drums = np.zeros((N, 2))
    bass = np.zeros((N, 2))
    music = np.zeros((N, 2))
    fx = np.zeros((N, 2))
    KI, CL, SN, HC, HO = kick(), clap(), snare(), hat(), hat(True)

    t_cierre, t_final = SC["cierre"]["desde"], SC["final"]["desde"]
    t_lib = K(20, "libre")
    fin_ritmo = LINES[-1]["hasta"] + 0.6
    first = REV - BEAT * np.floor(REV / BEAT)
    beats = np.arange(first, DUR, BEAT)
    t_era, t_tok = SC["era"]["desde"], SC["tokens"]["desde"]
    for bt in beats:
        k = int(round((bt - REV) / BEAT))
        if bt < REV - 0.2:
            # gancho: pulso grave en corcheas, platillos que crecen y un bombo que entra con la licencia
            prog = bt / REV
            for h in (0, 1):
                add(bass, sub_pulse(RAIZ(acorde(bt + 0.01)) - 12 + 12, BEAT / 2 * 0.9), bt + h * BEAT / 2, 0.28 + 0.2 * prog)
            add(drums, HC, bt + BEAT / 2, 0.06 + 0.12 * prog, 0.3)
            if bt >= SC["licencia"]["desde"] and k % 2 == 0:
                add(drums, KI, bt, 0.55 + 0.25 * prog)
            if bt >= t_tok and k % 2 == 1:
                add(drums, CL, bt, 0.25, 0.1)
        elif REV <= bt < t_cierre - 0.05 or t_final <= bt < fin_ritmo:
            add(drums, KI, bt, 0.9)
            if k % 2 == 1:
                add(drums, CL, bt, 0.45, 0.1)
            add(drums, HC, bt + BEAT / 2, 0.22, 0.35)
            add(drums, HC, bt + BEAT / 4, 0.08, -0.3)
            if k % 4 == 3:
                add(drums, HO, bt + BEAT / 2, 0.12, 0.35)
            root = RAIZ(acorde(bt + 0.01))
            for h in (0, 1):
                add(bass, bass_note(root + (12 if (h and k % 4 == 3) else 0), BEAT / 2 * 0.92), bt + h * BEAT / 2, 0.55)
        elif t_cierre <= bt < t_final:
            # puente suave: bombo a medio tiempo y bajo largo
            if k % 4 == 0:
                add(drums, KI, bt, 0.5)
                add(bass, bass_note(RAIZ(acorde(bt + 0.01)), 4 * BEAT * 0.95), bt, 0.35)
            add(drums, HC, bt + BEAT / 2, 0.07, 0.3)
    # redoble antes de la revelación (corcheas → semicorcheas) y silencio dramático
    for i, tt in enumerate(np.arange(REV - 4 * BEAT, REV - 0.18, BEAT / 4)):
        p = (tt - (REV - 4 * BEAT)) / (4 * BEAT)
        if p < 0.5 and i % 2:
            continue
        add(drums, SN, tt, 0.15 + 0.4 * p, 0.05)

    # colchón de acordes
    bars = sorted(set(list(np.arange(REV, -4 * BEAT, -4 * BEAT)) + list(np.arange(REV, DUR, 4 * BEAT))))
    bars = [b for b in bars if b > -4 * BEAT]
    for i, st in enumerate(bars):
        en = bars[i + 1] if i + 1 < len(bars) else DUR + 0.5
        if st < 0:
            st0, dur0 = 0.0, en
        else:
            st0, dur0 = st, en - st
        if st0 >= t_lib - 0.01:
            continue
        ch = acorde(st0 + 0.01)
        oscuro = st0 < REV
        for m in ch:
            add(music, pad_note(m - 12, dur0 + 0.35, 5 if oscuro else 9), st0, 0.15 if oscuro else 0.16, -0.3 + 0.3 * (m % 3))
    # acorde final, largo, en «libre»
    for m in DO + [72]:
        add(music, pad_note(m - 12, DUR - t_lib + 0.5), t_lib, 0.22, -0.3 + 0.3 * (m % 3))
        add(music, pluck(m, 2.4, 2.0), t_lib, 0.25, 0.2 - 0.1 * (m % 3))

    # arpegio en corcheas (y semicorcheas al final del gancho), con eco
    arp = np.zeros((N, 2))
    pat = [0, 1, 2, 1, 2, 0, 1, 2]
    paso = BEAT / 2
    for bt in np.arange(first - 40 * BEAT, fin_ritmo, BEAT / 4):
        if bt < 0:
            continue
        j = int(round((bt - first) / (BEAT / 4)))
        semis = t_tok + 2 <= bt < REV - 0.2
        if not semis and j % 2:
            continue
        if t_cierre <= bt < t_final and j % 4:
            continue
        if REV - 0.2 <= bt < REV:
            continue
        if bt < t_era and j % 4:
            continue
        ch = acorde(bt + 0.01)
        step = j // (1 if semis else 2)
        m = ch[pat[step % 8]] + (12 if step % 8 in (4, 6) else 0)
        g = 0.12 if bt < REV else (0.2 if t_cierre <= bt < t_final else 0.25)
        add(arp, pluck(m, 0.5 if bt < REV else 0.6, 0.7 if bt < REV else 1.0), bt, g, 0.25 if step % 2 else -0.25)
    d = int(0.375 * SR)
    echo = np.zeros_like(arp)
    echo[d:, 0] = arp[:-d, 1] * 0.35
    echo[2 * d:, 1] = arp[:-2 * d, 0] * 0.22
    music += arp + spectral(echo, lp(3500, 2))
    # el gancho va filtrado y se abre de a poco; en la revelación, todo de golpe
    t_all = np.arange(N) / SR
    gancho = t_all < REV
    if gancho.any():
        tramo = music[gancho]
        lento = spectral(tramo, lp(900, 2))
        abierto = spectral(tramo, lp(4000, 2))
        mezcla_g = np.clip((t_all[gancho] - t_era) / max(1e-3, REV - t_era), 0, 1)[:, None]
        music[gancho] = lento * (1 - mezcla_g) + abierto * mezcla_g
    # silencio dramático justo antes del golpe
    hueco = (t_all > REV - 0.18) & (t_all < REV)
    for bus in (music, bass, drums):
        bus[hueco] *= np.linspace(1, 0, hueco.sum())[:, None]

    # ------------------------------------------------------------ efectos
    ef = ESC["efectos"]
    add(fx, riser(3.6), REV - 3.6, 0.5)
    for tt in ef["golpe"]:
        add(fx, impact(), tt, 0.95)
        add(fx, crash(), tt, 0.5)
    for j, tt in enumerate(ef["pop"]):
        add(fx, pop(780 + 70 * (j % 5)), tt, 0.22, [-0.3, 0.3, 0, 0.15, -0.15][j % 5])
    for tt in ef["whoosh"]:
        add(fx, whoosh(0.5), tt - 0.3, 0.32, 0.3)
    # gancho
    add(fx, whoosh(1.6, True), K(1, "subes"), 0.22, -0.2)              # el archivo sube
    add(fx, thump(), K(1, "confidenciales"), 0.45)                      # sello CONFIDENCIAL
    for i in range(7):
        add(fx, moneda(i), K(2, "licencias") + i * 0.17, 0.18, (i % 3 - 1) * 0.4)
    add(fx, falla(0.22), K(3, "peor"), 0.4)
    add(fx, falla(0.6), K(3, "pirata") - 0.05, 0.5)
    for i in range(3):                                                  # tachones
        add(fx, whoosh(0.25, False), K(4, "necesario", -0.25 + i * 0.12), 0.25, (i - 1) * 0.5)
    for i in range(16):                                                 # contador de tokens
        add(fx, tecla(), K(6, "mil") + i * 0.09, 0.12, (i % 2 - 0.5) * 0.4)
    # funciones
    add(fx, bell(88, 1.0), K(7, "internet", 0.1), 0.25)
    add(fx, thump(), K(9, "firmas", 0.7), 0.4)                          # sello APROBADO
    add(fx, bell(84), K(10, "comprimes", 0.2), 0.3, 0.2)
    add(fx, scanner(1.0), K(11, "Reconoce"), 0.35)
    add(fx, whoosh(0.4, False), K(13, "verdad", -0.15), 0.3)
    add(fx, thump(), K(15, "Control", 0.3), 0.3)                        # Ctrl + K
    for i in range(6):                                                  # escribe «juntar»
        add(fx, tecla(), K(15, "juntar", -0.05 + i * 0.11), 0.3, (i % 2 - 0.5) * 0.3)
    t_ins, t_git = K(16, "instálame"), K(16, "GitHub", 0.35)
    for i, tt in enumerate(np.arange(t_ins - 0.1, t_git, 0.075)):       # escribe el mensaje a la IA
        add(fx, tecla(), tt + rng.uniform(-0.01, 0.01), 0.16, (i % 3 - 1) * 0.3)
    add(fx, pop(1200), K(16, "GitHub", 0.45), 0.3)
    add(fx, bell(91, 1.2), K(17, "listo"), 0.35)
    add(fx, pop(1000), K(18, "sígueme", 0.14), 0.35)
    add(fx, bell(79, 1.4), K(19, "corazón"), 0.2)
    for i in range(18):                                                 # confeti del remate
        add(fx, pop(1400 + 600 * rng.random()), t_lib + 0.05 + rng.random() * 0.9, 0.08, rng.uniform(-0.7, 0.7))
    add(fx, crash(), t_lib, 0.45)
    add(fx, impact(), t_lib, 0.5)

    # ------------------------------------------------------------ música lista: sala, fundidos y normalización
    mus = drums + bass + reverb(music, 1.8, 0.35)
    mus *= (np.clip(t_all / 0.2, 0, 1) * np.clip((DUR - t_all) / 1.8, 0, 1) ** 1.3)[:, None]
    mus = spectral(mus, hp(30, 2))
    fx = reverb(fx, 1.2, 0.18)

    # ------------------------------------------------------------ voz
    voz = np.zeros(N)
    habla = np.zeros(N)
    for L in LINES:
        sr, w = wavfile.read(HERE / "voz" / f"l{L['k']:02d}.wav")
        w = w.astype(np.float64)
        if w.dtype.kind == "i" or np.abs(w).max() > 2:
            w /= 32768.0
        if w.ndim == 2:
            w = w.mean(1)
        if sr != SR:
            w = resample_poly(w, SR, sr)
        hop = SR // 100
        fr = np.array([np.sqrt(np.mean(w[i:i + hop] ** 2)) for i in range(0, len(w) - hop, hop)])
        act = fr[fr > fr.max() * 0.1]
        w *= 0.1 / (np.sqrt(np.mean(act ** 2)) + 1e-9)                 # todas las líneas al mismo nivel
        i0 = int(L["desde"] * SR)
        n = min(len(w), N - i0)
        voz[i0:i0 + n] += w[:n]
        habla[i0:i0 + n] = 1
    voz = spectral(voz, hp(70, 2))
    rv = np.sqrt(np.mean(voz[habla > 0] ** 2))

    # ducking: la música baja a 0,24 de la voz mientras se habla y sube a ~0,55 en las pausas largas
    hop = 64
    env = np.zeros(N)
    acc = 0.0
    att, rel = np.exp(-hop / (0.05 * SR)), np.exp(-hop / (0.6 * SR))
    for i in range(0, N, hop):
        x = habla[i]
        c = att if x > acc else rel
        acc = c * acc + (1 - c) * x
        env[i:i + hop] = acc
    rm = np.sqrt(np.mean(mus ** 2)) + 1e-9
    mus_n = mus / rm * rv                                               # música al nivel de la voz…
    nivel = 0.55 - (0.55 - 0.24) * env                                  # …y bajada a 0,24 bajo la voz
    # efectos: como en los videos anteriores (voz con pico 0,5 y efectos ×0,2 con pico 1), su pico queda en 0,4 del
    # pico de la voz
    fx_n = fx / (np.abs(fx).max() + 1e-9) * np.abs(voz).max() * 0.4
    mezcla = mus_n * nivel[:, None] + fx_n + voz[:, None]
    mezcla *= 0.9 / (np.abs(mezcla).max() + 1e-9)

    def escribir(nombre: str, x: np.ndarray, norm: bool) -> Path:
        cruda = HERE / (nombre + "_cruda.wav")
        with wave.open(str(cruda), "wb") as f:
            f.setnchannels(2)
            f.setsampwidth(2)
            f.setframerate(SR)
            f.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())
        out = HERE / (nombre + ".wav")
        if norm:
            r = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(cruda), "-af", "loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json",
                                "-f", "null", "-"], capture_output=True, text=True)
            m = json.loads(re.findall(r"\{[^{}]*\"input_i\"[^{}]*\}", r.stderr)[-1])
            filtro = (f"loudnorm=I=-14:TP=-1.5:LRA=11:measured_I={m['input_i']}:measured_TP={m['input_tp']}:"
                      f"measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true")
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(cruda), "-af", filtro, "-ar", str(SR), str(out)], check=True)
        else:
            cruda.replace(out)
            return out
        cruda.unlink()
        return out

    out = escribir("mezcla", mezcla, True)
    sin_voz = mus_n * nivel[:, None] + fx_n
    escribir("musica", sin_voz * 0.9 / (np.abs(sin_voz).max() + 1e-9), False)
    # cuánto suena cada parte, para el informe
    def db(x):
        return 20 * np.log10(np.sqrt(np.mean(x ** 2)) + 1e-12)
    m_h = (mus_n * nivel[:, None])[habla > 0]
    print(f"listo: {out} ({DUR:.1f} s)")
    print(f"  bajo la voz: música {db(m_h) - db(voz[habla > 0]):+.1f} dB respecto a la voz (≈ ×{10 ** ((db(m_h) - db(voz[habla > 0])) / 20):.2f})")


if __name__ == "__main__":
    main()
