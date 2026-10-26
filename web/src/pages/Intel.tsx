import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiProblem, fmtDate, fmtDateTime, getToken } from "../api";
import { useAuth } from "../auth";
import { AsOf, Empty, ErrorBox, Field, Modal, PageHead, Simulated, Skeleton, Status, useStepUp, useToast } from "../ui";

const BASE = (import.meta.env.VITE_API_URL as string) || "http://localhost:8000/api/v1";
const POLL = 15000;
const fail = (toast: any) => (e: unknown) => toast(`${(e as ApiProblem).title}${(e as ApiProblem).detail ? ": " + (e as ApiProblem).detail : ""}`, true);
const BAND: Record<string, [string, string]> = { LOW_PRIORITY: ["t-green", "Low priority"], WATCH: ["t-amber", "Watch"], REVIEW_PRIORITY: ["t-red", "Review priority"] };
const SEV: Record<string, string> = { HIGH: "t-red", MEDIUM: "t-amber", LOW: "t-blue" };
const REVIEW_AID = "A review aid, not a verdict. It only prioritises reviews and triggers no penalty.";

/* ------------------------------------------------------------------ Trust Index */
export function TrustBars({ d }: { d: any }) {
  return (
    <div>
      <div className="row" style={{ alignItems: "baseline" }}><span style={{ fontSize: 40, fontWeight: 800 }}>{d.score.toFixed(1)}</span><span>/ 100</span><span className={"tag " + BAND[d.band][0]}>{BAND[d.band][1]}</span></div>
      <p className="hint">{REVIEW_AID} Config {d.config_version}.</p>
      {d.components.map((c: any) => (
        <div key={c.key} style={{ margin: "10px 0" }}>
          <div className="row" style={{ justifyContent: "space-between" }}><b>{c.label}</b><span>{c.points} / {c.max}</span></div>
          <div className="bar"><i style={{ width: `${(100 * c.points) / c.max}%` }} /></div>
          <div className="hint">{c.formula}</div>
          <div className="hint">Inputs: {Object.entries(c.inputs).map(([k, v]) => `${k.replace(/_/g, " ")}: ${v}`).join(" · ")}{c.evidence?.startsWith("/") ? <> · <Link to={c.evidence}>evidence</Link></> : ` · evidence: ${c.evidence}`}</div>
        </div>))}
      {d.history?.length > 1 ? <Spark pts={d.history.map((h: any) => h.score)} /> : null}
    </div>
  );
}
function Spark({ pts }: { pts: number[] }) {
  const W = 300, H = 50, mn = Math.min(...pts, 0), mx = Math.max(...pts, 100);
  const x = (i: number) => (i * W) / Math.max(pts.length - 1, 1), y = (v: number) => H - ((v - mn) / (mx - mn)) * H;
  return <svg viewBox={`0 0 ${W} ${H}`} style={{ width: 300 }} role="img" aria-label="Score history"><path d={pts.map((v, i) => `${i ? "L" : "M"}${x(i)},${y(v)}`).join(" ")} fill="none" stroke="#0f4d3a" strokeWidth={2} /></svg>;
}

export function TrustCard() {
  const { user } = useAuth();
  const q = useQuery({ queryKey: ["my-trust"], enabled: !!user?.institution && !!user.permissions.includes("trust:read"), queryFn: () => api<any>(`/trust/${user!.institution!.id}`), refetchInterval: POLL });
  if (!q.data) return null;
  return <div className="card" style={{ marginBottom: 16 }}><h3>Your Trust Index <span className="hint">{REVIEW_AID}</span></h3><TrustBars d={q.data} /></div>;
}

