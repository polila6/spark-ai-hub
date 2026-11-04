"use client";
// PDF pages drawn with pdf.js, so PDFs and converted Office documents look the same in every browser (phones have no
// built-in PDF viewer for embedded files). Pages are drawn only when they come near the screen.
import { Loader2, Minus, Plus } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui";
import { useL } from "@/lib/i18n";

type Doc = { numPages: number; getPage: (n: number) => Promise<any>; destroy: () => Promise<void> };

function Page({ doc, n, width, onVisible }: { doc: Doc; n: number; width: number; onVisible: (n: number) => void }) {
  const box = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const [near, setNear] = useState(n <= 2);
  const [ratio, setRatio] = useState(1.414);
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const io = new IntersectionObserver((es) => es.forEach((e) => { if (e.isIntersecting) setNear(true); }), { rootMargin: "800px" });
    const seen = new IntersectionObserver((es) => es.forEach((e) => { if (e.isIntersecting) onVisible(n); }), { threshold: 0.5 });
    io.observe(el); seen.observe(el);
    return () => { io.disconnect(); seen.disconnect(); };
  }, [n, onVisible]);
  useEffect(() => {
    if (!near || width <= 0) return;
    let task: any;
    let gone = false;
    doc.getPage(n).then((page) => {
      if (gone) return;
      const base = page.getViewport({ scale: 1 });
      setRatio(base.height / base.width);
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const vp = page.getViewport({ scale: (width / base.width) * dpr });
      const c = canvas.current;
      if (!c) return;
      c.width = Math.floor(vp.width);
      c.height = Math.floor(vp.height);
      task = page.render({ canvasContext: c.getContext("2d")!, viewport: vp });
      task.promise.catch(() => {});
    }).catch(() => {});
    return () => { gone = true; task?.cancel?.(); };
  }, [doc, n, near, width]);
  return (
    <div ref={box} data-pdf-page={n} className="mx-auto bg-white shadow-card" style={{ width, height: width * ratio }}>
      <canvas ref={canvas} className="block h-full w-full" />
    </div>
  );
}

export function PdfView({ url, waiting, fallback }: { url: string; waiting?: string; fallback?: React.ReactNode }) {
  const L = useL();
  const [doc, setDoc] = useState<Doc | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [zoom, setZoom] = useState(1);
  const [page, setPage] = useState(1);
  const [avail, setAvail] = useState(0);
  const [availH, setAvailH] = useState(0);
  const [landscape, setLandscape] = useState<number | null>(null);  // height/width of a landscape first page (slides)
  const list = useRef<HTMLDivElement>(null);
  useEffect(() => {
    let gone = false;
    let loaded: Doc | null = null;
    setDoc(null);
    setErr(null);
    setPage(1);
    (async () => {
      const pdfjs = await import("pdfjs-dist");
      pdfjs.GlobalWorkerOptions.workerSrc = new URL("pdfjs-dist/build/pdf.worker.min.mjs", import.meta.url).toString();
      const d = (await pdfjs.getDocument({ url, withCredentials: true, isEvalSupported: false }).promise) as unknown as Doc;
      if (gone) { d.destroy(); return; }
      loaded = d;
      const vp = (await d.getPage(1)).getViewport({ scale: 1 });
      if (gone) return;
      setLandscape(vp.height < vp.width ? vp.height / vp.width : null);
      setDoc(d);
    })().catch(async (e) => {
      if (gone) return;
      // The hub explains why a conversion failed: show that rather than pdf.js's generic error.
      const why = await fetch(url, { credentials: "same-origin" }).then((r) => (r.ok ? null : r.json())).then((j) => j?.detail?.message ?? j?.error?.message).catch(() => null);
      setErr(why || L("No se pudo abrir el documento.", "The document could not be opened.") + (e?.message ? ` (${String(e.message).slice(0, 120)})` : ""));
    });
    return () => { gone = true; loaded?.destroy().catch(() => {}); };
  }, [url]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const el = list.current;
    if (!el) return;
    const measure = () => { setAvail(el.clientWidth); setAvailH(el.clientHeight); };
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    measure();
    return () => ro.disconnect();
  }, []);
  // Fit to the width; a whole slide fits on screen too, so it does not need scrolling to be read.
  const fit = Math.min(avail - 32, 1000, landscape && availH > 200 ? (availH - 32) / landscape : Infinity);
  const width = Math.max(240, fit) * zoom;
  return (
    <div className="flex h-full min-h-0 flex-col bg-elevated/60">
      {doc && <div className="flex shrink-0 items-center justify-center gap-2 border-b border-border bg-surface px-2 py-1 text-xs text-muted">
        <span className="tabular-nums">{L(`Página ${page} de ${doc.numPages}`, `Page ${page} of ${doc.numPages}`)}</span>
        <span className="mx-1 h-4 w-px bg-border" />
        <Button size="icon" variant="ghost" className="!h-7 !w-7" aria-label={L("Reducir", "Zoom out")} disabled={zoom <= 0.5} onClick={() => setZoom((z) => Math.max(0.5, z - 0.25))}><Minus className="h-3.5 w-3.5" /></Button>
        <button type="button" className="w-10 tabular-nums hover:text-fg" title={L("Ajustar al ancho", "Fit to width")} onClick={() => setZoom(1)}>{Math.round(zoom * 100)}%</button>
        <Button size="icon" variant="ghost" className="!h-7 !w-7" aria-label={L("Ampliar", "Zoom in")} disabled={zoom >= 3} onClick={() => setZoom((z) => Math.min(3, z + 0.25))}><Plus className="h-3.5 w-3.5" /></Button>
      </div>}
      <div ref={list} className="min-h-0 flex-1 space-y-3 overflow-auto p-4">
        {err ? <div className="mx-auto max-w-md pt-10 text-center text-sm text-muted"><p>{err}</p>{fallback && <div className="mt-3">{fallback}</div>}</div>
          : !doc ? <div className="flex flex-col items-center gap-2 pt-16 text-sm text-muted"><Loader2 className="h-5 w-5 animate-spin" />{waiting ?? L("Abriendo el documento…", "Opening the document…")}</div>
          : Array.from({ length: doc.numPages }, (_, i) => <Page key={i} doc={doc} n={i + 1} width={width} onVisible={setPage} />)}
      </div>
    </div>
  );
}
