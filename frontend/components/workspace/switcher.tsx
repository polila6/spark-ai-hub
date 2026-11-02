"use client";
// Workspace switcher (dropdown with sizes and last activity) and the dialogs to create, rename and manage them.
import * as DM from "@radix-ui/react-dropdown-menu";
import { Check, ChevronsUpDown, Download, FolderCog, Pencil, Plus, SquareTerminal, Trash2 } from "lucide-react";
import { useState } from "react";
import { Button, Field, Input, Modal, Textarea, cx } from "@/components/ui";
import { ago, bytes, dt } from "@/lib/format";
import { useI18n, useL } from "@/lib/i18n";
import type { Ws } from "./common";

const byRecent = (a: Ws, b: Ws) => (b.last_used_at ?? b.created_at ?? "").localeCompare(a.last_used_at ?? a.created_at ?? "");

export function WorkspaceSwitcher({ list, current, onSwitch, onNew, onRename, onManage, onDelete }: {
  list: Ws[]; current?: Ws; onSwitch: (id: string) => void; onNew: () => void; onRename: (w: Ws) => void; onManage: () => void; onDelete: (w: Ws) => void;
}) {
  const L = useL();
  const { lang } = useI18n();
  const item = "flex cursor-pointer items-center gap-2 rounded-lg px-2.5 py-1.5 text-[13px] text-fg outline-none data-[highlighted]:bg-elevated";
  return (
    <DM.Root>
      <DM.Trigger asChild>
        <button type="button" className="flex min-w-0 max-w-[60vw] items-center gap-2 rounded-xl border border-border bg-surface px-2.5 py-1.5 text-left shadow-sm transition hover:bg-elevated focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 sm:max-w-[340px]">
          <span className="rounded-lg bg-accent/12 p-1 text-accent"><SquareTerminal className="h-4 w-4" /></span>
          <span className="min-w-0">
            <span className="block truncate text-sm font-semibold leading-tight">{current?.name ?? "Workspace"}</span>
            <span className="block truncate text-[10.5px] leading-tight text-muted">{current ? `${bytes(current.size_bytes)}${current.description ? ` · ${current.description}` : ""}` : "…"}</span>
          </span>
          <ChevronsUpDown className="ml-1 h-3.5 w-3.5 shrink-0 text-subtle" />
        </button>
      </DM.Trigger>
      <DM.Portal>
        <DM.Content align="start" sideOffset={6} className="z-[90] max-h-[70vh] w-[320px] overflow-y-auto rounded-xl border border-border bg-surface p-1 shadow-card">
          <p className="px-2.5 pb-1 pt-1.5 text-[11px] font-medium text-muted">{L("Tus workspaces · recientes primero", "Your workspaces · recent first")}</p>
          {[...list].sort(byRecent).map((w) => (
            <DM.Item key={w.id} className={item} onSelect={() => onSwitch(w.id)}>
              <Check className={cx("h-3.5 w-3.5 shrink-0 text-accent", w.id !== current?.id && "invisible")} />
              <span className="min-w-0 flex-1">
                <span className={cx("block truncate", w.id === current?.id && "font-medium")}>{w.name}</span>
                <span className="block truncate text-[11px] text-muted">{bytes(w.size_bytes)} · {w.last_used_at ? L(`usado ${ago(w.last_used_at, lang)}`, `used ${ago(w.last_used_at, lang)}`) : L("sin usar todavía", "not used yet")}</span>
              </span>
            </DM.Item>
          ))}
          <DM.Separator className="my-1 h-px bg-border" />
          <DM.Item className={item} onSelect={onNew}><Plus className="h-3.5 w-3.5 text-muted" />{L("Nuevo workspace…", "New workspace…")}</DM.Item>
          {current && <>
            <DM.Item className={item} onSelect={() => onRename(current)}><Pencil className="h-3.5 w-3.5 text-muted" />{L("Renombrar este workspace…", "Rename this workspace…")}</DM.Item>
            <DM.Item className={item} onSelect={() => { window.location.href = `/api/v1/workspaces/${current.id}/zip`; }}><Download className="h-3.5 w-3.5 text-muted" />{L("Descargar todo (ZIP)", "Download everything (ZIP)")}</DM.Item>
          </>}
          <DM.Item className={item} onSelect={onManage}><FolderCog className="h-3.5 w-3.5 text-muted" />{L("Gestionar workspaces…", "Manage workspaces…")}</DM.Item>
          {current && list.length > 1 && <>
            <DM.Separator className="my-1 h-px bg-border" />
            <DM.Item className={cx(item, "!text-danger data-[highlighted]:!bg-danger/10")} onSelect={() => onDelete(current)}><Trash2 className="h-3.5 w-3.5" />{L("Borrar este workspace…", "Delete this workspace…")}</DM.Item>
          </>}
        </DM.Content>
      </DM.Portal>
    </DM.Root>
  );
}

