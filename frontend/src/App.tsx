import { FormEvent, useCallback, useEffect, useState } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, download, refreshCsrf } from "./api";
import type { Job, Report, User } from "./types";

type Page = "dashboard" | "new" | "history" | "settings";
type Summary = { total: number; completed: number; in_progress: number; incomplete: number };

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : "Something went wrong. Please try again.";
}

function AuthScreen({ onLogin, initialError }: { onLogin: (user: User) => void; initialError: string }) {
  const [register, setRegister] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    const form = new FormData(event.currentTarget);
    const payload = {
      name: String(form.get("name") ?? ""),
      email: String(form.get("email") ?? ""),
      password: String(form.get("password") ?? ""),
      confirm_password: String(form.get("confirm_password") ?? ""),
    };
    try {
      await refreshCsrf();
      const response = await api<{ user: User }>(register ? "/auth/register" : "/auth/login", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      await refreshCsrf();
      onLogin(response.user);
    } catch (err) {
      setError(messageOf(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="auth-shell">
      <section className="auth-card">
        <div className="brand-mark">MR</div>
        <p className="eyebrow">SMARTER RESEARCH. BETTER DECISIONS.</p>
        <h1>{register ? "Create your account" : "Welcome back"}</h1>
        <p className="muted">Research markets with current evidence and cited sources.</p>
        {initialError && <p className="error" role="alert">{initialError}</p>}
        <form onSubmit={submit} className="stack">
          {register && <label>Full name<input name="name" autoComplete="name" required maxLength={120} /></label>}
          <label>Email address<input name="email" type="email" autoComplete="email" required /></label>
          <label>Password<input name="password" type="password" autoComplete={register ? "new-password" : "current-password"} required minLength={register ? 12 : undefined} /></label>
          {register && <label>Confirm password<input name="confirm_password" type="password" autoComplete="new-password" required minLength={12} /></label>}
          {error && <p className="error" role="alert">{error}</p>}
          <button className="primary" disabled={busy}>{busy ? "Please wait…" : register ? "Create account" : "Log in"}</button>
        </form>
        <button className="text-button" onClick={() => { setRegister(!register); setError(""); }}>
          {register ? "Already registered? Log in" : "New here? Create an account"}
        </button>
      </section>
    </main>
  );
}

function App() {
  const [user, setUser] = useState<User | null>(null);
  const [page, setPage] = useState<Page>("dashboard");
  const [summary, setSummary] = useState<Summary>({ total: 0, completed: 0, in_progress: 0, incomplete: 0 });
  const [jobs, setJobs] = useState<Job[]>([]);
  const [selected, setSelected] = useState<Job | null>(null);
  const [report, setReport] = useState<Report | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [providerStatus, setProviderStatus] = useState<{ tavily: string; gemini: string } | null>(null);

  const loadDashboard = useCallback(async () => {
    if (!user) return;
    setError("");
    try {
      const [summaryData, recentData] = await Promise.all([
        api<{ summary: Summary }>("/dashboard/summary"),
        api<{ items: Job[] }>("/dashboard/recent"),
      ]);
      setSummary(summaryData.summary);
      setJobs(recentData.items);
    } catch (err) {
      setError(messageOf(err));
    }
  }, [user]);

  useEffect(() => {
    refreshCsrf().then(() => api<{ user: User }>("/auth/me"))
      .then(({ user: account }) => { setError(""); setUser(account); })
      .catch((err: unknown) => {
        if (err instanceof Error && !("status" in err && (err as Error & { status: number }).status === 401)) setError(messageOf(err));
      });
  }, []);

  useEffect(() => { void loadDashboard(); }, [loadDashboard]);

  useEffect(() => {
    if (!selected || !["pending", "running"].includes(selected.status)) return;
    const timer = window.setInterval(async () => {
      try {
        const data = await api<{ job: Job }>(`/research/${selected.id}/status`);
        setSelected(data.job);
        setJobs((current) => current.map((item) => item.id === data.job.id ? data.job : item));
        if (!["pending", "running"].includes(data.job.status)) {
          if (page === "history") {
            const historyData = await api<{ items: Job[] }>("/research?per_page=50");
            setJobs(historyData.items);
          } else {
            await loadDashboard();
          }
          await openReport(data.job);
        }
      } catch (err) {
        setError(messageOf(err));
      }
    }, 2500);
    return () => window.clearInterval(timer);
  }, [selected, loadDashboard, page]);

  async function openReport(job: Job) {
    setPage("history");
    setSelected(job);
    setReport(null);
    setError("");
    try {
      const data = await api<{ report: { report_content: Report } }>(`/research/${job.id}/report`);
      setReport(data.report.report_content);
    } catch (err) {
      if (!(err instanceof Error && "status" in err && (err as Error & { status: number }).status === 409)) setError(messageOf(err));
    }
  }

  async function submitResearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLoading(true);
    setError("");
    const values = new FormData(event.currentTarget);
    const question = String(values.get("question") ?? "").trim();
    const scope: Record<string, string> = {};
    for (const field of ["industry", "geography", "target_audience", "time_period", "instructions"]) {
      const value = String(values.get(field) ?? "").trim();
      if (value) scope[field] = value;
    }
    try {
      const data = await api<{ job: Job }>("/research", { method: "POST", body: JSON.stringify({ question, scope }) });
      setSelected(data.job);
      setReport(null);
      setPage("history");
      await loadDashboard();
    } catch (err) {
      setError(messageOf(err));
    } finally {
      setLoading(false);
    }
  }

  async function logout() {
    try {
      await api("/auth/logout", { method: "POST" });
      setUser(null);
      setSelected(null);
      setReport(null);
    } catch (err) {
      setError(messageOf(err));
    }
  }

  async function loadHistory() {
    setLoading(true);
    try {
      const data = await api<{ items: Job[] }>("/research?per_page=50");
      setJobs(data.items);
      setPage("history");
    } catch (err) {
      setError(messageOf(err));
    } finally {
      setLoading(false);
    }
  }

  async function openSettings() {
    setPage("settings");
    setProviderStatus(null);
    try {
      const data = await api<{ providers: { tavily: string; gemini: string } }>("/health/providers");
      setProviderStatus(data.providers);
    } catch (err) {
      setError(messageOf(err));
    }
  }

  async function removeJob(job: Job) {
    if (!window.confirm("Delete this research and its saved report?")) return;
    try {
      await api(`/research/${job.id}`, { method: "DELETE" });
      if (selected?.id === job.id) { setSelected(null); setReport(null); }
      if (page === "history") await loadHistory();
      else await loadDashboard();
    } catch (err) {
      setError(messageOf(err));
    }
  }

  async function retryJob(job: Job) {
    try {
      const data = await api<{ job: Job }>(`/research/${job.id}/retry`, { method: "POST" });
      const retrying = { ...job, ...data.job, status: "pending", error_summary: null, current_stage: null };
      setSelected(retrying);
      setJobs((current) => current.map((item) => item.id === job.id ? retrying : item));
      setPage("history");
    } catch (err) {
      setError(messageOf(err));
    }
  }

  if (!user) return <AuthScreen onLogin={(account) => { setError(""); setUser(account); }} initialError={error} />;

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand"><span className="brand-mark small">MR</span><span>Market<span className="brand-light">Lens</span></span></div>
        <p className="nav-label">WORKSPACE</p>
        <button aria-label="Dashboard" className={page === "dashboard" ? "nav-item active" : "nav-item"} onClick={() => { setPage("dashboard"); setSelected(null); setReport(null); void loadDashboard(); }}>◫ <span>Dashboard</span></button>
        <button aria-label="New research" className={page === "new" ? "nav-item active" : "nav-item"} onClick={() => { setPage("new"); setSelected(null); setReport(null); }}>＋ <span>New research</span></button>
        <button aria-label="Research history" className={page === "history" ? "nav-item active" : "nav-item"} onClick={() => void loadHistory()}>◷ <span>Research history</span></button>
        <p className="nav-label second">ACCOUNT</p>
        <button aria-label="Settings" className={page === "settings" ? "nav-item active" : "nav-item"} onClick={() => void openSettings()}>⚙ <span>Settings</span></button>
        <div className="sidebar-bottom">
          <div className="user-chip"><span className="avatar">{user.name.charAt(0).toUpperCase()}</span><span className="user-name">{user.name}<small>{user.email}</small></span></div>
          <button className="nav-item" onClick={() => void logout()}>↪ <span>Log out</span></button>
        </div>
      </aside>

      <main className="main-panel">
        <header className="topbar">
          <div><span className="breadcrumb">Workspace</span><span className="crumb-divider">/</span><strong>{page === "new" ? "New research" : page === "history" ? "Research history" : page === "settings" ? "Settings" : "Overview"}</strong></div>
          <span className="secure-pill"><i /> Private workspace</span>
        </header>
        <div className="content">
          {error && <div className="banner-error" role="alert"><span>{error}</span><button onClick={() => setError("")}>Dismiss</button></div>}

          {page === "dashboard" && <>
            <section className="welcome-row"><div><p className="eyebrow">YOUR RESEARCH WORKSPACE</p><h1>Good research starts with a question.</h1><p className="muted">Turn live market evidence into clear, cited insights.</p></div><button className="primary" onClick={() => { setPage("new"); setSelected(null); setReport(null); }}>＋ Start research</button></section>
            <section className="metric-grid">
              {[
                ["Total research", summary.total, "Across your workspace", "metric-blue"],
                ["Completed reports", summary.completed, "Evidence-backed", "metric-green"],
                ["In progress", summary.in_progress, "Currently running", "metric-violet"],
                ["Needs attention", summary.incomplete, "Failed or partial", "metric-amber"],
              ].map(([label, value, note, color]) => <article className="metric-card" key={String(label)}><div className="metric-top"><span>{label}</span><span className={`metric-icon ${color}`}>◈</span></div><strong>{value}</strong><small>{note}</small></article>)}
            </section>
            <section className="panel">
              <div className="panel-heading"><div><h2>Recent research</h2><p className="muted">Your latest research jobs and reports</p></div><button className="text-button" onClick={loadHistory}>View history →</button></div>
              <JobTable jobs={jobs.slice(0, 5)} onOpen={(job) => void openReport(job)} onDelete={removeJob} />
            </section>
          </>}

          {page === "new" && <section className="panel form-panel">
            <div className="panel-heading"><div><p className="eyebrow">LIVE, EVIDENCE-BASED RESEARCH</p><h1>What would you like to understand?</h1><p className="muted">The agent searches the web, analyzes retrieved evidence, then checks and cites findings.</p></div></div>
            <form className="research-form" onSubmit={submitResearch}>
              <label>Research question <span className="required">*</span><textarea name="question" required maxLength={2000} rows={4} placeholder="e.g. What are the emerging trends and leading competitors in the electric bicycle market?" /></label>
              <div className="input-grid">
                <label>Industry<input name="industry" maxLength={500} placeholder="e.g. Electric mobility" /></label>
                <label>Geographic market<input name="geography" maxLength={500} placeholder="e.g. Northern Europe" /></label>
                <label>Target audience<input name="target_audience" maxLength={500} placeholder="e.g. Urban commuters" /></label>
                <label>Time period<input name="time_period" maxLength={500} placeholder="e.g. 2024–2026" /></label>
              </div>
              <label>Additional instructions<textarea name="instructions" rows={3} maxLength={500} placeholder="Any specific scope, comparison criteria, or questions to prioritize" /></label>
              <div className="form-foot"><span className="muted">A live search and AI provider must be configured by the server.</span><button className="primary" disabled={loading}>{loading ? "Submitting…" : "Start research →"}</button></div>
            </form>
          </section>}

          {page === "history" && <section className="panel">
            <div className="panel-heading"><div><p className="eyebrow">YOUR WORKSPACE</p><h1>Research history</h1><p className="muted">Track job stages, revisit reports, or retry incomplete research.</p></div><button className="primary" onClick={() => { setPage("new"); setSelected(null); setReport(null); }}>＋ New research</button></div>
            <JobTable jobs={jobs} onOpen={(job) => void openReport(job)} onDelete={removeJob} onRetry={retryJob} />
            {selected && <JobDetails
              job={selected}
              report={report}
              onExport={(type) => {
                void download(`/research/${selected.id}/export/${type}`, `research-${selected.id}.${type}`)
                  .catch((err: unknown) => setError(messageOf(err)));
              }}
            />}
          </section>}

          {page === "settings" && <section className="panel settings-panel">
            <p className="eyebrow">ACCOUNT & PROVIDERS</p><h1>Settings</h1><p className="muted">Provider status is reported by the backend. Secret keys are never returned to the browser.</p>
            <div className="settings-block"><h2>Profile</h2><ProfileEditor user={user} onUpdated={setUser} onError={setError} /></div>
            <div className="settings-block"><h2>Research providers</h2><ProviderRow name="Tavily live web search" status={providerStatus?.tavily ?? "Checking"} /><ProviderRow name="Google Gemini analysis" status={providerStatus?.gemini ?? "Checking"} /><button className="secondary" onClick={() => void openSettings()}>Refresh provider status</button></div>
            <div className="settings-block"><h2>Privacy</h2><p className="muted">Research records and reports are private to your account. Use HTTPS and a strong backend secret in production.</p></div>
          </section>}
        </div>
      </main>
    </div>
  );
}

function ProfileEditor({ user, onUpdated, onError }: {
  user: User;
  onUpdated: (user: User) => void;
  onError: (message: string) => void;
}) {
  const [name, setName] = useState(user.name);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => setName(user.name), [user.name]);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setSaved(false);
    try {
      const response = await api<{ user: User }>("/auth/me", {
        method: "PATCH",
        body: JSON.stringify({ name: name.trim() }),
      });
      onUpdated(response.user);
      setSaved(true);
    } catch (err) {
      onError(messageOf(err));
    } finally {
      setBusy(false);
    }
  }

  return <form className="profile-form" onSubmit={save}>
    <label htmlFor="profile-name">Full name</label>
    <div className="profile-edit-row">
      <input id="profile-name" value={name} onChange={(event) => { setName(event.target.value); setSaved(false); }} maxLength={120} required />
      <button className="secondary" disabled={busy || name.trim() === user.name}>{busy ? "Saving…" : "Save profile"}</button>
    </div>
    <p className="muted">{user.email}</p>
    {saved && <p className="profile-saved" role="status">Profile saved.</p>}
  </form>;
}

