"use client";
// File tree of a workspace: icons by type, folders that open/close, a context menu (right click or ⋯), drag & drop
// to upload into a folder or to move files and folders, F2 to rename and Delete to remove.
import * as DM from "@radix-ui/react-dropdown-menu";
import {
  ChevronRight, Copy, Download, Ellipsis, ExternalLink, FilePlus2, FolderInput, FolderPlus, HardDriveDownload, HardDriveUpload,
  Pencil, Play, Trash2, Upload,
} from "lucide-react";
import { useState } from "react";
import { cx } from "@/components/ui";
import { bytes } from "@/lib/format";
import { useL } from "@/lib/i18n";
import { type Entry, FileIcon, RUNNABLE, baseName, ext, parentOf } from "./common";

export type TreeAction = "open" | "run" | "newtab" | "download" | "export" | "rename" | "move" | "copy" | "delete"
  | "newfile" | "newdir" | "upload" | "import";
export const DRAG_TYPE = "application/x-sah-ws-path";

type Menu = { entry: Entry | null; x: number; y: number };

export function FileTree({ entries, selected, active, closed, onToggle, filter, onSelect, onAction, onDropFiles, onMove }: {
  entries: Entry[]; selected: string | null; active: string | null; closed: Set<string>; onToggle: (path: string) => void; filter: string;
  onSelect: (e: Entry) => void; onAction: (a: TreeAction, e: Entry | null) => void;
  onDropFiles: (folder: string, files: FileList) => void; onMove: (src: string, folder: string) => void;
}) {
  const L = useL();
  const [menu, setMenu] = useState<Menu | null>(null);
  const [drop, setDrop] = useState<string | null>(null);
  const q = filter.trim().toLowerCase();
  const visible = q
    ? entries.filter((e) => baseName(e.path).toLowerCase().includes(q) || (q.includes("/") && e.path.toLowerCase().includes(q)))
    : entries.filter((e) => { const parts = e.path.split("/"); for (let i = 1; i < parts.length; i++) if (closed.has(parts.slice(0, i).join("/"))) return false; return true; });

  const accepts = (ev: React.DragEvent) => ev.dataTransfer.types.includes("Files") || ev.dataTransfer.types.includes(DRAG_TYPE);
  function dropOn(ev: React.DragEvent, folder: string) {
    ev.preventDefault();
    ev.stopPropagation();
    setDrop(null);
    const src = ev.dataTransfer.getData(DRAG_TYPE);
    if (src) {
      if (src === folder || folder.startsWith(`${src}/`) || parentOf(src) === folder) return;
      onMove(src, folder);
    } else if (ev.dataTransfer.files.length) onDropFiles(folder, ev.dataTransfer.files);
  }
  const openMenu = (entry: Entry | null, x: number, y: number) => { if (entry) onSelect(entry); setMenu({ entry, x, y }); };

  return (
    <div className={cx("min-h-full rounded-lg pb-6 transition", drop === "" && "bg-accent/[0.06] ring-1 ring-inset ring-accent/40")}
      onContextMenu={(ev) => { ev.preventDefault(); openMenu(null, ev.clientX, ev.clientY); }}
      onDragOver={(ev) => { if (!accepts(ev)) return; ev.preventDefault(); setDrop(""); }}
      onDragLeave={(ev) => { if (!ev.currentTarget.contains(ev.relatedTarget as Node)) setDrop(null); }}
      onDrop={(ev) => dropOn(ev, "")}>
      {q && visible.length === 0 && <p className="px-3 py-4 text-center text-xs text-muted">{L("Ningún archivo coincide", "No file matches")}</p>}
      <ul role="tree" aria-label={L("Archivos del workspace", "Workspace files")} className="space-y-px text-[13px]">
        {visible.map((e) => {
          const depth = q ? 0 : e.path.split("/").length - 1;
          const isDir = e.type === "dir";
          const open = isDir && !closed.has(e.path);
          const target = isDir ? e.path : parentOf(e.path);
          return (
            <li key={e.path} role="treeitem" aria-expanded={isDir ? open : undefined} aria-selected={selected === e.path}
              draggable onDragStart={(ev) => { ev.dataTransfer.setData(DRAG_TYPE, e.path); ev.dataTransfer.effectAllowed = "move"; }}
              onDragOver={(ev) => { if (!accepts(ev)) return; ev.preventDefault(); ev.stopPropagation(); setDrop(target); }}
              onDrop={(ev) => dropOn(ev, target)}
              onContextMenu={(ev) => { ev.preventDefault(); ev.stopPropagation(); openMenu(e, ev.clientX, ev.clientY); }}
              className={cx("group relative flex items-center rounded-md",
                selected === e.path ? "bg-accent/12 text-fg" : active === e.path ? "bg-elevated/70" : "hover:bg-elevated",
                isDir && drop === e.path && "bg-accent/15 ring-1 ring-inset ring-accent/50")}>
              <button type="button" style={{ paddingLeft: 4 + depth * 14 }} title={e.path}
                className="flex min-w-0 flex-1 items-center gap-1.5 py-[5px] pr-1 text-left outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent/50 rounded-md"
                onClick={() => { onSelect(e); if (isDir && !q) onToggle(e.path); else if (!isDir) onAction("open", e); }}
                onKeyDown={(ev) => {
                  if (ev.key === "F2") { ev.preventDefault(); onAction("rename", e); }
                  if (ev.key === "Delete") { ev.preventDefault(); onAction("delete", e); }
                }}>
                {isDir && !q ? <ChevronRight className={cx("h-3 w-3 shrink-0 text-subtle transition-transform", open && "rotate-90")} /> : <span className="w-3 shrink-0" />}
                <FileIcon path={e.path} type={e.type} open={open} />
                <span className={cx("truncate", e.type === "link" && "italic text-subtle", active === e.path && "font-medium")}>{baseName(e.path)}</span>
                {q && parentOf(e.path) && <span className="truncate text-[11px] text-subtle">{parentOf(e.path)}/</span>}
                {e.type === "file" && <span className="ml-auto shrink-0 pl-1 text-[10px] tabular-nums text-subtle opacity-0 transition group-hover:opacity-100">{bytes(e.size)}</span>}
              </button>
              <button type="button" aria-label={L("Acciones", "Actions")} title={L("Acciones (o clic derecho)", "Actions (or right click)")}
                className="mr-0.5 shrink-0 rounded p-0.5 text-subtle opacity-0 transition hover:bg-border/50 hover:text-fg focus-visible:opacity-100 group-hover:opacity-100 [@media(hover:none)]:opacity-100"
                onClick={(ev) => { const r = ev.currentTarget.getBoundingClientRect(); openMenu(e, r.left, r.bottom); }}>
                <Ellipsis className="h-3.5 w-3.5" />
              </button>
            </li>
          );
        })}
      </ul>
      {menu && <EntryMenu menu={menu} onClose={() => setMenu(null)} onAction={onAction} />}
    </div>
  );
}

