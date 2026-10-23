export type User = { id: number; name: string; email: string };
export type Stage = {
  name: string;
  status: "pending" | "running" | "completed" | "failed" | "skipped" | "partially completed";
  started_at?: string | null;
  completed_at?: string | null;
  duration?: number | null;
  error?: string | null;
};
export type Job = {
  id: string;
  original_query: string;
  status: string;
  created_at: string;
  completed_at?: string | null;
  current_stage?: string | null;
  error_summary?: string | null;
  stages: Stage[];
};
export type Report = {
  research_question: string;
  research_date: string;
  completion_status: string;
  executive_summary: string;
  executive_summary_source_ids?: number[];
  findings: Array<{ category?: string; text: string; source_ids?: number[]; verification_status?: string }>;
  opportunities: Array<{ text: string; source_ids?: number[]; verification_status?: string }>;
  risks: Array<{ text: string; source_ids?: number[]; verification_status?: string }>;
  recommendations: Array<{ text: string; source_ids?: number[]; verification_status?: string }>;
  limitations: string[];
  market_data?: Array<{
    metric: string;
    value: string;
    unit: string;
    period?: string | null;
    geography?: string | null;
    assumptions?: string[];
    evidence_quote: string;
    source_ids: number[];
  }>;
  sources: Array<{ id: number; title: string; url: string; retrieved_at: string }>;
};
