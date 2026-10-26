import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiProblem, fmtDate, fmtDateTime } from "../api";
import { useAuth } from "../auth";
import { AsOf, Empty, ErrorBox, Field, Modal, PageHead, Simulated, Skeleton, Status, useToast } from "../ui";

const REFRESH = 15000;
const Tile = ({ v, l, s }: { v: any; l: string; s?: string }) => <div className="kpi"><div className="v">{typeof v === "number" ? v.toLocaleString("en-IN") : v}</div><div className="l">{l}</div>{s ? <div className="s">{s}</div> : null}</div>;

function TrendChart({ items }: { items: any[] }) {
  if (!items.length) return <Empty>No claim activity in this period.</Empty>;
  const W = 640, H = 200, P = 34, max = Math.max(...items.map((i) => i.claims), 1), maxC = Math.max(...items.map((i) => i.cumulative), 1);
  const x = (i: number) => P + (i * (W - 2 * P)) / Math.max(items.length - 1, 1);
  const line = items.map((p, i) => `${i ? "L" : "M"}${x(i)},${H - P - ((H - 2 * P) * p.cumulative) / maxC}`).join(" ");
  return (
    <div className="chart"><svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Claims per day (bars) and cumulative claims (line)">
      {items.map((p, i) => { const h = ((H - 2 * P) * p.claims) / max; return <rect key={i} x={x(i) - 3} y={H - P - h} width={6} height={Math.max(h, 1)} fill="#9fd3bb"><title>{p.day}: {p.claims} claims</title></rect>; })}
      <path d={line} fill="none" stroke="#0f4d3a" strokeWidth={2} />
      <text x={P} y={14} fontSize="11" fill="#5b6e67">Cumulative claims (line, max {maxC.toLocaleString("en-IN")}) · claims per day (bars, max {max})</text>
      <text x={P} y={H - 8} fontSize="11" fill="#5b6e67">{items[0].day}</text><text x={W - P} y={H - 8} fontSize="11" fill="#5b6e67" textAnchor="end">{items[items.length - 1].day}</text>
    </svg></div>
  );
}

export function AicteOverview() {
  const q = useQuery({ queryKey: ["aicte-ov"], queryFn: () => api<any>("/dashboards/aicte/overview"), refetchInterval: REFRESH });
  const t = useQuery({ queryKey: ["aicte-trend"], queryFn: () => api<any>("/dashboards/aicte/trends?days=90"), refetchInterval: REFRESH });
  if (q.isLoading) return <Skeleton rows={8} />;
  if (q.error) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  const d = q.data, k = d.kpis;
  const pct = (a: number, b: number) => (b ? `${((100 * a) / b).toFixed(1)}%` : "-");
  return (
    <>
      <PageHead title="National dashboard" sub={`Aggregates only · ${d.academic_year} · scope: ${d.scope.type}${d.scope.value ? " " + d.scope.value : ""}`}><AsOf at={d.as_of} onRefresh={() => { q.refetch(); t.refetch(); }} /></PageHead>
      <div className="kpis">
        <Tile v={k.institutions} l="Institutions with seats" /><Tile v={k.seats} l="Approved seats" s="incl. approved extra" /><Tile v={k.occupied} l="Seats occupied" s={pct(k.occupied, k.seats) + " of approved"} />
        <Tile v={k.verified} l="Verified enrolments" s={pct(k.verified, k.total) + " of live"} /><Tile v={k.pending} l="Awaiting confirmation" /><Tile v={k.unconfirmed} l="Unconfirmed" s="flag only" />
        <Tile v={k.disputed} l="Disputed" /><Tile v={k.pending_fix} l="Identity fixes due" /><Tile v={k.pending_registry} l="Pending registry" /><Tile v={k.low_trust} l="Low-trust confirmations" s="assisted / institution mobile" />
        <Tile v={k.buckets_full} l="Buckets at 100%" s={`${k.buckets_near} at 90–99%`} /><Tile v={k.conflicts} l="Claim conflicts (12 mo)" s={`${k.seat_limit} seat-limit hits`} />
      </div>
      <div className="cols2">
        <div className="card"><h3>Claims trend (90 days)</h3>{t.data ? <TrendChart items={t.data.items} /> : <Skeleton />}</div>
        <div className="card"><h3>State utilisation <span className="hint">groups under 5 institutions are suppressed</span></h3>
          {d.states.map((s: any) => <div className="statebar" key={s.state}><span>{s.state}</span><div className={"bar " + (s.pct >= 100 ? "red" : s.pct >= 90 ? "amber" : s.pct < 50 ? "neutral" : "")}><i style={{ width: `${Math.min(s.pct, 100)}%` }} /></div><span>{s.pct}%</span></div>)}
          {!d.states.length ? <Empty>No data in scope.</Empty> : null}
        </div>
      </div>
      <p className="hint" style={{ marginTop: 14 }}>{d.definitions.occupied} {d.definitions.cumulative_admitted} {d.definitions.verified} The geographic map is not available in this prototype; a state table is shown instead.</p>
      <div className="alert info">Student-level records are never shown here. AICTE can see an individual only through a case-gated, time-limited grant (planned for the next phase).</div>
    </>
  );
}