function ProviderRow({ name, status }: { name: string; status: string }) {
  return <div className="provider-row"><span>{name}</span><span className="status-badge">{status.replaceAll("_", " ")}</span></div>;
}

function JobTable({ jobs, onOpen, onDelete, onRetry }: {
  jobs: Job[];
  onOpen: (job: Job) => void;
  onDelete: (job: Job) => void;
  onRetry?: (job: Job) => void;
}) {
  if (jobs.length === 0) return <div className="empty-state"><span className="empty-icon">⌕</span><strong>No research yet</strong><p>Your completed and in-progress research will appear here.</p></div>;
  return <div className="table-scroll"><table><thead><tr><th>RESEARCH QUESTION</th><th>CREATED</th><th>STATUS</th><th /></tr></thead><tbody>
    {jobs.map((job) => <tr key={job.id}><td><button className="query-link" onClick={() => onOpen(job)}>{job.original_query}</button><small className="job-stage">{job.current_stage ? `Current stage: ${job.current_stage.replaceAll("_", " ")}` : job.error_summary ?? ""}</small></td><td>{new Date(job.created_at).toLocaleDateString()}</td><td><span className={`status status-${job.status}`}>{job.status}</span></td><td className="row-actions"><button className="icon-button" aria-label={`Open research: ${job.original_query}`} onClick={() => onOpen(job)}>↗</button>{onRetry && ["failed", "partial"].includes(job.status) && <button className="icon-button" aria-label={`Retry research: ${job.original_query}`} onClick={() => onRetry(job)}>⟳</button>}<button className="icon-button danger" aria-label={`Delete research: ${job.original_query}`} onClick={() => onDelete(job)}>×</button></td></tr>)}
  </tbody></table></div>;
}

