"use client";
import { Cpu, Lock } from "lucide-react";
import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { api } from "@/lib/api";
import { useI18n, useL } from "@/lib/i18n";
import { Button, ErrorBox, Field, Input } from "@/components/ui";

function LoginForm() {
  const { t, lang, setLang } = useI18n();
  const L = useL();
  const params = useSearchParams();
  const [username, setU] = useState("");
  const [password, setP] = useState("");
  const [err, setErr] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [sso, setSso] = useState<{ id: string; name: string }[]>([]);
  useEffect(() => { api("/api/v1/auth/oidc/providers").then(setSso).catch(() => {}); }, []);
  const ssoErr = params.get("sso_error");
  const ssoMsg: Record<string, string> = {
    pending: L("Tu cuenta se ha creado y está pendiente de aprobación. Te avisaremos por correo.", "Your account was created and is awaiting approval. We will e-mail you."),
    closed: L("El registro está cerrado: pide una invitación al administrador.", "Sign-up is closed: ask the administrator for an invitation."),
    domain: L("Tu dirección de correo no está permitida en esta plataforma.", "Your e-mail address is not allowed on this platform."),
    link: L("Ya existe una cuenta con ese correo. Entra con tu usuario y contraseña (el administrador puede vincularla).", "An account with that e-mail exists. Sign in with your username and password."),
    inactive: L("Tu cuenta está desactivada.", "Your account is disabled."),
    cancelled: L("Inicio de sesión cancelado.", "Sign-in cancelled."),
  };
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      await api("/api/v1/auth/login", { method: "POST", json: { username, password } });
      const next = params.get("next");
      window.location.href = next && next.startsWith("/") && !next.startsWith("//") ? next : "/";
    } catch (e: any) {
      setErr(e.status === 401 ? new Error(t("auth.invalid"))
        : e.code === "account_pending" ? new Error(L("Tu solicitud de cuenta está pendiente de aprobación. Te avisaremos por correo.", "Your account request is awaiting approval. We will e-mail you."))
        : e.code === "account_rejected" ? new Error(L("Tu solicitud de cuenta no fue aprobada. Contacta con el administrador.", "Your account request was not approved. Contact the administrator."))
        : e);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="relative grid min-h-screen place-items-center overflow-hidden px-4">
      <div aria-hidden className="pointer-events-none absolute -top-40 left-1/2 h-[520px] w-[820px] -translate-x-1/2 rounded-full bg-accent/20 blur-3xl" />
      <div className="relative w-full max-w-sm animate-fade-in">
        <div className="mb-8 flex flex-col items-center text-center">
          <div className="grid h-12 w-12 place-items-center rounded-2xl bg-gradient-to-br from-accent to-emerald-600 text-white shadow-lg"><Cpu className="h-6 w-6" /></div>
          <h1 className="mt-4 text-2xl font-semibold tracking-tight">{t("app.name")}</h1>
          <p className="mt-1 text-sm text-muted">{t("app.tagline")}</p>
        </div>
        <form onSubmit={submit} className="space-y-4 rounded-2xl border border-border bg-surface/90 p-6 shadow-card backdrop-blur">
          <Field label={t("auth.username")} htmlFor="u"><Input id="u" autoComplete="username" autoFocus value={username} onChange={(e) => setU(e.target.value)} required /></Field>
          <Field label={t("auth.password")} htmlFor="p"><Input id="p" type="password" autoComplete="current-password" value={password} onChange={(e) => setP(e.target.value)} required /></Field>
          {ssoErr && !err && <ErrorBox error={new Error(ssoMsg[ssoErr] ?? L("No se pudo iniciar sesión con el proveedor externo. Inténtalo de nuevo.", "External sign-in failed. Try again."))} />}
          {err && <ErrorBox error={err} />}
          <Button type="submit" variant="primary" className="w-full" loading={busy}><Lock className="h-4 w-4" />{t("auth.login")}</Button>
          {sso.length > 0 && <div className="space-y-2">
            <div className="flex items-center gap-2 text-[11px] text-subtle"><span className="h-px flex-1 bg-border" />{L("o", "or")}<span className="h-px flex-1 bg-border" /></div>
            {sso.map((p) => <a key={p.id} href={`/api/v1/auth/oidc/${p.id}/start?next=${encodeURIComponent(params.get("next") ?? "/")}`} className="flex h-10 w-full items-center justify-center gap-2 rounded-xl border border-border bg-bg text-sm font-medium hover:bg-elevated">
              {p.id === "google" ? <svg viewBox="0 0 48 48" className="h-4 w-4"><path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3C33.7 32.7 29.2 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.3-.1-2.4-.4-3.5z"/><path fill="#FF3D00" d="m6.3 14.7 6.6 4.8C14.7 15.1 19 12 24 12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 16.3 4 9.7 8.3 6.3 14.7z"/><path fill="#4CAF50" d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2C29.2 35.1 26.7 36 24 36c-5.2 0-9.6-3.3-11.3-8l-6.5 5C9.5 39.6 16.2 44 24 44z"/><path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-.8 2.2-2.2 4.2-4.1 5.6l6.2 5.2C37 39.2 44 34 44 24c0-1.3-.1-2.4-.4-3.5z"/></svg>
                : p.id === "microsoft" ? <svg viewBox="0 0 23 23" className="h-4 w-4"><path fill="#f35325" d="M1 1h10v10H1z"/><path fill="#81bc06" d="M12 1h10v10H12z"/><path fill="#05a6f0" d="M1 12h10v10H1z"/><path fill="#ffba08" d="M12 12h10v10H12z"/></svg> : <Lock className="h-4 w-4" />}
              {L(`Entrar con ${p.name}`, `Sign in with ${p.name}`)}</a>)}
          </div>}
          <div className="flex justify-between text-xs">
            <a href="/forgot" className="text-muted hover:text-fg">{L("¿Olvidaste la contraseña?", "Forgot password?")}</a>
            <a href="/signup" className="font-medium text-accent hover:underline">{L("Solicitar cuenta", "Request an account")}</a>
          </div>
          <p className="text-center text-[11px] text-subtle">{t("auth.private_notice")}</p>
        </form>
        <div className="mt-4 text-center">
          <button className="text-xs text-muted hover:text-fg" onClick={() => setLang(lang === "es" ? "en" : "es")}>{lang === "es" ? "English" : "Español"}</button>
        </div>
      </div>
    </div>
  );
}

export default function LoginPage() {
  return <Suspense><LoginForm /></Suspense>;
}
