"use client";
import * as Dialog from "@radix-ui/react-dialog";
import * as RSwitch from "@radix-ui/react-switch";
import * as RTabs from "@radix-ui/react-tabs";
import * as RTooltip from "@radix-ui/react-tooltip";
import clsx from "clsx";
import { AlertTriangle, Check, Copy, HelpCircle, Inbox, Loader2, X } from "lucide-react";
import { forwardRef, useState } from "react";
import { useI18n } from "@/lib/i18n";

export const cx = clsx;

// ---------------------------------------------------------------- Button
type BtnProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "ghost" | "danger" | "outline";
  size?: "sm" | "md" | "lg" | "icon";
  loading?: boolean;
};
export const Button = forwardRef<HTMLButtonElement, BtnProps>(function Button(
  { variant = "secondary", size = "md", loading, className, children, disabled, ...rest }, ref) {
  return (
    <button
      ref={ref}
      disabled={disabled || loading}
      className={cx(
        "inline-flex items-center justify-center gap-2 rounded-lg font-medium transition-all duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60 focus-visible:ring-offset-2 focus-visible:ring-offset-bg disabled:cursor-not-allowed disabled:opacity-50 active:scale-[.98]",
        {
          primary: "bg-accent text-accent-fg shadow-sm hover:bg-accent/90",
          secondary: "bg-elevated text-fg border border-border hover:bg-border/40",
          outline: "border border-border text-fg hover:bg-elevated",
          ghost: "text-muted hover:bg-elevated hover:text-fg",
          danger: "bg-danger text-white hover:bg-danger/90",
        }[variant],
        { sm: "h-8 px-3 text-xs", md: "h-9 px-4 text-sm", lg: "h-11 px-5 text-sm", icon: "h-9 w-9" }[size],
        className,
      )}
      {...rest}
    >
      {loading && <Loader2 className="h-4 w-4 animate-spin" aria-hidden />}
      {children}
    </button>
  );
});