function JobDetails({ job, report, onExport }: { job: Job; report: Report | null; onExport: (type: "pdf" | "csv") => void }) {
  return <div className="job-details">
    <div className="panel-heading"><div><p className="eyebrow">RESEARCH JOB</p><h2>{job.original_query}</h2><p className="muted" aria-live="polite">Status: {job.status}{job.error_summary ? ` — ${job.error_summary}` : ""}</p></div>
      <div className="button-row"><button className="secondary" onClick={() => onExport("csv")}>↓ CSV</button><button className="secondary" onClick={() => onExport("pdf")} disabled={!report}>↓ PDF</button></div>
    </div>
    <h3>Research stages</h3>
    <div className="stage-list">{job.stages?.map((stage) => <div className="stage-row" key={stage.name}><span className={`stage-dot stage-${stage.status}`} /><strong>{stage.name.replaceAll("_", " ")}</strong><span className={`status status-${stage.status}`}>{stage.status}</span><small>{stage.duration != null ? `${stage.duration.toFixed(1)}s` : stage.error ?? ""}</small></div>)}</div>
    {report && <ReportView report={report} />}
  </div>;
}

function ReportView({ report }: { report: Report }) {
  const sourceById = new Map(report.sources.map((source) => [source.id, source]));
  const marketData = report.market_data ?? [];
  const chartData = marketData.map((item) => ({
    label: item.period ? `${item.metric} (${item.period})` : item.metric,
    value: Number.parseFloat(item.value.replaceAll(",", "")),
    unit: item.unit,
  })).filter((item) => Number.isFinite(item.value));
  const canChart = chartData.length > 1 && new Set(chartData.map((item) => item.unit)).size === 1;
  function citations(ids?: number[]) {
    return ids?.map((id) => {
      const source = sourceById.get(id);
      return source ? <a className="citation" key={id} href={source.url} target="_blank" rel="noopener noreferrer">[{id}]</a> : null;
    });
  }
  return <div className="report-view">
    <div className="report-heading"><span className={`status status-${report.completion_status}`}>{report.completion_status} report</span><small className="muted">{new Date(report.research_date).toLocaleString()}</small></div>
    <section><h3>Executive summary</h3><p>{report.executive_summary} {citations(report.executive_summary_source_ids)}</p></section>
    {(["findings", "opportunities", "risks", "recommendations"] as const).map((key) => <section key={key}><h3>{key[0].toUpperCase() + key.slice(1)}</h3>{report[key].length ? <ul>{report[key].map((item, index) => <li key={`${key}-${index}`}>{item.text} {citations(item.source_ids)}{item.verification_status && <small className="verification">{item.verification_status}</small>}</li>)}</ul> : <p className="muted">No supported items available.</p>}</section>)}
    <section><h3>Numeric market data</h3>
      {canChart && <div className="chart-panel" aria-label="Sourced numeric market data chart">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={chartData} margin={{ top: 12, right: 20, left: 4, bottom: 50 }}>
            <CartesianGrid strokeDasharray="3 3" vertical={false} />
            <XAxis dataKey="label" angle={-25} textAnchor="end" interval={0} tick={{ fontSize: 10 }} />
            <YAxis tick={{ fontSize: 10 }} />
            <Tooltip />
            <Bar dataKey="value" name={chartData[0].unit} fill="#4964d4" radius={[4, 4, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </div>}
      {marketData.length === 0 ? <p className="muted">No source-supported numeric series was available for charting.</p> : <>
        {!canChart && <p className="muted">The sourced values use different units or there are too few values for a chart; showing the evidence table instead.</p>}
        <div className="table-scroll"><table><thead><tr><th>METRIC</th><th>VALUE</th><th>PERIOD / GEOGRAPHY</th><th>SOURCE</th></tr></thead><tbody>
          {marketData.map((item, index) => <tr key={`${item.metric}-${index}`}><td>{item.metric}</td><td>{item.value} {item.unit}</td><td>{[item.period, item.geography].filter(Boolean).join(" · ") || "Not specified"}</td><td>{citations(item.source_ids)}</td></tr>)}
        </tbody></table></div>
      </>}
    </section>
    {!!report.limitations.length && <section><h3>Limitations</h3><ul>{report.limitations.map((item, index) => <li key={index}>{item}</li>)}</ul></section>}
    <section><h3>Sources ({report.sources.length})</h3>{report.sources.length ? <ul className="source-list">{report.sources.map((source) => <li key={source.id}><a href={source.url} target="_blank" rel="noopener noreferrer">{source.title}</a><small>Retrieved {new Date(source.retrieved_at).toLocaleString()}</small></li>)}</ul> : <p className="muted">No sources were retrieved.</p>}</section>
  </div>;
}

export default App;
