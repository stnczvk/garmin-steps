// Renderuje wszystkie animowane grafiki dnia jednym uruchomieniem (jedna paczka, jedna przeglądarka).
// Użycie: node render.mjs zadania.json
// zadania.json: {"public": "<folder z PNG/fontem>", "jobs": [{"comp": "Obraz", "props": {...}, "out": "x.mov"}]}
import {bundle} from '@remotion/bundler';
import {renderMedia, selectComposition, openBrowser} from '@remotion/renderer';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const spec = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const serveUrl = await bundle({entryPoint: path.join(here, 'src/index.ts'), publicDir: spec.public});
const browser = await openBrowser('chrome', {browserExecutable: process.env.REMOTION_BROWSER || null});
let ok = 0;
for (const job of spec.jobs) {
  try {
    const composition = await selectComposition({serveUrl, id: job.comp, inputProps: job.props, puppeteerInstance: browser});
    await renderMedia({
      composition, serveUrl, inputProps: job.props, outputLocation: job.out, puppeteerInstance: browser,
      codec: 'prores', proResProfile: '4444', imageFormat: 'png', pixelFormat: 'yuva444p10le',
      concurrency: Number(process.env.REMOTION_CONCURRENCY) || null, logLevel: 'error',
    });
    ok++;
    console.log(`OK ${path.basename(job.out)}`);
  } catch (e) {
    console.log(`BŁĄD ${path.basename(job.out)}: ${String(e).slice(0, 300)}`);
  }
}
await browser.close({silent: true});
console.log(`animacje: ${ok}/${spec.jobs.length}`);
