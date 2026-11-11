// Render cuadro a cuadro del video de RonyPDF 2.0 con la voz, la música y los efectos, en MP4 H.264 + AAC.
//
//   node render.mjs                         → los dos formatos: salida/RonyPDF-2.0-reel.mp4 y salida/RonyPDF-2.0-feed.mp4
//   node render.mjs --f=reel                → solo el vertical 1080 × 1920 (Instagram, TikTok, Shorts)
//   node render.mjs --f=feed --from=10 --to=15 --out=prueba.mp4
//   node render.mjs --f=reel --snap=1.5,12.4,20.9   → capturas PNG sueltas en snaps/ (para revisar)
//   node render.mjs --portada               → salida/portada.png (la portada del reel)
//
// Opciones: --fps=30 --workers=6 --audio=mezcla.wav --keep-frames --grano (ruido fino de película)
// Exporta con -crf 21 -maxrate 6M -bufsize 12M, que es lo que piden las redes sin perder detalle.
// Usa el Chromium sin ventana de Playwright y ffmpeg del PATH.
import { createRequire } from "module";
import path from "path";
import fs from "fs";
import { spawnSync } from "child_process";
import { pathToFileURL, fileURLToPath } from "url";

const require = createRequire(import.meta.url);
const { chromium } = require(path.join(process.env.APPDATA, "npm/node_modules/@playwright/mcp/node_modules/playwright-core"));
const CHROME = path.join(process.env.LOCALAPPDATA,
  "ms-playwright/chromium_headless_shell-1243/chrome-headless-shell-win64/chrome-headless-shell.exe");
const here = path.dirname(fileURLToPath(import.meta.url));

const args = process.argv.slice(2);
const opt = (name, def) => {
  const a = args.find(x => x.startsWith(`--${name}=`));
  return a ? a.slice(name.length + 3) : def;
};
const FORMATS = { reel: { w: 1080, h: 1920, out: "RonyPDF-2.0-reel.mp4" }, feed: { w: 1080, h: 1350, out: "RonyPDF-2.0-feed.mp4" } };
const escenas = JSON.parse(fs.readFileSync(path.join(here, "escenas.js"), "utf8").replace(/^[\s\S]*?window\.ESCENAS\s*=\s*/, "").replace(/;\s*$/, ""));
const DURATION = escenas.duracion;
const fps = Number(opt("fps", 30));
const workers = Math.max(1, Number(opt("workers", 6)));
const grano = args.includes("--grano");
const keepFrames = args.includes("--keep-frames");
const formats = opt("f", "") ? [opt("f")] : ["reel", "feed"];
const audio = path.resolve(here, opt("audio", "mezcla.wav"));

async function openPage(fmt, extra = "") {
  const { w, h } = FORMATS[fmt];
  const browser = await chromium.launch({ executablePath: CHROME, args: ["--allow-file-access-from-files"] });
  const page = await browser.newPage({ viewport: { width: w, height: h }, deviceScaleFactor: 1 });
  const errors = [];
  page.on("console", m => { if (m.type() === "error") errors.push(m.text()); });
  page.on("pageerror", e => errors.push(String(e)));
  await page.goto(pathToFileURL(path.join(here, "index.html")).href + `?render&f=${fmt}${extra}`, { waitUntil: "load" });
  await page.evaluate(() => window.TL.ready);
  return { browser, page, errors };
}

async function snaps(fmt, times) {
  const dir = path.join(here, "snaps");
  fs.mkdirSync(dir, { recursive: true });
  const ctx = await openPage(fmt);
  for (const t of times) {
    await ctx.page.evaluate(t => window.TL.seek(t), t);
    const out = path.join(dir, `${fmt}-t${t}.png`);
    await ctx.page.screenshot({ path: out });
    console.log(out);
  }
  if (ctx.errors.length) console.log("ERRORES DE CONSOLA:\n  " + ctx.errors.join("\n  "));
  await ctx.browser.close();
}

