"use client";
// Document viewer of the whole hub: images, video, audio, PDF, Word, PowerPoint, Excel/CSV, Markdown, HTML, code and text
// open here, in place, instead of having to be downloaded. Pages call useFileViewer().open(file, siblings).
import { ChevronLeft, ChevronRight, Download, ExternalLink, FileQuestion, Loader2, X } from "lucide-react";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import useSWR from "swr";
import { PdfView } from "@/components/pdf-view";
import { Button, Notice, cx } from "@/components/ui";
import { TableView } from "@/components/workspace/viewer";
import { bytes } from "@/lib/format";
import { useL } from "@/lib/i18n";

export type ViewerFile = { name: string; url: string; download?: string; size?: number | null; pdf?: string; table?: string; text?: string };

export function hubFile(f: any): ViewerFile {
  const id = f.id ?? f.file_id;
  const url = f.url ?? `/api/v1/files/${id}/content`;
  return { name: f.filename ?? f.name ?? "archivo", url, download: `${url}?download=true`, size: f.size_bytes,
    pdf: `/api/v1/files/${id}/preview.pdf`, table: `/api/v1/files/${id}/table`, text: `/api/v1/files/${id}/plain` };
}

const EXT = (n: string) => (n.match(/\.([a-z0-9]+)$/i)?.[1] ?? "").toLowerCase();
const has = (list: string, e: string) => list.split(" ").includes(e);
export type ViewerKind = "image" | "video" | "audio" | "pdf" | "office" | "table" | "markdown" | "html" | "text" | "other";
export function viewerKind(name: string): ViewerKind {
  const e = EXT(name);
  if (has("png jpg jpeg gif webp bmp svg avif", e)) return "image";
  if (has("mp4 webm ogv mov m4v mkv", e)) return "video";
  if (has("mp3 wav flac ogg m4a opus aac", e)) return "audio";
  if (e === "pdf") return "pdf";
  if (has("doc docx odt rtf ppt pptx odp xls ods", e)) return "office";
  if (has("csv tsv xlsx xlsm", e)) return "table";
  if (has("md markdown", e)) return "markdown";
  if (has("html htm", e)) return "html";
  if (has("txt log py js ts tsx jsx java c h cpp hpp cs go rs rb php r m jl sh sql kt swift scala lua f90 vhd v json jsonl yaml yml toml ini cfg xml tex bib rst ipynb srt vtt css", e)) return "text";
  return "other";
}

const fetchText = async (u: string) => {
  const r = await fetch(u, { credentials: "same-origin" });
  if (!r.ok) throw new Error((await r.json().catch(() => null))?.detail?.message ?? `HTTP ${r.status}`);
  return r.text();
};

function ImageBody({ f }: { f: ViewerFile }) {
  const L = useL();
  const [full, setFull] = useState(false);
  return (
    <div className={cx("h-full overflow-auto bg-[repeating-conic-gradient(rgb(var(--elevated))_0%_25%,transparent_0%_50%)] bg-[length:16px_16px] p-2 sm:p-4", !full && "grid place-items-center")}>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={f.url} alt={f.name} title={full ? L("Ajustar a la ventana", "Fit to window") : L("Ver a tamaño real", "Actual size")} onClick={() => setFull(!full)}
        className={cx("rounded", full ? "max-w-none cursor-zoom-out" : "max-h-full max-w-full cursor-zoom-in object-contain")} />
    </div>
  );
}

