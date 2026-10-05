"use client";

import { useRef, useState } from "react";
import { CheckCircle2, FileText, Loader2, UploadCloud, XCircle } from "lucide-react";
import { uploadAndApply } from "@/lib/actions";
import { STATUS_META } from "@/lib/pipeline";
import type { Application } from "@/lib/api";
import Modal, { Field, PrimaryButton, SecondaryButton, inputClass } from "@/components/Modal";

type Row = { file: File; state: "queued" | "working" | "done" | "error"; message?: string; app?: Application };

const ACCEPT = ".pdf,.docx,.txt";

export default function UploadModal({
  jobId,
  jobTitle,
  onClose,
  onDone,
}: {
  jobId: string;
  jobTitle: string;
  onClose: () => void;
  onDone: () => void;
}) {
  const [rows, setRows] = useState<Row[]>([]);
  const [source, setSource] = useState("Direct");
  const [phone, setPhone] = useState("");
  const [running, setRunning] = useState(false);
  const [over, setOver] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  const addFiles = (list: FileList | null) => {
    if (!list) return;
    const ok = Array.from(list).filter((f) => /\.(pdf|docx|txt)$/i.test(f.name));
    setRows((prev) => [...prev, ...ok.map((file) => ({ file, state: "queued" as const }))]);
  };

  const patch = (i: number, p: Partial<Row>) => setRows((prev) => prev.map((r, idx) => (idx === i ? { ...r, ...p } : r)));

  const run = async () => {
    setRunning(true);
    for (let i = 0; i < rows.length; i++) {
      if (rows[i].state === "done") continue;
      patch(i, { state: "working", message: undefined });
      try {
        // a phone number only makes sense for a single resume; bulk uploads rely on the number in each resume
        const app = await uploadAndApply(rows[i].file, { jobId, source, phone: rows.length === 1 ? phone : undefined });
        patch(i, { state: "done", app });
      } catch (e) {
        patch(i, { state: "error", message: e instanceof Error ? e.message : "Failed" });
      }
    }
    setRunning(false);
    onDone();
  };

  const pending = rows.some((r) => r.state === "queued" || r.state === "error");

  return (
    <Modal
      wide
      title="Add candidates"
      subtitle={`Resumes are parsed and screened for “${jobTitle}” automatically.`}
      onClose={onClose}
      footer={
        <>
          <SecondaryButton onClick={onClose}>{rows.some((r) => r.state === "done") && !pending ? "Close" : "Cancel"}</SecondaryButton>
          <PrimaryButton onClick={run} disabled={!pending || running}>
            {running ? "Screening…" : `Upload & screen${rows.length ? ` (${rows.filter((r) => r.state !== "done").length})` : ""}`}
          </PrimaryButton>
        </>
      }
    >
      <div
        onDragOver={(e) => { e.preventDefault(); setOver(true); }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => { e.preventDefault(); setOver(false); addFiles(e.dataTransfer.files); }}
        onClick={() => input.current?.click()}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && input.current?.click()}
        className={`flex cursor-pointer flex-col items-center gap-2 rounded-xl border-2 border-dashed px-6 py-8 text-center transition-colors ${
          over ? "border-brand bg-brand-soft" : "border-line-strong bg-surface hover:border-brand"
        }`}
      >
        <UploadCloud className="h-8 w-8 text-subtle" />
        <p className="text-sm font-medium text-fg">Drop resumes here or click to browse</p>
        <p className="text-xs text-subtle">PDF, DOCX or TXT. Several at once is fine.</p>
        <input ref={input} type="file" accept={ACCEPT} multiple hidden onChange={(e) => { addFiles(e.target.files); e.target.value = ""; }} />
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Source">
          <select className={inputClass} value={source} onChange={(e) => setSource(e.target.value)}>
            <option>Direct</option>
            <option>LinkedIn</option>
            <option>Naukri</option>
            <option>Referral</option>
          </select>
        </Field>
        <Field label="Phone number" hint={rows.length > 1 ? "Only for single uploads; otherwise read from each resume." : "Used for the AI interview call (e.g. +919876543210)."}>
          <input className={inputClass} value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="+91 98765 43210" disabled={rows.length > 1} />
        </Field>
      </div>

      {rows.length > 0 && (
        <ul className="divide-y divide-line rounded-xl border border-line bg-surface">
          {rows.map((r, i) => (
            <li key={i} className="flex items-center gap-3 px-4 py-3 text-sm">
              <FileText className="h-4 w-4 shrink-0 text-subtle" />
              <span className="min-w-0 flex-1 truncate text-fg">{r.file.name}</span>
              {r.state === "working" && <Loader2 className="h-4 w-4 animate-spin text-brand" />}
              {r.state === "done" && r.app && (
                <span className="inline-flex items-center gap-1.5 text-xs text-ok">
                  <CheckCircle2 className="h-4 w-4" />
                  {STATUS_META[r.app.status]?.label ?? r.app.status}
                  {r.app.screening ? ` · ${r.app.screening.match_score}%` : ""}
                </span>
              )}
              {r.state === "error" && (
                <span className="inline-flex max-w-[50%] items-center gap-1.5 truncate text-xs text-bad" title={r.message}>
                  <XCircle className="h-4 w-4 shrink-0" />{r.message}
                </span>
              )}
              {r.state === "queued" && !running && (
                <button className="text-xs text-subtle hover:text-bad" onClick={() => setRows((p) => p.filter((_, idx) => idx !== i))}>Remove</button>
              )}
            </li>
          ))}
        </ul>
      )}
    </Modal>
  );
}
