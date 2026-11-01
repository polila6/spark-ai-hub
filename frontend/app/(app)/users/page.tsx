"use client";
import { Check, Mail, ShieldCheck, UserPlus, Users, X } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import useSWR from "swr";
import { Guide } from "@/components/guide";
import { Badge, Button, Card, Code, Empty, ErrorBox, Field, Input, Modal, Notice, PageHeader, Select, Table, Tabs, Td, Textarea } from "@/components/ui";
import { api } from "@/lib/api";
import { ago, bytes, dt } from "@/lib/format";
import { useI18n, useL } from "@/lib/i18n";
import { isManager, useMe, useToast } from "@/lib/providers";

function InviteResult({ r, onClose }: { r: any; onClose: () => void }) {
  const L = useL();
  const link = r.invitation_url ?? r.reset_url;
  return (
    <div className="space-y-3">
      {r.email_status === "pending" ? <Notice tone="ok" icon={<Mail className="h-4 w-4" />}>{L("Correo enviado con el enlace de activación y los pasos para instalar Tailscale.", "E-mail sent with the activation link and the steps to install Tailscale.")}</Notice>
        : r.email_status === "mail_disabled" || r.email_status === "skipped" ? <Notice tone="warn">{L("El correo no está configurado (Ajustes → Acceso y correo): envía tú este enlace por un canal seguro.", "Mail is not configured (Settings → Access and mail): send this link yourself through a secure channel.")}</Notice>
        : r.email_status === "no_email" ? <Notice tone="warn">{L("El usuario no tiene correo: envía tú este enlace.", "The user has no e-mail: send this link yourself.")}</Notice> : null}
      <p className="text-xs text-muted">{L("Enlace de un solo uso", "One-time link")} ({r.expires_in_hours} h):</p>
      <Code>{link}</Code>
      {r.tailscale_invite ? <><p className="text-xs text-muted">{L("Invitación de Tailscale para el Spark", "Tailscale invitation for the Spark")} ({r.tailscale_source === "api" ? L("creada por API", "created via API") : L("enlace compartido", "shared link")}):</p><Code>{r.tailscale_invite}</Code></>
        : r.invitation_url && <Notice tone="info">{L("Recuerda compartir el Spark en Tailscale con esta persona (Machines → (tu Spark) → Share) o configura el enlace en Ajustes → Acceso.", "Remember to share the Spark in Tailscale with this person (Machines → (your Spark) → Share) or configure the link in Settings → Access.")}</Notice>}
      <div className="flex justify-end"><Button onClick={onClose}>{L("Cerrar", "Close")}</Button></div>
    </div>
  );
}