function TextBody({ f, kind }: { f: ViewerFile; kind: ViewerKind }) {
  const L = useL();
  const { data: text, error } = useSWR(f.text ?? f.url, fetchText, { revalidateOnFocus: false });
  const [source, setSource] = useState(false);
  if (error) return <div className="p-4"><Notice tone="danger">{error.message}</Notice></div>;
  if (text === undefined) return <Loader2 className="m-6 h-5 w-5 animate-spin text-muted" />;
  const rendered = kind !== "text" && !source;
  return (
    <div className="flex h-full min-h-0 flex-col">
      {kind !== "text" && <div className="flex shrink-0 justify-center gap-1 border-b border-border bg-surface px-2 py-1 text-xs">
        <Button size="sm" variant={rendered ? "outline" : "ghost"} className="!h-9 sm:!h-7" onClick={() => setSource(false)}>{L("Vista", "View")}</Button>
        <Button size="sm" variant={rendered ? "ghost" : "outline"} className="!h-9 sm:!h-7" onClick={() => setSource(true)}>{L("Código", "Source")}</Button></div>}
      <div className="min-h-0 flex-1 overflow-auto">
        {!rendered ? <pre className="min-h-full whitespace-pre-wrap break-words p-3 font-mono text-[12.5px] sm:p-4 sm:text-xs leading-relaxed">{text}</pre>
          : kind === "markdown" ? <div className="prose-chat mx-auto max-w-3xl p-4 text-[15px] sm:p-6 sm:text-[14.5px]"><ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown></div>
          /* no allow-same-origin: the page cannot reach the hub or the session */
          : <iframe title={f.name} sandbox="allow-scripts" srcDoc={text} className="h-full w-full bg-white" />}
      </div>
    </div>
  );
}

function TableBody({ f }: { f: ViewerFile }) {
  const { data, error } = useSWR(f.table ?? null, { revalidateOnFocus: false });
  const [sheet, setSheet] = useState(0);
  return <TableView tbl={data} error={error} sheet={sheet} setSheet={setSheet} />;
}

function Body({ f, dl }: { f: ViewerFile; dl: React.ReactNode }) {
  const L = useL();
  const kind = viewerKind(f.name);
  if (kind === "image") return <ImageBody f={f} />;
  if (kind === "video") return <div className="grid h-full place-items-center bg-black"><video key={f.url} src={f.url} controls className="max-h-full max-w-full" /></div>;
  if (kind === "audio") return <div className="grid h-full place-items-center p-6"><audio key={f.url} src={f.url} controls className="w-full max-w-xl" /></div>;
  if (kind === "pdf") return <PdfView url={f.url} fallback={dl} />;
  if (kind === "office") return f.pdf ? <PdfView url={f.pdf} fallback={dl} waiting={L("Preparando el documento… La primera vez puede tardar unos segundos.", "Preparing the document… The first time can take a few seconds.")} /> : null;
  if (kind === "table" && f.table) return <TableBody f={f} />;
  if (kind === "markdown" || kind === "html" || kind === "text") return <TextBody f={f} kind={kind} />;
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 p-6 text-center text-sm text-muted">
      <FileQuestion className="h-8 w-8 text-subtle" />
      <p>{L("Este tipo de archivo no se puede ver aquí.", "This type of file cannot be viewed here.")}</p>{dl}
    </div>
  );
}

const Ctx = createContext<{ open: (f: ViewerFile, siblings?: ViewerFile[]) => void }>({ open: () => {} });
export const useFileViewer = () => useContext(Ctx);