// ---------------------------------------------------------------- Card
export function Card({ className, children, ...rest }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={cx("animate-fade-in rounded-2xl border border-border bg-surface shadow-card", className)} {...rest}>
      {children}
    </div>
  );
}
export function CardHeader({ title, subtitle, actions, icon }: { title: React.ReactNode; subtitle?: React.ReactNode; actions?: React.ReactNode; icon?: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border/70 px-5 py-4">
      <div className="flex min-w-0 items-start gap-3">
        {icon && <div className="mt-0.5 rounded-lg bg-accent/10 p-2 text-accent">{icon}</div>}
        <div className="min-w-0">
          <h3 className="truncate text-sm font-semibold text-fg">{title}</h3>
          {subtitle && <p className="mt-0.5 text-xs text-muted">{subtitle}</p>}
        </div>
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}
export function CardBody({ className, children }: { className?: string; children: React.ReactNode }) {
  return <div className={cx("p-5", className)}>{children}</div>;
}

// ---------------------------------------------------------------- PageHeader
export function PageHeader({ title, subtitle, actions, icon }: { title: string; subtitle?: string; actions?: React.ReactNode; icon?: React.ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div className="flex items-center gap-3">
        {icon && <div className="rounded-xl bg-gradient-to-br from-accent/20 to-info/10 p-2.5 text-accent">{icon}</div>}
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-fg sm:text-2xl">{title}</h1>
          {subtitle && <p className="mt-1 text-sm text-muted">{subtitle}</p>}
        </div>
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

// ---------------------------------------------------------------- Badge / Status
const TONES = {
  ok: "bg-ok/12 text-ok ring-ok/25", warn: "bg-warn/12 text-warn ring-warn/25", danger: "bg-danger/12 text-danger ring-danger/25",
  info: "bg-info/12 text-info ring-info/25", muted: "bg-elevated text-muted ring-border", accent: "bg-accent/12 text-accent ring-accent/25",
};
export type Tone = keyof typeof TONES;
export function Badge({ tone = "muted", children, className, title }: { tone?: Tone; children: React.ReactNode; className?: string; title?: string }) {
  return (
    <span title={title} className={cx("inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 ring-inset", TONES[tone], className)}>
      {children}
    </span>
  );
}

const STATE_TONE: Record<string, Tone> = {
  completed: "ok", ready: "ok", installed: "ok", ok: "ok", verified: "ok", running: "info", loading: "info", downloading: "info",
  preparing: "info", installing: "info", verifying: "info", busy: "info", queued: "muted", pending: "muted", discovered: "muted",
  downloaded: "accent", stopped: "muted", likely: "accent", experimental: "warn", paused: "warn", unloading: "warn",
  degraded: "warn", interrupted: "warn", insufficient: "warn", failed: "danger", down: "danger", unsupported: "danger",
  cancelled: "muted", disabled: "muted", removing: "warn", not_installed: "muted",
};
export function StatusBadge({ state, label }: { state?: string | null; label?: string }) {
  const { t } = useI18n();
  if (!state) return <Badge>—</Badge>;
  const text = label ?? (t(`state.${state}`) !== `state.${state}` ? t(`state.${state}`) : t(`discover.verdict.${state}`) !== `discover.verdict.${state}` ? t(`discover.verdict.${state}`) : state);
  const tone = STATE_TONE[state] ?? "muted";
  return (
    <Badge tone={tone}>
      {["running", "loading", "downloading", "preparing", "installing", "verifying"].includes(state) && <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-current" />}
      {text}
    </Badge>
  );
}

// ---------------------------------------------------------------- Inputs
export const Input = forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(function Input({ className, ...rest }, ref) {
  return (
    <input ref={ref} className={cx("h-9 w-full rounded-lg border border-border bg-bg px-3 text-sm text-fg placeholder:text-subtle transition focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/25 disabled:opacity-60", className)} {...rest} />
  );
});
export const Textarea = forwardRef<HTMLTextAreaElement, React.TextareaHTMLAttributes<HTMLTextAreaElement>>(function Textarea({ className, ...rest }, ref) {
  return (
    <textarea ref={ref} className={cx("w-full rounded-lg border border-border bg-bg px-3 py-2 text-sm text-fg placeholder:text-subtle transition focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/25", className)} {...rest} />
  );
});
export function Select({ className, children, ...rest }: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select className={cx("h-9 w-full rounded-lg border border-border bg-bg px-2.5 text-sm text-fg focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/25", className)} {...rest}>
      {children}
    </select>
  );
}
export function Field({ label, hint, children, htmlFor, tip }: { label: string; hint?: string; children: React.ReactNode; htmlFor?: string; tip?: string }) {
  return (
    <div className="space-y-1.5">
      <label htmlFor={htmlFor} className="flex items-center gap-1 text-xs font-medium text-muted">
        {label}
        {tip && <Tip text={tip} />}
      </label>
      {children}
      {hint && <p className="text-[11px] text-subtle">{hint}</p>}
    </div>
  );
}
export function Switch({ checked, onChange, label, id }: { checked: boolean; onChange: (v: boolean) => void; label?: string; id?: string }) {
  return (
    <label className="inline-flex cursor-pointer items-center gap-2 text-sm text-fg" htmlFor={id}>
      <RSwitch.Root id={id} checked={checked} onCheckedChange={onChange} className="relative h-5 w-9 rounded-full bg-border transition data-[state=checked]:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50">
        <RSwitch.Thumb className="block h-4 w-4 translate-x-0.5 rounded-full bg-white shadow transition data-[state=checked]:translate-x-[18px]" />
      </RSwitch.Root>
      {label}
    </label>
  );
}

// ---------------------------------------------------------------- Tooltip
export function Tip({ text, children }: { text: string; children?: React.ReactNode }) {
  return (
    <RTooltip.Root>
      <RTooltip.Trigger asChild>
        {children ?? (
          <button type="button" aria-label={text} className="text-subtle hover:text-muted">
            <HelpCircle className="h-3.5 w-3.5" />
          </button>
        )}
      </RTooltip.Trigger>
      <RTooltip.Portal>
        <RTooltip.Content sideOffset={6} className="z-[90] max-w-xs rounded-lg border border-border bg-elevated px-3 py-2 text-xs text-fg shadow-card">
          {text}
        </RTooltip.Content>
      </RTooltip.Portal>
    </RTooltip.Root>
  );
}

// ---------------------------------------------------------------- Modal
export function Modal({ open, onOpenChange, title, description, children, wide }: { open: boolean; onOpenChange: (o: boolean) => void; title: string; description?: string; children: React.ReactNode; wide?: boolean }) {
  const { t } = useI18n();
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-[80] bg-black/40 backdrop-blur-sm data-[state=open]:animate-fade-in" />
        <Dialog.Content className={cx("fixed left-1/2 top-1/2 z-[81] max-h-[90vh] w-[calc(100vw-2rem)] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-2xl border border-border bg-surface shadow-2xl focus:outline-none", wide ? "max-w-4xl" : "max-w-lg")}>
          <div className="flex items-start justify-between gap-4 border-b border-border px-5 py-4">
            <div>
              <Dialog.Title className="text-base font-semibold text-fg">{title}</Dialog.Title>
              {description && <Dialog.Description className="mt-1 text-xs text-muted">{description}</Dialog.Description>}
            </div>
            <Dialog.Close asChild>
              <Button variant="ghost" size="icon" aria-label={t("common.close")}><X className="h-4 w-4" /></Button>
            </Dialog.Close>
          </div>
          <div className="p-5">{children}</div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

// ---------------------------------------------------------------- Tabs
export function Tabs({ tabs, value, onValueChange }: { tabs: { id: string; label: React.ReactNode; content: React.ReactNode }[]; value?: string; onValueChange?: (v: string) => void }) {
  return (
    <RTabs.Root defaultValue={tabs[0]?.id} value={value} onValueChange={onValueChange}>
      <RTabs.List className="mb-4 flex gap-1 overflow-x-auto rounded-xl border border-border bg-elevated/60 p-1">
        {tabs.map((tb) => (
          <RTabs.Trigger key={tb.id} value={tb.id} className="whitespace-nowrap rounded-lg px-3 py-1.5 text-sm text-muted transition hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 data-[state=active]:bg-surface data-[state=active]:text-fg data-[state=active]:shadow-sm">
            {tb.label}
          </RTabs.Trigger>
        ))}
      </RTabs.List>
      {tabs.map((tb) => (
        <RTabs.Content key={tb.id} value={tb.id} className="focus:outline-none">{tb.content}</RTabs.Content>
      ))}
    </RTabs.Root>
  );
}

// ---------------------------------------------------------------- Feedback
export function Skeleton({ className }: { className?: string }) {
  return <div className={cx("relative overflow-hidden rounded-lg bg-elevated", className)}><div className="absolute inset-0 -translate-x-full animate-shimmer bg-gradient-to-r from-transparent via-border/40 to-transparent" /></div>;
}
export function Empty({ title, hint, icon, action }: { title?: string; hint?: string; icon?: React.ReactNode; action?: React.ReactNode }) {
  const { t } = useI18n();
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-12 text-center">
      <div className="rounded-full bg-elevated p-3 text-subtle">{icon ?? <Inbox className="h-5 w-5" />}</div>
      <p className="text-sm font-medium text-fg">{title ?? t("common.none")}</p>
      {hint && <p className="max-w-md text-xs text-muted">{hint}</p>}
      {action}
    </div>
  );
}
export function ErrorBox({ error, onRetry }: { error: any; onRetry?: () => void }) {
  const { t } = useI18n();
  if (!error) return null;
  return (
    <div role="alert" className="flex items-start gap-3 rounded-xl border border-danger/30 bg-danger/8 p-4 text-sm text-danger">
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
      <div className="min-w-0 flex-1">
        <p className="font-medium">{error.message || String(error)}</p>
        {error.detail?.remediation && <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap text-xs opacity-80">{error.detail.remediation}</pre>}
      </div>
      {onRetry && <Button size="sm" variant="outline" onClick={onRetry}>{t("common.retry")}</Button>}
    </div>
  );
}
export function Notice({ tone = "info", children, icon }: { tone?: Tone; children: React.ReactNode; icon?: React.ReactNode }) {
  return <div className={cx("flex items-start gap-2.5 rounded-xl px-4 py-3 text-xs ring-1 ring-inset", TONES[tone])}>{icon}<div className="min-w-0 flex-1">{children}</div></div>;
}
export function Progress({ value, tone = "accent", className }: { value?: number | null; tone?: "accent" | "ok" | "warn" | "danger"; className?: string }) {
  const v = Math.max(0, Math.min(100, value ?? 0));
  return (
    <div className={cx("h-1.5 w-full overflow-hidden rounded-full bg-elevated", className)} role="progressbar" aria-valuenow={Math.round(v)} aria-valuemin={0} aria-valuemax={100}>
      <div className={cx("h-full rounded-full transition-all duration-500", { accent: "bg-accent", ok: "bg-ok", warn: "bg-warn", danger: "bg-danger" }[tone])} style={{ width: `${v}%` }} />
    </div>
  );
}
export function Stat({ label, value, sub, icon, tone, unavailable }: { label: string; value: React.ReactNode; sub?: React.ReactNode; icon?: React.ReactNode; tone?: Tone; unavailable?: boolean }) {
  return (
    <Card className="p-4">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-muted">{label}</span>
        {icon && <span className={cx("rounded-lg p-1.5", tone ? TONES[tone] : "bg-elevated text-muted")}>{icon}</span>}
      </div>
      <div className={cx("mt-2 text-2xl font-semibold tracking-tight tabular-nums", unavailable ? "text-subtle" : "text-fg")}>{value}</div>
      {sub && <div className="mt-1 text-xs text-muted">{sub}</div>}
    </Card>
  );
}

export function CopyButton({ text, label }: { text: string; label?: string }) {
  const [done, setDone] = useState(false);
  const { t } = useI18n();
  return (
    <Button size="sm" variant="ghost" aria-label={t("common.copy")} onClick={async () => {
      try {
        await navigator.clipboard.writeText(text);
      } catch {
        const ta = document.createElement("textarea");
        ta.value = text;
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        ta.remove();
      }
      setDone(true);
      setTimeout(() => setDone(false), 1500);
    }}>
      {done ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
      {label && (done ? t("common.copied") : label)}
    </Button>
  );
}

export function Code({ children, className }: { children: string; className?: string }) {
  return (
    <div className={cx("group relative", className)}>
      <pre className="overflow-x-auto rounded-xl border border-border bg-bg p-4 font-mono text-xs leading-relaxed text-fg"><code>{children}</code></pre>
      <div className="absolute right-2 top-2 opacity-60 transition group-hover:opacity-100"><CopyButton text={children} /></div>
    </div>
  );
}

export function Table({ head, children, className }: { head: React.ReactNode[]; children: React.ReactNode; className?: string }) {
  return (
    <div className={cx("overflow-x-auto", className)}>
      <table className="w-full text-left text-sm">
        <thead><tr className="border-b border-border text-xs text-muted">{head.map((h, i) => <th key={i} className="whitespace-nowrap px-4 py-2.5 font-medium">{h}</th>)}</tr></thead>
        <tbody className="divide-y divide-border/60">{children}</tbody>
      </table>
    </div>
  );
}
export function Td({ children, className }: { children?: React.ReactNode; className?: string }) {
  return <td className={cx("px-4 py-2.5 align-middle text-fg", className)}>{children}</td>;
}

export function Confirm({ open, onOpenChange, title, text, onConfirm, danger, busy }: { open: boolean; onOpenChange: (o: boolean) => void; title: string; text: string; onConfirm: () => void; danger?: boolean; busy?: boolean }) {
  const { t } = useI18n();
  return (
    <Modal open={open} onOpenChange={onOpenChange} title={title}>
      <p className="text-sm text-muted">{text}</p>
      <div className="mt-5 flex justify-end gap-2">
        <Button variant="ghost" onClick={() => onOpenChange(false)}>{t("common.cancel")}</Button>
        <Button variant={danger ? "danger" : "primary"} loading={busy} onClick={onConfirm}>{t("common.confirm")}</Button>
      </div>
    </Modal>
  );
}
