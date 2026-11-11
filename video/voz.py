#!/usr/bin/env python3
"""Narración del video con Qwen3-TTS (pesos abiertos, Apache 2.0), en local con GPU y sin internet.

  python voz.py         genera de 4 a 8 tomas por línea, las transcribe con Whisper y se queda con la mejor
  python voz.py todo    lo mismo, pero rehace también las líneas que ya estaban (si el texto no cambió se reutilizan)
  python voz.py 7 12    rehace solo esas líneas (numeradas desde 1)
  python voz.py verificar   vuelve a transcribir las tomas elegidas (sin generar voz) y actualiza voz/lineas.json

Se corre con un Python que tenga torch, qwen-tts y transformers. Usa solo los pesos
que ya están en la caché de Hugging Face (HF_HUB_OFFLINE=1): no descarga nada.

Cómo se elige la toma de cada línea:
  1. Whisper (large-v3-turbo) transcribe cada toma; el «parecido» es 1.0 solo si oyó exactamente las palabras del
     guion (sin contar tildes, mayúsculas, puntuación ni espacios entre palabras). El audio se le pasa a 16 kHz:
     el pipeline de transformers no remuestrea un {"raw", "sampling_rate"} de 24 kHz, así que Whisper oiría la voz
     1,5 veces más lenta y las marcas de tiempo saldrían estiradas.
  2. Entre las tomas exactas y de duración razonable (0,85 a 1,25 veces la mediana) gana la más expresiva:
     la que más varía de tono (desviación del tono en semitonos, con pYIN) y de energía (desviación en dB).

Escribe voz/lNN.wav (sin silencios al inicio y al final) y voz/lineas.json con la duración, lo que oyó Whisper,
el parecido, la expresividad y las palabras con su tiempo (para los subtítulos palabra por palabra).
La voz es la del narrador de siempre: voz/referencia.wav es una copia de la del reel de la casa.
"""
import os
import sys

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

import json  # noqa: E402

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402

from texto import parecido  # noqa: E402  (cómo se comparan guion y Whisper: texto.py)

AQUI = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(AQUI, "voz")
TOMAS, TOMAS_MAX, EXACTAS = 4, 8, 2   # al menos 4 tomas; se sigue hasta tener 2 exactas o llegar a 8


def oir(asr, w, sr):
    """Lo que oye Whisper, con la marca de tiempo de cada palabra. Siempre a 16 kHz (ver arriba)."""
    import librosa
    w16 = librosa.resample(np.asarray(w, np.float32), orig_sr=sr, target_sr=16000) if sr != 16000 else w
    r = asr({"raw": w16, "sampling_rate": 16000}, generate_kwargs={"language": "spanish"}, return_timestamps="word")
    return r["text"].strip(), palabras(r["chunks"])


def recortar(w, sr, umbral_db=-42, margen=0.06):
    """Quita el silencio del inicio y del final, con un margen pequeño."""
    hop = int(sr * 0.01)
    e = np.array([np.sqrt(np.mean(w[i:i + hop] ** 2) + 1e-12) for i in range(0, len(w) - hop, hop)])
    db = 20 * np.log10(e / (e.max() + 1e-12))
    voz = np.where(db > umbral_db)[0]
    if len(voz) == 0:
        return w
    a = max(0, voz[0] * hop - int(margen * sr))
    b = min(len(w), (voz[-1] + 1) * hop + int(margen * 2 * sr))
    return w[a:b]


def expresividad(w, sr):
    """Cuánto varía la voz: desviación del tono (semitonos) y de la energía (dB) en los tramos con voz."""
    import librosa
    y = librosa.resample(w, orig_sr=sr, target_sr=16000)
    f0, voiced, _ = librosa.pyin(y, fmin=70, fmax=400, sr=16000, frame_length=1024, hop_length=160)
    f0 = f0[voiced & np.isfinite(f0)]
    st = float(np.std(12 * np.log2(f0 / np.median(f0)))) if len(f0) > 10 else 0.0
    rms = librosa.feature.rms(y=y, frame_length=1024, hop_length=160)[0]
    db = 20 * np.log10(rms + 1e-9)
    db = db[db > db.max() - 30]
    edb = float(np.std(db)) if len(db) > 10 else 0.0
    return round(st, 3), round(edb, 3), round(st + 0.3 * edb, 3)


def palabras(chunks):
    """Las palabras de Whisper con su tiempo: [{"p": texto, "a": inicio, "b": fin}]."""
    out = []
    for c in chunks:
        a, b = c["timestamp"]
        if a is None:
            continue
        b = a + 0.2 if b is None else b
        out.append({"p": c["text"].strip(), "a": round(float(a), 3), "b": round(float(b), 3)})
    return out


def snapshot(repo):
    from huggingface_hub import snapshot_download
    return snapshot_download(repo, local_files_only=True)   # ruta local: transformers no consulta la red


