// Files the assistant produced in a message (canvas documents, workspace files, files from run_code) and the links that
// open them straight from its answer: «aquí está el informe» becomes a blue link, no need to look in the chat's files.
// Pure functions (no React) so they can be tested; the components that draw the links live in components/deliverable-link.tsx.

export type Deliverable = { kind: "canvas" | "ws" | "file"; ref: string; name: string; file?: any };

export const SCHEME = "sah:";

export function deliverablesOf(tools?: any[]): Deliverable[] {
  const out: Deliverable[] = [];
  const add = (d: Deliverable) => {
    const i = out.findIndex((x) => x.kind === d.kind && x.ref === d.ref);
    if (i >= 0) out[i] = { ...out[i], ...d }; else out.push(d);
  };
  for (const t of tools ?? []) {
    if (!t) continue;
    if (t.canvas?.id) add({ kind: "canvas", ref: String(t.canvas.id), name: String(t.canvas.title ?? "Documento") });
    for (const p of t.workspace?.changed ?? []) add({ kind: "ws", ref: String(p), name: String(p).split("/").pop() || String(p) });
    for (const f of t.code?.files ?? []) {
      const id = f.id ?? f.file_id;
      if (id) add({ kind: "file", ref: String(id), name: String(f.filename ?? f.name ?? "archivo"), file: f });
    }
  }
  return out;
}

export const hrefOf = (d: Deliverable) => `${SCHEME}${d.kind}:${encodeURIComponent(d.ref)}`;

export function parseHref(href: string | undefined, items: Deliverable[]): Deliverable | null {
  if (!href || !href.startsWith(SCHEME)) return null;
  const [kind, ...rest] = href.slice(SCHEME.length).split(":");
  const ref = decodeURIComponent(rest.join(":"));
  return items.find((d) => d.kind === kind && d.ref === ref) ?? null;
}

const esc = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
// what must never be touched: code blocks, inline code that is not a bare file name, existing links and URLs
const FENCED = /(```[\s\S]*?```)/g;
// what must never be touched: existing links, URLs and inline code (a bare `file.md` is unwrapped first, see below)
const PROTECTED = /(\[[^\]\n]*\]\([^)\n]*\)|https?:\/\/\S+|`[^`\n]+`)/g;

const NOUN = "informe|reporte|documento|resumen|an[aá]lisis|archivo|fichero|presentaci[oó]n|resultado|gr[aá]fico|gr[aá]fica|tabla|script|c[oó]digo|hoja de c[aá]lculo|excel|csv|pdf|plan|propuesta|art[ií]culo|memoria|borrador|report|document|summary|analysis|file|presentation|result|chart|graph|table|spreadsheet|draft|proposal|article";
// JS `\b` only knows ASCII letters, so «aquí» / «está» need explicit Unicode boundaries
const B = "(?<![\\p{L}\\p{N}_])";
const E = "(?![\\p{L}\\p{N}_])";
const NOUN_PHRASE = new RegExp(`${B}(?:el|la|los|las|tu|su|este|esta|un|una|the|your|this|a|an)\\s+(?:${NOUN})(?:\\s+(?:final|completo|solicitado|pedido|generado|actualizado|complete|requested|generated|updated))?${E}`, "iu");
const CUE = new RegExp(`${B}(aqu[ií]|tienes|te dejo|te paso|he (?:creado|generado|preparado|guardado|escrito|elaborado|redactado|hecho)|ha quedado|est[aá] (?:listo|lista|disponible|guardado|guardada)|listo|lista|here(?:'s| is| are)?|i(?:'ve| have) (?:created|written|saved|generated|prepared|made)|is ready|ready)${E}`, "iu");

function affinity(noun: string, d: Deliverable): number {
  const n = noun.toLowerCase();
  const ext = (d.name.match(/\.([a-z0-9]+)$/i)?.[1] ?? "").toLowerCase();
  if (/gr[aá]fic|chart|graph/.test(n)) return "png jpg jpeg svg webp".includes(ext) && ext ? 3 : 0;
  if (/tabla|excel|csv|hoja|spreadsheet|table/.test(n)) return "csv xlsx xls tsv ods".split(" ").includes(ext) ? 3 : 0;
  if (/script|c[oó]digo/.test(n)) return "py js ts sh r sql ipynb".split(" ").includes(ext) ? 3 : 0;
  if (/pdf/.test(n)) return ext === "pdf" ? 3 : 0;
  if (/presentaci|presentation/.test(n)) return "pptx ppt odp".split(" ").includes(ext) ? 3 : 0;
  // written deliverables: a canvas document first, then text-like files
  if (d.kind === "canvas") return 2;
  return "md txt docx doc pdf html rtf odt".split(" ").includes(ext) ? 1 : 0;
}

/** Turn mentions of the produced files into links. First the exact names; then, for the lines that hand something over
 *  («aquí está el informe»), the noun phrase that names it. Everything else is left exactly as written. */
export function linkDeliverables(content: string, items: Deliverable[]): string {
  if (!content || !items.length) return content;
  const used = new Set<string>();
  const key = (d: Deliverable) => `${d.kind}:${d.ref}`;
  const byLength = [...items].filter((d) => d.name.length >= 3).sort((a, b) => b.name.length - a.name.length);
  const links: string[] = [];  // links are put in at the end, so a shorter name never matches inside a longer one's link
  const mark = (d: Deliverable, label: string) => { links.push(`[${label}](${hrefOf(d)})`); return `\uE000${links.length - 1}\uE001`; };
  // pass 1: exact names, only the first mention of each; `name` written as inline code is a plain mention of the file
  const pieces = content.split(FENCED).flatMap((fence, f) => {
    if (f % 2 === 1) return [{ text: fence, code: true }];
    let seg = fence;
    for (const d of byLength) seg = seg.replace(new RegExp("`" + esc(d.name) + "`", "gi"), d.name);
    return seg.split(PROTECTED).map((t, i) => ({ text: t, code: i % 2 === 1 }));
  });
  const text = pieces.map((p) => {
    if (p.code) return p.text;
    let s = p.text;
    for (const d of byLength) {
      if (used.has(key(d))) continue;
      const re = new RegExp(`(?<![\\w/.\\-])${esc(d.name)}(?![\\w\\-])`, "i");
      s = s.replace(re, (m) => { used.add(key(d)); return mark(d, m); });
    }
    return s;
  });
  // pass 2: hand-over lines without a file name («aquí está el informe»)
  const open = items.filter((d) => !used.has(key(d)));
  let linked = 0;
  const out = pieces.map((p, idx) => {
    const t = text[idx];
    if (p.code || linked >= 3 || !open.length) return t;
    return t.split("\n").map((line) => {
      if (linked >= 3 || !CUE.test(line)) return line;
      const m = NOUN_PHRASE.exec(line);
      if (!m) return line;
      const pool = open.filter((d) => !used.has(key(d)));
      if (!pool.length) return line;
      const best = pool.map((d, i) => ({ d, score: affinity(m[0], d), i })).sort((x, y) => y.score - x.score || y.i - x.i)[0];
      if (best.score === 0 && items.length > 1) return line; // ambiguous: better no link than a wrong one
      used.add(key(best.d)); linked++;
      return line.slice(0, m.index) + mark(best.d, m[0]) + line.slice(m.index + m[0].length);
    }).join("\n");
  });
  return out.join("").replace(/\uE000(\d+)\uE001/g, (_, n) => links[Number(n)]);
}