/** Create (w undefined) or rename/describe a workspace. */
export function WsDialog({ w, onClose, onSubmit }: { w?: Ws; onClose: () => void; onSubmit: (name: string, description: string) => Promise<void> }) {
  const L = useL();
  const [name, setName] = useState(w?.name ?? "");
  const [desc, setDesc] = useState(w?.description ?? "");
  const [busy, setBusy] = useState(false);
  return (
    <Modal open onOpenChange={(o) => !o && onClose()} title={w ? L("Renombrar workspace", "Rename workspace") : L("Nuevo workspace", "New workspace")}
      description={w ? undefined : L("Un workspace es una carpeta propia con su terminal y su chat. Crea uno por proyecto.", "A workspace is a folder of its own with its terminal and chat. Make one per project.")}>
      <form className="space-y-3" onSubmit={async (e) => { e.preventDefault(); if (!name.trim()) return; setBusy(true); try { await onSubmit(name.trim(), desc.trim()); } finally { setBusy(false); } }}>
        <Field label={L("Nombre", "Name")}><Input autoFocus value={name} maxLength={120} onChange={(e) => setName(e.target.value)} placeholder={L("Tesis, Proyecto X…", "Thesis, Project X…")} /></Field>
        <Field label={L("Descripción (opcional)", "Description (optional)")} hint={L("Se muestra en el selector para distinguirlos.", "Shown in the switcher to tell them apart.")}>
          <Textarea rows={2} value={desc} maxLength={2000} onChange={(e) => setDesc(e.target.value)} /></Field>
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>{L("Cancelar", "Cancel")}</Button>
          <Button type="submit" variant="primary" loading={busy} disabled={!name.trim()}>{w ? L("Guardar", "Save") : L("Crear", "Create")}</Button>
        </div>
      </form>
    </Modal>
  );
}

export function ManageDialog({ list, current, maxGb, onClose, onOpen, onNew, onRename, onDelete }: {
  list: Ws[]; current?: Ws; maxGb?: number; onClose: () => void; onOpen: (id: string) => void; onNew: () => void; onRename: (w: Ws) => void; onDelete: (w: Ws) => void;
}) {
  const L = useL();
  const { lang } = useI18n();
  const total = list.reduce((s, w) => s + (w.size_bytes ?? 0), 0);
  return (
    <Modal open wide onOpenChange={(o) => !o && onClose()} title={L("Tus workspaces", "Your workspaces")}
      description={L(`${list.length} workspaces · ${bytes(total)} en total${maxGb ? ` · máximo ${maxGb} GB cada uno` : ""}`, `${list.length} workspaces · ${bytes(total)} in total${maxGb ? ` · up to ${maxGb} GB each` : ""}`)}>
      <div className="divide-y divide-border/70 rounded-xl border border-border">
        {list.map((w) => (
          <div key={w.id} className={cx("flex flex-wrap items-center gap-3 px-4 py-3", w.id === current?.id && "bg-accent/[0.05]")}>
            <div className="min-w-0 flex-1">
              <p className="flex items-center gap-2 truncate text-sm font-medium">{w.name}{w.id === current?.id && <span className="rounded-full bg-accent/12 px-2 py-0.5 text-[10.5px] font-medium text-accent">{L("abierto", "open")}</span>}</p>
              {w.description && <p className="truncate text-xs text-muted">{w.description}</p>}
              <p className="text-[11px] text-subtle">{bytes(w.size_bytes)} · {L("creado", "created")} {dt(w.created_at, lang)} · {w.last_used_at ? L(`último uso ${ago(w.last_used_at, lang)}`, `last used ${ago(w.last_used_at, lang)}`) : L("sin usar todavía", "not used yet")}</p>
            </div>
            <div className="flex items-center gap-1">
              {w.id !== current?.id && <Button size="sm" variant="outline" onClick={() => onOpen(w.id)}>{L("Abrir", "Open")}</Button>}
              <Button size="icon" variant="ghost" className="!h-8 !w-8" title={L("Renombrar", "Rename")} onClick={() => onRename(w)}><Pencil className="h-3.5 w-3.5" /></Button>
              <a href={`/api/v1/workspaces/${w.id}/zip`} title={L("Descargar ZIP", "Download ZIP")}><Button size="icon" variant="ghost" className="!h-8 !w-8"><Download className="h-3.5 w-3.5" /></Button></a>
              {list.length > 1 && <Button size="icon" variant="ghost" className="!h-8 !w-8 hover:!text-danger" title={L("Borrar", "Delete")} onClick={() => onDelete(w)}><Trash2 className="h-3.5 w-3.5" /></Button>}
            </div>
          </div>
        ))}
      </div>
      <div className="mt-4 flex items-center justify-between gap-3">
        <p className="text-[11px] text-muted">{L("Borrar un workspace elimina sus archivos y su chat. Descarga el ZIP antes si lo necesitas.", "Deleting a workspace removes its files and its chat. Download the ZIP first if you need it.")}</p>
        <Button variant="primary" size="sm" onClick={onNew}><Plus className="h-3.5 w-3.5" />{L("Nuevo workspace", "New workspace")}</Button>
      </div>
    </Modal>
  );
}
