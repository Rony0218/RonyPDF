# Video de RonyPDF 2.0, con voz

El video promocional de RonyPDF 2.0, hecho con código: la voz es Qwen3-TTS corriendo en local, la animación es HTML
con una línea de tiempo determinista, las pantallas son capturas reales de la app y la música y los efectos se
generan con numpy. Nada se descarga ni pasa por servicios externos.

Salen dos formatos, con subtítulos quemados palabra por palabra (mucha gente ve sin sonido):

- `salida/RonyPDF-2.0-reel.mp4`: 1080 × 1920, para Instagram, TikTok y Shorts.
- `salida/RonyPDF-2.0-feed.mp4`: 1080 × 1350, para LinkedIn.
- `salida/portada.png`: la portada del reel.

El ritmo lo marca la voz: las escenas, los subtítulos y los efectos se calculan con la duración real de cada línea
y con las marcas de tiempo de Whisper. Si cambias el guion, se vuelve a correr todo en orden y el video se reacomoda
solo.

| Archivo | Qué es |
|---|---|
| `guion.json` | Las líneas de la voz: texto, cómo se escribe en pantalla, pausa, escena y palabras clave. |
| `voz.py` | Genera la voz línea por línea y elige la mejor toma con Whisper → `voz/lNN.wav`, `voz/lineas.json`. |
| `texto.py` | Cómo se compara el guion con lo que oye Whisper y cómo se alinean los subtítulos. |
| `capturas.py` | Abre la app real sin ventana, a escala 2, y toma las capturas → `capturas/`. |
| `tiempos.py` | Arma la línea de tiempo desde la voz → `escenas.js`. |
| `musica.py` | Música, efectos y mezcla con la voz → `mezcla.wav` (−14 LUFS) y `musica.wav` (sin voz). |
| `index.html` | La animación. `?f=reel`, `?f=feed` o `?f=reel&portada`. Ábrelo en el navegador para verla, sin sonido. |
| `config.js` | Nombre, enlaces del cierre y el medio para apoyar el proyecto. Lo que quede en `null` no aparece. |
| `render.mjs` | Captura cada cuadro con Chromium sin ventana y arma los MP4 con ffmpeg. |
| `textos-publicacion.md` | Textos para Instagram y LinkedIn. |

## Cómo se regenera todo, en orden

Desde la carpeta `video/`:

```powershell
# 0. La voz de referencia, una sola vez: un WAV de 10 a 20 s con la voz del narrador
Copy-Item <tu-voz>.wav voz\referencia.wav

# 1. Voz: 4 a 8 tomas por línea, Whisper elige la exacta más expresiva (necesita la GPU; unos 15 min)
#    con un Python que tenga torch, qwen-tts y transformers
& <python-con-qwen-tts> -X utf8 voz.py
#    voz.py 7 12   rehace solo esas líneas · voz.py todo   rehace todas · voz.py verificar   vuelve a transcribir

# 2. Capturas reales de la app 2.0 (con el Python de la app; unos 2 min)
& ..\.venv\Scripts\python.exe -X utf8 capturas.py

# 3. Línea de tiempo (escenas, subtítulos y efectos) a partir de la voz
python tiempos.py

# 4. Música, efectos y mezcla final
python musica.py

# 5. Video: los dos formatos y la portada (unos 10 min en total)
node render.mjs
node render.mjs --portada
```

Para revisar sin renderizar todo: `node render.mjs --f=reel --snap=5,12.5,40` deja cuadros sueltos en `snaps/`.
Para un tramo: `node render.mjs --f=feed --from=40 --to=48 --out=prueba.mp4`.

## Lo que hay que saber

- **Sin descargas.** `voz.py` corre con `HF_HUB_OFFLINE=1` y usa los pesos que ya están en la caché de Hugging Face
  (Qwen3-TTS 1.7B Base y Whisper large-v3-turbo). Les pasa la ruta local del modelo, porque transformers intenta
  consultar la red aunque esté en modo sin conexión si recibe el nombre del repositorio.
- **Whisper a 16 kHz.** El pipeline de transformers no remuestrea un audio de 24 kHz que se le pasa como
  `{"raw", "sampling_rate"}`: lo oye 1,5 veces más lento y las marcas de tiempo salen estiradas. `voz.py` lo
  remuestrea antes.
- **Parecido 1.0.** Una toma sirve solo si Whisper oyó exactamente las palabras del guion, sin contar tildes,
  mayúsculas, puntuación ni espacios. Los nombres propios tienen alias: Whisper escribe «Ronnie PDF» o «Roni PDF»
  cuando la voz dice «Rony PDF», y «Ctrl» por «Control».
- **El texto de la pantalla puede ser distinto del hablado.** La voz lee «I Love PDF» (así lo pronuncia bien) y el
  subtítulo dice «iLovePDF»; `texto.py` alinea las dos versiones letra por letra.
- **Datos reales.** El tamaño del catálogo comprimido y lo que encuentra Censurar salen de correr la app en
  `capturas.py` (`capturas/datos.json`). La cifra de tokens (1.393.340.181) es la del 10 de noviembre de 2026.
- **La ficha de proveedor** que se censura la crea `capturas.py`, con datos personales inventados, porque los
  documentos de ejemplo de `scripts/demo_docs.py` no traen cédulas ni celulares.
- **Rutas.** `render.mjs` usa el Chromium sin ventana de Playwright (`%LOCALAPPDATA%\ms-playwright`) y el
  playwright-core de `@playwright/mcp`; si cambian, ajústalas al inicio del archivo. ffmpeg tiene que estar en el PATH.
- **Enlaces.** El repositorio sale en `config.js` (`repo`). LinkedIn, Instagram y el medio para apoyar el proyecto
  están en `null` hasta que se definan; después se vuelve a correr `node render.mjs`.