export function Utilisation() {
  const [f, setF] = useState<any>({ state: "", region: "", level: "", institution_type: "", band: "", q: "", sort: "pct" });
  const [page, setPage] = useState(0);
  const lk = useQuery({ queryKey: ["lookups"], queryFn: () => api<any>("/ref/lookups"), staleTime: 3600000 });
  const params = new URLSearchParams({ limit: "25", offset: String(page * 25), sort: f.sort });
  Object.entries(f).forEach(([k, v]) => { if (v && k !== "sort") params.set(k, v as string); });
  const q = useQuery({ queryKey: ["util", params.toString()], queryFn: () => api<any>("/dashboards/aicte/utilisation?" + params), refetchInterval: REFRESH, placeholderData: (x: any) => x });
  const set = (k: string, v: string) => { setF({ ...f, [k]: v }); setPage(0); };
  return (
    <>
      <PageHead title="Seat utilisation" sub="Programme-level seat buckets across institutions (aggregates)."><AsOf at={q.data?.as_of} onRefresh={() => q.refetch()} /></PageHead>
      <div className="toolbar">
        <div className="grow"><label>Search institution or programme</label><input type="text" value={f.q} onChange={(e) => set("q", e.target.value)} /></div>
        <div><label>State</label><select value={f.state} onChange={(e) => set("state", e.target.value)}><option value="">All</option>{lk.data?.states.map((s: any) => <option key={s.state}>{s.state}</option>)}</select></div>
        <div><label>Level</label><select value={f.level} onChange={(e) => set("level", e.target.value)}><option value="">All</option>{["UG", "PG", "DIPLOMA", "PGDM"].map((s) => <option key={s}>{s}</option>)}</select></div>
        <div><label>Type</label><select value={f.institution_type} onChange={(e) => set("institution_type", e.target.value)}><option value="">All</option>{lk.data?.institution_types.map((s: string) => <option key={s}>{s}</option>)}</select></div>
        <div><label>Band</label><select value={f.band} onChange={(e) => set("band", e.target.value)}><option value="">All</option><option value="FULL">100% (full)</option><option value="NEAR">90–99%</option><option value="HEALTHY">50–89%</option><option value="LOW">Under 50%</option></select></div>
        <div><label>Sort</label><select value={f.sort} onChange={(e) => set("sort", e.target.value)}><option value="pct">Utilisation</option><option value="occupied">Occupied</option><option value="cap">Approved</option><option value="institution">Institution</option></select></div>
      </div>
      {q.error ? <ErrorBox error={q.error} retry={() => q.refetch()} /> : null}
      <div className="tablewrap"><table><thead><tr><th>Institution</th><th>State</th><th>Programme</th><th>Bucket</th><th>Occupied / approved</th><th style={{ width: 180 }}>Utilisation</th></tr></thead><tbody>
        {q.isLoading ? <tr><td colSpan={6}><Skeleton /></td></tr> : q.data.items.map((r: any, i: number) => <tr key={i}><td>{r.institution_name}<div className="hint">{r.institution_type}</div></td><td>{r.state}</td><td>{r.programme_name}<div className="hint">{r.level}</div></td><td>{r.bucket_type}</td>
          <td>{r.occupied}/{r.cap + r.approved_extra}{r.approved_extra ? ` (+${r.approved_extra})` : ""}</td>
          <td><div className={"bar " + (r.pct >= 100 ? "red" : r.pct >= 90 ? "amber" : r.pct < 50 ? "neutral" : "")}><i style={{ width: `${Math.min(r.pct, 100)}%` }} /></div><span className="hint">{r.pct}% {r.pct >= 100 ? "· stop" : r.pct >= 90 ? "· warning" : ""}</span></td></tr>)}
        {q.data && !q.data.items.length ? <tr><td colSpan={6} className="empty">No buckets match these filters.</td></tr> : null}
      </tbody></table></div>
      <div className="pager"><span>{q.data?.total ?? "…"} buckets</span><span className="row"><button className="btn small ghost" disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</button><button className="btn small ghost" disabled={!q.data || (page + 1) * 25 >= q.data.total} onClick={() => setPage(page + 1)}>Next</button></span></div>
    </>
  );
}

