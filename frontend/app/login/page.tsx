"use client";
import { Cpu, Lock } from "lucide-react";
import { Suspense, useState } from "react";
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
          {err && <ErrorBox error={err} />}
          <Button type="submit" variant="primary" className="w-full" loading={busy}><Lock className="h-4 w-4" />{t("auth.login")}</Button>
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