export function TrustBoard() {
  const [band, setBand] = useState(""); const [qs, setQs] = useState(""); const [page, setPage] = useState(0); const [sel, setSel] = useState<string | null>(null);
  const q = useQuery({ queryKey: ["trust", band, qs, page], queryFn: () => api<any>(`/trust?limit=20&offset=${page * 20}${band ? "&band=" + band : ""}${qs ? "&q=" + encodeURIComponent(qs) : ""}`), refetchInterval: POLL, placeholderData: (x: any) => x });
  const d = useQuery({ queryKey: ["trust-d", sel], enabled: !!sel, queryFn: () => api<any>(`/trust/${sel}`) });
  const toast = useToast(); const qc = useQueryClient();
  const run = useMutation({ mutationFn: () => api("/intel/run-now", { body: {} }), onSuccess: () => { toast("Pipeline finished"); qc.invalidateQueries(); }, onError: fail(toast) });
  return (
    <>
      <PageHead title="Trust Index" sub={REVIEW_AID}><button className="btn" disabled={run.isPending} onClick={() => run.mutate()}>{run.isPending ? "Running…" : "Run pipeline now"}</button></PageHead>
      <div className="toolbar"><div className="grow"><label>Search institution</label><input type="text" value={qs} onChange={(e) => { setQs(e.target.value); setPage(0); }} /></div>
        <div><label>Band</label><select value={band} onChange={(e) => { setBand(e.target.value); setPage(0); }}><option value="">All</option>{Object.entries(BAND).map(([k, v]) => <option key={k} value={k}>{v[1]}</option>)}</select></div></div>
      {q.error ? <ErrorBox error={q.error} retry={() => q.refetch()} /> : null}
      <div className="tablewrap"><table><thead><tr><th>Institution</th><th>State</th><th>Type</th><th>Score</th><th>Band</th><th>Open alerts</th></tr></thead><tbody>
        {q.isLoading ? <tr><td colSpan={6}><Skeleton /></td></tr> : q.data.items.map((r: any) => <tr key={r.institution_id} className="click" onClick={() => setSel(r.institution_id)}><td>{r.institution_name}</td><td>{r.state}</td><td>{r.type}</td>
          <td><b>{r.score.toFixed(1)}</b></td><td><span className={"tag " + BAND[r.band][0]}>{BAND[r.band][1]}</span></td><td>{r.open_alerts || ""}</td></tr>)}
        {q.data && !q.data.items.length ? <tr><td colSpan={6} className="empty">No scores in this view. Run the pipeline.</td></tr> : null}</tbody></table></div>
      <div className="pager"><span>{q.data?.total ?? "…"} institutions, lowest score first</span><span className="row"><button className="btn small ghost" disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</button><button className="btn small ghost" disabled={!q.data || (page + 1) * 20 >= q.data.total} onClick={() => setPage(page + 1)}>Next</button></span></div>
      {sel ? <Modal title={d.data?.institution.name || "Trust Index"} drawer onClose={() => setSel(null)}>{!d.data ? <Skeleton rows={6} /> : <>
        <TrustBars d={d.data} />{d.data.open_alerts.length ? <><h4>Open alerts</h4>{d.data.open_alerts.map((a: any) => <div key={a.id}><span className={"tag " + SEV[a.severity]}>{a.severity}</span> <Link to="/aicte/alerts">{a.type.replace(/_/g, " ")}</Link> (layer {a.layer})</div>)}</> : null}</>}</Modal> : null}
    </>
  );
}