function SignupsTab() {
  const L = useL();
  const { lang } = useI18n();
  const { me } = useMe();
  const { push } = useToast();
  const { data, mutate } = useSWR("/api/v1/users?status=pending", { refreshInterval: 20000 });
  const [d, setD] = useState<{ u: any; action: "approve" | "reject" } | null>(null);
  const [role, setRole] = useState("researcher");
  const [msg, setMsg] = useState("");
  return (
    <Card>
      {!data?.length ? <Empty title={L("No hay solicitudes pendientes", "No pending requests")} hint={L("Cuando alguien se registre desde /signup aparecerá aquí y te llegará un aviso.", "When someone signs up at /signup it appears here and you get a notice.")} /> : (
        <Table head={[L("Persona", "Person"), L("Usuario", "Username"), L("Motivo", "Reason"), L("Correo verificado", "E-mail verified"), L("Fecha", "Date"), ""]}>
          {data.map((u: any) => <tr key={u.id}><Td className="font-medium">{u.display_name}<div className="text-[11px] text-muted">{u.email}</div></Td><Td className="font-mono text-xs">{u.username}</Td>
            <Td className="max-w-xs text-xs text-muted">{u.signup_reason || "—"}</Td><Td>{u.email_verified ? <Badge tone="ok">✓</Badge> : <Badge>{L("no", "no")}</Badge>}</Td><Td className="whitespace-nowrap text-xs text-muted">{dt(u.created_at, lang)}</Td>
            <Td><div className="flex gap-1"><Button size="sm" variant="primary" onClick={() => { setD({ u, action: "approve" }); setRole("researcher"); setMsg(""); }}><Check className="h-3.5 w-3.5" />{L("Aprobar", "Approve")}</Button>
              <Button size="sm" variant="ghost" onClick={() => { setD({ u, action: "reject" }); setMsg(""); }}><X className="h-3.5 w-3.5" />{L("Rechazar", "Reject")}</Button></div></Td></tr>)}
        </Table>
      )}
      <Modal open={!!d} onOpenChange={() => setD(null)} title={d?.action === "approve" ? L("Aprobar cuenta", "Approve account") : L("Rechazar solicitud", "Reject request")} description={d ? `${d.u.display_name} <${d.u.email}>` : ""}>
        <div className="space-y-3">
          {d?.action === "approve" && <Field label={L("Rol", "Role")}><Select value={role} onChange={(e) => setRole(e.target.value)}><option value="researcher">researcher</option><option value="viewer">viewer</option>{me?.role === "admin" && <><option value="manager">manager</option><option value="admin">admin</option></>}</Select></Field>}
          <Field label={L("Mensaje (opcional, se envía por correo)", "Message (optional, e-mailed)")}><Textarea rows={3} value={msg} onChange={(e) => setMsg(e.target.value)} /></Field>
          {d?.action === "approve" && <Notice tone="info">{L("Asegúrate de que la persona tiene acceso al Spark en Tailscale (invitación de «Share»).", "Make sure the person has access to the Spark in Tailscale (“Share” invitation).")}</Notice>}
          <div className="flex justify-end"><Button variant={d?.action === "approve" ? "primary" : "danger"} onClick={async () => { try { await api(`/api/v1/users/${d!.u.id}/${d!.action}`, { method: "POST", json: { role, message: msg } }); push("ok", L("Hecho", "Done")); setD(null); mutate(); } catch (e: any) { push("error", e.message); } }}>{L("Confirmar", "Confirm")}</Button></div>
        </div>
      </Modal>
    </Card>
  );
}

