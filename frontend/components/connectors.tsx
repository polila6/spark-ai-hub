"use client";
// Knowledge connectors: keep a collection in sync with Nextcloud/WebDAV, GitHub, Zotero or a network share.
import { CloudDownload, FolderGit2, HardDrive, Library, Pencil, Plus, RefreshCw, Trash2, Waypoints } from "lucide-react";
import { useState } from "react";
import useSWR from "swr";
import { api } from "@/lib/api";
import { dt } from "@/lib/format";
import { useI18n, useL } from "@/lib/i18n";
import { useToast } from "@/lib/providers";
import { Badge, Button, Card, CardBody, CardHeader, Field, Input, Modal, Notice, Select, Switch } from "./ui";

type Kind = "webdav" | "github" | "zotero" | "smb";
const ICONS: Record<Kind, any> = { webdav: CloudDownload, github: FolderGit2, zotero: Library, smb: HardDrive };

function kindInfo(L: (es: string, en: string) => string) {
  return {
    webdav: {
      title: "Nextcloud / WebDAV",
      fields: [["url", L("Dirección de la carpeta", "Folder address"), "https://cloud.ejemplo.org/remote.php/dav/files/USUARIO/Documentos"], ["username", L("Usuario", "Username"), ""]],
      secret: L("Contraseña de aplicación", "App password"),
      steps: [L("En Nextcloud: Ajustes personales → Seguridad → «Crear nueva contraseña de aplicación» (no uses tu contraseña normal).", "In Nextcloud: Personal settings → Security → “Create new app password” (do not use your normal password)."),
        L("La dirección es https://TU-NUBE/remote.php/dav/files/TU-USUARIO/ seguida de la carpeta (en Nextcloud: Archivos → Ajustes, abajo a la izquierda, «WebDAV»).", "The address is https://YOUR-CLOUD/remote.php/dav/files/YOUR-USER/ plus the folder (Nextcloud: Files → Settings, bottom left, “WebDAV”)."),
        L("También vale ownCloud, SharePoint con WebDAV o cualquier servidor WebDAV.", "ownCloud, SharePoint over WebDAV or any WebDAV server also work.")],
    },
    github: {
      title: "GitHub",
      fields: [["repo", L("Repositorio (propietario/nombre)", "Repository (owner/name)"), "mi-grupo/documentacion"], ["branch", L("Rama (opcional)", "Branch (optional)"), "main"], ["path", L("Carpeta (opcional)", "Folder (optional)"), "docs"]],
      secret: L("Token (solo para repos privados)", "Token (private repos only)"),
      steps: [L("Repos públicos: no hace falta token.", "Public repos: no token needed."),
        L("Repos privados: github.com → Settings → Developer settings → Fine-grained tokens → acceso solo a ese repositorio con permiso «Contents: Read-only».", "Private repos: github.com → Settings → Developer settings → Fine-grained tokens → only that repository, “Contents: Read-only”."),
        L("Se importan documentos, Markdown y código (no binarios).", "Documents, Markdown and code are imported (no binaries).")],
    },
    zotero: {
      title: "Zotero",
      fields: [["library_id", L("ID de la biblioteca", "Library ID"), "1234567"], ["library_type", L("Tipo", "Type"), "user"]],
      secret: L("Clave API de Zotero", "Zotero API key"),
      steps: [L("Entra en zotero.org/settings/keys: arriba verás «Your userID for use in API calls is …» (ese es el ID).", "Go to zotero.org/settings/keys: at the top it says “Your userID for use in API calls is …” (that is the ID)."),
        L("Pulsa «Create new private key», marca solo «Allow library access» (lectura) y copia la clave.", "Click “Create new private key”, tick only “Allow library access” (read) and copy the key."),
        L("Para una biblioteca de grupo usa tipo «group» y el número del grupo (está en la dirección del grupo). Se importan los PDF y capturas guardados en Zotero.", "For a group library use type “group” and the group number (in the group URL). Stored PDFs and snapshots are imported.")],
    },
    smb: {
      title: L("Carpeta de red (Windows / NAS)", "Network share (Windows / NAS)"),
      fields: [["host", L("Servidor", "Server"), "nas.laboratorio.local"], ["share", L("Recurso compartido", "Share"), "Documentos"], ["path", L("Subcarpeta (opcional)", "Subfolder (optional)"), "Proyectos/2026"], ["username", L("Usuario", "Username"), ""], ["domain", L("Dominio (opcional)", "Domain (optional)"), ""]],
      secret: L("Contraseña", "Password"),
      steps: [L("Es lo que en Windows escribes como \\\\servidor\\recurso\\carpeta.", "It is what you type in Windows as \\\\server\\share\\folder."),
        L("Las carpetas de red suelen estar en la red interna: un administrador debe permitir ese servidor o red en Ajustes → Sistema → «Redes privadas para conectores».", "Network shares are usually on the internal network: an administrator must allow that server or network in Settings → System → “Private networks for connectors”."),
        L("Usa una cuenta de solo lectura si puedes.", "Use a read-only account if you can.")],
    },
  } as Record<Kind, { title: string; fields: [string, string, string][]; secret: string; steps: string[] }>;
}

