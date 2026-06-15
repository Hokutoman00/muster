/**
 * _render.mjs — renders the submission media assets locally via the shared Chrome.
 *   cover.html  -> cover.png   (1200x675 viewport screenshot)
 *   deck.html   -> deck.pdf    (1280x720 landscape, CSS @page paginated)
 * Self-contained: opens its own page, closes only its own page (non-interfering).
 *   node assets/_render.mjs            (run from cases/band-of-agents/)
 */
import { fileURLToPath, pathToFileURL } from 'node:url';
import { dirname, join } from 'node:path';
import { openWorkPage } from '../../../.claude/scripts/browser/shared-chrome.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const coverUrl = pathToFileURL(join(HERE, 'cover.html')).href;
const deckUrl = pathToFileURL(join(HERE, 'deck.html')).href;
const coverPng = join(HERE, 'cover.png');
const deckPdf = join(HERE, 'deck.pdf');

const { page, dispose } = await openWorkPage({ headless: true });
try {
  // cover: exact-size screenshot
  await page.setViewportSize({ width: 1200, height: 675 });
  await page.goto(coverUrl, { waitUntil: 'networkidle' });
  await page.screenshot({ path: coverPng });
  console.log('wrote', coverPng);

  // deck: print to PDF using the page's own @page size
  await page.goto(deckUrl, { waitUntil: 'networkidle' });
  await page.pdf({ path: deckPdf, preferCSSPageSize: true, printBackground: true });
  console.log('wrote', deckPdf);
} finally {
  await dispose();
}
