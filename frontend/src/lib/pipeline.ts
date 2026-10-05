import type { Application, ApplicationStatus } from "@/lib/api";

export type Tone = "neutral" | "ok" | "info" | "violet" | "warn" | "bad";

/** Literal class strings so Tailwind can see them. */
export const TONE_CLASSES: Record<Tone, string> = {
  neutral: "bg-surface-2 text-muted border-line",
  ok: "bg-ok-soft text-ok border-ok-line",
  info: "bg-info-soft text-info border-info-line",
  violet: "bg-violet-soft text-violet border-violet-line",
  warn: "bg-warn-soft text-warn border-warn-line",
  bad: "bg-bad-soft text-bad border-bad-line",
};

export const STATUS_META: Record<ApplicationStatus, { label: string; tone: Tone }> = {
  APPLIED: { label: "Applied", tone: "neutral" },
  PROCESSING: { label: "Screening", tone: "warn" },
  MANUAL_REVIEW: { label: "Needs review", tone: "warn" },
  SUITABLE: { label: "Suitable", tone: "ok" },
  SHORTLISTED: { label: "Shortlisted", tone: "info" },
  INTERVIEW_QUEUED: { label: "Call queued", tone: "violet" },
  IN_PROGRESS: { label: "Interview live", tone: "violet" },
  CALL_INTERRUPTED: { label: "Call dropped", tone: "warn" },
  COMPLETED: { label: "Interview done", tone: "violet" },
  INTERVIEW_COMPLETED: { label: "Interview done", tone: "violet" },
  EVALUATED: { label: "Scored", tone: "ok" },
  REJECTED: { label: "Rejected", tone: "bad" },
  HIRED: { label: "Hired", tone: "ok" },
};

export type StageKey = "ALL" | "SCREENING" | "REVIEW" | "SUITABLE" | "SHORTLISTED" | "INTERVIEW" | "REJECTED";

export const STAGES: { key: StageKey; label: string; statuses: ApplicationStatus[] | null }[] = [
  { key: "ALL", label: "All", statuses: null },
  { key: "SCREENING", label: "Screening", statuses: ["APPLIED", "PROCESSING"] },
  { key: "REVIEW", label: "Needs review", statuses: ["MANUAL_REVIEW"] },
  { key: "SUITABLE", label: "Suitable", statuses: ["SUITABLE"] },
  { key: "SHORTLISTED", label: "Shortlisted", statuses: ["SHORTLISTED"] },
  {
    key: "INTERVIEW",
    label: "In interview",
    statuses: ["INTERVIEW_QUEUED", "IN_PROGRESS", "CALL_INTERRUPTED", "COMPLETED", "INTERVIEW_COMPLETED", "EVALUATED", "HIRED"],
  },
  { key: "REJECTED", label: "Rejected", statuses: ["REJECTED"] },
];

export function inStage(app: Application, key: StageKey): boolean {
  const stage = STAGES.find((s) => s.key === key);
  return !stage?.statuses || stage.statuses.includes(app.status);
}

export interface RowAction {
  label: string;
  to: ApplicationStatus;
  variant: "primary" | "danger";
}

/** Only offers moves the backend state machine accepts. */
export function actionsFor(status: ApplicationStatus): RowAction[] {
  switch (status) {
    case "SUITABLE":
      return [
        { label: "Shortlist", to: "SHORTLISTED", variant: "primary" },
        { label: "Reject", to: "REJECTED", variant: "danger" },
      ];
    case "MANUAL_REVIEW":
      return [
        { label: "Approve", to: "SUITABLE", variant: "primary" },
        { label: "Reject", to: "REJECTED", variant: "danger" },
      ];
    case "SHORTLISTED":
      return [{ label: "Reject", to: "REJECTED", variant: "danger" }];
    case "EVALUATED":
      return [
        { label: "Mark hired", to: "HIRED", variant: "primary" },
        { label: "Reject", to: "REJECTED", variant: "danger" },
      ];
    default:
      return [];
  }
}

export function scoreTone(app: Application): Tone {
  const s = app.screening;
  if (!s || app.status === "PROCESSING" || app.status === "APPLIED") return "neutral";
  if (s.deterministic_passed === false || s.decision === "NOT_SUITABLE") return "bad";
  if (s.decision === "SUITABLE") return "ok";
  return "warn";
}

export function initials(name: string): string {
  return (
    name
      .split(/\s+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((p) => p[0]?.toUpperCase())
      .join("") || "?"
  );
}