async function render(fmt) {
  const from = Math.max(0, Number(opt("from", 0)));
  const to = Math.min(DURATION, Number(opt("to", DURATION)));
  const outArg = opt("out", path.join("salida", FORMATS[fmt].out));
  const out = path.isAbsolute(outArg) ? outArg : path.join(here, outArg);
  fs.mkdirSync(path.dirname(out), { recursive: true });
  const firstFrame = Math.round(from * fps);
  const total = Math.round((to - from) * fps);
  const framesDir = path.join(here, "frames", fmt);
  fs.rmSync(framesDir, { recursive: true, force: true });
  fs.mkdirSync(framesDir, { recursive: true });
  const framePath = j => path.join(framesDir, String(j).padStart(5, "0") + ".jpg");
  console.log(`[${fmt}] ${total} cuadros (${from}–${to} s a ${fps} fps) con ${workers} navegadores → ${path.relative(here, out)}`);

  let next = 0, done = 0, lastLog = 0;
  const t0 = Date.now();
  const errors = new Set();
  async function worker(id) {
    let ctx = await openPage(fmt);
    while (true) {
      const j = next++;
      if (j >= total) break;
      const t = (firstFrame + j) / fps;
      let ok = false;
      for (let attempt = 1; attempt <= 4 && !ok; attempt++) {
        try {
          await ctx.page.evaluate(t => window.TL.seek(t), t);
          await ctx.page.screenshot({ path: framePath(j), type: "jpeg", quality: 93 });
          ok = fs.statSync(framePath(j)).size > 1000;
          if (!ok) throw new Error("archivo vacío");
        } catch (e) {
          console.warn(`\n  [navegador ${id}] cuadro ${j} falló (intento ${attempt}): ${e.message}`);
          try { await ctx.browser.close(); } catch { /* ya estaba cerrado */ }
          ctx = await openPage(fmt);
        }
      }
      if (!ok) throw new Error(`No se pudo capturar el cuadro ${j}`);
      done++;
      const now = Date.now();
      if (now - lastLog > 2000) {
        lastLog = now;
        const rate = done / ((now - t0) / 1000);
        console.log(`  ${done}/${total} cuadros · ${rate.toFixed(1)} cuadros/s · faltan ~${Math.ceil((total - done) / rate)} s`);
      }
    }
    ctx.errors.forEach(e => errors.add(e));
    await ctx.browser.close();
  }
  await Promise.all(Array.from({ length: Math.min(workers, total) }, (_, i) => worker(i + 1)));
  console.log(`  captura lista en ${((Date.now() - t0) / 1000).toFixed(1)} s`);
  if (errors.size) console.log("  Errores de consola:\n    " + [...errors].join("\n    "));

  const ff = ["-y", "-hide_banner", "-loglevel", "error", "-framerate", String(fps), "-i", path.join(framesDir, "%05d.jpg")];
  const hasAudio = fs.existsSync(audio);
  if (hasAudio) { if (from > 0) ff.push("-ss", String(from)); ff.push("-i", audio); }
  else console.warn(`  Aviso: no existe ${path.basename(audio)}; el video saldrá sin audio (corre: python musica.py).`);
  // JPEG (BT.601 rango completo) → BT.709 rango limitado, lo que esperan Instagram, LinkedIn y los reproductores
  ff.push("-vf", (grano ? "noise=c0s=3:allf=t," : "") + "scale=in_color_matrix=bt601:out_color_matrix=bt709:in_range=pc:out_range=tv,format=yuv420p",
    "-c:v", "libx264", "-preset", "slow", "-crf", "21", "-maxrate", "6M", "-bufsize", "12M", "-profile:v", "high", "-pix_fmt", "yuv420p",
    "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv", "-r", String(fps));
  if (hasAudio) ff.push("-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-shortest");
  ff.push("-movflags", "+faststart", out);
  const r = spawnSync("ffmpeg", ff, { stdio: "inherit" });
  if (r.status !== 0) { console.error(`ffmpeg falló (código ${r.status}); los cuadros quedan en ${framesDir}`); process.exit(1); }
  if (!keepFrames) fs.rmSync(framesDir, { recursive: true, force: true });
  console.log(`  listo → ${out} (${(fs.statSync(out).size / 1e6).toFixed(1)} MB)`);
}

async function portada() {
  const out = path.join(here, "salida", "portada.png");
  fs.mkdirSync(path.dirname(out), { recursive: true });
  const ctx = await openPage("reel", "&portada");
  await ctx.page.screenshot({ path: out });
  if (ctx.errors.length) console.log("ERRORES DE CONSOLA: " + ctx.errors.join(" | "));
  await ctx.browser.close();
  console.log("portada →", out);
}

const snapArg = opt("snap", "");
if (args.includes("--portada")) { await portada(); process.exit(0); }
for (const fmt of formats) {
  if (!FORMATS[fmt]) { console.error(`Formato desconocido: ${fmt} (usa reel o feed)`); process.exit(1); }
  if (snapArg) await snaps(fmt, snapArg.split(",").map(Number));
  else await render(fmt);
}