function UsersTab() {
  const { t, lang } = useI18n();
  const { me } = useMe();
  const { push } = useToast();
  const { data, mutate } = useSWR("/api/v1/users");
  const [open, setOpen] = useState(false);
  const [f, setF] = useState({ username: "", email: "", display_name: "", role: "researcher" });
  const [res, setRes] = useState<any>(null);
  const [err, setErr] = useState<any>(null);
  const L = useL();
  const roles = me?.role === "admin" ? ["admin", "manager", "researcher", "viewer"] : ["researcher", "viewer"];
  return (
    <div className="space-y-4">
      <div className="flex justify-end"><Button variant="primary" onClick={() => { setRes(null); setOpen(true); }}><UserPlus className="h-4 w-4" />{t("users.invite")}</Button></div>
      <Card>
        <Table head={[t("auth.username"), t("common.name"), t("users.role"), t("common.status"), L("último acceso", "last login"), L("espacio", "space"), L("trabajos", "jobs"), t("common.actions")]}>
          {data?.filter((u: any) => u.signup_status !== "pending").map((u: any) => (
            <tr key={u.id}>
              <Td className="font-medium">{u.username}<div className="text-[11px] text-muted">{u.email}</div></Td>
              <Td>{u.display_name}</Td>
              <Td><Select className="h-8 w-32 text-xs" disabled={u.id === me?.id || !u.manageable} value={u.role} onChange={async (e) => { try { await api(`/api/v1/users/${u.id}`, { method: "PATCH", json: { role: e.target.value } }); mutate(); } catch (x: any) { push("error", x.message); } }}>
                {(u.manageable ? roles : [u.role]).map((r) => <option key={r}>{r}</option>)}</Select></Td>
              <Td>{u.signup_status === "rejected" ? <Badge tone="danger">{L("rechazado", "rejected")}</Badge> : !u.activated ? <Badge tone="info">{L("invitado", "invited")}</Badge> : u.is_active ? (u.locked ? <Badge tone="warn">{L("bloqueado", "locked")}</Badge> : <Badge tone="ok">{L("activo", "active")}</Badge>) : <Badge>{L("inactivo", "inactive")}</Badge>}</Td>
              <Td className="text-xs text-muted">{u.last_login_at ? ago(u.last_login_at, lang) : L("nunca", "never")}</Td>
              <Td className="text-xs">{bytes(u.used_bytes)}</Td>
              <Td>{u.active_jobs}</Td>
              <Td><div className="flex gap-1">{u.manageable && <>
                {!u.activated && <Button size="sm" variant="ghost" onClick={async () => { try { setRes(await api(`/api/v1/users/${u.id}/invite`, { method: "POST" })); setOpen(true); } catch (x: any) { push("error", x.message); } }}>{L("Reenviar invitación", "Resend invitation")}</Button>}
                {u.activated && <Button size="sm" variant="ghost" onClick={async () => { setRes(await api(`/api/v1/users/${u.id}/reset-link?send_email=true`, { method: "POST" })); setOpen(true); }}>{t("users.reset")}</Button>}</>}
                {u.id !== me?.id && u.manageable && (u.is_active ? <Button size="sm" variant="ghost" onClick={async () => { await api(`/api/v1/users/${u.id}`, { method: "DELETE" }); mutate(); }}>{t("users.deactivate")}</Button>
                  : <Button size="sm" variant="ghost" onClick={async () => { await api(`/api/v1/users/${u.id}`, { method: "PATCH", json: { is_active: true } }); mutate(); }}>{L("Reactivar", "Reactivate")}</Button>)}
              </div></Td>
            </tr>
          ))}
        </Table>
      </Card>
      <Modal open={open} onOpenChange={setOpen} title={res ? (res.reset_url ? L("Enlace de restablecimiento", "Reset link") : L("Invitación creada", "Invitation created")) : t("users.invite")}>
        {res ? (
          <InviteResult r={res} onClose={() => setOpen(false)} />
        ) : (
          <div className="space-y-3">
            <Field label={t("auth.username")} hint="a-z 0-9 . _ -"><Input value={f.username} onChange={(e) => setF({ ...f, username: e.target.value.toLowerCase() })} /></Field>
            <Field label={t("common.name")}><Input value={f.display_name} onChange={(e) => setF({ ...f, display_name: e.target.value })} /></Field>
            <Field label="Email" hint={L("Recibirá la invitación y los pasos de Tailscale (si el correo está configurado)", "Receives the invitation and the Tailscale steps (if mail is configured)")}><Input type="email" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} /></Field>
            <Field label={t("users.role")} hint={L("manager = gestor: invita, aprueba cuentas y modelos; sin ajustes de sistema", "manager: invites, approves accounts and models; no system settings")}><Select value={f.role} onChange={(e) => setF({ ...f, role: e.target.value })}>{roles.map((r) => <option key={r} value={r}>{r}</option>)}</Select></Field>
            {err && <ErrorBox error={err} />}
            <div className="flex justify-end gap-2"><Button variant="ghost" onClick={() => setOpen(false)}>{t("common.cancel")}</Button>
              <Button variant="primary" disabled={!f.username} onClick={async () => { setErr(null); try { const r = await api("/api/v1/users", { method: "POST", json: { ...f, email: f.email || null } }); setRes(r); setF({ username: "", email: "", display_name: "", role: "researcher" }); mutate(); } catch (e) { setErr(e); } }}>{t("common.create")}</Button></div>
          </div>
        )}
      </Modal>
    </div>
  );
}