/* ------------------------------------------------------------------ alerts */
export function Alerts() {
  const { can } = useAuth(); const toast = useToast(); const qc = useQueryClient(); const nav = useNavigate();
  const [status, setStatus] = useState("OPEN"); const [layer, setLayer] = useState(""); const [sev, setSev] = useState(""); const [page, setPage] = useState(0); const [sel, setSel] = useState<string | null>(null); const [reason, setReason] = useState("");
  const q = useQuery({ queryKey: ["alerts", status, layer, sev, page], queryFn: () => api<any>(`/alerts?limit=15&offset=${page * 15}&status=${status}${layer ? "&layer=" + layer : ""}${sev ? "&severity=" + sev : ""}`), refetchInterval: POLL, placeholderData: (x: any) => x });
  const d = useQuery({ queryKey: ["alert", sel], enabled: !!sel, queryFn: () => api<any>(`/alerts/${sel}`) });
  const open = useMutation({ mutationFn: () => api<any>(`/alerts/${sel}/open-case`, { body: {} }), onSuccess: (r) => { toast("Case opened"); nav(`/aicte/cases/${r.case_id}`); }, onError: fail(toast) });
  const dismiss = useMutation({ mutationFn: () => api(`/alerts/${sel}/dismiss`, { body: { reason } }), onSuccess: () => { toast("Alert dismissed"); setSel(null); setReason(""); qc.invalidateQueries({ queryKey: ["alerts"] }); }, onError: fail(toast) });
  const LAYER: Record<string, string> = { A: "A · rule findings", B: "B · duplicates", C: "C · risk model", G: "Graph cluster" };
  return (
    <>
      <PageHead title="Alerts" sub="Explainable leads, ranked by severity. Each one shows its baseline, deviation and contributing features. Never an automatic verdict." />
      <div className="toolbar">
        <div><label>Status</label><select value={status} onChange={(e) => { setStatus(e.target.value); setPage(0); }}>{["OPEN", "IN_CASE", "DISMISSED", "CLOSED", ""].map((s) => <option key={s} value={s}>{s || "All"}</option>)}</select></div>
        <div><label>Layer</label><select value={layer} onChange={(e) => { setLayer(e.target.value); setPage(0); }}><option value="">All</option>{Object.entries(LAYER).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></div>
        <div><label>Severity</label><select value={sev} onChange={(e) => { setSev(e.target.value); setPage(0); }}><option value="">All</option>{["HIGH", "MEDIUM", "LOW"].map((s) => <option key={s}>{s}</option>)}</select></div></div>
      {q.error ? <ErrorBox error={q.error} retry={() => q.refetch()} /> : null}
      <div className="tablewrap"><table><thead><tr><th>Severity</th><th>Alert</th><th>Institution</th><th>Layer</th><th>Deviation</th><th>Status</th><th>Raised</th></tr></thead><tbody>
        {q.isLoading ? <tr><td colSpan={7}><Skeleton /></td></tr> : q.data.items.map((a: any) => <tr key={a.id} className="click" onClick={() => setSel(a.id)}><td><span className={"tag " + SEV[a.severity]}>{a.severity}</span></td><td>{a.type.replace(/_/g, " ").toLowerCase().replace(/^./, (c: string) => c.toUpperCase())}</td><td>{a.institution_name}<div className="hint">{a.state}</div></td>
          <td>{LAYER[a.layer]}</td><td>{a.deviation ?? "-"}</td><td><Status v={a.status === "OPEN" ? "OPEN" : a.status === "IN_CASE" ? "PENDING" : "WITHDRAWN"} label={a.status.replace(/_/g, " ")} /></td><td>{fmtDate(a.created_at)}</td></tr>)}
        {q.data && !q.data.items.length ? <tr><td colSpan={7} className="empty">No alerts in this view.</td></tr> : null}</tbody></table></div>
      <div className="pager"><span>{q.data?.total ?? "…"} alerts</span><span className="row"><button className="btn small ghost" disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</button><button className="btn small ghost" disabled={!q.data || (page + 1) * 15 >= q.data.total} onClick={() => setPage(page + 1)}>Next</button></span></div>
      {sel ? <Modal title="Alert detail" drawer onClose={() => setSel(null)}>{!d.data ? <Skeleton rows={6} /> : <>
        <p><span className={"tag " + SEV[d.data.severity]}>{d.data.severity}</span> <b>{d.data.type.replace(/_/g, " ")}</b> · {d.data.institution_name} · layer {d.data.layer}</p>
        <p>{d.data.explanation?.description}</p>
        <h4>Baseline and deviation</h4><p className="hint">Baseline: {JSON.stringify(d.data.baseline)} · deviation {d.data.deviation ?? "n/a"} · model {d.data.model_version}</p>
        <h4>Evidence</h4><pre className="mono" style={{ whiteSpace: "pre-wrap" }}>{JSON.stringify(d.data.evidence, null, 1)}</pre>
        <h4>Contributing features</h4>
        <div className="tablewrap"><table><tbody>{(d.data.explanation?.contributions || []).map((c: any, i: number) => <tr key={i}><td>{String(c.feature).replace(/_/g, " ")}</td><td>{c.robust_z !== undefined ? `robust z ${c.robust_z ?? "n/a"}` : c.value}</td></tr>)}</tbody></table></div>
        <div className="alert info" style={{ marginTop: 10 }}><b>Suggested action:</b> {d.data.suggested_action}</div>
        {d.data.status === "OPEN" && can("cases:manage") ? <>
          <div className="row"><button className="btn" onClick={() => open.mutate()}>Open a case</button></div>
          <Field label="Or dismiss with a reason"><input type="text" value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
          <button className="btn small ghost" disabled={reason.length < 5} onClick={() => dismiss.mutate()}>Dismiss alert</button></> : d.data.case_id ? <Link to={`/aicte/cases/${d.data.case_id}`}>Open the linked case</Link> : null}</>}</Modal> : null}
    </>
  );
}

/* ------------------------------------------------------------------ cases (AICTE) */
const CASE_STATUS: Record<string, string> = { OPEN: "OPEN", AWAITING_RESPONSE: "PENDING", UNDER_REVIEW: "PENDING_FIX", CLOSED: "WITHDRAWN", APPEALED: "DISPUTED" };
export function Cases() {
  const nav = useNavigate();
  const [status, setStatus] = useState(""); const [type, setType] = useState(""); const [qs, setQs] = useState(""); const [page, setPage] = useState(0);
  const q = useQuery({ queryKey: ["cases", status, type, qs, page], queryFn: () => api<any>(`/cases?limit=20&offset=${page * 20}${status ? "&status=" + status : ""}${type ? "&type=" + type : ""}${qs ? "&q=" + encodeURIComponent(qs) : ""}`), refetchInterval: POLL, placeholderData: (x: any) => x });
  return (
    <>
      <PageHead title="Cases" sub="Workspace for alerts, confirmed duplicates and student disputes. Outcomes are labels; none triggers an automatic penalty." />
      <div className="toolbar"><div className="grow"><label>Search case number, title or institution</label><input type="text" value={qs} onChange={(e) => { setQs(e.target.value); setPage(0); }} /></div>
        <div><label>Status</label><select value={status} onChange={(e) => { setStatus(e.target.value); setPage(0); }}><option value="">All</option>{Object.keys(CASE_STATUS).map((s) => <option key={s}>{s}</option>)}</select></div>
        <div><label>Type</label><select value={type} onChange={(e) => { setType(e.target.value); setPage(0); }}><option value="">All</option>{["ANOMALY", "DUPLICATE", "STUDENT_DISPUTE", "UNCONFIRMED_SHARE", "SEAT_RELEASE", "OTHER"].map((s) => <option key={s}>{s}</option>)}</select></div></div>
      {q.error ? <ErrorBox error={q.error} retry={() => q.refetch()} /> : null}
      <div className="tablewrap"><table><thead><tr><th>Case</th><th>Type</th><th>Title</th><th>Institution</th><th>Status</th><th>Owner</th><th>Response due</th></tr></thead><tbody>
        {q.isLoading ? <tr><td colSpan={7}><Skeleton /></td></tr> : q.data.items.map((c: any) => <tr key={c.id} className="click" onClick={() => nav(`/aicte/cases/${c.id}`)}><td><code>{c.case_no}</code></td><td>{c.type.replace(/_/g, " ")}</td><td>{c.title}</td><td>{c.institution_name}</td>
          <td><Status v={CASE_STATUS[c.status]} label={c.status.replace(/_/g, " ")} />{c.outcome_code ? <div className="hint">{c.outcome_code.replace(/_/g, " ").toLowerCase()}</div> : null}</td><td>{c.owner_name}</td><td>{c.status === "AWAITING_RESPONSE" ? fmtDate(c.response_due_at) : ""}</td></tr>)}
        {q.data && !q.data.items.length ? <tr><td colSpan={7} className="empty">No cases in this view.</td></tr> : null}</tbody></table></div>
      <div className="pager"><span>{q.data?.total ?? "…"} cases</span><span className="row"><button className="btn small ghost" disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</button><button className="btn small ghost" disabled={!q.data || (page + 1) * 20 >= q.data.total} onClick={() => setPage(page + 1)}>Next</button></span></div>
    </>
  );
}

export function CaseWorkspace() {
  const { id } = useParams(); const { can, user } = useAuth(); const toast = useToast(); const qc = useQueryClient(); const askStepUp = useStepUp();
  const q = useQuery({ queryKey: ["case", id], queryFn: () => api<any>(`/cases/${id}`), refetchInterval: POLL });
  const grants = useQuery({ queryKey: ["grants"], queryFn: () => api<any>("/me/elevations"), refetchInterval: 10000 });
  const [msg, setMsg] = useState(""); const [outcome, setOutcome] = useState("NOT_SUBSTANTIATED"); const [just, setJust] = useState(""); const [mins, setMins] = useState(30); const [people, setPeople] = useState<any>(null);
  const [relReason, setRelReason] = useState(""); const [appDec, setAppDec] = useState("UPHELD");
  const refresh = () => { qc.invalidateQueries({ queryKey: ["case", id] }); qc.invalidateQueries({ queryKey: ["grants"] }); };
  const act = useMutation({ mutationFn: async ({ path, body, step }: any) => api<any>(`/cases/${id}/${path}`, { body: body ?? {}, headers: step ? { "X-Step-Up-Token": await askStepUp() } : undefined }), onSuccess: () => { toast("Done"); setMsg(""); refresh(); }, onError: fail(toast) });
  const elevate = useMutation({ mutationFn: () => api("/me/elevations", { body: { case_id: id, justification: just, duration_minutes: mins } }), onSuccess: () => { toast("Elevation granted"); refresh(); }, onError: fail(toast) });
  const reveal = useMutation({ mutationFn: () => api<any>(`/cases/${id}/reveal`, { body: {} }), onSuccess: setPeople, onError: fail(toast) });
  if (q.isLoading) return <Skeleton rows={8} />;
  if (q.error) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  const c = q.data; const g = grants.data?.items.find((x: any) => x.case_id === c.id);
  const mine = c.owner_id === user?.id;
  return (
    <>
      <PageHead title={c.case_no} sub={<>{c.title} · {c.institution_name} · owner {c.owner}</>}><Status v={CASE_STATUS[c.status]} label={c.status.replace(/_/g, " ")} /><Link className="btn ghost" to="/aicte/cases">← Cases</Link></PageHead>
      <div className="alert info">{c.statement} A confirmed violation is a label for review quality, not a sanction.</div>
      {c.overdue ? <div className="alert warn">The institution's response is overdue ({fmtDate(c.response_due_at)}). You may start the review.</div> : null}
      <div className="cols2">
        <div className="card"><h3>Actions</h3>
          {c.status === "OPEN" || c.status === "UNDER_REVIEW" ? <><Field label="Request a response (due in 14 days)"><textarea rows={2} value={msg} onChange={(e) => setMsg(e.target.value)} /></Field>
            <button className="btn small" disabled={msg.length < 5} onClick={() => act.mutate({ path: "request-response", body: { message: msg } })}>Request response</button></> : null}
          {(c.status === "OPEN" || c.overdue) ? <button className="btn small ghost" style={{ marginLeft: 8 }} onClick={() => act.mutate({ path: "start-review" })}>Start review</button> : null}
          {c.status === "UNDER_REVIEW" ? <div style={{ marginTop: 12 }}><Field label="Outcome code"><select value={outcome} onChange={(e) => setOutcome(e.target.value)}>{["CONFIRMED_VIOLATION", "NOT_SUBSTANTIATED", "DATA_CORRECTED", "REFERRED_FOR_INSPECTION", "FALSE_POSITIVE"].map((o) => <option key={o}>{o}</option>)}</select></Field>
            <Field label="Notes (min 5 characters)"><textarea rows={2} value={msg} onChange={(e) => setMsg(e.target.value)} /></Field><button className="btn small danger" disabled={msg.length < 5} onClick={() => act.mutate({ path: "close", body: { outcome_code: outcome, notes: msg } })}>Close case</button></div> : null}
          {c.status === "APPEALED" ? <div><p>An appeal is pending. {mine ? <b>You own this case, so a different reviewer must decide it.</b> : null}</p>
            <Field label="Decision"><select value={appDec} onChange={(e) => setAppDec(e.target.value)}><option>UPHELD</option><option>OVERTURNED</option></select></Field>
            <Field label="Notes"><textarea rows={2} value={msg} onChange={(e) => setMsg(e.target.value)} /></Field>
            <button className="btn small" disabled={msg.length < 5 || mine} onClick={() => act.mutate({ path: "appeal/decide", body: { decision: appDec, notes: msg } })}>Decide appeal</button></div> : null}
          {c.enrolment_id && can("seats:release") && c.status !== "CLOSED" && !c.release_enrolment_id ? <div style={{ marginTop: 14 }}><h4>Release the seat</h4><p className="hint">The gate-abuse control: releases the seat, notifies the institution and records the reason. Needs your password.</p>
            <input type="text" placeholder="Reason (min 5 characters)" value={relReason} onChange={(e) => setRelReason(e.target.value)} /><button className="btn small danger" style={{ marginTop: 6 }} disabled={relReason.length < 5} onClick={() => act.mutate({ path: "release-seat", body: { reason: relReason }, step: true })}>Release seat</button></div> : null}
          {c.status === "CLOSED" ? <p>Closed {fmtDateTime(c.closed_at)} · outcome <b>{c.outcome_code}</b>. {c.appealable ? `The institution may appeal until ${fmtDate(c.appeal_deadline)}.` : ""}</p> : null}
        </div>
        <div className="card"><h3>Student-level access <span className="tag t-grey">case-gated · audited</span></h3>
          {g ? <><p>Elevation active until <b>{fmtDateTime(g.expires_at)}</b> ({g.records_revealed} records revealed). Every reveal is audited and visible to the student.</p>
            <button className="btn small" onClick={() => reveal.mutate()}>Reveal identities for this case</button></>
            : <><p className="hint">Student details are hidden. To see them, request a time-limited elevation tied to this case (max 4 hours).</p>
              <Field label="Justification (min 30 characters)"><textarea rows={3} value={just} onChange={(e) => setJust(e.target.value)} /></Field>
              <Field label="Minutes"><input type="number" min={5} max={240} value={mins} onChange={(e) => setMins(Number(e.target.value))} /></Field>
              <button className="btn small" disabled={just.length < 30} onClick={() => elevate.mutate()}>Request elevation</button></>}
          {people ? <div className="alert warn" style={{ marginTop: 10 }}><b>Personal data (handle with care)</b>{people.people.map((p: any) => <div key={p.enrolment_ref}><code>{p.enrolment_ref}</code> {p.name} · {p.dob} · parent {p.parent_name || "-"} · {p.mobile_masked || "no mobile"} · {p.institution}, {p.programme}</div>)}{!people.people.length ? " No linked enrolments." : null}</div> : null}
        </div>
      </div>
      <div className="cols2" style={{ marginTop: 16 }}>
        <div className="card"><h3>Timeline</h3><ul className="timeline" style={{ padding: 0 }}>{c.events.map((e: any) => <li key={e.id}><b>{e.event_type.replace(/_/g, " ")}</b> · {fmtDateTime(e.created_at)} · {e.actor || "system"}{e.body ? <div className="hint">{e.body}</div> : null}</li>)}</ul>
          {c.response_text ? <div className="alert ok"><b>Institution response ({fmtDate(c.responded_at)}):</b> {c.response_text}</div> : null}
          {c.appeals.map((a: any) => <div key={a.id} className="alert warn"><b>Appeal {a.status}</b> by {a.filed_by}: {a.grounds}{a.decision_notes ? <div>Decision by {a.decided_by}: {a.decision_notes}</div> : null}</div>)}</div>
        <div className="card"><h3>Evidence pack</h3><p className="hint">SHA-256 <code>{c.evidence_sha256?.slice(0, 20)}…</code> (recomputed on close)</p>
          {c.alert ? <><p><span className={"tag " + SEV[c.alert.severity]}>{c.alert.severity}</span> {c.alert.type.replace(/_/g, " ")} · layer {c.alert.layer}</p><p className="hint">{c.alert.explanation?.description}</p><p className="hint">Suggested action: {c.alert.suggested_action}</p></> : null}
          <pre className="mono" style={{ whiteSpace: "pre-wrap", maxHeight: 260, overflow: "auto" }}>{JSON.stringify(c.evidence_pack, null, 1)}</pre></div>
      </div>
    </>
  );
}

/* ------------------------------------------------------------------ reports (both portals) */
export function Reports() {
  const { user } = useAuth(); const toast = useToast(); const qc = useQueryClient();
  const types = useQuery({ queryKey: ["report-types"], queryFn: () => api<any>("/reports/types") });
  const list = useQuery({ queryKey: ["reports"], queryFn: () => api<any>("/reports"), refetchInterval: (q: any) => (q.state.data?.items.some((j: any) => ["QUEUED", "RUNNING"].includes(j.status)) ? 2000 : 15000) });
  const [type, setType] = useState("SEAT_UTILISATION"); const [fmt, setFmt] = useState("PDF");
  const req = useMutation({ mutationFn: () => api("/reports", { body: { type, format: fmt } }), onSuccess: () => { toast("Report queued"); qc.invalidateQueries({ queryKey: ["reports"] }); }, onError: fail(toast) });
  const dl = async (j: any) => {
    const r = await fetch(`${BASE}/reports/${j.id}/download`, { headers: { Authorization: `Bearer ${getToken()}` } });
    if (!r.ok) return toast("Download failed", true);
    const a = document.createElement("a"); a.href = URL.createObjectURL(await r.blob()); a.download = `${j.type.toLowerCase()}.${j.format.toLowerCase()}`; a.click();
  };
  return (
    <>
      <PageHead title="Reports" sub={user?.institution ? "Your institution's reports. Student-level exports are available only here, audited." : "Aggregate reports for your scope. Small groups are suppressed; AICTE cannot export student-level data."} />
      <div className="card"><div className="toolbar" style={{ margin: 0 }}>
        <div className="grow"><label>Report</label><select value={type} onChange={(e) => setType(e.target.value)}>{types.data?.items.map((t: any) => <option key={t.code} value={t.code}>{t.label}</option>)}</select></div>
        <div><label>Format</label><select value={fmt} onChange={(e) => setFmt(e.target.value)}>{["PDF", "XLSX", "CSV"].map((f) => <option key={f}>{f}</option>)}</select></div>
        <button className="btn" disabled={req.isPending} onClick={() => req.mutate()}>Generate</button></div>
        <p className="hint">{types.data?.definitions} Every file is stamped with the rule-set version, cut-off, generator and a QR that opens a public check of its SHA-256.</p>
        {type === "ENROLMENT_EXPORT" ? <div className="alert warn">This file contains decrypted personal data. The export is audited. Handle it accordingly.</div> : null}</div>
      <div className="tablewrap" style={{ marginTop: 16 }}><table><thead><tr><th>Report</th><th>Format</th><th>Status</th><th>SHA-256</th><th>Requested</th><th></th></tr></thead><tbody>
        {list.data?.items.map((j: any) => <tr key={j.id}><td>{j.title || j.type}</td><td>{j.format}</td><td><Status v={j.status === "DONE" ? "VERIFIED" : j.status === "FAILED" ? "FAILED" : "QUEUED"} label={j.status} />{j.error ? <div className="hint">{j.error}</div> : null}</td>
          <td><code>{j.sha256?.slice(0, 12)}</code> {j.ref ? <a href={`${BASE}/public/reports/${j.ref}`} target="_blank" rel="noreferrer">verify</a> : null}</td><td>{fmtDateTime(j.created_at)}</td><td>{j.status === "DONE" ? <button className="btn small ghost" onClick={() => dl(j)}>Download</button> : null}</td></tr>)}
        {list.data && !list.data.items.length ? <tr><td colSpan={6} className="empty">No reports yet. Choose a type and press Generate (needs the worker running).</td></tr> : null}</tbody></table></div>
    </>
  );
}

/* ------------------------------------------------------------------ institution cases */
export function InstCases() {
  const { can } = useAuth(); const toast = useToast(); const qc = useQueryClient();
  const q = useQuery({ queryKey: ["inst-cases"], queryFn: () => api<any>("/institution/cases"), refetchInterval: POLL });
  const [sel, setSel] = useState<string | null>(null); const [text, setText] = useState("");
  const d = useQuery({ queryKey: ["inst-case", sel], enabled: !!sel, queryFn: () => api<any>(`/institution/cases/${sel}`) });
  const act = useMutation({ mutationFn: (path: string) => api(`/institution/cases/${sel}/${path}`, { body: path === "respond" ? { message: text } : { grounds: text } }), onSuccess: () => { toast("Sent"); setText(""); qc.invalidateQueries(); }, onError: fail(toast) });
  return (
    <>
      <PageHead title="Cases" sub="AICTE reviews that involve your institution. You can respond within the window and appeal a closed case within 15 days." />
      {q.error ? <ErrorBox error={q.error} retry={() => q.refetch()} /> : null}
      <div className="tablewrap"><table><thead><tr><th>Case</th><th>Title</th><th>Status</th><th>Response due</th><th>Outcome</th></tr></thead><tbody>
        {q.data?.items.map((c: any) => <tr key={c.id} className="click" onClick={() => setSel(c.id)}><td><code>{c.case_no}</code></td><td>{c.title}</td><td><Status v={CASE_STATUS[c.status]} label={c.status.replace(/_/g, " ")} /></td><td>{c.status === "AWAITING_RESPONSE" ? fmtDate(c.response_due_at) : ""}</td><td>{c.outcome_code?.replace(/_/g, " ").toLowerCase() || ""}</td></tr>)}
        {q.data && !q.data.items.length ? <tr><td colSpan={5} className="empty">No cases involve your institution.</td></tr> : null}</tbody></table></div>
      {sel ? <Modal title={d.data?.case_no || "Case"} drawer onClose={() => setSel(null)}>{!d.data ? <Skeleton rows={5} /> : <>
        <p>{d.data.title} <Status v={CASE_STATUS[d.data.status]} label={d.data.status.replace(/_/g, " ")} /></p>
        <p className="hint">{d.data.statement}</p>
        <ul className="timeline" style={{ padding: 0 }}>{d.data.events.map((e: any) => <li key={e.id}><b>{e.event_type.replace(/_/g, " ")}</b> · {fmtDateTime(e.created_at)}{e.body ? <div className="hint">{e.body}</div> : null}</li>)}</ul>
        {d.data.status === "AWAITING_RESPONSE" && can("cases:respond") ? <><Field label={`Your response (due ${fmtDate(d.data.response_due_at)})`}><textarea rows={4} value={text} onChange={(e) => setText(e.target.value)} /></Field><button className="btn" disabled={text.length < 5} onClick={() => act.mutate("respond")}>Send response</button></> : null}
        {d.data.status === "CLOSED" ? <><p>Outcome: <b>{d.data.outcome_code}</b>. {d.data.outcome_notes}</p>{d.data.appealable && can("appeals:file") ? <><Field label={`Grounds for appeal (min 20 characters, until ${fmtDate(d.data.appeal_deadline)})`}><textarea rows={4} value={text} onChange={(e) => setText(e.target.value)} /></Field><button className="btn" disabled={text.length < 20} onClick={() => act.mutate("appeal")}>File appeal</button></> : <p className="hint">The appeal window has passed.</p>}</> : null}
        {d.data.appeals.map((a: any) => <div key={a.id} className="alert warn"><b>Appeal {a.status}</b>: {a.grounds}{a.decision_notes ? <div>Decision: {a.decision_notes}</div> : null}</div>)}</>}</Modal> : null}
    </>
  );
}
