"use client";

import { useEffect } from "react";
import { X, Mail, Phone, MapPin, CheckCircle2, XCircle, Sparkles } from "lucide-react";
import type { Application, ApplicationStatus } from "@/lib/api";
import { actionsFor, STAGES } from "@/lib/pipeline";
import { Avatar, SourceTag, StatusBadge, MatchScore } from "@/components/ui";
import InterviewPanel from "@/components/InterviewPanel";

const INTERVIEW_STATUSES = STAGES.find((s) => s.key === "INTERVIEW")!.statuses!;

function Section({ title, icon, children }: { title: string; icon?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className="rounded-xl border border-line bg-surface p-4">
      <h3 className="mb-3 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-subtle">
        {icon}
        {title}
      </h3>
      {children}
    </section>
  );
}

export default function CandidateDrawer({
  app,
  live,
  onClose,
  onAction,
  onChanged,
}: {
  app: Application;
  live: boolean;
  onClose: () => void;
  onAction: (id: string, to: ApplicationStatus) => void;
  onChanged: () => void;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
  }, [onClose]);

  const { candidate, screening: scr } = app;
  const actions = actionsFor(app.status);
  const inInterview = INTERVIEW_STATUSES.includes(app.status);

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="anim-fade absolute inset-0 bg-black/40 backdrop-blur-[2px]" onClick={onClose} aria-hidden />
      <aside
        role="dialog"
        aria-modal="true"
        aria-label={`${candidate.full_name} details`}
        className="anim-drawer relative flex h-full w-full max-w-xl flex-col border-l border-line bg-bg shadow-drawer"
      >
        <header className="flex items-start gap-4 border-b border-line bg-surface px-6 py-5">
          <Avatar name={candidate.full_name} size="lg" />
          <div className="min-w-0 flex-1">
            <h2 className="truncate text-lg font-semibold text-fg">{candidate.full_name}</h2>
            <p className="truncate text-sm text-muted">{candidate.headline || "Applicant"}</p>
            <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-subtle">
              <span className="inline-flex items-center gap-1"><Mail className="h-3.5 w-3.5" />{candidate.email}</span>
              {candidate.phone && <span className="inline-flex items-center gap-1"><Phone className="h-3.5 w-3.5" />{candidate.phone}</span>}
              {candidate.location && <span className="inline-flex items-center gap-1"><MapPin className="h-3.5 w-3.5" />{candidate.location}</span>}
            </div>
          </div>
          <button
            onClick={onClose}
            aria-label="Close"
            className="rounded-lg p-1.5 text-subtle transition-colors hover:bg-surface-2 hover:text-fg"
          >
            <X className="h-5 w-5" />
          </button>
        </header>

        <div className="flex-1 space-y-4 overflow-y-auto px-6 py-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <StatusBadge status={app.status} />
              <SourceTag source={app.source} />
            </div>
            <span className="text-xs text-subtle">{candidate.total_experience_years} yrs experience</span>
          </div>

          {scr ? (
            <>
              <Section title="Eligibility check" icon={scr.deterministic_passed ? <CheckCircle2 className="h-4 w-4 text-ok" /> : <XCircle className="h-4 w-4 text-bad" />}>
                <p className="text-sm text-fg">
                  <span className={`font-medium ${scr.deterministic_passed ? "text-ok" : "text-bad"}`}>
                    {scr.deterministic_passed ? "Passed" : "Did not pass"}
                  </span>
                  <span className="text-muted"> — {scr.deterministic_notes || "All baseline criteria met."}</span>
                </p>
              </Section>

              <Section title="AI match" icon={<Sparkles className="h-4 w-4 text-brand" />}>
                <div className="mb-4"><MatchScore app={app} /></div>
                <div className="grid gap-4 sm:grid-cols-2">
                  <div>
                    <div className="mb-1.5 text-xs font-medium text-ok">Matched skills</div>
                    <div className="flex flex-wrap gap-1.5">
                      {scr.matched_skills.length === 0 ? (
                        <span className="text-xs text-subtle">None</span>
                      ) : scr.matched_skills.map((s) => (
                        <span key={s} className="rounded-md border border-ok-line bg-ok-soft px-2 py-0.5 text-xs text-ok">{s}</span>
                      ))}
                    </div>
                  </div>
                  <div>
                    <div className="mb-1.5 text-xs font-medium text-bad">Missing skills</div>
                    <div className="flex flex-wrap gap-1.5">
                      {scr.missing_skills.length === 0 ? (
                        <span className="text-xs text-subtle">None</span>
                      ) : scr.missing_skills.map((s) => (
                        <span key={s} className="rounded-md border border-bad-line bg-bad-soft px-2 py-0.5 text-xs text-bad">{s}</span>
                      ))}
                    </div>
                  </div>
                </div>
                {scr.reason && (
                  <p className="mt-4 rounded-lg bg-surface-2 p-3 text-sm leading-relaxed text-muted">{scr.reason}</p>
                )}
              </Section>
            </>
          ) : (
            <Section title="Screening">
              <p className="text-sm text-muted">Screening hasn&apos;t finished for this candidate yet.</p>
            </Section>
          )}

          {(inInterview || app.status === "SHORTLISTED") && (
            <InterviewPanel app={app} live={live} onChanged={onChanged} />
          )}
        </div>

        {actions.length > 0 && (
          <footer className="flex items-center justify-end gap-2 border-t border-line bg-surface px-6 py-4">
            {actions.map((a) => (
              <button
                key={a.to}
                onClick={() => { onAction(app.id, a.to); onClose(); }}
                className={
                  a.variant === "primary"
                    ? "rounded-lg bg-brand px-4 py-2 text-sm font-medium text-brand-fg shadow-card transition-colors hover:bg-brand-hover"
                    : "rounded-lg border border-line-strong bg-surface px-4 py-2 text-sm font-medium text-bad transition-colors hover:bg-bad-soft"
                }
              >
                {a.label}
              </button>
            ))}
          </footer>
        )}
      </aside>
    </div>
  );
}