export function FileViewerProvider({ children }: { children: React.ReactNode }) {
  const L = useL();
  const [list, setList] = useState<ViewerFile[]>([]);
  const [i, setI] = useState(0);
  const closeBtn = useRef<HTMLButtonElement>(null);
  const back = useRef<Element | null>(null);
  const open = useCallback((f: ViewerFile, siblings?: ViewerFile[]) => {
    const all = siblings?.length ? siblings : [f];
    back.current = document.activeElement;
    setList(all);
    setI(Math.max(0, all.findIndex((x) => x.url === f.url)));
  }, []);
  const close = useCallback(() => { setList([]); (back.current as HTMLElement | null)?.focus?.(); }, []);
  const f = list[i];
  const n = list.length;
  useEffect(() => {
    if (!f) return;
    closeBtn.current?.focus();
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
      else if (n > 1 && e.key === "ArrowLeft") setI((x) => (x - 1 + n) % n);
      else if (n > 1 && e.key === "ArrowRight") setI((x) => (x + 1) % n);
    };
    window.addEventListener("keydown", key);
    const body = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { window.removeEventListener("keydown", key); document.body.style.overflow = body; };
  }, [f, n, close]);
  const touch = useRef<{ x: number; y: number } | null>(null);
  const swipeable = !!f && n > 1 && ["image", "markdown", "video", "other"].includes(viewerKind(f.name));
  const value = useMemo(() => ({ open }), [open]);
  const href = f?.download ?? (f ? `${f.url}${f.url.includes("?") ? "&" : "?"}download=true` : "");
  const dl = f && <a href={href}><Button size="sm" variant="outline"><Download className="h-3.5 w-3.5" />{L("Descargar", "Download")}</Button></a>;
  return (
    <Ctx.Provider value={value}>
      {children}
      {f && (
        <div className="fixed inset-0 z-[90] flex bg-black/60 pt-[env(safe-area-inset-top)] sm:p-6 sm:pt-6" role="dialog" aria-modal="true" aria-label={f.name} onClick={(e) => { if (e.target === e.currentTarget) close(); }}>
          <div className="mx-auto flex h-full w-full max-w-6xl flex-col overflow-hidden bg-surface pb-[env(safe-area-inset-bottom)] shadow-2xl sm:rounded-2xl sm:pb-0">
            <div className="flex shrink-0 items-center gap-1 border-b border-border px-3 py-2">
              {n > 1 && <>
                <Button size="icon" variant="ghost" className="!h-10 !w-10 sm:!h-8 sm:!w-8" aria-label={L("Anterior", "Previous")} onClick={() => setI((i - 1 + n) % n)}><ChevronLeft className="h-4 w-4" /></Button>
                <span className="text-xs tabular-nums text-muted">{i + 1}/{n}</span>
                <Button size="icon" variant="ghost" className="!h-10 !w-10 sm:!h-8 sm:!w-8" aria-label={L("Siguiente", "Next")} onClick={() => setI((i + 1) % n)}><ChevronRight className="h-4 w-4" /></Button></>}
              <div className="ml-1 min-w-0 flex-1">
                <p className="truncate text-sm font-medium" title={f.name}>{f.name}</p>
                {f.size != null && <p className="text-[11px] text-muted">{bytes(f.size)}</p>}
              </div>
              {viewerKind(f.name) !== "other" && <a href={viewerKind(f.name) === "office" && f.pdf ? f.pdf : f.url} target="_blank" rel="noreferrer" title={L("Abrir en otra pestaña", "Open in another tab")}><Button size="icon" variant="ghost" className="!h-10 !w-10 sm:!h-8 sm:!w-8" aria-label={L("Abrir en otra pestaña", "Open in another tab")}><ExternalLink className="h-4 w-4" /></Button></a>}
              <a href={href} title={L("Descargar", "Download")}><Button size="icon" variant="ghost" className="!h-10 !w-10 sm:!h-8 sm:!w-8" aria-label={L("Descargar", "Download")}><Download className="h-4 w-4" /></Button></a>
              <Button ref={closeBtn} size="icon" variant="ghost" className="!h-10 !w-10 sm:!h-8 sm:!w-8" aria-label={L("Cerrar", "Close")} onClick={close}><X className="h-4 w-4" /></Button>
            </div>
            <div className="min-h-0 flex-1 overflow-hidden"
              onTouchStart={swipeable ? (e) => { touch.current = { x: e.touches[0].clientX, y: e.touches[0].clientY }; } : undefined}
              onTouchEnd={swipeable ? (e) => {
                const s0 = touch.current; touch.current = null;
                if (!s0) return;
                const dx = e.changedTouches[0].clientX - s0.x, dy = e.changedTouches[0].clientY - s0.y;
                if (Math.abs(dx) > 70 && Math.abs(dy) < 40) setI((x) => (dx < 0 ? (x + 1) % n : (x - 1 + n) % n));
              } : undefined}><Body key={f.url} f={f} dl={dl} /></div>
          </div>
        </div>
      )}
    </Ctx.Provider>
  );
}
