"use client";
// Remote databases: connect a PostgreSQL server once and ask questions that run THERE. The Spark never copies, syncs or
// indexes the data: each query goes to the remote server in a read-only session and only a limited number of rows come back.
import { Database, Pencil, Play, Plus, ShieldCheck, Table2, Trash2 } from "lucide-react";
import { useState } from "react";
import useSWR from "swr";
import { api } from "@/lib/api";
import { useL } from "@/lib/i18n";
import { useToast } from "@/lib/providers";
import { Badge, Button, Card, CardBody, CardHeader, Field, Input, Modal, Notice, Select, Textarea } from "./ui";

const READ_ONLY_SQL = `CREATE ROLE spark_reader LOGIN PASSWORD 'CHANGE-ME';
GRANT CONNECT ON DATABASE your_database TO spark_reader;
GRANT USAGE ON SCHEMA public TO spark_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO spark_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO spark_reader;`;

function Editor({ value, onClose, onSaved }: { value: any; onClose: () => void; onSaved: () => void }) {
  const L = useL();
  const { push } = useToast();
  const [c, setC] = useState<any>({ kind: "postgres", port: 5432, sslmode: "require", row_limit: 500, timeout_s: 20, password: "", ...value });
  const [busy, setBusy] = useState(false);
  const [warn, setWarn] = useState<string[]>([]);
  const set = (k: string, v: any) => setC({ ...c, [k]: v });
  async function save() {
    setBusy(true);
    try {
      const body = { name: c.name, kind: c.kind, host: c.host, port: Number(c.port), database: c.database, username: c.username, sslmode: c.sslmode,
        row_limit: Number(c.row_limit), timeout_s: Number(c.timeout_s), password: c.id && c.password === "" ? null : c.password };
      const r = await api(c.id ? `/api/v1/databases/${c.id}` : "/api/v1/databases", { method: c.id ? "PUT" : "POST", json: body });
      const t = await api(`/api/v1/databases/${r.id}/test`, { method: "POST" });
      if (t.ok) {
        push("ok", L(`Conectado a ${t.database} como ${t.user} (${t.ms} ms)`, `Connected to ${t.database} as ${t.user} (${t.ms} ms)`));
        if (t.warnings?.length) { setWarn(t.warnings); } else { onSaved(); return; }
        onSaved();
      } else push("error", t.message);
    } catch (e: any) { push("error", e.message); } finally { setBusy(false); }
  }
  return (
    <Modal open onOpenChange={(o) => !o && onClose()} wide title={c.id ? L("Editar base de datos", "Edit database") : L("Conectar una base de datos PostgreSQL", "Connect a PostgreSQL database")}
      description={L("El Spark no se descarga la base de datos: cada consulta se ejecuta en su servidor, en solo lectura, y solo vuelven unas pocas filas.", "The Spark does not download the database: every query runs on its server, read-only, and only a few rows come back.")}>
      <ol className="mb-4 list-decimal space-y-1.5 rounded-xl bg-elevated/60 p-3 pl-7 text-xs text-muted">
        <li>{L("En tu base de datos crea un usuario de SOLO LECTURA para el Spark (no uses el administrador). Ejemplo para PostgreSQL:", "In your database create a READ-ONLY user for the Spark (do not use the administrator). PostgreSQL example:")}
          <pre className="mt-1 overflow-x-auto rounded-lg bg-bg p-2 font-mono text-[11px] text-fg">{READ_ONLY_SQL}</pre></li>
        <li>{L("Permite el acceso del Spark: abre el puerto (normalmente 5432) en el cortafuegos y en pg_hba.conf para la dirección del Spark. Usa SSL.", "Allow the Spark in: open the port (usually 5432) in the firewall and in pg_hba.conf for the Spark's address. Use SSL.")}</li>
        <li>{L("Si el servidor está en una red interna (dirección privada), un administrador debe permitirlo en Ajustes → Sistema → «Redes privadas para conectores».", "If the server is on an internal network (private address), an administrator must allow it in Settings → System → “Private networks for connectors”.")}</li>
        <li>{L("Escribe los datos abajo y pulsa «Guardar y probar». La contraseña se guarda cifrada y no se vuelve a mostrar.", "Enter the details below and click “Save and test”. The password is stored encrypted and never shown again.")}</li>
        <li>{L("Después pregúntale al asistente en el chat (p. ej. «¿cuántos pedidos hubo por mes en 2025?»): mirará el esquema, escribirá la consulta y te dará el resultado.", "Then ask the assistant in the chat (e.g. “how many orders per month in 2025?”): it looks at the schema, writes the query and gives you the result.")}</li>
      </ol>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label={L("Nombre", "Name")} hint={L("Con este nombre la nombrará el asistente", "The assistant will refer to it by this name")}><Input value={c.name ?? ""} placeholder="Laboratorio" onChange={(e) => set("name", e.target.value)} /></Field>
        <Field label={L("Servidor", "Server")}><Input value={c.host ?? ""} placeholder="db.ejemplo.org" onChange={(e) => set("host", e.target.value)} /></Field>
        <Field label={L("Puerto", "Port")}><Input type="number" value={c.port} onChange={(e) => set("port", e.target.value)} /></Field>
        <Field label={L("Base de datos", "Database")}><Input value={c.database ?? ""} onChange={(e) => set("database", e.target.value)} /></Field>
        <Field label={L("Usuario (solo lectura)", "User (read-only)")}><Input value={c.username ?? ""} autoComplete="off" onChange={(e) => set("username", e.target.value)} /></Field>
        <Field label={L("Contraseña", "Password")} hint={c.has_secret ? L("Guardada (cifrada). Escribe para cambiarla.", "Stored (encrypted). Type to change it.") : L("Se guarda cifrada", "Stored encrypted")}>
          <Input type="password" autoComplete="new-password" value={c.password ?? ""} onChange={(e) => set("password", e.target.value)} /></Field>
        <Field label="SSL" hint={L("«require» cifra la conexión; «verify-full» además comprueba el certificado", "“require” encrypts the connection; “verify-full” also checks the certificate")}>
          <Select value={c.sslmode} onChange={(e) => set("sslmode", e.target.value)}><option value="require">require</option><option value="verify-full">verify-full</option><option value="verify-ca">verify-ca</option><option value="prefer">prefer</option><option value="disable">disable</option></Select></Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label={L("Filas máx.", "Max rows")} hint={L("por consulta", "per query")}><Input type="number" min={1} max={5000} value={c.row_limit} onChange={(e) => set("row_limit", e.target.value)} /></Field>
          <Field label={L("Tiempo máx. (s)", "Max time (s)")}><Input type="number" min={1} max={120} value={c.timeout_s} onChange={(e) => set("timeout_s", e.target.value)} /></Field>
        </div>
      </div>
      {warn.map((w, i) => <div key={i} className="mt-3"><Notice tone="warn">{w}</Notice></div>)}
      <div className="mt-4 flex justify-end gap-2"><Button variant="ghost" onClick={onClose}>{warn.length ? L("Cerrar", "Close") : L("Cancelar", "Cancel")}</Button>
        {!warn.length && <Button variant="primary" loading={busy} disabled={!c.name || !c.host || !c.database || !c.username} onClick={save}>{L("Guardar y probar", "Save and test")}</Button>}</div>
    </Modal>
  );
}