def verificar():
    """Vuelve a pasar por Whisper las tomas elegidas: parecido y marcas de tiempo al día, sin generar voz."""
    import torch
    from transformers import pipeline
    g = json.load(open(os.path.join(AQUI, "guion.json"), encoding="utf-8"))
    p_info = os.path.join(OUT, "lineas.json")
    info = json.load(open(p_info, encoding="utf-8"))
    asr = pipeline("automatic-speech-recognition", model=snapshot("openai/whisper-large-v3-turbo"),
                   dtype=torch.float16, device="cuda:0")
    for k, (ln, x) in enumerate(zip(g["lineas"], info), 1):
        if x["texto"] != ln["texto"]:
            print(f"l{k:02d}: el texto cambió; córrela de nuevo con «python voz.py {k}»")
            continue
        w, sr = sf.read(os.path.join(OUT, x["archivo"]), dtype="float32")
        oido, pals = oir(asr, w, sr)
        x.update(oido=oido, parecido=round(parecido(ln["texto"], oido), 3), palabras=pals, dur=round(len(w) / sr, 3))
        print(f"l{k:02d}: {x['dur']:4.1f} s  parecido {x['parecido']:.3f}  «{oido}»", flush=True)
    json.dump(info, open(p_info, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    malas = [x["archivo"] for x in info if x["parecido"] < 1.0]
    print("verificado:", p_info, "· sin parecido 1.0:", ", ".join(malas) if malas else "ninguna")


def main():
    import torch
    from qwen_tts import Qwen3TTSModel
    from transformers import pipeline

    if "verificar" in sys.argv[1:]:
        return verificar()
    g = json.load(open(os.path.join(AQUI, "guion.json"), encoding="utf-8"))
    os.makedirs(OUT, exist_ok=True)
    kw = dict(device_map="cuda:0", dtype=torch.bfloat16)

    ref = os.path.join(OUT, "referencia.wav")
    if not os.path.exists(ref):
        raise SystemExit("Falta voz/referencia.wav: copia ahí un WAV de 10 a 20 s con la voz del narrador.")
    ref_w, ref_sr = sf.read(ref, dtype="float32")

    base = Qwen3TTSModel.from_pretrained(snapshot("Qwen/Qwen3-TTS-12Hz-1.7B-Base"), **kw)
    prompt = base.create_voice_clone_prompt(ref_audio=(ref_w, ref_sr), ref_text=g["muestra"])
    asr = pipeline("automatic-speech-recognition", model=snapshot("openai/whisper-large-v3-turbo"),
                   dtype=torch.float16, device="cuda:0")

    args = sys.argv[1:]
    rehacer = {int(a) for a in args if a.isdigit()}
    previas = {}
    p_info = os.path.join(OUT, "lineas.json")
    if os.path.exists(p_info) and "todo" not in args:
        for x in json.load(open(p_info, encoding="utf-8")):
            if os.path.exists(os.path.join(OUT, x["archivo"])):
                previas[(x["archivo"], x["texto"])] = x
    info = []
    for k, ln in enumerate(g["lineas"], 1):
        vieja = previas.get((f"l{k:02d}.wav", ln["texto"]))
        if vieja and k not in rehacer:
            print(f"l{k:02d}: se reutiliza ({vieja['dur']:.1f} s, parecido {vieja['parecido']:.2f})", flush=True)
            info.append(vieja)
            continue
        tomas = []
        toma = 0
        while toma < TOMAS or (toma < TOMAS_MAX and sum(m["p"] >= 1.0 for m in tomas) < EXACTAS):
            torch.manual_seed(1000 * k + toma)
            wavs, sr = base.generate_voice_clone(text=ln["texto"], language="Spanish", voice_clone_prompt=prompt)
            w = recortar(np.asarray(wavs[0], np.float32), sr)
            oido, pals = oir(asr, w, sr)
            p = parecido(ln["texto"], oido)
            st, edb, ex = expresividad(w, sr)
            tomas.append(dict(p=p, w=w, sr=sr, dur=len(w) / sr, oido=oido, st=st, edb=edb, ex=ex,
                              palabras=pals, semilla=1000 * k + toma))
            print(f"l{k:02d} toma {toma}: {len(w) / sr:4.1f} s  parecido {p:.3f}  tono {st:.2f} st  energía {edb:.2f} dB"
                  f"  «{oido}»", flush=True)
            toma += 1
        med = float(np.median([m["dur"] for m in tomas]))
        exactas = [m for m in tomas if m["p"] >= 1.0]
        buenas = [m for m in exactas if 0.85 * med <= m["dur"] <= 1.25 * med] or exactas
        if buenas:
            m = max(buenas, key=lambda m: m["ex"])
        else:                                   # ninguna exacta: la más parecida (se avisa al final)
            m = max(tomas, key=lambda m: (m["p"], m["ex"]))
        sf.write(os.path.join(OUT, f"l{k:02d}.wav"), m["w"], m["sr"])
        print(f"l{k:02d} → toma de semilla {m['semilla']} ({m['dur']:.2f} s, parecido {m['p']:.3f}, expresividad {m['ex']:.2f})",
              flush=True)
        info.append({"texto": ln["texto"], "archivo": f"l{k:02d}.wav", "dur": round(m["dur"], 3), "oido": m["oido"],
                     "parecido": round(m["p"], 3), "tono_st": m["st"], "energia_db": m["edb"], "expresividad": m["ex"],
                     "tomas": len(tomas), "exactas": len(exactas), "semilla": m["semilla"], "sr": m["sr"],
                     "palabras": m["palabras"]})
        resto = [x for x in previas.values() if int(x["archivo"][1:3]) > k]   # si se interrumpe, no se pierden
        json.dump(info + resto, open(p_info, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(info, open(p_info, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    malas = [x["archivo"] for x in info if x["parecido"] < 1.0]
    print("listo:", p_info, "· sin parecido 1.0:", ", ".join(malas) if malas else "ninguna")


if __name__ == "__main__":
    main()
