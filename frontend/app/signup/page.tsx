"use client";
import { CheckCircle2, Cpu, UserPlus } from "lucide-react";
import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import { Button, Card, ErrorBox, Field, Input, Notice, Textarea } from "@/components/ui";
import { api } from "@/lib/api";
import { useI18n, useL } from "@/lib/i18n";

export default function SignupPage() {
  const { lang, setLang } = useI18n();
  const L = useL();
  const [info, setInfo] = useState<any>(null);
  const [f, setF] = useState({ username: "", email: "", display_name: "", password: "", password2: "", reason: "", accept_terms: false });
  const [err, setErr] = useState<any>(null);
  const [done, setDone] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [showTerms, setShowTerms] = useState(false);
  useEffect(() => { api("/api/v1/auth/signup-info").then(setInfo).catch(setErr); }, []);
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    if (f.password !== f.password2) return setErr(new Error(L("Las contraseñas no coinciden", "Passwords do not match")));
    setBusy(true);
    try {
      const { password2, ...body } = f;
      setDone(await api("/api/v1/auth/signup", { method: "POST", json: { ...body, language: lang } }));
    } catch (x) { setErr(x); } finally { setBusy(false); }
  }
  return (
    <div className="grid min-h-screen place-items-center px-4 py-10">
      <div className="w-full max-w-md">
        <div className="mb-6 flex items-center justify-center gap-3">
          <div className="grid h-10 w-10 place-items-center rounded-xl bg-gradient-to-br from-accent to-emerald-600 text-white"><Cpu className="h-5 w-5" /></div>
          <div><h1 className="text-lg font-semibold">{L("Solicitar cuenta", "Request an account")}</h1><p className="text-xs text-muted">Spark AI Hub</p></div>
        </div>
        <Card className="p-6">
          {done ? (
            <div className="space-y-3 text-center">
              <CheckCircle2 className="mx-auto h-10 w-10 text-ok" />
              <p className="font-medium">{done.status === "pending" ? L("¡Solicitud enviada!", "Request sent!") : L("¡Cuenta creada!", "Account created!")}</p>
              <p className="text-sm text-muted">{done.status === "pending"
                ? L("Un administrador revisará tu solicitud. Recibirás un correo cuando esté aprobada; después podrás entrar con tu usuario y contraseña.", "An administrator will review your request. You will get an e-mail when it is approved; then sign in with your username and password.")
                : L("Ya puedes entrar con tu usuario y contraseña.", "You can now sign in with your username and password.")}</p>
              {info?.mail_enabled && <p className="text-xs text-muted">{L("Te hemos enviado un correo para confirmar tu dirección.", "We sent you an e-mail to confirm your address.")}</p>}
              <a href="/login"><Button variant="primary" className="mt-2">{L("Ir a iniciar sesión", "Go to sign in")}</Button></a>
            </div>
          ) : info?.mode === "closed" ? (
            <Notice tone="warn">{L("El registro está cerrado. Pide una invitación al administrador de la plataforma.", "Sign-up is closed. Ask the platform administrator for an invitation.")}</Notice>
          ) : (
            <form className="space-y-3" onSubmit={submit}>
              <Notice tone="info">{info?.mode === "open" ? L("Tu cuenta se activará al momento.", "Your account will be active immediately.") : L("Tu solicitud la aprueba un administrador o gestor.", "Your request is approved by an administrator or manager.")}
                {info?.allowed_domains?.length ? ` ${L("Dominios permitidos", "Allowed domains")}: ${info.allowed_domains.join(", ")}` : ""}</Notice>
              <Field label={L("Nombre completo", "Full name")}><Input value={f.display_name} onChange={(e) => setF({ ...f, display_name: e.target.value })} required /></Field>
              <Field label={L("Correo electrónico", "E-mail")}><Input type="email" autoComplete="email" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} required /></Field>
              <Field label={L("Usuario", "Username")} hint={L("Minúsculas, números, . _ - (p. ej. nombre.apellido)", "Lowercase, digits, . _ - (e.g. name.surname)")}><Input autoComplete="username" value={f.username} onChange={(e) => setF({ ...f, username: e.target.value.toLowerCase() })} required /></Field>
              <Field label={L("Contraseña", "Password")} hint={L("Mínimo 10 caracteres, mayúsculas, minúsculas y un número.", "At least 10 characters, upper and lower case and a digit.")}><Input type="password" autoComplete="new-password" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} required minLength={10} /></Field>
              <Field label={L("Repite la contraseña", "Repeat password")}><Input type="password" autoComplete="new-password" value={f.password2} onChange={(e) => setF({ ...f, password2: e.target.value })} required /></Field>
              <Field label={L("¿Para qué la usarás? (grupo, proyecto, asignatura)", "What will you use it for? (group, project, course)")}><Textarea rows={3} value={f.reason} onChange={(e) => setF({ ...f, reason: e.target.value })} /></Field>
              <label className="flex items-start gap-2 text-xs text-muted">
                <input type="checkbox" className="mt-0.5" checked={f.accept_terms} onChange={(e) => setF({ ...f, accept_terms: e.target.checked })} />
                <span>{L("Acepto las", "I accept the")} <button type="button" className="text-accent underline" onClick={() => setShowTerms(!showTerms)}>{L("normas de uso", "terms of use")}</button>.</span>
              </label>
              {showTerms && info && <div className="prose-chat max-h-60 overflow-y-auto rounded-lg border border-border p-3 text-xs"><ReactMarkdown>{info.terms.text[lang] ?? info.terms.text.es}</ReactMarkdown></div>}
              {err && <ErrorBox error={err} />}
              <Button type="submit" variant="primary" className="w-full" loading={busy} disabled={!f.accept_terms}><UserPlus className="h-4 w-4" />{L("Enviar solicitud", "Send request")}</Button>
              <p className="text-center text-xs text-muted"><a href="/login" className="hover:text-fg">{L("¿Ya tienes cuenta? Inicia sesión", "Already have an account? Sign in")}</a></p>
            </form>
          )}
        </Card>
        <div className="mt-4 text-center"><button className="text-xs text-muted hover:text-fg" onClick={() => setLang(lang === "es" ? "en" : "es")}>{lang === "es" ? "English" : "Español"}</button></div>
      </div>
    </div>
  );
}