export function Reviews() {
  const { can } = useAuth(); const qc = useQueryClient(); const toast = useToast();
  const q = useQuery({ queryKey: ["exceptions", "all"], queryFn: () => api<any>("/seat-exceptions"), refetchInterval: REFRESH });
  const [sel, setSel] = useState<any>(null); const [reason, setReason] = useState(""); const [seats, setSeats] = useState<string>("");
  const decide = useMutation({
    mutationFn: (approve: boolean) => api(`/seat-exceptions/${sel.id}/decide`, { body: { approve, reason, approved_seats: seats ? Number(seats) : null } }),
    onSuccess: () => { toast("Decision recorded"); setSel(null); setReason(""); qc.invalidateQueries({ queryKey: ["exceptions"] }); },
    onError: (e) => toast((e as ApiProblem).title, true),
  });
  return (
    <>
      <PageHead title="Reviews" sub="Seat exceptions within your regional scope. Window extensions, corrections, data requests and onboarding arrive in a later phase." />
      {q.error ? <ErrorBox error={q.error} retry={() => q.refetch()} /> : null}
      <div className="tablewrap"><table><thead><tr><th>Institution</th><th>Programme / bucket</th><th>Seats</th><th>Justification</th><th>Status</th><th></th></tr></thead><tbody>
        {q.data?.items.map((x: any) => <tr key={x.id}><td>{x.institution_name}<div className="hint">{x.state}</div></td><td>{x.programme_name} · {x.bucket_type}</td><td>{x.extra_seats}{x.approved_seats ? ` → ${x.approved_seats}` : ""}</td><td>{x.justification}</td><td><Status v={x.status} /></td>
          <td>{x.status === "PENDING" && can("seat_exceptions:decide") ? <button className="btn small" onClick={() => { setSel(x); setSeats(String(x.extra_seats)); }}>Decide</button> : x.decision_reason}</td></tr>)}
        {q.data && !q.data.items.length ? <tr><td colSpan={6} className="empty">No exception requests in your scope.</td></tr> : null}
      </tbody></table></div>
      {sel ? <Modal title="Decide seat exception" onClose={() => setSel(null)}>
        <p>{sel.institution_name} requests {sel.extra_seats} extra seats in {sel.programme_name} ({sel.bucket_type}).</p><p className="hint">{sel.justification}</p>
        <Field label="Seats to approve"><input type="number" min={1} max={sel.extra_seats} value={seats} onChange={(e) => setSeats(e.target.value)} /></Field>
        <Field label="Reason (recorded in the audit trail)"><textarea rows={2} value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
        <div className="row" style={{ marginTop: 12 }}><button className="btn" disabled={reason.length < 5} onClick={() => decide.mutate(true)}>Approve</button><button className="btn danger" disabled={reason.length < 5} onClick={() => decide.mutate(false)}>Reject</button></div></Modal> : null}
    </>
  );
}