function PoliciesTab() {
  const { t } = useI18n();
  const { push } = useToast();
  const { data, mutate } = useSWR("/api/v1/users/policies");
  const DEF: any = { admin: { max_concurrent_jobs: 4, max_queued_jobs: 100, storage_quota_gb: 2000, api_rpm: 600 }, manager: { max_concurrent_jobs: 3, max_queued_jobs: 50, storage_quota_gb: 500, api_rpm: 300 }, researcher: { max_concurrent_jobs: 2, max_queued_jobs: 20, storage_quota_gb: 200, api_rpm: 60 }, viewer: { max_concurrent_jobs: 0, max_queued_jobs: 0, storage_quota_gb: 1, api_rpm: 20 } };
  const [edit, setEdit] = useState<any>({});
  return (
    <Card>
      <Table head={[t("users.role"), "trabajos concurrentes", "trabajos en cola", "cuota GB", "API req/min", "tokens/mes", "vídeo", ""]}>
        {["admin", "manager", "researcher", "viewer"].map((r) => {
          const cur = { ...DEF[r], allow_video: r !== "viewer", monthly_token_quota: null, priority_boost: 0, ...(data?.find((p: any) => p.scope === "role" && p.target === r) ?? {}), ...(edit[r] ?? {}) };
          const set = (k: string, v: any) => setEdit({ ...edit, [r]: { ...cur, [k]: v } });
          return (
            <tr key={r}>
              <Td className="font-medium">{r}</Td>
              {["max_concurrent_jobs", "max_queued_jobs", "storage_quota_gb", "api_rpm"].map((k) => <Td key={k}><Input className="h-8 w-24" type="number" value={cur[k]} onChange={(e) => set(k, Number(e.target.value))} /></Td>)}
              <Td><Input className="h-8 w-28" type="number" value={cur.monthly_token_quota ?? ""} placeholder="∞" onChange={(e) => set("monthly_token_quota", e.target.value ? Number(e.target.value) : null)} /></Td>
              <Td><input type="checkbox" checked={cur.allow_video} onChange={(e) => set("allow_video", e.target.checked)} aria-label="video" /></Td>
              <Td><Button size="sm" disabled={!edit[r]} onClick={async () => { const { scope, target, ...body } = cur; await api(`/api/v1/users/policies/role/${r}`, { method: "PUT", json: body }); push("ok", t("common.save")); setEdit({ ...edit, [r]: undefined }); mutate(); }}>{t("common.save")}</Button></Td>
            </tr>
          );
        })}
      </Table>
    </Card>
  );
}

function AuditTab() {
  const { lang } = useI18n();
  const [q, setQ] = useState("");
  const { data } = useSWR(`/api/v1/admin/audit?limit=300${q ? `&action=${q}` : ""}`, { refreshInterval: 15000 });
  return (
    <Card>
      <div className="p-3"><Input placeholder="filtrar acción (auth., models., users.…)" value={q} onChange={(e) => setQ(e.target.value)} /></div>
      {!data?.length ? <Empty /> : (
        <Table head={["fecha", "actor", "acción", "objetivo", "ok", "IP", "detalles"]}>
          {data.map((e: any) => <tr key={e.id}><Td className="whitespace-nowrap text-xs text-muted">{dt(e.ts, lang)}</Td><Td>{e.actor ?? "—"}</Td><Td><Badge>{e.action}</Badge></Td><Td className="text-xs">{e.target_type} {e.target_id?.slice(0, 12)}</Td><Td>{e.success ? "✔" : <span className="text-danger">✘</span>}</Td><Td className="text-xs text-muted">{e.ip}</Td><Td className="max-w-xs truncate font-mono text-[10px] text-muted">{JSON.stringify(e.details)}</Td></tr>)}
        </Table>
      )}
    </Card>
  );
}

function UsersInner() {
  const { t } = useI18n();
  const L = useL();
  const { me } = useMe();
  const sp = useSearchParams();
  const [tab, setTab] = useState(sp.get("tab") ?? "u");
  const { data: pend } = useSWR(isManager(me) ? "/api/v1/users?status=pending" : null);
  if (!isManager(me)) return <Notice tone="warn"><ShieldCheck className="inline h-4 w-4" /> {L("Solo administradores y gestores.", "Administrators and managers only.")}</Notice>;
  const tabs = [{ id: "u", label: t("users.title"), content: <UsersTab /> },
    { id: "signups", label: <span>{L("Solicitudes de cuenta", "Account requests")}{pend?.length ? <Badge tone="warn" className="ml-1.5">{pend.length}</Badge> : null}</span>, content: <SignupsTab /> }];
  if (me?.role === "admin") tabs.push({ id: "p", label: t("users.policies"), content: <PoliciesTab /> }, { id: "a", label: t("users.audit"), content: <AuditTab /> });
  return (
    <div>
      <PageHeader title={t("users.title")} subtitle={t("users.subtitle")} icon={<Users className="h-5 w-5" />} />
      <Guide id="users" />
      <Tabs tabs={tabs as any} value={tab} onValueChange={setTab} />
    </div>
  );
}

export default function UsersPage() {
  return <Suspense><UsersInner /></Suspense>;
}
