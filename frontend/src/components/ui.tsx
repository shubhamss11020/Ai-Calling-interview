"use client";

import { Check } from "lucide-react";
import type { Application, ApplicationStatus } from "@/lib/api";
import { STATUS_META, TONE_CLASSES, scoreTone, initials, type Tone } from "@/lib/pipeline";

export function StatusBadge({ status }: { status: ApplicationStatus }) {
  const meta = STATUS_META[status] ?? { label: status, tone: "neutral" as Tone };
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium ${TONE_CLASSES[meta.tone]}`}
    >
      <span className="h-1.5 w-1.5 rounded-full bg-current" aria-hidden />
      {meta.label}
    </span>
  );
}

const BAR: Record<Tone, string> = {
  neutral: "bg-line-strong",
  ok: "bg-ok",
  info: "bg-info",
  violet: "bg-violet",
  warn: "bg-warn",
  bad: "bg-bad",
};
const TEXT: Record<Tone, string> = {
  neutral: "text-subtle",
  ok: "text-ok",
  info: "text-info",
  violet: "text-violet",
  warn: "text-warn",
  bad: "text-bad",
};

export function verdictFor(app: Application): string {
  const s = app.screening;
  if (!s || scoreTone(app) === "neutral") return "Pending";
  if (s.deterministic_passed === false) return "Disqualified";
  if (s.decision === "SUITABLE") return "Strong match";
  if (s.decision === "MARGINAL") return "Partial match";
  return "Weak match";
}

export function MatchScore({ app, onEvidence }: { app: Application; onEvidence?: () => void }) {
  const s = app.screening;
  const tone = scoreTone(app);
  const pending = !s || tone === "neutral";
  const score = pending ? 0 : s.match_score;
  return (
    <div className="w-40">
      <div className="flex items-baseline justify-between gap-2">
        <span className={`text-sm font-semibold tabular-nums ${TEXT[tone]}`}>{pending ? "—" : `${score}%`}</span>
        <span className="text-xs text-subtle">{verdictFor(app)}</span>
      </div>
      <div
        className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-surface-2 ring-1 ring-inset ring-line"
        role="meter"
        aria-valuenow={score}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label="Match score"
      >
        <div className={`h-full rounded-full ${BAR[tone]}`} style={{ width: `${score}%` }} />
      </div>
      {onEvidence && !pending && (
        <button
          onClick={onEvidence}
          className="mt-1 text-xs font-medium text-brand underline-offset-2 hover:text-brand-hover hover:underline"
        >
          View evidence
        </button>
      )}
    </div>
  );
}

const AVATAR_HUES = [
  "bg-brand-soft text-brand",
  "bg-info-soft text-info",
  "bg-violet-soft text-violet",
  "bg-ok-soft text-ok",
  "bg-warn-soft text-warn",
];

export function Avatar({ name, size = "md" }: { name: string; size?: "md" | "lg" }) {
  const hue = AVATAR_HUES[[...name].reduce((a, c) => a + c.charCodeAt(0), 0) % AVATAR_HUES.length];
  const dim = size === "lg" ? "h-12 w-12 text-base" : "h-9 w-9 text-xs";
  return (
    <span className={`${dim} ${hue} inline-flex shrink-0 items-center justify-center rounded-full font-semibold`} aria-hidden>
      {initials(name)}
    </span>
  );
}

const SOURCE_TONE: Record<string, Tone> = { LinkedIn: "info", Naukri: "warn", Direct: "neutral", Referral: "violet" };

export function SourceTag({ source }: { source: string }) {
  return (
    <span className={`rounded-md border px-1.5 py-0.5 text-[11px] font-medium ${TONE_CLASSES[SOURCE_TONE[source] ?? "neutral"]}`}>
      {source}
    </span>
  );
}

export function SkillChips({ skills, max = 4 }: { skills: string[]; max?: number }) {
  return (
    <div className="flex flex-wrap items-center gap-1">
      {skills.slice(0, max).map((s) => (
        <span key={s} className="rounded-md bg-surface-2 px-2 py-0.5 text-xs text-muted ring-1 ring-inset ring-line">
          {s}
        </span>
      ))}
      {skills.length > max && (
        <span className="text-xs text-subtle" title={skills.slice(max).join(", ")}>
          +{skills.length - max}
        </span>
      )}
    </div>
  );
}

export function Checkbox({
  checked,
  onChange,
  label,
  disabled,
  indeterminate,
}: {
  checked: boolean;
  onChange: () => void;
  label: string;
  disabled?: boolean;
  indeterminate?: boolean;
}) {
  const on = checked || indeterminate;
  return (
    <button
      type="button"
      role="checkbox"
      aria-checked={indeterminate ? "mixed" : checked}
      aria-label={label}
      disabled={disabled}
      onClick={onChange}
      className={`flex h-[18px] w-[18px] items-center justify-center rounded border transition-colors disabled:cursor-not-allowed disabled:opacity-35 ${
        on ? "border-brand bg-brand text-brand-fg" : "border-line-strong bg-surface hover:border-brand"
      }`}
    >
      {indeterminate ? (
        <span className="h-0.5 w-2 rounded bg-current" />
      ) : checked ? (
        <Check className="h-3 w-3" strokeWidth={3} />
      ) : null}
    </button>
  );
}
