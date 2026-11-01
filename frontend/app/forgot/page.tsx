"use client";
import { MailCheck } from "lucide-react";
import { useState } from "react";
import { Button, Card, ErrorBox, Field, Input, Notice } from "@/components/ui";
import { api } from "@/lib/api";
import { useL } from "@/lib/i18n";

export default function ForgotPage() {
  const L = useL();
  const [email, setEmail] = useState("");
  const [res, setRes] = useState<any>(null);
  const [err, setErr] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  return (
    <div className="grid min-h-screen place-items-center px-4">
      <Card className="w-full max-w-sm space-y-4 p-6">
        <h1 className="text-lg font-semibold">{L("Recuperar contraseña", "Recover password")}</h1>
        {res ? (
          res.mail_enabled
            ? <Notice tone="ok" icon={<MailCheck className="h-4 w-4" />}>{L("Si el correo corresponde a una cuenta, recibirás un enlace válido durante 2 horas. Revisa también la carpeta de spam.", "If the e-mail belongs to an account you will receive a link valid for 2 hours. Check your spam folder too.")}</Notice>
            : <Notice tone="warn">{L("El envío de correo no está configurado. Pide a un administrador o gestor un enlace de restablecimiento (Usuarios → Restablecer).", "Mail is not configured. Ask an administrator or manager for a reset link (Users → Reset).")}</Notice>
        ) : (
          <form className="space-y-3" onSubmit={async (e) => { e.preventDefault(); setBusy(true); setErr(null); try { setRes(await api("/api/v1/auth/password-reset-request", { method: "POST", json: { email } })); } catch (x) { setErr(x); } finally { setBusy(false); } }}>
            <p className="text-sm text-muted">{L("Escribe el correo de tu cuenta.", "Enter your account e-mail.")}</p>
            <Field label="Email"><Input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required /></Field>
            {err && <ErrorBox error={err} />}
            <Button type="submit" variant="primary" className="w-full" loading={busy}>{L("Enviar enlace", "Send link")}</Button>
          </form>
        )}
        <a href="/login" className="block text-center text-xs text-muted hover:text-fg">{L("Volver a iniciar sesión", "Back to sign in")}</a>
      </Card>
    </div>
  );
}
