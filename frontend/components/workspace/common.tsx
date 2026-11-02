"use client";
// Shared pieces of the Workspace page: types, file-type icons, textarea helpers, remembered layout and the
// drag handle between panels.
import {
  File, FileArchive, FileAudio, FileBraces, FileCode, FileImage, FileSpreadsheet, FileTerminal, FileText, FileVideo,
  Folder, FolderOpen, Globe, Link2,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { Confirm, cx } from "@/components/ui";

export type Entry = { path: string; type: "file" | "dir" | "link"; size: number; mtime?: number; collapsed?: boolean };
export type Run = {
  id: string; status?: string; command: string; stdout?: string; stderr?: string; exit_code?: number; timed_out?: boolean;
  oom_killed?: boolean; seconds?: number; changed?: string[]; running?: boolean; started_at?: string; stop_requested?: boolean; stopped?: boolean;
};
export type Ws = { id: string; name: string; description?: string; size_bytes?: number; created_at?: string; last_used_at?: string | null };
export type Draft = { text: string; mtime?: number };

export const ext = (p: string) => { const n = p.split("/").pop() ?? ""; return n.includes(".") ? (n.split(".").pop() ?? "").toLowerCase() : ""; };
export const baseName = (p: string) => p.split("/").pop() ?? p;
export const parentOf = (p: string) => p.split("/").slice(0, -1).join("/");
export const IMG = ["png", "jpg", "jpeg", "gif", "webp", "bmp", "svg", "ico"];
export const TABLE = ["csv", "tsv", "xlsx", "xlsm"];
export const RUNNABLE: Record<string, (p: string) => string> = {
  py: (p) => `python "${p}"`, sh: (p) => `bash "${p}"`, js: (p) => `node "${p}"`, mjs: (p) => `node "${p}"`,
  r: (p) => `Rscript "${p}"`, tex: (p) => `latexmk -pdf -interaction=nonstopmode "${p}"`,
};
const CODE = ["py", "js", "mjs", "ts", "tsx", "jsx", "c", "h", "cpp", "hpp", "rs", "go", "java", "r", "jl", "m", "rb", "php", "lua", "sql", "css", "scss", "toml", "yaml", "yml", "ini", "cfg", "tex", "bib", "ipynb", "makefile"];
/** Files edited as prose (wrapped lines, no line numbers). */
export const PROSE = ["md", "txt", "tex", "bib", "rst", "log", ""];

export const rawUrl = (wid: string, path: string, download = false) =>
  `/api/v1/workspaces/${wid}/raw?path=${encodeURIComponent(path)}${download ? "&download=true" : ""}`;

export function FileIcon({ path, type, open, className }: { path: string; type: Entry["type"]; open?: boolean; className?: string }) {
  const c = cx("h-3.5 w-3.5 shrink-0", className);
  if (type === "dir") return open ? <FolderOpen className={cx(c, "text-accent")} /> : <Folder className={cx(c, "text-accent")} />;
  if (type === "link") return <Link2 className={cx(c, "text-subtle")} />;
  const e = ext(path);
  if (["py", "sh", "bash", "zsh"].includes(e)) return <FileTerminal className={cx(c, "text-info")} />;
  if (CODE.includes(e)) return <FileCode className={cx(c, "text-info")} />;
  if (TABLE.includes(e) || e === "parquet" || e === "ods") return <FileSpreadsheet className={cx(c, "text-ok")} />;
  if (IMG.includes(e)) return <FileImage className={cx(c, "text-warn")} />;
  if (e === "pdf") return <FileText className={cx(c, "text-danger")} />;
  if (["md", "txt", "docx", "doc", "odt", "rtf", "pptx", "rst", "log"].includes(e)) return <FileText className={cx(c, "text-muted")} />;
  if (["json", "jsonl", "geojson", "xml"].includes(e)) return <FileBraces className={cx(c, "text-warn")} />;
  if (["html", "htm"].includes(e)) return <Globe className={cx(c, "text-info")} />;
  if (["zip", "tar", "gz", "tgz", "bz2", "xz", "7z", "rar"].includes(e)) return <FileArchive className={cx(c, "text-muted")} />;
  if (["mp3", "wav", "flac", "ogg", "m4a"].includes(e)) return <FileAudio className={cx(c, "text-muted")} />;
  if (["mp4", "webm", "mov", "mkv", "avi"].includes(e)) return <FileVideo className={cx(c, "text-muted")} />;
  return <File className={cx(c, "text-subtle")} />;
}

// ---------------------------------------------------------------- textarea helpers

/** Insert text at the caret keeping the browser's undo history (falls back to setRangeText). */
export function insertText(el: HTMLTextAreaElement, text: string) {
  el.focus();
  let ok = false;
  try { ok = document.execCommand("insertText", false, text); } catch { ok = false; }
  if (!ok) {
    el.setRangeText(text, el.selectionStart, el.selectionEnd, "end");
    el.dispatchEvent(new Event("input", { bubbles: true }));
  }
}

/** Tab / Shift+Tab inside a code textarea: indent (or outdent) instead of leaving the field. Returns true if handled. */
export function handleIndent(e: React.KeyboardEvent<HTMLTextAreaElement>, unit = "    "): boolean {
  if (e.key !== "Tab" || e.ctrlKey || e.metaKey || e.altKey) return false;
  e.preventDefault();
  const el = e.currentTarget;
  const { value, selectionStart: s, selectionEnd: t } = el;
  if (!e.shiftKey && s === t) { insertText(el, unit); return true; }
  const start = value.lastIndexOf("\n", s - 1) + 1;
  const end = t > s && value[t - 1] === "\n" ? t - 1 : t;
  const lines = value.slice(start, end).split("\n");
  const out = e.shiftKey ? lines.map((l) => l.replace(new RegExp(`^( {1,${unit.length}}|\\t)`), "")) : lines.map((l) => unit + l);
  const block = out.join("\n");
  el.setSelectionRange(start, end);
  insertText(el, block);
  el.setSelectionRange(start, start + block.length);
  return true;
}

/** Shift+Enter in code: new line keeping the current line's indentation. */
export function newlineKeepIndent(el: HTMLTextAreaElement) {
  const { value, selectionStart: s } = el;
  const line = value.slice(value.lastIndexOf("\n", s - 1) + 1, s);
  insertText(el, `\n${line.match(/^[ \t]*/)?.[0] ?? ""}`);
}

/** Grow a textarea with its content, up to max px (then it scrolls). */
export function useAutoGrow(ref: React.RefObject<HTMLTextAreaElement | null>, value: string, max = 240) {
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(max, el.scrollHeight)}px`;
    el.style.overflowY = el.scrollHeight > max ? "auto" : "hidden";
  }, [ref, value, max]);
}

// ---------------------------------------------------------------- remembered state

/** useState that is remembered in this browser (localStorage, ignored when unavailable). */
export function usePersisted<T>(key: string, initial: T): [T, (v: T | ((p: T) => T)) => void] {
  const [v, setV] = useState<T>(initial);
  useEffect(() => {
    try {
      const raw = localStorage.getItem(key);
      if (raw) setV((p) => (typeof initial === "object" && initial && !Array.isArray(initial) ? { ...p, ...JSON.parse(raw) } : JSON.parse(raw)));
    } catch { /* storage unavailable */ }
  }, [key]); // eslint-disable-line react-hooks/exhaustive-deps
  const set = useCallback((x: T | ((p: T) => T)) => {
    setV((p) => {
      const n = typeof x === "function" ? (x as (p: T) => T)(p) : x;
      try { localStorage.setItem(key, JSON.stringify(n)); } catch { /* storage unavailable */ }
      return n;
    });
  }, [key]);
  return [v, set];
}

export function useMedia(query: string) {
  const [m, setM] = useState(false);
  useEffect(() => {
    const mq = window.matchMedia(query);
    const on = () => setM(mq.matches);
    on();
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, [query]);
  return m;
}

/** Current time, ticking every second while active (elapsed time of running commands). */
export function useNow(active: boolean) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [active]);
  return now;
}

/** Promise-based confirmation dialog: `if (await confirm({...})) …`. */
export function useConfirm() {
  const [st, setSt] = useState<null | { title: string; text: string; danger?: boolean; resolve: (v: boolean) => void }>(null);
  const confirm = useCallback((o: { title: string; text: string; danger?: boolean }) => new Promise<boolean>((resolve) => setSt({ ...o, resolve })), []);
  const element = st ? (
    <Confirm open title={st.title} text={st.text} danger={st.danger}
      onOpenChange={(o) => { if (!o) { st.resolve(false); setSt(null); } }}
      onConfirm={() => { st.resolve(true); setSt(null); }} />
  ) : null;
  return { confirm, confirmElement: element };
}

// ---------------------------------------------------------------- panel resize handle

/** Drag handle between two panels. dir "x" resizes widths, "y" heights; arrow keys move it too; double click resets. */
export function Splitter({ dir, onDelta, onReset, label }: { dir: "x" | "y"; onDelta: (d: number) => void; onReset?: () => void; label: string }) {
  const last = useRef<number | null>(null);
  const [drag, setDrag] = useState(false);
  const pos = (e: React.PointerEvent) => (dir === "x" ? e.clientX : e.clientY);
  return (
    <>
      <div role="separator" aria-orientation={dir === "x" ? "vertical" : "horizontal"} aria-label={label} title={label} tabIndex={0}
        className={cx("group relative z-10 shrink-0 touch-none select-none outline-none",
          dir === "x" ? "-mx-1 w-2 cursor-col-resize" : "-my-1 h-2 cursor-row-resize")}
        onPointerDown={(e) => { if (e.button !== 0) return; e.currentTarget.setPointerCapture(e.pointerId); last.current = pos(e); setDrag(true); }}
        onPointerMove={(e) => { if (last.current === null) return; const p = pos(e); if (p !== last.current) { onDelta(p - last.current); last.current = p; } }}
        onPointerUp={(e) => { last.current = null; setDrag(false); e.currentTarget.releasePointerCapture(e.pointerId); }}
        onPointerCancel={() => { last.current = null; setDrag(false); }}
        onDoubleClick={onReset}
        onKeyDown={(e) => {
          const k: Record<string, number> = dir === "x" ? { ArrowLeft: -16, ArrowRight: 16 } : { ArrowUp: -16, ArrowDown: 16 };
          const d = k[e.key];
          if (d) { e.preventDefault(); onDelta(d); }
        }}>
        <div className={cx("absolute bg-border transition-colors group-hover:bg-accent/40 group-focus-visible:bg-accent/60",
          dir === "x" ? "inset-y-0 left-1/2 w-px -translate-x-1/2 group-hover:w-[3px]" : "inset-x-0 top-1/2 h-px -translate-y-1/2 group-hover:h-[3px]",
          drag && "!bg-accent/60")} />
      </div>
      {/* while dragging, iframes (PDF/HTML previews) must not swallow the pointer */}
      {drag && <div className={cx("fixed inset-0 z-[70]", dir === "x" ? "cursor-col-resize" : "cursor-row-resize")} />}
    </>
  );
}