function EntryMenu({ menu, onClose, onAction }: { menu: Menu; onClose: () => void; onAction: (a: TreeAction, e: Entry | null) => void }) {
  const L = useL();
  const e = menu.entry;
  const Item = ({ a, icon, label, danger, hint }: { a: TreeAction; icon: React.ReactNode; label: string; danger?: boolean; hint?: string }) => (
    <DM.Item onSelect={() => onAction(a, e)}
      className={cx("flex cursor-pointer items-center gap-2 rounded-lg px-2.5 py-1.5 text-[13px] outline-none",
        danger ? "text-danger data-[highlighted]:bg-danger/10" : "text-fg data-[highlighted]:bg-elevated")}>
      <span className="text-muted">{icon}</span>{label}{hint && <span className="ml-auto pl-4 text-[11px] text-subtle">{hint}</span>}
    </DM.Item>
  );
  const Sep = () => <DM.Separator className="my-1 h-px bg-border" />;
  const i = "h-3.5 w-3.5";
  const isDir = !e || e.type === "dir";
  return (
    <DM.Root open onOpenChange={(o) => { if (!o) onClose(); }}>
      <DM.Trigger asChild><span aria-hidden style={{ position: "fixed", left: menu.x, top: menu.y, width: 1, height: 1 }} /></DM.Trigger>
      <DM.Portal>
        <DM.Content align="start" sideOffset={2} collisionPadding={8} className="z-[90] min-w-[220px] rounded-xl border border-border bg-surface p-1 shadow-card"
          onCloseAutoFocus={(ev) => ev.preventDefault()}>
          {e && <p className="truncate px-2.5 pb-1 pt-1 font-mono text-[11px] text-subtle" title={e.path}>{e.path}</p>}
          {e && e.type === "file" && <>
            <Item a="open" icon={<FileIcon path={e.path} type="file" className="!text-muted" />} label={L("Abrir", "Open")} />
            {RUNNABLE[ext(e.path)] && <Item a="run" icon={<Play className={i} />} label={L("Ejecutar en el terminal", "Run in the terminal")} />}
            <Item a="newtab" icon={<ExternalLink className={i} />} label={L("Abrir en una pestaña nueva", "Open in a new browser tab")} />
            <Item a="download" icon={<Download className={i} />} label={L("Descargar", "Download")} />
            <Item a="export" icon={<HardDriveUpload className={i} />} label={L("Guardar copia en Mis archivos", "Save a copy to My files")} />
            <Sep />
          </>}
          {isDir && <>
            <Item a="newfile" icon={<FilePlus2 className={i} />} label={e ? L("Nuevo archivo aquí…", "New file here…") : L("Nuevo archivo…", "New file…")} />
            <Item a="newdir" icon={<FolderPlus className={i} />} label={e ? L("Nueva carpeta aquí…", "New folder here…") : L("Nueva carpeta…", "New folder…")} />
            <Item a="upload" icon={<Upload className={i} />} label={e ? L("Subir archivos aquí…", "Upload files here…") : L("Subir archivos…", "Upload files…")} />
            <Item a="import" icon={<HardDriveDownload className={i} />} label={L("Traer desde Mis archivos…", "Bring from My files…")} />
            <Item a="download" icon={<Download className={i} />} label={e ? L("Descargar carpeta (ZIP)", "Download folder (ZIP)") : L("Descargar todo (ZIP)", "Download everything (ZIP)")} />
            {e && <Sep />}
          </>}
          {e && <>
            <Item a="rename" icon={<Pencil className={i} />} label={L("Renombrar…", "Rename…")} hint="F2" />
            <Item a="move" icon={<FolderInput className={i} />} label={L("Mover a…", "Move to…")} />
            <Item a="copy" icon={<Copy className={i} />} label={L("Copiar ruta", "Copy path")} />
            <Sep />
            <Item a="delete" icon={<Trash2 className={i} />} label={L("Borrar…", "Delete…")} danger hint={L("Supr", "Del")} />
          </>}
        </DM.Content>
      </DM.Portal>
    </DM.Root>
  );
}
