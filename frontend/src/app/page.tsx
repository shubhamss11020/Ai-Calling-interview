"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AudioLines,
  Briefcase,
  CheckCircle2,
  Clock,
  Info,
  ListChecks,
  MapPin,
  PhoneCall,
  Plus,
  Search,
  UploadCloud,
  Users,
  X,
} from "lucide-react";
import {
  checkBackend,
  dispatchBatchInterviews,
  fetchApplications,
  fetchJobs,
  updateApplicationStatus,
  type Application,
  type ApplicationStatus,
  type Job,
} from "@/lib/api";
import { STAGES, actionsFor, inStage, type StageKey } from "@/lib/pipeline";
import { Avatar, Checkbox, MatchScore, SkillChips, SourceTag, StatusBadge } from "@/components/ui";
import CandidateDrawer from "@/components/CandidateDrawer";
import NewJobModal from "@/components/NewJobModal";
import UploadModal from "@/components/UploadModal";
import KnowledgeModal from "@/components/KnowledgeModal";
import { PrimaryButton, SecondaryButton } from "@/components/Modal";

type Toast = { kind: "success" | "error"; text: string } | null;

export default function DashboardPage() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [selectedJobId, setSelectedJobId] = useState("");
  const [applications, setApplications] = useState<Application[]>([]);
  const [loading, setLoading] = useState(true);
  const [live, setLive] = useState(true);
  const [backendUp, setBackendUp] = useState<boolean | null>(null);

  const [stage, setStage] = useState<StageKey>("ALL");
  const [sourceFilter, setSourceFilter] = useState("ALL");
  const [query, setQuery] = useState("");
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [viewingId, setViewingId] = useState<string | null>(null);
  const [dispatching, setDispatching] = useState(false);
  const [toast, setToast] = useState<Toast>(null);
  const [modal, setModal] = useState<null | "job" | "upload" | "questions">(null);

  const notify = useCallback((kind: "success" | "error", text: string) => {
    setToast({ kind, text });
    setTimeout(() => setToast(null), 4500);
  }, []);

  const loadAll = useCallback(async (preferJobId?: string) => {
    const [{ data: loadedJobs, live: jobsLive }, up] = await Promise.all([fetchJobs(), checkBackend()]);
    setBackendUp(up);
    setJobs(loadedJobs);
    const target = loadedJobs.find((j) => j.id === preferJobId) ?? loadedJobs[0];
    if (target) {
      setSelectedJobId(target.id);
      const { data, live: appsLive } = await fetchApplications(target.id);
      setApplications(data);
      setLive(jobsLive && appsLive);
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    const id = setTimeout(loadAll, 0); // async data load: state is set after the fetch resolves
    return () => clearTimeout(id);
  }, [loadAll]);

  // quietly refresh the candidate list (screening results, live interviews)
  const reloadApplications = useCallback(async () => {
    if (!selectedJobId || !live) return;
    const { data, live: ok } = await fetchApplications(selectedJobId);
    if (ok) setApplications(data);
  }, [selectedJobId, live]);

  const busyStatuses = ["APPLIED", "PROCESSING", "INTERVIEW_QUEUED", "IN_PROGRESS", "COMPLETED"];
  const hasWork = live && applications.some((a) => a.job_id === selectedJobId && busyStatuses.includes(a.status));
  useEffect(() => {
    if (!hasWork) return;
    const t = setInterval(reloadApplications, 5000);
    return () => clearInterval(t);
  }, [hasWork, reloadApplications]);

  const switchJob = async (jobId: string) => {
    setSelectedJobId(jobId);
    setSelectedIds([]);
    setStage("ALL");
    setLoading(true);
    const { data, live: appsLive } = await fetchApplications(jobId);
    setApplications(data);
    setLive(appsLive);
    setLoading(false);
  };

  const currentJob = jobs.find((j) => j.id === selectedJobId);
  const jobApps = useMemo(() => applications.filter((a) => a.job_id === selectedJobId), [applications, selectedJobId]);

  const counts = useMemo(() => {
    const out = {} as Record<StageKey, number>;
    for (const s of STAGES) out[s.key] = jobApps.filter((a) => inStage(a, s.key)).length;
    return out;
  }, [jobApps]);

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    return jobApps.filter(
      (a) =>
        inStage(a, stage) &&
        (sourceFilter === "ALL" || a.source === sourceFilter) &&
        (!q ||
          a.candidate.full_name.toLowerCase().includes(q) ||
          a.candidate.extracted_skills.some((s) => s.toLowerCase().includes(q)))
    );
  }, [jobApps, stage, sourceFilter, query]);

  // Only shortlisted candidates can be sent to the AI interviewer
  const selectableRows = rows.filter((a) => a.status === "SHORTLISTED");
  const allSelected = selectableRows.length > 0 && selectableRows.every((a) => selectedIds.includes(a.id));
  const someSelected = selectedIds.length > 0 && !allSelected;

  const toggleAll = () => setSelectedIds(allSelected ? [] : selectableRows.map((a) => a.id));
  const toggleOne = (id: string) => setSelectedIds((p) => (p.includes(id) ? p.filter((i) => i !== id) : [...p, id]));

  const changeStatus = async (appId: string, to: ApplicationStatus) => {
    const previous = applications.find((a) => a.id === appId)?.status;
    setApplications((p) => p.map((a) => (a.id === appId ? { ...a, status: to } : a)));
    if (!live) return notify("success", "Updated (demo data, not saved)");
    const res = await updateApplicationStatus(appId, to);
    if (!res.ok && previous) {
      setApplications((p) => p.map((a) => (a.id === appId ? { ...a, status: previous } : a)));
      return notify("error", res.message ?? "Couldn't update the candidate");
    }
    notify("success", "Candidate updated");
  };

  const startInterviews = async () => {
    if (selectedIds.length === 0) return;
    setDispatching(true);
    const count = selectedIds.length;
    const res = live ? await dispatchBatchInterviews(selectedIds) : { ok: true };
    if (res.ok) {
      setApplications((p) => p.map((a) => (selectedIds.includes(a.id) ? { ...a, status: "INTERVIEW_QUEUED" } : a)));
      setSelectedIds([]);
      notify("success", `${count} interview${count > 1 ? "s" : ""} queued. Candidates will be called shortly.`);
    } else {
      notify("error", res.message ?? "Couldn't start the interviews");
    }
    setDispatching(false);
  };

  const viewing = applications.find((a) => a.id === viewingId) ?? null;

  return (
    <div className="flex min-h-screen flex-col">
      {/* Top bar */}
      <header className="sticky top-0 z-30 border-b border-line bg-surface/90 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-7xl items-center justify-between px-4 sm:px-6">
          <div className="flex items-center gap-2.5">
            <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand text-brand-fg">
              <AudioLines className="h-4.5 w-4.5" />
            </span>
            <span className="text-[15px] font-semibold tracking-tight text-fg">TalentVoice</span>
          </div>
          <div className="flex items-center gap-2 text-xs text-muted" role="status">
            <span
              className={`h-2 w-2 rounded-full ${backendUp === null ? "bg-line-strong" : backendUp ? "bg-ok" : "bg-bad"}`}
              aria-hidden
            />
            {backendUp === null ? "Connecting" : backendUp ? "Connected" : "Backend offline"}
          </div>
        </div>
      </header>

      <main className="mx-auto flex w-full max-w-7xl flex-1 flex-col gap-6 px-4 py-8 sm:px-6">
        {!loading && !live && (
          <div className="flex flex-col gap-3 rounded-xl border border-info-line bg-info-soft px-4 py-3 text-sm text-info sm:flex-row sm:items-center sm:justify-between">
            <p className="flex items-start gap-3">
              <Info className="mt-0.5 h-4 w-4 shrink-0" />
              {backendUp
                ? "You have no jobs yet, so this is sample data. Create your first job to start screening real candidates."
                : "The backend can't be reached, so this is sample data and nothing you change will be saved."}
            </p>
            {backendUp && <PrimaryButton onClick={() => setModal("job")}><Plus className="h-4 w-4" />Create your first job</PrimaryButton>}
          </div>
        )}

        {/* Page header */}
        <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
          <div className="min-w-0">
            <p className="text-xs font-medium uppercase tracking-wide text-subtle">Hiring pipeline</p>
            {currentJob ? (
              <>
                <h1 className="mt-1 truncate text-2xl font-semibold tracking-tight text-fg">{currentJob.title}</h1>
                <p className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-muted">
                  <span className="inline-flex items-center gap-1.5"><MapPin className="h-4 w-4 text-subtle" />{currentJob.location}</span>
                  <span className="inline-flex items-center gap-1.5"><Briefcase className="h-4 w-4 text-subtle" />{currentJob.employment_type}</span>
                  <span className="inline-flex items-center gap-1.5"><Clock className="h-4 w-4 text-subtle" />{currentJob.min_experience_years}+ yrs experience</span>
                </p>
              </>
            ) : (
              <div className="skeleton mt-2 h-8 w-80" />
            )}
          </div>
          <div className="flex flex-wrap items-center gap-2">
          {live && currentJob && (
            <>
              <SecondaryButton onClick={() => setModal("questions")}><ListChecks className="h-4 w-4" />Questions</SecondaryButton>
              <SecondaryButton onClick={() => setModal("upload")}><UploadCloud className="h-4 w-4" />Add candidates</SecondaryButton>
            </>
          )}
          {backendUp && <PrimaryButton onClick={() => setModal("job")}><Plus className="h-4 w-4" />New job</PrimaryButton>}
          {jobs.length > 1 && (
            <label className="flex items-center gap-2 text-sm text-muted">
              <span className="sr-only sm:not-sr-only">Role</span>
              <select
                value={selectedJobId}
                onChange={(e) => switchJob(e.target.value)}
                className="rounded-lg border border-line-strong bg-surface px-3 py-2 text-sm font-medium text-fg shadow-card"
              >
                {jobs.map((j) => (
                  <option key={j.id} value={j.id}>{j.title}</option>
                ))}
              </select>
            </label>
          )}
          </div>
        </div>

        {/* Table card */}
        <div className="overflow-hidden rounded-xl border border-line bg-surface shadow-card">
          {/* Stage tabs */}
          <div className="flex gap-1 overflow-x-auto border-b border-line px-3 pt-2" role="tablist" aria-label="Pipeline stage">
            {STAGES.map((s) => {
              const active = stage === s.key;
              return (
                <button
                  key={s.key}
                  role="tab"
                  aria-selected={active}
                  onClick={() => { setStage(s.key); setSelectedIds([]); }}
                  className={`relative flex shrink-0 items-center gap-2 px-3 py-2.5 text-sm font-medium transition-colors ${
                    active ? "text-brand" : "text-muted hover:text-fg"
                  }`}
                >
                  {s.label}
                  <span className={`rounded-full px-1.5 py-0.5 text-xs tabular-nums ${active ? "bg-brand-soft text-brand" : "bg-surface-2 text-subtle"}`}>
                    {counts[s.key]}
                  </span>
                  {active && <span className="absolute inset-x-2 -bottom-px h-0.5 rounded-full bg-brand" />}
                </button>
              );
            })}
          </div>

          {/* Toolbar */}
          <div className="flex flex-col gap-3 border-b border-line px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex flex-1 items-center gap-2">
              <div className="relative w-full max-w-xs">
                <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-subtle" />
                <input
                  type="search"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="Search name or skill"
                  aria-label="Search candidates"
                  className="w-full rounded-lg border border-line-strong bg-surface py-2 pl-9 pr-3 text-sm text-fg placeholder:text-subtle"
                />
              </div>
              <select
                value={sourceFilter}
                onChange={(e) => setSourceFilter(e.target.value)}
                aria-label="Filter by source"
                className="rounded-lg border border-line-strong bg-surface px-3 py-2 text-sm text-fg"
              >
                <option value="ALL">All sources</option>
                <option value="LinkedIn">LinkedIn</option>
                <option value="Naukri">Naukri</option>
                <option value="Direct">Direct</option>
                <option value="Referral">Referral</option>
              </select>
            </div>

            <div className="flex items-center gap-3">
              {selectedIds.length > 0 && <span className="text-sm text-muted">{selectedIds.length} selected</span>}
              <button
                onClick={startInterviews}
                disabled={selectedIds.length === 0 || dispatching}
                title={selectedIds.length === 0 ? "Select shortlisted candidates to start AI interviews" : undefined}
                className="inline-flex items-center gap-2 rounded-lg bg-brand px-4 py-2 text-sm font-medium text-brand-fg shadow-card transition-colors hover:bg-brand-hover disabled:cursor-not-allowed disabled:bg-surface-2 disabled:text-subtle disabled:shadow-none disabled:ring-1 disabled:ring-inset disabled:ring-line"
              >
                <PhoneCall className="h-4 w-4" />
                {dispatching ? "Starting…" : "Start AI interviews"}
              </button>
            </div>
          </div>

          {/* Table */}
          <div className="overflow-x-auto">
            <table className="w-full min-w-[860px] text-left text-sm">
              <thead>
                <tr className="border-b border-line bg-surface-2 text-xs font-medium text-subtle">
                  <th className="w-12 py-3 pl-4">
                    <Checkbox
                      checked={allSelected}
                      indeterminate={someSelected}
                      disabled={selectableRows.length === 0}
                      onChange={toggleAll}
                      label="Select all shortlisted candidates"
                    />
                  </th>
                  <th className="px-3 py-3 font-medium">Candidate</th>
                  <th className="px-3 py-3 font-medium">Experience</th>
                  <th className="px-3 py-3 font-medium">Match</th>
                  <th className="px-3 py-3 font-medium">Skills</th>
                  <th className="px-3 py-3 font-medium">Status</th>
                  <th className="px-4 py-3 text-right font-medium">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {loading &&
                  Array.from({ length: 5 }).map((_, i) => (
                    <tr key={i}>
                      <td colSpan={7} className="px-4 py-4"><div className="skeleton h-10 w-full" /></td>
                    </tr>
                  ))}

                {!loading && rows.length === 0 && (
                  <tr>
                    <td colSpan={7} className="px-4 py-16 text-center">
                      <Users className="mx-auto h-8 w-8 text-line-strong" />
                      <p className="mt-3 text-sm font-medium text-fg">No candidates here</p>
                      <p className="mt-1 text-sm text-subtle">
                        {jobApps.length === 0 ? "Applications will appear as candidates apply." : "Try a different stage, source or search."}
                      </p>
                    </td>
                  </tr>
                )}

                {!loading &&
                  rows.map((app) => {
                    const selectable = app.status === "SHORTLISTED";
                    const selected = selectedIds.includes(app.id);
                    const actions = actionsFor(app.status);
                    return (
                      <tr key={app.id} className={`transition-colors hover:bg-surface-2 ${selected ? "bg-brand-soft/60" : ""}`}>
                        <td className="py-3.5 pl-4 align-middle">
                          <Checkbox
                            checked={selected}
                            disabled={!selectable}
                            onChange={() => toggleOne(app.id)}
                            label={selectable ? `Select ${app.candidate.full_name}` : `${app.candidate.full_name} can't be interviewed yet`}
                          />
                        </td>
                        <td className="px-3 py-3.5 align-middle">
                          <button onClick={() => setViewingId(app.id)} className="flex items-center gap-3 text-left">
                            <Avatar name={app.candidate.full_name} />
                            <span className="min-w-0">
                              <span className="block truncate font-medium text-fg hover:text-brand">{app.candidate.full_name}</span>
                              <span className="mt-0.5 flex items-center gap-2 text-xs text-subtle">
                                <span className="truncate">{app.candidate.email}</span>
                                <SourceTag source={app.source} />
                              </span>
                            </span>
                          </button>
                        </td>
                        <td className="whitespace-nowrap px-3 py-3.5 align-middle tabular-nums text-muted">
                          {app.candidate.total_experience_years} yrs
                        </td>
                        <td className="px-3 py-3.5 align-middle">
                          <MatchScore app={app} onEvidence={() => setViewingId(app.id)} />
                        </td>
                        <td className="max-w-[260px] px-3 py-3.5 align-middle">
                          <SkillChips skills={app.candidate.extracted_skills} />
                        </td>
                        <td className="px-3 py-3.5 align-middle">
                          <StatusBadge status={app.status} />
                        </td>
                        <td className="px-4 py-3.5 text-right align-middle">
                          <div className="flex justify-end gap-1.5">
                            {actions.map((a) => (
                              <button
                                key={a.to}
                                onClick={() => changeStatus(app.id, a.to)}
                                className={
                                  a.variant === "primary"
                                    ? "rounded-lg border border-brand/30 bg-brand-soft px-3 py-1.5 text-xs font-medium text-brand transition-colors hover:bg-brand hover:text-brand-fg"
                                    : "rounded-lg border border-line-strong bg-surface px-3 py-1.5 text-xs font-medium text-muted transition-colors hover:border-bad-line hover:bg-bad-soft hover:text-bad"
                                }
                              >
                                {a.label}
                              </button>
                            ))}
                          </div>
                        </td>
                      </tr>
                    );
                  })}
              </tbody>
            </table>
          </div>

          {!loading && rows.length > 0 && (
            <div className="flex items-center justify-between border-t border-line bg-surface-2 px-4 py-2.5 text-xs text-subtle">
              <span>Showing {rows.length} of {jobApps.length} candidates</span>
              <span className="inline-flex items-center gap-1.5">
                <CheckCircle2 className="h-3.5 w-3.5" /> Only shortlisted candidates can be sent to AI interview
              </span>
            </div>
          )}
        </div>
      </main>

      {viewing && (
        <CandidateDrawer app={viewing} live={live} onClose={() => setViewingId(null)} onAction={changeStatus} onChanged={reloadApplications} />
      )}

      {modal === "job" && (
        <NewJobModal
          onClose={() => setModal(null)}
          onCreated={async (job) => {
            setModal(null);
            setLoading(true);
            await loadAll(job.id);
            notify("success", "Job created. Add candidates to start screening.");
          }}
        />
      )}
      {modal === "upload" && currentJob && (
        <UploadModal
          jobId={currentJob.id}
          jobTitle={currentJob.title}
          onClose={() => setModal(null)}
          onDone={reloadApplications}
        />
      )}
      {modal === "questions" && currentJob && (
        <KnowledgeModal jobId={currentJob.id} jobTitle={currentJob.title} onClose={() => setModal(null)} />
      )}

      {toast && (
        <div
          role="status"
          aria-live="polite"
          className={`anim-rise fixed bottom-6 left-1/2 z-[60] flex -translate-x-1/2 items-center gap-3 rounded-xl border px-4 py-3 text-sm font-medium shadow-pop ${
            toast.kind === "success"
              ? "border-ok-line bg-surface text-fg"
              : "border-bad-line bg-bad-soft text-bad"
          }`}
        >
          {toast.kind === "success" ? <CheckCircle2 className="h-4.5 w-4.5 text-ok" /> : <X className="h-4.5 w-4.5" />}
          {toast.text}
        </div>
      )}
    </div>
  );
}
