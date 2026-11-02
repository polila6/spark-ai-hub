"use client";
// Chat message actions shared by every chat surface (playground, workspace assistant…):
// copy a message / a code block, edit a sent message, regenerate, resume from a message, reply quoting a selection.
import { Check, Copy, History, Pencil, RefreshCw, Reply, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Button, Textarea, Tip, cx } from "@/components/ui";
import { useL } from "@/lib/i18n";

// Put this class on the element that wraps a message bubble and its <MessageActions>: actions show on hover there.
export const MSG_GROUP = "group/msg";
// User bubbles use the accent colour as background, where the global accent ::selection is invisible.
export const USER_BUBBLE_SELECTION = "selection:bg-accent-fg selection:text-accent";

export async function copyText(text: string) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {  // http (no secure context) or denied permission
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    ta.remove();
  }
}

function useCopied(): [boolean, (text: string) => void] {
  const [done, setDone] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => () => clearTimeout(timer.current), []);
  return [done, (text) => { copyText(text).then(() => { setDone(true); clearTimeout(timer.current); timer.current = setTimeout(() => setDone(false), 1500); }); }];
}

/** Markdown quote of `text` for the composer ("> line" per line, then an empty line to type after it). */
export function quoteMarkdown(text: string) {
  return text.trim().split("\n").map((l) => (l.trim() ? `> ${l}` : ">")).join("\n") + "\n\n";
}

/** The start of a message to quote with the plain "Reply" button (whole message if short, cut at a word otherwise). */
export function quoteStart(text: string, max = 280) {
  const t = text.replace(/```[\s\S]*?```/g, "[…]").trim();
  if (t.length <= max) return t;
  const cut = t.slice(0, max);
  return `${cut.slice(0, Math.max(cut.lastIndexOf(" "), max * 0.6))}…`;
}

function hastText(n: any): string {
  if (!n) return "";
  if (n.type === "text") return n.value ?? "";
  return (n.children ?? []).map(hastText).join("");
}

/** ReactMarkdown `pre` override: fenced code with its language and a copy button. Use: components={{ pre: CodeBlock }}. */
export function CodeBlock({ node, children }: { node?: any; children?: React.ReactNode }) {
  const L = useL();
  const [done, copy] = useCopied();
  const code = node?.children?.find((c: any) => c.type === "element" && c.tagName === "code");
  const cls: string[] = code?.properties?.className ?? [];
  const lang = cls.map(String).find((c) => c.startsWith("language-"))?.slice(9);
  const text = hastText(code ?? node).replace(/\n$/, "");
  return (
    <div className="my-2 overflow-hidden rounded-[0.6rem] border border-border bg-bg">
      <div className="flex items-center gap-2 border-b border-border/70 px-3 py-1 text-[10px] text-subtle">
        <span className="font-mono uppercase tracking-wide">{lang || L("código", "code")}</span>
        <button type="button" className="ml-auto inline-flex items-center gap-1 rounded px-1.5 py-0.5 hover:bg-elevated hover:text-fg" aria-label={L("Copiar código", "Copy code")} onClick={() => copy(text)}>
          {done ? <Check className="h-3 w-3 text-ok" /> : <Copy className="h-3 w-3" />}{done ? L("Copiado", "Copied") : L("Copiar", "Copy")}
        </button>
      </div>
      <pre className="!m-0 !rounded-none !border-0">{children}</pre>
    </div>
  );
}

function Act({ label, onClick, disabled, children }: { label: string; onClick: () => void; disabled?: boolean; children: React.ReactNode }) {
  return (
    <Tip text={label}>
      <button type="button" aria-label={label} disabled={disabled} onClick={onClick}
        className="grid h-7 w-7 place-items-center rounded-md text-subtle transition hover:bg-elevated hover:text-fg disabled:pointer-events-none disabled:opacity-40">{children}</button>
    </Tip>
  );
}

export type MessageActionsProps = {
  role: string;              // "user" | "assistant"
  text: string;              // raw markdown that "Copy" copies
  onEdit?: () => void;       // user messages: open the inline editor
  onRegenerate?: () => void; // answer again (last answer, or a last user message left without answer)
  onResume?: () => void;     // "resume from here": delete everything after this message (ask first)
  onReply?: () => void;      // quote this message in the composer
  busy?: boolean;            // a turn is running: everything but Copy is disabled
  className?: string;
};

/** Small icon row under a bubble: always visible on touch screens, on hover (inside MSG_GROUP) with a mouse. */
export function MessageActions({ role, text, onEdit, onRegenerate, onResume, onReply, busy, className }: MessageActionsProps) {
  const L = useL();
  const [done, copy] = useCopied();
  return (
    <div className={cx("mt-0.5 flex items-center gap-0.5 transition-opacity [@media(hover:hover)]:opacity-0 [@media(hover:hover)]:focus-within:opacity-100 [@media(hover:hover)]:group-hover/msg:opacity-100", role === "user" && "justify-end", className)}>
      {text && <Act label={done ? L("Copiado", "Copied") : L("Copiar mensaje", "Copy message")} onClick={() => copy(text)}>{done ? <Check className="h-3.5 w-3.5 text-ok" /> : <Copy className="h-3.5 w-3.5" />}</Act>}
      {onReply && text && <Act label={L("Responder citando este mensaje", "Reply quoting this message")} onClick={onReply}><Reply className="h-3.5 w-3.5" /></Act>}
      {onEdit && <Act label={L("Editar y volver a enviar", "Edit and resend")} onClick={onEdit} disabled={busy}><Pencil className="h-3.5 w-3.5" /></Act>}
      {onRegenerate && <Act label={L("Regenerar respuesta", "Regenerate answer")} onClick={onRegenerate} disabled={busy}><RefreshCw className="h-3.5 w-3.5" /></Act>}
      {onResume && <Act label={L("Retomar desde aquí (borra lo posterior)", "Resume from here (deletes what follows)")} onClick={onResume} disabled={busy}><History className="h-3.5 w-3.5" /></Act>}
    </div>
  );
}