function Explorer({ db, onClose }: { db: any; onClose: () => void }) {
  const L = useL();
  const [table, setTable] = useState("");
  const [sql, setSql] = useState("SELECT 1");
  const [out, setOut] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const run = async (fn: () => Promise<string>) => { setBusy(true); setErr(""); try { setOut(await fn()); } catch (e: any) { setErr(e.message); setOut(""); } finally { setBusy(false); } };
  return (
    <Modal open onOpenChange={(o) => !o && onClose()} wide title={`${db.name} · ${db.database}`} description={L("Se ejecuta en el servidor remoto, en solo lectura. No se descarga nada.", "Runs on the remote server, read-only. Nothing is downloaded.")}>
      <div className="flex flex-wrap items-end gap-2">
        <Field label={L("Tabla (opcional)", "Table (optional)")}><Input value={table} placeholder="public.pedidos" onChange={(e) => setTable(e.target.value)} /></Field>
        <Button variant="outline" loading={busy} onClick={() => run(async () => (await api(`/api/v1/databases/${db.id}/schema${table ? `?table=${encodeURIComponent(table)}` : ""}`)).markdown)}><Table2 className="h-4 w-4" />{L("Ver esquema", "Show schema")}</Button>
      </div>
      <div className="mt-3 space-y-2">
        <Field label={L("Consulta de prueba (solo lectura)", "Test query (read-only)")}><Textarea rows={3} className="font-mono text-xs" value={sql} onChange={(e) => setSql(e.target.value)} /></Field>
        <Button variant="primary" size="sm" loading={busy} onClick={() => run(async () => (await api(`/api/v1/databases/${db.id}/query`, { method: "POST", json: { sql } })).markdown)}><Play className="h-4 w-4" />{L("Ejecutar", "Run")}</Button>
      </div>
      {err && <div className="mt-3"><Notice tone="danger">{err}</Notice></div>}
      {out && <pre className="mt-3 max-h-[45vh] overflow-auto whitespace-pre-wrap rounded-xl bg-elevated/60 p-3 font-mono text-[11px]">{out}</pre>}
    </Modal>
  );
}

