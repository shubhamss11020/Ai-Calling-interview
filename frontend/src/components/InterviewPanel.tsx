"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Bot, Loader2, MessageSquare, Mic, Send, User } from "lucide-react";
import {
  completeInterview,
  createPractice,
  listInterviews,
  practiceStart,
  practiceTurn,
  type Interview,
  type TranscriptTurn,
} from "@/lib/actions";
import type { Application } from "@/lib/api";
import { PrimaryButton, SecondaryButton, inputClass } from "@/components/Modal";

const LABEL: Record<string, string> = {
  QUEUED: "Waiting to be called",
  CALLING: "Calling the candidate",
  IN_PROGRESS: "Interview in progress",
  CALL_INTERRUPTED: "Call dropped, waiting for the candidate to call back",
  COMPLETED: "Interview finished, scoring",
  MISSED: "Candidate didn't answer",
  FAILED: "Could not reach the candidate",
};

const COMPETENCY_LABEL: Record<string, string> = {
  technical_depth: "Technical depth",
  problem_solving: "Problem solving",
  communication: "Communication",
  architecture: "Architecture",
};

function Bubble({ turn }: { turn: TranscriptTurn }) {
  const ai = turn.speaker.toLowerCase().startsWith("ai");
  return (
    <div className={`flex gap-2 ${ai ? "" : "flex-row-reverse"}`}>
      <span className={`mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full ${ai ? "bg-brand-soft text-brand" : "bg-surface-2 text-muted"}`}>
        {ai ? <Bot className="h-3.5 w-3.5" /> : <User className="h-3.5 w-3.5" />}
      </span>
      <p className={`max-w-[85%] rounded-xl px-3 py-2 text-sm leading-relaxed ${ai ? "bg-surface-2 text-fg" : "bg-brand text-brand-fg"}`}>{turn.text}</p>
    </div>
  );
}

function scoreColour(n: number) {
  return n >= 70 ? "bg-ok" : n >= 45 ? "bg-warn" : "bg-bad";
}