/** Inline editor for a sent user message. Ctrl/⌘+Enter saves, Esc cancels. */
export function MessageEditor({ initial, onSave, onCancel, busy }: { initial: string; onSave: (text: string) => void; onCancel: () => void; busy?: boolean }) {
  const L = useL();
  const [text, setText] = useState(initial);
  const ref = useRef<HTMLTextAreaElement>(null);
  useEffect(() => { const el = ref.current; if (el) { el.focus(); el.setSelectionRange(el.value.length, el.value.length); } }, []);
  const ok = text.trim().length > 0 && !busy;
  return (
    <div className="w-full max-w-[88%] space-y-2 rounded-2xl border border-accent/50 bg-surface p-2">
      <Textarea ref={ref} rows={Math.min(12, Math.max(3, text.split("\n").length + 1))} value={text} onChange={(e) => setText(e.target.value)} aria-label={L("Editar mensaje", "Edit message")}
        onKeyDown={(e) => { if (e.key === "Escape") { e.preventDefault(); onCancel(); } else if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && ok) { e.preventDefault(); onSave(text.trim()); } }} />
      <div className="flex flex-wrap items-center justify-end gap-2">
        <span className="mr-auto text-[11px] text-subtle">{L("Se borrará todo lo que viene después y se generará una respuesta nueva.", "Everything after it is deleted and a new answer is generated.")}</span>
        <Button size="sm" variant="ghost" onClick={onCancel}><X className="h-3.5 w-3.5" />{L("Cancelar", "Cancel")}</Button>
        <Button size="sm" variant="primary" disabled={!ok} onClick={() => onSave(text.trim())}><RefreshCw className="h-3.5 w-3.5" />{L("Guardar y regenerar", "Save & regenerate")}</Button>
      </div>
    </div>
  );
}

/**
 * Floating "Reply" button over a text selection inside an element marked `data-quote-scope` within `root`
 * (mark the rendered assistant answers). Clicking it hands the selected text to `onQuote` (e.g. quoteMarkdown → composer).
 */
export function SelectionQuote({ root, onQuote }: { root: React.RefObject<HTMLElement | null>; onQuote: (text: string) => void }) {
  const L = useL();
  const [pos, setPos] = useState<{ x: number; y: number; text: string } | null>(null);
  useEffect(() => {
    const touch = typeof window !== "undefined" && window.matchMedia?.("(pointer: coarse)").matches;
    let hideT: ReturnType<typeof setTimeout> | undefined;
    // hiding waits a little: a tap on the button (touch) collapses the selection just before the click arrives
    const hide = () => { clearTimeout(hideT); hideT = setTimeout(() => setPos(null), 350); };
    const update = () => {
      const s = window.getSelection();
      const text = s && !s.isCollapsed && s.rangeCount ? s.toString().trim() : "";
      if (!s || !text) return hide();
      const r = s.getRangeAt(0);
      const c = r.commonAncestorContainer;
      const scope = (c.nodeType === 1 ? (c as Element) : c.parentElement)?.closest("[data-quote-scope]");
      if (!scope || !root.current?.contains(scope)) return hide();
      const b = r.getBoundingClientRect();
      if (!b.width && !b.height) return hide();
      clearTimeout(hideT);
      // touch screens show their own copy/paste menu above the selection: go below it there
      setPos({ x: Math.min(window.innerWidth - 60, Math.max(60, b.left + b.width / 2)), y: touch ? b.bottom + 10 : Math.max(8, b.top - 38), text });
    };
    let t: ReturnType<typeof setTimeout> | undefined;
    const later = () => { clearTimeout(t); t = setTimeout(update, 120); };
    document.addEventListener("selectionchange", later);
    window.addEventListener("scroll", later, true);
    window.addEventListener("resize", later);
    return () => { clearTimeout(t); clearTimeout(hideT); document.removeEventListener("selectionchange", later); window.removeEventListener("scroll", later, true); window.removeEventListener("resize", later); };
  }, [root]);
  if (!pos || typeof document === "undefined") return null;
  return createPortal(
    <button type="button" style={{ left: pos.x, top: pos.y }}
      className="fixed z-[70] inline-flex -translate-x-1/2 items-center gap-1.5 rounded-lg border border-border bg-surface px-2.5 py-1.5 text-xs font-medium text-fg shadow-card hover:bg-elevated"
      onMouseDown={(e) => e.preventDefault()} onClick={() => { const txt = pos.text; window.getSelection()?.removeAllRanges(); setPos(null); onQuote(txt); }}>
      <Reply className="h-3.5 w-3.5 text-accent" />{L("Responder", "Reply")}
    </button>, document.body);
}