export function Databases() {
  const L = useL();
  const { push } = useToast();
  const { data, mutate } = useSWR("/api/v1/databases");
  const [edit, setEdit] = useState<any>(null);
  const [explore, setExplore] = useState<any>(null);
  return (
    <Card>
      <CardHeader title={L("Bases de datos", "Databases")} icon={<Database className="h-4 w-4" />}
        subtitle={L("Consulta bases de datos remotas sin descargarlas: las preguntas se ejecutan en su servidor.", "Query remote databases without downloading them: questions run on their server.")}
        actions={<Button size="sm" variant="outline" onClick={() => setEdit({})}><Plus className="h-3.5 w-3.5" />PostgreSQL</Button>} />
      <CardBody className="space-y-3">
        <Notice tone="info" icon={<ShieldCheck className="h-4 w-4" />}>{L("El Spark no copia ni indexa estas bases de datos. Cada consulta va al servidor remoto en una sesión de solo lectura y solo vuelven unas pocas filas (por defecto 500), así que una base enorme no llena el disco ni la memoria.",
          "The Spark neither copies nor indexes these databases. Each query goes to the remote server in a read-only session and only a few rows come back (500 by default), so a huge database never fills the disk or memory.")}</Notice>
        {(data?.databases ?? []).map((d: any) => (
          <div key={d.id} className="flex flex-wrap items-center gap-2 rounded-xl border border-border p-3 text-sm">
            <Database className="h-4 w-4 text-accent" /><span className="font-medium">{d.name}</span>
            <span className="text-xs text-muted">{d.username}@{d.host}:{d.port}/{d.database}</span>
            <Badge>{d.sslmode}</Badge><span className="text-xs text-subtle">≤ {d.row_limit} {L("filas", "rows")} · {d.timeout_s} s</span>
            <div className="ml-auto flex gap-1">
              <Button size="sm" variant="ghost" onClick={async () => { try { const t = await api(`/api/v1/databases/${d.id}/test`, { method: "POST" }); if (t.ok) { push("ok", L(`Conexión correcta (${t.ms} ms)`, `Connection OK (${t.ms} ms)`)); t.warnings?.forEach((w: string) => push("info", w)); } else push("error", t.message); } catch (e: any) { push("error", e.message); } }}>{L("Probar", "Test")}</Button>
              <Button size="sm" variant="ghost" onClick={() => setExplore(d)}><Table2 className="h-3.5 w-3.5" />{L("Explorar", "Explore")}</Button>
              <Button size="sm" variant="ghost" aria-label={L("Editar", "Edit")} onClick={() => setEdit({ ...d, password: "" })}><Pencil className="h-3.5 w-3.5" /></Button>
              <Button size="sm" variant="ghost" aria-label={L("Eliminar", "Delete")} onClick={async () => { if (!window.confirm(L("¿Eliminar esta conexión? La base de datos remota no se toca.", "Delete this connection? The remote database is not touched."))) return; await api(`/api/v1/databases/${d.id}`, { method: "DELETE" }); mutate(); }}><Trash2 className="h-3.5 w-3.5" /></Button>
            </div>
          </div>
        ))}
        {data && !data.databases.length && <Notice tone="muted">{L("Aún no hay bases de datos conectadas. Pulsa «PostgreSQL» para conectar la primera: te explica cómo crear el usuario de solo lectura.", "No databases connected yet. Click “PostgreSQL” to connect the first one: it explains how to create the read-only user.")}</Notice>}
      </CardBody>
      {edit && <Editor value={edit} onClose={() => setEdit(null)} onSaved={() => { setEdit(null); mutate(); }} />}
      {explore && <Explorer db={explore} onClose={() => setExplore(null)} />}
    </Card>
  );
}