export function Audit() {
  const [action, setAction] = useState(""); const [res, setRes] = useState<any>(null); const toast = useToast();
  const q = useQuery({ queryKey: ["audit", action], queryFn: () => api<any>("/audit?limit=100" + (action ? `&action=${encodeURIComponent(action)}` : "")), refetchInterval: REFRESH });
  const run = useMutation({ mutationFn: () => api<any>("/audit/integrity-check", { body: {} }), onSuccess: (r) => { setRes(r); toast(r.ok ? "Integrity check passed" : "Integrity check FAILED", !r.ok); }, onError: (e) => toast((e as ApiProblem).title, true) });
  return (
    <>
      <PageHead title="Audit trail" sub="Hash-chained: each entry commits to the one before it. Checkpoints are signed.">
        <button className="btn" disabled={run.isPending} onClick={() => run.mutate()}>{run.isPending ? "Checking…" : "Run integrity check"}</button></PageHead>
      {res ? <div className={"alert " + (res.ok ? "ok" : "err")} role="status"><b>{res.ok ? "Chain verified" : "Tampering detected"}</b> — {res.checked.toLocaleString("en-IN")} entries, {res.checkpoints_verified} signed checkpoints checked.
        {!res.ok ? <div>First broken sequence number: <b>{res.first_broken_seq}</b>. {res.problems.join("; ")}</div> : null}</div> : null}
      {q.data ? <p className="hint">Sealed through #{q.data.chain.sealed_through_seq} · {q.data.chain.unsealed_entries} entries awaiting the sealer · WORM copy: {q.data.chain.worm} <Simulated what="an object-lock (WORM) bucket" /></p> : null}
      <div className="toolbar"><div className="grow"><label>Filter by action prefix</label><input type="text" value={action} onChange={(e) => setAction(e.target.value)} placeholder="e.g. claim. , authz. , confirmation." /></div></div>
      {q.error ? <ErrorBox error={q.error} retry={() => q.refetch()} /> : null}
      <div className="tablewrap"><table><thead><tr><th>Seq</th><th>When</th><th>Actor</th><th>Action</th><th>Entity</th><th>Details (no personal data)</th></tr></thead><tbody>
        {q.isLoading ? <tr><td colSpan={6}><Skeleton /></td></tr> : q.data.items.map((a: any) => <tr key={a.id}><td>{a.seq ?? <span className="tag t-amber">unsealed</span>}</td><td>{fmtDateTime(a.occurred_at)}</td><td>{a.actor || "-"} <span className="hint">{a.actor_role || ""}</span></td><td><code>{a.action}</code></td><td>{a.entity_type || "-"}</td><td className="hint">{a.justification ? a.justification + " · " : ""}{JSON.stringify(a.payload)}</td></tr>)}
      </tbody></table></div>
    </>
  );
}

export function Ops() {
  const q = useQuery({ queryKey: ["ops"], queryFn: () => api<any>("/ops/status"), refetchInterval: 5000 });
  if (q.isLoading) return <Skeleton rows={6} />;
  if (q.error) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  const d = q.data;
  return (
    <>
      <PageHead title="Operations" sub={d.note}><AsOf at={d.projector_as_of} onRefresh={() => q.refetch()} /></PageHead>
      <div className="kpis"><Tile v={d.outbox_lag_events} l="Outbox lag" s="events not yet projected" /><Tile v={d.audit_unsealed} l="Audit sealer lag" s="entries not yet chained" /><Tile v={d.pending_registry_checks} l="Pending registry checks" />
        <div className="kpi"><div className="v"><Status v={d.registry_breaker === "OPEN" ? "OPEN_BREAKER" : d.registry_breaker === "CLOSED" ? "UP" : "SLOW"} label={d.registry_breaker} /></div><div className="l">Registry circuit breaker <Simulated what="the APAAR registry" /></div></div></div>
      <div className="cols2">
        <div className="card"><h3>Job queues</h3><div className="tablewrap"><table><thead><tr><th>Queue</th><th>Queued</th><th>Running</th><th>Dead</th><th>Done</th><th>Oldest queued</th></tr></thead><tbody>
          {d.queues.map((x: any) => <tr key={x.queue}><td>{x.queue}</td><td>{x.queued}</td><td>{x.running}</td><td>{x.dead ? <Status v="DEAD" label={String(x.dead)} /> : 0}</td><td>{x.succeeded}</td><td>{x.oldest_queued_s ? `${Math.round(x.oldest_queued_s)}s` : "-"}</td></tr>)}
          {!d.queues.length ? <tr><td colSpan={6} className="empty">No jobs have run yet. Start the worker.</td></tr> : null}</tbody></table></div></div>
        <div className="card"><h3>Scheduler</h3><div className="tablewrap"><table><thead><tr><th>Schedule</th><th>Every</th><th>Last run</th></tr></thead><tbody>
          {d.schedules.map((s: any) => <tr key={s.name}><td>{s.name}</td><td>{s.interval_seconds}s</td><td>{fmtDateTime(s.last_run_at)}</td></tr>)}</tbody></table></div></div>
      </div>
      <div className="card" style={{ marginTop: 16 }}><h3>Request latency (this API process)</h3><div className="tablewrap"><table><thead><tr><th>Route group</th><th>Requests</th><th>p50 ms</th><th>p95 ms</th></tr></thead><tbody>
        {Object.entries(d.latency).map(([k, v]: any) => <tr key={k}><td><code>{k}</code></td><td>{v.count}</td><td>{v.p50_ms}</td><td>{v.p95_ms}</td></tr>)}</tbody></table></div></div>
      {d.dead_jobs.length ? <div className="card" style={{ marginTop: 16 }}><h3>Dead jobs</h3>{d.dead_jobs.map((j: any) => <div key={j.id}><Status v="DEAD" /> {j.type} — {j.last_error}</div>)}</div> : null}
    </>
  );
}

export function Demo() {
  const toast = useToast(); const qc = useQueryClient();
  const st = useQuery({ queryKey: ["demo-state"], queryFn: () => api<any>("/demo/state"), refetchInterval: 5000 });
  const [days, setDays] = useState("0");
  const call = useMutation({ mutationFn: ({ path, body }: any) => api(path, { body }), onSuccess: (r: any) => { toast("Applied"); qc.invalidateQueries({ queryKey: ["demo-state"] }); return r; }, onError: (e) => toast((e as ApiProblem).title + ": " + (e as ApiProblem).detail, true) });
  const [job, setJob] = useState<any>(null);
  return (
    <>
      <PageHead title="Demo tools" sub="Only available because DEMO_MODE is on. They exist to demonstrate outages and deadlines in minutes." />
      {st.error ? <ErrorBox error={st.error} retry={() => st.refetch()} /> : null}
      <div className="cols2">
        <div className="card"><h3>APAAR registry <Simulated what="the national APAAR registry" /></h3>
          <p>Current mode: <Status v={st.data?.registry?.mode} /> · circuit breaker {st.data?.breaker}</p>
          <p className="hint">DOWN makes new claims PENDING_VERIFICATION (the seat is held, never a silent pass). Restore UP and run the revalidation job to resolve them.</p>
          <div className="row">{["UP", "DOWN", "SLOW"].map((m) => <button key={m} className="btn small ghost" onClick={() => call.mutate({ path: "/demo/registry-mode", body: { mode: m } })}>Set {m}</button>)}</div></div>
        <div className="card"><h3>Clock</h3><p>Application time: <b>{st.data ? fmtDateTime(st.data.now) : "…"}</b> (offset {st.data?.clock_offset_days ?? 0} days)</p>
          <p className="hint">Advance time to see 30-day confirmation deadlines pass. Applies to the API; the worker picks it up from the database.</p>
          <div className="row"><input type="number" value={days} onChange={(e) => setDays(e.target.value)} style={{ width: 90 }} aria-label="Days to advance" /><button className="btn small" onClick={() => call.mutate({ path: "/demo/clock", body: { advance_days: Number(days) } })}>Set offset (days)</button></div></div>
      </div>
      <div className="card" style={{ marginTop: 16 }}><h3>Run a scheduled job now</h3>
        <div className="row">{["identity.revalidate", "confirmations.deadlines", "projector.tick", "audit.seal"].map((t) => <button key={t} className="btn small ghost" onClick={async () => { try { setJob(await api("/demo/run-job", { body: { type: t } })); } catch (e) { toast((e as ApiProblem).title, true); } }}>{t}</button>)}</div>
        {job ? <pre className="mono" style={{ whiteSpace: "pre-wrap" }}>{JSON.stringify(job, null, 2)}</pre> : null}</div>
      <div className="alert info" style={{ marginTop: 16 }}><Link to="/aicte/audit">Open the audit trail</Link> to run the tamper demo: <code>python -m app.cli demo tamper-audit --seq 40</code>, then “Run integrity check”.</div>
    </>
  );
}
