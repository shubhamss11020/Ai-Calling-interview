"use client";

import { useState } from "react";
import { createJob } from "@/lib/actions";
import type { Job } from "@/lib/api";
import Modal, { Field, PrimaryButton, SecondaryButton, inputClass } from "@/components/Modal";

export default function NewJobModal({ onClose, onCreated }: { onClose: () => void; onCreated: (job: Job) => void }) {
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [skills, setSkills] = useState("");
  const [minExp, setMinExp] = useState("2");
  const [location, setLocation] = useState("Remote");
  const [maxQuestions, setMaxQuestions] = useState("6");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const skillList = skills.split(",").map((s) => s.trim()).filter(Boolean);
  const valid = title.trim().length > 2 && description.trim().length > 10 && skillList.length > 0;

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const job = await createJob({
        title: title.trim(),
        description: description.trim(),
        required_skills: skillList,
        min_experience_years: Number(minExp) || 0,
        location: location.trim() || "Remote",
        employment_type: "Full-time",
        max_questions: Math.max(2, Math.min(15, Number(maxQuestions) || 6)),
      });
      onCreated(job);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't create the job");
      setBusy(false);
    }
  };

  return (
    <Modal
      title="New job"
      subtitle="Candidates are screened against the skills and experience you set here."
      onClose={onClose}
      footer={
        <>
          <SecondaryButton onClick={onClose}>Cancel</SecondaryButton>
          <PrimaryButton onClick={submit} disabled={!valid || busy}>{busy ? "Creating…" : "Create job"}</PrimaryButton>
        </>
      }
    >
      <Field label="Job title">
        <input className={inputClass} value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Backend Engineer" autoFocus />
      </Field>
      <Field label="Description" hint="The AI reads this when screening resumes and planning interview questions.">
        <textarea className={`${inputClass} min-h-28`} value={description} onChange={(e) => setDescription(e.target.value)} placeholder="What the person will build and the stack they will use…" />
      </Field>
      <Field label="Required skills" hint="Comma separated. Screening and the interview focus on these.">
        <input className={inputClass} value={skills} onChange={(e) => setSkills(e.target.value)} placeholder="Python, FastAPI, PostgreSQL, Redis" />
      </Field>
      <div className="grid grid-cols-3 gap-3">
        <Field label="Min. years">
          <input className={inputClass} type="number" min="0" step="0.5" value={minExp} onChange={(e) => setMinExp(e.target.value)} />
        </Field>
        <Field label="Location">
          <input className={inputClass} value={location} onChange={(e) => setLocation(e.target.value)} />
        </Field>
        <Field label="Interview questions">
          <input className={inputClass} type="number" min="2" max="15" value={maxQuestions} onChange={(e) => setMaxQuestions(e.target.value)} />
        </Field>
      </div>
      {error && <p role="alert" className="rounded-lg border border-bad-line bg-bad-soft px-3 py-2 text-sm text-bad">{error}</p>}
    </Modal>
  );
}
