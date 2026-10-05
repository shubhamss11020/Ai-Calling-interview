export const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api/v1";

export interface Candidate {
  id: string;
  full_name: string;
  email: string;
  phone?: string;
  location?: string;
  extracted_skills: string[];
  total_experience_years: number;
  headline?: string;
}

export interface Screening {
  id: string;
  deterministic_passed: boolean;
  deterministic_notes?: string;
  match_score: number;
  decision: "SUITABLE" | "MARGINAL" | "NOT_SUITABLE";
  matched_skills: string[];
  missing_skills: string[];
  experience_matched: boolean;
  reason?: string;
}

export type ApplicationStatus =
  | "APPLIED"
  | "PROCESSING"
  | "MANUAL_REVIEW"
  | "SUITABLE"
  | "SHORTLISTED"
  | "INTERVIEW_QUEUED"
  | "IN_PROGRESS"
  | "CALL_INTERRUPTED"
  | "COMPLETED"
  | "INTERVIEW_COMPLETED" // legacy alias of COMPLETED
  | "EVALUATED"
  | "REJECTED"
  | "HIRED";

export interface Application {
  id: string;
  job_id: string;
  candidate_id: string;
  source: "LinkedIn" | "Naukri" | "Direct" | "Referral";
  status: ApplicationStatus;
  applied_at: string;
  candidate: Candidate;
  screening?: Screening;
}

export interface Job {
  id: string;
  title: string;
  description: string;
  required_skills: string[];
  preferred_skills: string[];
  min_experience_years: number;
  location: string;
  employment_type: string;
  status: string;
  total_applications: number;
  processing_count: number;
  suitable_count: number;
  shortlisted_count: number;
  rejected_count: number;
  interview_pending_count: number;
  interview_completed_count: number;
}

// Initial mock data to ensure the UI is immediately interactive
export const MOCK_JOBS: Job[] = [
  {
    id: "job-101",
    title: "Senior Backend Developer (Node / Python)",
    description: "Build scalable microservices and async calling agents.",
    required_skills: ["Node.js", "Express", "PostgreSQL", "REST APIs", "Redis"],
    preferred_skills: ["FastAPI", "Docker", "AWS"],
    min_experience_years: 1.5,
    location: "Bangalore / Remote",
    employment_type: "Full-time",
    status: "ACTIVE",
    total_applications: 1024,
    processing_count: 24,
    suitable_count: 312,
    shortlisted_count: 150,
    rejected_count: 562,
    interview_pending_count: 150,
    interview_completed_count: 42,
  },
  {
    id: "job-102",
    title: "AI Voice Systems Engineer",
    description: "Develop sub-300ms audio streaming and telephony pipelines.",
    required_skills: ["Python", "WebSockets", "Deepgram", "FastAPI"],
    preferred_skills: ["Vobiz", "ElevenLabs", "Twilio"],
    min_experience_years: 2.0,
    location: "Remote",
    employment_type: "Full-time",
    status: "ACTIVE",
    total_applications: 418,
    processing_count: 12,
    suitable_count: 98,
    shortlisted_count: 45,
    rejected_count: 275,
    interview_pending_count: 45,
    interview_completed_count: 18,
  }
];