function Editor({ value, collections, onClose, onSaved }: { value: any; collections: any[]; onClose: () => void; onSaved: () => void }) {
  const L = useL();
  const { push } = useToast();
  const info = kindInfo(L);
  const [c, setC] = useState<any>({ secret: null, schedule: "daily", enabled: true, config: {}, ...value });
  const [busy, setBusy] = useState(false);
  const k = info[c.kind as Kind];
  async function save() {
    setBusy(true);
    try {
      const body = { kind: c.kind, name: c.name, config: c.config, secret: c.secret, collection_id: c.collection_id || null, schedule: c.schedule, enabled: c.enabled };
      const r = await api(c.id ? `/api/v1/connectors/${c.id}` : "/api/v1/connectors", { method: c.id ? "PUT" : "POST", json: body });
      const t = await api(`/api/v1/connectors/${r.id}/test`, { method: "POST" });
      if (t.ok) { push("ok", L(`Conectado: ${t.importable} archivos importables. Sincronizando…`, `Connected: ${t.importable} importable files. Syncing…`)); await api(`/api/v1/connectors/${r.id}/sync`, { method: "POST" }); }
      else push("error", t.message);
      onSaved();
    } catch (e: any) { push("error", e.message); } finally { setBusy(false); }
  }
  return (
    <Modal open onOpenChange={(o) => !o && onClose()} wide title={c.id ? L("Editar conector", "Edit connector") : `${L("Conectar", "Connect")} ${k.title}`}
      description={c.kind === "smb" && c.config.index_only !== false
        ? L("Los originales se leen desde la red y se descartan: solo se guardan texto extraído, vectores y metadatos. Se vuelve a sincronizar automáticamente.", "Originals are read from the network and discarded: only extracted text, vectors and metadata are stored. It re-syncs automatically.")
        : L("Los archivos se copian a Mis archivos → Conectores y se indexan en la colección elegida. Se vuelven a sincronizar solos.", "Files are copied to My files → Connectors and indexed into the chosen collection. They re-sync automatically.")}>
      <ol className="mb-4 list-decimal space-y-1 rounded-xl bg-elevated/60 p-3 pl-7 text-xs text-muted">{k.steps.map((s, i) => <li key={i}>{s}</li>)}</ol>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label={L("Nombre", "Name")}><Input value={c.name ?? ""} onChange={(e) => setC({ ...c, name: e.target.value })} placeholder={k.title} /></Field>
        {k.fields.map(([key, label, ph]) => key === "library_type" ? (
          <Field key={key} label={label}><Select value={c.config[key] ?? "user"} onChange={(e) => setC({ ...c, config: { ...c.config, [key]: e.target.value } })}><option value="user">{L("Personal", "Personal")}</option><option value="group">{L("Grupo", "Group")}</option></Select></Field>
        ) : <Field key={key} label={label}><Input value={c.config[key] ?? ""} placeholder={ph} onChange={(e) => setC({ ...c, config: { ...c.config, [key]: e.target.value } })} /></Field>)}
        <Field label={k.secret} hint={c.has_secret ? L("Guardada (cifrada). Escribe para cambiarla.", "Stored (encrypted). Type to change it.") : L("Se guarda cifrada", "Stored encrypted")}>
          <Input type="password" autoComplete="new-password" value={c.secret ?? ""} onChange={(e) => setC({ ...c, secret: e.target.value })} /></Field>
        <Field label={L("Colección destino", "Target collection")}><Select value={c.collection_id ?? ""} onChange={(e) => setC({ ...c, collection_id: e.target.value })}><option value="">{L("Mis documentos", "My documents")}</option>{collections.filter((x) => x.is_owner).map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</Select></Field>
        <Field label={L("Sincronizar", "Sync")}><Select value={c.schedule} onChange={(e) => setC({ ...c, schedule: e.target.value })}><option value="hourly">{L("Cada hora", "Hourly")}</option><option value="daily">{L("Cada día", "Daily")}</option><option value="manual">{L("Solo a mano", "Manually")}</option></Select></Field>
        {c.kind === "smb" && <div className="flex items-end"><Switch checked={c.config.index_only !== false} onChange={(v) => setC({ ...c, config: { ...c.config, index_only: v } })} label={L("Solo índice: no guardar originales", "Index only: do not store originals")} /></div>}
        <div className="flex items-end"><Switch checked={c.enabled} onChange={(v) => setC({ ...c, enabled: v })} label={L("Activado", "Enabled")} /></div>
      </div>
      <div className="mt-4 flex justify-end gap-2"><Button variant="ghost" onClick={onClose}>{L("Cancelar", "Cancel")}</Button><Button variant="primary" loading={busy} disabled={!c.name} onClick={save}>{L("Guardar y probar", "Save and test")}</Button></div>
    </Modal>
  );
}

export function Connectors({ collections }: { collections: any[] }) {
  const L = useL();
  const { lang } = useI18n();
  const { push } = useToast();
  const info = kindInfo(L);
  const { data, mutate } = useSWR("/api/v1/connectors", { refreshInterval: (d: any) => (d?.connectors?.some((c: any) => c.status === "syncing") ? 3000 : 20000) });
  const [edit, setEdit] = useState<any>(null);
  return (
    <Card>
      <CardHeader title={L("Conectores", "Connectors")} icon={<Waypoints className="h-4 w-4" />}
        subtitle={L("Trae documentos de otros sitios y mantenlos al día en tu base de conocimiento.", "Bring documents from other places and keep them up to date in your knowledge base.")} />
      <CardBody className="space-y-3">
        <div className="flex flex-wrap gap-2">{(Object.keys(info) as Kind[]).map((k) => { const I = ICONS[k]; return <Button key={k} size="sm" variant="outline" onClick={() => setEdit({ kind: k, name: info[k].title, config: k === "zotero" ? { library_type: "user" } : k === "smb" ? { index_only: true } : {} })}><Plus className="h-3.5 w-3.5" /><I className="h-3.5 w-3.5" />{info[k].title}</Button>; })}</div>
        {(data?.connectors ?? []).map((c: any) => { const I = ICONS[c.kind as Kind]; return (
          <div key={c.id} className="flex flex-wrap items-center gap-2 rounded-xl border border-border p-3 text-sm">
            <I className="h-4 w-4 text-accent" /><span className="font-medium">{c.name}</span>
            <Badge tone={c.status === "ok" ? "ok" : c.status === "error" ? "danger" : "info"}>{c.status === "syncing" ? L("sincronizando…", "syncing…") : c.status}</Badge>
            {!c.enabled && <Badge tone="warn">{L("pausado", "paused")}</Badge>}
            <span className="text-xs text-muted">{c.stats?.files ?? 0} {L("archivos", "files")}{c.last_sync_at ? ` · ${dt(c.last_sync_at, lang)}` : ""} · {c.schedule === "hourly" ? L("cada hora", "hourly") : c.schedule === "daily" ? L("cada día", "daily") : L("manual", "manual")}</span>
            <div className="ml-auto flex gap-1">
              <Button size="sm" variant="ghost" disabled={c.status === "syncing"} onClick={async () => { try { await api(`/api/v1/connectors/${c.id}/sync`, { method: "POST" }); push("ok", L("Sincronizando", "Syncing")); setTimeout(mutate, 1000); } catch (e: any) { push("error", e.message); } }}><RefreshCw className="h-3.5 w-3.5" />{L("Sincronizar", "Sync")}</Button>
              <Button size="sm" variant="ghost" onClick={() => setEdit({ ...c, secret: null })}><Pencil className="h-3.5 w-3.5" /></Button>
              <Button size="sm" variant="ghost" onClick={async () => { if (!window.confirm(c.config?.index_only ? L("¿Eliminar el conector y su índice? Los originales de red no se modificarán.", "Delete the connector and its index? Network originals will not be changed.") : L("¿Eliminar el conector y los archivos que importó?", "Delete the connector and the files it imported?"))) return; await api(`/api/v1/connectors/${c.id}`, { method: "DELETE" }); mutate(); }}><Trash2 className="h-3.5 w-3.5" /></Button>
            </div>
            {c.status_message && <p className="w-full text-xs text-danger">{c.status_message}</p>}
            {c.stats?.skipped > 0 && <p className="w-full text-xs text-muted">{L(`${c.stats.skipped} archivo(s) de base de datos o volcados grandes no se copian: conéctalos en «Bases de datos» para consultarlos en remoto.`, `${c.stats.skipped} database file(s) or large dumps are not copied: connect them under “Databases” to query them remotely.`)}</p>}
          </div>); })}
        {data && !data.connectors.length && <Notice tone="muted">{L("Aún no hay conectores. Elige arriba de dónde quieres traer documentos.", "No connectors yet. Pick above where to bring documents from.")}</Notice>}
      </CardBody>
      {edit && <Editor value={edit} collections={collections} onClose={() => setEdit(null)} onSaved={() => { setEdit(null); mutate(); }} />}
    </Card>
  );
}
