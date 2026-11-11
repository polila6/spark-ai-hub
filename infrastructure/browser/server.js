// Renders ONE page per request in a throw-away Chromium and returns its text, links, images and (optionally) a screenshot.
const http = require('http');
const { chromium } = require('playwright-core');

const PROXY = process.env.BROWSER_PROXY || 'http://webproxy:3128';
const MAX_ACTIVE = 3;
const HARD_MS = 55000;
const MAX_TEXT = 300000;
const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36';
let active = 0;

const ARGS = ['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu', '--disable-extensions', '--disable-background-networking',
  '--disable-sync', '--disable-component-update', '--no-first-run', '--mute-audio', '--disable-notifications',
  '--force-webrtc-ip-handling-policy=disable_non_proxied_udp', '--webrtc-ip-handling-policy=disable_non_proxied_udp',
  '--js-flags=--max-old-space-size=768', '--disable-features=Translate,MediaRouter,OptimizationHints'];

function okUrl(u) {
  try { const x = new URL(u); return x.protocol === 'http:' || x.protocol === 'https:'; } catch { return false; }
}

async function render(o) {
  const t0 = Date.now();
  const blocked = [];
  const browser = await chromium.launch({ headless: true, args: ARGS, proxy: { server: PROXY }, timeout: 20000 });
  const killer = setTimeout(() => browser.close().catch(() => {}), HARD_MS);
  try {
    const ctx = await browser.newContext({
      userAgent: UA, locale: o.locale || 'es-ES', viewport: { width: 1366, height: 900 }, acceptDownloads: false,
      serviceWorkers: 'block', javaScriptEnabled: true, permissions: [], ignoreHTTPSErrors: false,
    });
    await ctx.route('**/*', (route) => {
      const r = route.request();
      if (!okUrl(r.url()) && !r.url().startsWith('data:') && !r.url().startsWith('blob:')) { blocked.push(r.url().slice(0, 120)); return route.abort(); }
      if (r.resourceType() === 'media') return route.abort();
      return route.continue();
    });
    ctx.on('page', (p) => { if (p !== page) p.close().catch(() => {}); });
    const page = await ctx.newPage();
    page.on('dialog', (d) => d.dismiss().catch(() => {}));
    page.on('download', (d) => d.cancel().catch(() => {}));
    page.setDefaultTimeout(15000);
    let status = 0;
    try {
      const resp = await page.goto(o.url, { waitUntil: 'domcontentloaded', timeout: 25000 });
      status = resp ? resp.status() : 0;
    } catch (e) {
      if (!/Timeout/.test(String(e))) throw e;
    }
    await page.waitForLoadState('networkidle', { timeout: Math.min(o.wait_ms || 7000, 15000) }).catch(() => {});
    if (o.wait_for) await page.waitForSelector(String(o.wait_for).slice(0, 200), { timeout: 10000 }).catch(() => {});
    for (let i = 0; i < 4; i++) {   // lazy content
      await page.evaluate(() => window.scrollBy(0, window.innerHeight * 0.9)).catch(() => {});
      await page.waitForTimeout(350);
    }
    await page.evaluate(() => window.scrollTo(0, 0)).catch(() => {});
    const data = await page.evaluate(() => {
      const abs = (u) => { try { return new URL(u, location.href).href; } catch { return ''; } };
      const clone = document.body ? document.body.cloneNode(true) : null;
      if (clone) clone.querySelectorAll('script,style,noscript,template,svg').forEach((n) => n.remove());
      const text = (document.body ? document.body.innerText : '') || '';
      const links = [...document.querySelectorAll('a[href]')].slice(0, 250).map((a) => ({ text: (a.innerText || a.title || '').trim().slice(0, 120), href: abs(a.getAttribute('href')) }))
        .filter((l) => /^https?:/.test(l.href));
      const images = [...document.images].map((i) => ({ src: i.currentSrc || i.src, alt: (i.alt || '').slice(0, 160), w: i.naturalWidth, h: i.naturalHeight }))
        .filter((i) => /^https?:/.test(i.src) && i.w >= 120 && i.h >= 80).slice(0, 80);
      const meta = (n) => (document.querySelector(`meta[property="${n}"],meta[name="${n}"]`) || {}).content || '';
      return { title: document.title || '', text, links, images, description: meta('description') || meta('og:description'), og_image: abs(meta('og:image') || '') };
    });
    let shot = null;
    if (o.screenshot) {
      const buf = await page.screenshot({ type: 'jpeg', quality: 72, fullPage: !!o.full_page,
        clip: o.full_page ? undefined : undefined }).catch(() => null);
      if (buf && buf.length < 6 * 1024 * 1024) shot = buf.toString('base64');
    }
    return { ok: true, final_url: page.url(), status, title: data.title, description: data.description, text: data.text.slice(0, MAX_TEXT),
      links: data.links, images: data.images, og_image: data.og_image, screenshot_b64: shot, blocked: blocked.slice(0, 20),
      elapsed_ms: Date.now() - t0 };
  } finally {
    clearTimeout(killer);
    await browser.close().catch(() => {});
  }
}

http.createServer(async (req, res) => {
  const send = (code, obj) => { res.writeHead(code, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(obj)); };
  if (req.method === 'GET' && req.url === '/health') return send(200, { ok: true, active });
  if (req.method !== 'POST' || req.url !== '/render') return send(404, { ok: false, error: 'not found' });
  let body = '';
  req.on('data', (c) => { body += c; if (body.length > 20000) req.destroy(); });
  req.on('end', async () => {
    let o;
    try { o = JSON.parse(body); } catch { return send(400, { ok: false, error: 'JSON no válido' }); }
    if (!okUrl(o.url)) return send(400, { ok: false, error: 'solo http(s)' });
    if (active >= MAX_ACTIVE) return send(429, { ok: false, error: 'navegador ocupado, reintenta en unos segundos' });
    active++;
    try { send(200, await render(o)); }
    catch (e) { send(200, { ok: false, error: String(e.message || e).split('\n')[0].slice(0, 300) }); }
    finally { active--; }
  });
}).listen(3000, '0.0.0.0', () => console.log('browser listening on :3000'));
process.on('uncaughtException', (e) => console.error('uncaught', e));
process.on('unhandledRejection', (e) => console.error('unhandled', e));
