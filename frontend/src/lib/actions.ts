import { API_BASE, type Application, type Job } from "@/lib/api";

/** Normalised error from any backend call. */
export class ApiError extends Error {}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, init);
  } catch {
    throw new ApiError("Can't reach the backend. Is it running?");
  }
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    const detail = body?.error?.message ?? (typeof body?.detail === "string" ? body.detail : null);
    const first = Array.isArray(body?.detail) ? body.detail[0]?.msg : null;
    throw new ApiError(detail ?? first ?? `Request failed (${res.status})`);
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

const json = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

// ------------------------------------------------------------------ jobs

export interface NewJob {
  title: string;
  description: string;
  required_skills: string[];
  min_experience_years: number;
  location: string;
  employment_type: string;
  max_questions: number;
}

export function createJob(job: NewJob): Promise<Job> {
  const { max_questions, ...rest } = job;
  return request<Job>("/jobs", json("POST", { ...rest, interview_config: { max_questions } }));
}

// ------------------------------------------------------------------ candidates

export interface UploadOptions {
  jobId: string;
  source: string;
  phone?: string;
  name?: string;
  email?: string;
}

/** Upload one resume, then apply the candidate to the job (screening runs on the backend). */
export async function uploadAndApply(file: File, opts: UploadOptions): Promise<Application> {
  const form = new FormData();
  form.append("file", file);
  if (opts.name) form.append("full_name", opts.name);
  if (opts.email) form.append("email", opts.email);
  if (opts.phone) form.append("phone", opts.phone);
  const candidate = await request<{ id: string }>("/candidates/upload-resume", { method: "POST", body: form });
  return request<Application>(`/applications/jobs/${opts.jobId}`, json("POST", { candidate_id: candidate.id, source: opts.source }));
}

// ------------------------------------------------------------------ interviews

export interface TranscriptTurn {
  speaker: string;
  text: string;
  timestamp?: number;
}

export interface InterviewResult {
  overall_score: number;
  summary?: string | null;
  competency_scores: Record<string, number>;
  full_transcript: TranscriptTurn[];
  recording_url?: string | null;
}

export interface Interview {
  id: string;
  application_id: string;
  pin_code: string;
  status: string;
  call_direction: "OUTBOUND" | "INBOUND" | "PRACTICE";
  duration_seconds?: number | null;
  recording_url?: string | null;
  transcript: TranscriptTurn[];
  result?: InterviewResult | null;
}

export const listInterviews = (applicationId: string) =>
  request<Interview[]>(`/interviews?application_id=${applicationId}`);

export const createPractice = (applicationId: string) =>
  request<Interview>("/interviews/practice", json("POST", { application_id: applicationId }));

export interface TurnResponse {
  say: string;
  done: boolean;
  interview: Interview;
}

export const practiceStart = (id: string) => request<TurnResponse>(`/interviews/${id}/practice/start`, { method: "POST" });

export const practiceTurn = (id: string, answer: string) =>
  request<TurnResponse>(`/interviews/${id}/practice/turn`, json("POST", { answer }));

export const completeInterview = (id: string) => request<Interview>(`/interviews/${id}/complete`, { method: "POST" });

// ------------------------------------------------------------------ interview knowledge base (RAG)

export interface KnowledgeItem {
  id: string;
  kind: "question" | "rubric" | "jd" | "note";
  topic?: string | null;
  content: string;
}

export const listKnowledge = (jobId: string) => request<KnowledgeItem[]>(`/jobs/${jobId}/knowledge`);

export const addKnowledge = (jobId: string, item: Pick<KnowledgeItem, "kind" | "topic" | "content">) =>
  request<KnowledgeItem>(`/jobs/${jobId}/knowledge`, json("POST", item));

export const deleteKnowledge = (jobId: string, itemId: string) =>
  request<void>(`/jobs/${jobId}/knowledge/${itemId}`, { method: "DELETE" });
