"use client";

import { useCallback, useEffect, useState } from "react";
import { Loader2, Trash2 } from "lucide-react";
import { addKnowledge, deleteKnowledge, listKnowledge, type KnowledgeItem } from "@/lib/actions";
import Modal, { Field, PrimaryButton, inputClass } from "@/components/Modal";

const KIND_LABEL: Record<string, string> = { question: "Question", rubric: "Scoring guide", jd: "Job description", note: "Note" };

export default function KnowledgeModal({ jobId, jobTitle, onClose }: { jobId: string; jobTitle: string; onClose: () => void }) {
  const [items, setItems] = useState<KnowledgeItem[] | null>(null);
  const [kind, setKind] = useState<KnowledgeItem["kind"]>("question");
  const [topic, setTopic] = useState("");
  const [content, setContent] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setItems(await listKnowledge(jobId));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't load questions");
      setItems([]);
    }
  }, [jobId]);

  useEffect(() => {
    const id = setTimeout(load, 0);
    return () => clearTimeout(id);
  }, [load]);

  const add = async () => {
    setBusy(true);
    setError(null);
    try {
      await addKnowledge(jobId, { kind, topic: topic.trim() || null, content: content.trim() });
      setContent("");
      setTopic("");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't add");
    }
    setBusy(false);
  };

  const remove = async (id: string) => {
    setItems((p) => p?.filter((i) => i.id !== id) ?? p);
    try { await deleteKnowledge(jobId, id); } catch (e) { setError(e instanceof Error ? e.message : "Couldn't delete"); load(); }
  };

  const custom = items?.filter((i) => i.kind !== "jd") ?? [];
  const jdCount = (items?.length ?? 0) - custom.length;

  return (
    <Modal
      wide
      title="Interview questions"
      subtitle={`The AI interviewer draws on these for “${jobTitle}” and prefers them to inventing its own.`}
      onClose={onClose}
    >
      <div className="space-y-3 rounded-xl border border-line bg-surface p-4">
        <div className="grid gap-3 sm:grid-cols-[160px_1fr]">
          <Field label="Type">
            <select className={inputClass} value={kind} onChange={(e) => setKind(e.target.value as KnowledgeItem["kind"])}>
              <option value="question">Question to ask</option>
              <option value="rubric">What a good answer covers</option>
              <option value="note">Note</option>
            </select>
          </Field>
          <Field label="Skill (optional)">
            <input className={inputClass} value={topic} onChange={(e) => setTopic(e.target.value)} placeholder="PostgreSQL" />
          </Field>
        </div>
        <Field label={kind === "rubric" ? "A strong answer…" : "Question"}>
          <textarea
            className={`${inputClass} min-h-20`}
            value={content}
            onChange={(e) => setContent(e.target.value)}
            placeholder={kind === "rubric" ? "mentions composite indexes, EXPLAIN ANALYZE and write amplification" : "How do you decide which columns to index?"}
          />
        </Field>
        <div className="flex justify-end">
          <PrimaryButton onClick={add} disabled={busy || content.trim().length < 5}>
            {busy ? "Adding…" : "Add"}
          </PrimaryButton>
        </div>
      </div>

      {error && <p role="alert" className="rounded-lg border border-bad-line bg-bad-soft px-3 py-2 text-sm text-bad">{error}</p>}

      {items === null ? (
        <div className="flex justify-center py-6"><Loader2 className="h-5 w-5 animate-spin text-subtle" /></div>
      ) : custom.length === 0 ? (
        <p className="py-4 text-center text-sm text-subtle">No custom questions yet. The AI will ask about the required skills on its own.</p>
      ) : (
        <ul className="divide-y divide-line rounded-xl border border-line bg-surface">
          {custom.map((i) => (
            <li key={i.id} className="flex items-start gap-3 px-4 py-3">
              <div className="min-w-0 flex-1">
                <div className="mb-1 flex items-center gap-2 text-xs text-subtle">
                  <span className="rounded-md bg-surface-2 px-1.5 py-0.5 ring-1 ring-inset ring-line">{KIND_LABEL[i.kind] ?? i.kind}</span>
                  {i.topic && <span>{i.topic}</span>}
                </div>
                <p className="text-sm text-fg">{i.content}</p>
              </div>
              <button onClick={() => remove(i.id)} aria-label="Delete" className="rounded-lg p-1.5 text-subtle hover:bg-bad-soft hover:text-bad">
                <Trash2 className="h-4 w-4" />
              </button>
            </li>
          ))}
        </ul>
      )}
      {jdCount > 0 && <p className="text-xs text-subtle">The job description is also indexed ({jdCount} passages) and used automatically.</p>}
    </Modal>
  );
}