export default function InterviewPanel({
  app,
  live,
  onChanged,
}: {
  app: Application;
  live: boolean;
  onChanged: () => void;
}) {
  const [interviews, setInterviews] = useState<Interview[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [answer, setAnswer] = useState("");
  const chatEnd = useRef<HTMLDivElement>(null);

  const refresh = useCallback(async () => {
    if (!live) return;
    try {
      setInterviews(await listInterviews(app.id));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't load the interview");
    }
  }, [app.id, live]);

  useEffect(() => {
    const id = setTimeout(refresh, 0);
    return () => clearTimeout(id);
  }, [refresh, app.status]);

  const current = interviews?.[0];
  const phoneActive = !!current && current.call_direction !== "PRACTICE" && ["QUEUED", "CALLING", "IN_PROGRESS", "CALL_INTERRUPTED", "COMPLETED"].includes(current.status);

  // follow a live phone call
  useEffect(() => {
    if (!phoneActive) return;
    const t = setInterval(() => { refresh(); onChanged(); }, 4000);
    return () => clearInterval(t);
  }, [phoneActive, refresh, onChanged]);

  useEffect(() => { chatEnd.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [current?.transcript.length]);

  const guard = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try { await fn(); } catch (e) { setError(e instanceof Error ? e.message : "Something went wrong"); }
    setBusy(false);
  };

  const startPractice = () => guard(async () => {
    const itw = await createPractice(app.id);
    await practiceStart(itw.id);
    await refresh();
    onChanged();
  });

  const send = () => guard(async () => {
    if (!current || !answer.trim()) return;
    const text = answer.trim();
    setAnswer("");
    const res = await practiceTurn(current.id, text);
    setInterviews((prev) => (prev ? [res.interview, ...prev.slice(1)] : [res.interview]));
    if (res.done) onChanged();
  });

  const endAndScore = () => guard(async () => {
    if (!current) return;
    await completeInterview(current.id);
    await refresh();
    onChanged();
  });

  if (!live) return null;

  const result = current?.result;
  const transcript = result?.full_transcript?.length ? result.full_transcript : current?.transcript ?? [];
  const practiceLive = current?.call_direction === "PRACTICE" && current.status === "IN_PROGRESS";

  return (
    <section className="rounded-xl border border-line bg-surface p-4">
      <h3 className="mb-3 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-subtle">
        <MessageSquare className="h-4 w-4 text-violet" /> AI interview
      </h3>

      {interviews === null && !error && <Loader2 className="h-4 w-4 animate-spin text-subtle" />}

      {interviews && !current && (
        app.status === "SHORTLISTED" ? (
          <div className="space-y-2">
            <p className="text-sm text-muted">Select this candidate in the table and choose <strong>Start AI interviews</strong> to call them, or rehearse the interview here without a phone.</p>
            <SecondaryButton onClick={startPractice} disabled={busy}><Mic className="h-4 w-4" />{busy ? "Starting…" : "Practice interview (no phone)"}</SecondaryButton>
          </div>
        ) : (
          <p className="text-sm text-muted">No interview yet. Shortlist the candidate to enable it.</p>
        )
      )}

      {current && (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-subtle">
            <span className="font-medium text-fg">
              {current.call_direction === "PRACTICE" ? "Practice session" : LABEL[current.status] ?? current.status}
            </span>
            <span>
              {current.call_direction !== "PRACTICE" && <>Candidate PIN <strong className="font-mono text-fg">{current.pin_code}</strong></>}
              {current.duration_seconds ? ` · ${Math.round(current.duration_seconds / 60)} min` : ""}
            </span>
          </div>

          {transcript.length > 0 && (
            <div className="max-h-72 space-y-2 overflow-y-auto rounded-lg border border-line bg-bg p-3" aria-live="polite">
              {transcript.map((t, i) => <Bubble key={i} turn={t} />)}
              <div ref={chatEnd} />
            </div>
          )}

          {practiceLive && (
            <div className="space-y-2">
              <div className="flex gap-2">
                <input
                  className={inputClass}
                  value={answer}
                  onChange={(e) => setAnswer(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && !busy && send()}
                  placeholder="Type the candidate's answer…"
                  aria-label="Candidate answer"
                  disabled={busy}
                />
                <PrimaryButton onClick={send} disabled={busy || !answer.trim()} aria-label="Send answer"><Send className="h-4 w-4" /></PrimaryButton>
              </div>
              <button onClick={endAndScore} disabled={busy} className="text-xs text-subtle underline-offset-2 hover:text-fg hover:underline">End interview now and score it</button>
            </div>
          )}

          {current.status === "COMPLETED" && !result && (
            <p className="flex items-center gap-2 text-sm text-muted"><Loader2 className="h-4 w-4 animate-spin" /> Scoring the interview…</p>
          )}

          {result && (
            <div className="space-y-3 rounded-lg border border-line bg-bg p-4">
              <div className="flex items-baseline justify-between">
                <span className="text-sm font-medium text-fg">Interview score</span>
                <span className="text-2xl font-semibold tabular-nums text-fg">{result.overall_score}<span className="text-sm font-normal text-subtle">/100</span></span>
              </div>
              <div className="space-y-2">
                {Object.entries(result.competency_scores ?? {}).map(([k, v]) => (
                  <div key={k}>
                    <div className="mb-1 flex justify-between text-xs text-muted"><span>{COMPETENCY_LABEL[k] ?? k}</span><span className="tabular-nums">{v}</span></div>
                    <div className="h-1.5 overflow-hidden rounded-full bg-surface-2 ring-1 ring-inset ring-line">
                      <div className={`h-full rounded-full ${scoreColour(v)}`} style={{ width: `${v}%` }} />
                    </div>
                  </div>
                ))}
              </div>
              {result.summary && <p className="text-sm leading-relaxed text-muted">{result.summary}</p>}
              {(result.recording_url || current.recording_url) && (
                <a className="text-sm font-medium text-brand hover:underline" href={result.recording_url ?? current.recording_url ?? "#"} target="_blank" rel="noreferrer">Listen to the recording</a>
              )}
            </div>
          )}
        </div>
      )}

      {error && <p role="alert" className="mt-3 rounded-lg border border-bad-line bg-bad-soft px-3 py-2 text-sm text-bad">{error}</p>}
    </section>
  );
}