export const MOCK_APPLICATIONS: Application[] = [
  {
    id: "app-1",
    job_id: "job-101",
    candidate_id: "cand-1",
    source: "LinkedIn",
    status: "SUITABLE",
    applied_at: "2026-09-29T08:30:00Z",
    candidate: {
      id: "cand-1",
      full_name: "Rahul Sharma",
      email: "rahul.sharma@example.com",
      phone: "+91 98765 43210",
      location: "Bengaluru, India",
      extracted_skills: ["Node.js", "Express", "PostgreSQL", "REST APIs", "Git", "Docker"],
      total_experience_years: 1.8,
      headline: "Backend Engineer @ TechCorp",
    },
    screening: {
      id: "scr-1",
      deterministic_passed: true,
      match_score: 84,
      decision: "SUITABLE",
      matched_skills: ["Node.js", "Express", "PostgreSQL", "REST APIs"],
      missing_skills: ["Redis"],
      experience_matched: true,
      reason: "Meets most mandatory backend requirements with 1.8 yrs Node.js & PostgreSQL experience. Strong alignment with production REST architecture.",
    }
  },
  {
    id: "app-2",
    job_id: "job-101",
    candidate_id: "cand-2",
    source: "Naukri",
    status: "SHORTLISTED",
    applied_at: "2026-09-29T07:15:00Z",
    candidate: {
      id: "cand-2",
      full_name: "Ananya Iyer",
      email: "ananya.iyer@example.com",
      phone: "+91 98111 22334",
      location: "Hyderabad, India",
      extracted_skills: ["Node.js", "PostgreSQL", "Redis", "REST APIs", "AWS", "Python"],
      total_experience_years: 3.2,
      headline: "Full-Stack & Cloud Specialist",
    },
    screening: {
      id: "scr-2",
      deterministic_passed: true,
      match_score: 95,
      decision: "SUITABLE",
      matched_skills: ["Node.js", "Express", "PostgreSQL", "REST APIs", "Redis"],
      missing_skills: [],
      experience_matched: true,
      reason: "Exceptional match! Demonstrates hands-on Redis cache clustering and PostgreSQL optimization.",
    }
  },
  {
    id: "app-3",
    job_id: "job-101",
    candidate_id: "cand-3",
    source: "LinkedIn",
    status: "INTERVIEW_QUEUED",
    applied_at: "2026-09-28T16:45:00Z",
    candidate: {
      id: "cand-3",
      full_name: "Vikram Malhotra",
      email: "vikram.m@example.com",
      phone: "+91 97654 32190",
      location: "Pune, India",
      extracted_skills: ["Node.js", "PostgreSQL", "REST APIs", "Microservices"],
      total_experience_years: 2.4,
      headline: "API & Distributed Systems Engineer",
    },
    screening: {
      id: "scr-3",
      deterministic_passed: true,
      match_score: 82,
      decision: "SUITABLE",
      matched_skills: ["Node.js", "PostgreSQL", "REST APIs"],
      missing_skills: ["Redis"],
      experience_matched: true,
      reason: "Solid experience in relational schema design and high throughput endpoints.",
    }
  },
  {
    id: "app-4",
    job_id: "job-101",
    candidate_id: "cand-4",
    source: "Direct",
    status: "REJECTED",
    applied_at: "2026-09-28T11:20:00Z",
    candidate: {
      id: "cand-4",
      full_name: "Siddharth Verma",
      email: "sid.v@example.com",
      phone: "+91 99887 76655",
      location: "Delhi NCR, India",
      extracted_skills: ["HTML", "CSS", "WordPress"],
      total_experience_years: 0.5,
      headline: "Junior Web Designer",
    },
    screening: {
      id: "scr-4",
      deterministic_passed: false,
      match_score: 18,
      decision: "NOT_SUITABLE",
      matched_skills: [],
      missing_skills: ["Node.js", "Express", "PostgreSQL", "REST APIs", "Redis"],
      experience_matched: false,
      reason: "Disqualified by Stage 1 deterministic criteria: 0.5 yrs vs 1.5 yrs minimum required, lacks core backend stack.",
    }
  }
];

export interface Loaded<T> {
  data: T;
  /** false when the backend was unreachable/empty and demo data is shown */
  live: boolean;
}

export async function checkBackend(): Promise<boolean> {
  try {
    const res = await fetch(`${API_BASE.replace(/\/api\/v1$/, "")}/health`, { cache: "no-store" });
    return res.ok;
  } catch {
    return false;
  }
}

export async function fetchJobs(): Promise<Loaded<Job[]>> {
  try {
    const res = await fetch(`${API_BASE}/jobs`);
    if (res.ok) {
      const data = await res.json();
      if (Array.isArray(data) && data.length > 0) return { data, live: true };
    }
  } catch {
    // backend offline: fall through to demo data
  }
  return { data: MOCK_JOBS, live: false };
}

export async function fetchApplications(jobId?: string): Promise<Loaded<Application[]>> {
  try {
    const url = jobId ? `${API_BASE}/applications?job_id=${jobId}` : `${API_BASE}/applications`;
    const res = await fetch(url);
    if (res.ok) {
      const data = await res.json();
      if (Array.isArray(data)) return { data, live: true };
    }
  } catch {
    // backend offline: fall through to demo data
  }
  return { data: MOCK_APPLICATIONS, live: false };
}

export async function updateApplicationStatus(
  applicationId: string,
  status: ApplicationStatus
): Promise<{ ok: boolean; message?: string }> {
  try {
    const res = await fetch(`${API_BASE}/applications/${applicationId}/status`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status }),
    });
    if (res.ok) return { ok: true };
    const body = await res.json().catch(() => null);
    return { ok: false, message: body?.error?.message ?? `Request failed (${res.status})` };
  } catch {
    return { ok: false, message: "Backend unreachable" };
  }
}

export async function dispatchBatchInterviews(
  applicationIds: string[]
): Promise<{ ok: boolean; message?: string }> {
  try {
    const res = await fetch(`${API_BASE}/interviews/dispatch-batch`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ application_ids: applicationIds }),
    });
    if (res.ok) return { ok: true };
    const body = await res.json().catch(() => null);
    return { ok: false, message: body?.error?.message ?? `Request failed (${res.status})` };
  } catch {
    return { ok: false, message: "Backend unreachable" };
  }
}
