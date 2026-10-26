import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiProblem, fmtDate, fmtDateTime } from "../api";
import { useAuth } from "../auth";
import { Empty, ErrorBox, Field, Modal, PageHead, Simulated, Skeleton, Status, useStepUp, useToast } from "../ui";

const POLL = 15000;
const fail = (toast: any) => (e: unknown) => toast(`${(e as ApiProblem).title}${(e as ApiProblem).detail ? ": " + (e as ApiProblem).detail : ""}`, true);

/* ------------------------------------------------------------------ duplicate queue */
const TL: Record<string, [string, string]> = { OVERLAP_FT: ["t-red", "Overlapping full-time"], SEQUENTIAL: ["t-blue", "Sequential"], FT_PLUS_ODL: ["t-green", "Full-time + ODL (allowed)"], EXCEPTION: ["t-green", "Approved exception"],
  SAME_INST_READMIT: ["t-amber", "Same-institution re-admit"], TRANSFER: ["t-green", "Transfer"] };

export function Duplicates() {
  const { can } = useAuth(); const toast = useToast(); const qc = useQueryClient();
  const [status, setStatus] = useState("REVIEW"); const [timeline, setTimeline] = useState(""); const [page, setPage] = useState(0); const [sel, setSel] = useState<any>(null);
  const [decision, setDecision] = useState("DISMISS"); const [reason, setReason] = useState(""); const [rev, setRev] = useState<any>(null);
  const q = useQuery({ queryKey: ["dups", status, timeline, page], queryFn: () => api<any>(`/duplicates?status=${status}&limit=15&offset=${page * 15}${timeline ? "&timeline=" + timeline : ""}`), refetchInterval: POLL, placeholderData: (x: any) => x });
  const decide = useMutation({ mutationFn: () => api(`/duplicates/${sel.id}/decide`, { body: { decision, reason } }), onSuccess: (r: any) => { toast(r.case_id ? "Confirmed. A case was opened and both institutions received a finding." : "Dismissed. This pair will not be flagged again."); setSel(null); setReason(""); qc.invalidateQueries({ queryKey: ["dups"] }); }, onError: fail(toast) });
  const bar = (v: number | null, label: string) => v === null ? <div className="hint">{label}: not available</div> : <div className="statebar" style={{ gridTemplateColumns: "70px 1fr 40px" }}><span>{label}</span><div className="bar"><i style={{ width: `${v * 100}%` }} /></div><span>{v.toFixed(2)}</span></div>;
  return (
    <>
      <PageHead title="Duplicate queue" sub="Candidate pairs from privacy-preserving matching. Scores are explained per field; no student details are shown.">
        <span className="hint">{Object.entries(q.data?.counts || {}).map(([k, v]) => `${k.replace(/_/g, " ").toLowerCase()}: ${v}`).join(" · ")}</span></PageHead>
      <div className="toolbar">
        <div><label>Status</label><select value={status} onChange={(e) => { setStatus(e.target.value); setPage(0); }}>{["REVIEW", "AUTO_LINKED", "CONFIRMED_DUP", "DISMISSED"].map((s) => <option key={s}>{s}</option>)}</select></div>
        <div><label>Timeline class</label><select value={timeline} onChange={(e) => { setTimeline(e.target.value); setPage(0); }}><option value="">All</option>{Object.keys(TL).map((s) => <option key={s}>{s}</option>)}</select></div>
      </div>
      {q.error ? <ErrorBox error={q.error} retry={() => q.refetch()} /> : null}
      <div className="alert info">{q.data?.privacy} Pairs are leads, never verdicts: a confirmed duplicate opens a case and gives both institutions a finding; nothing is penalised automatically.</div>
      {q.isLoading ? <Skeleton rows={6} /> : q.data.items.map((c: any) => (
        <div className="card" key={c.id} style={{ marginBottom: 12 }}>
          <h3><span>Score <b>{c.score.toFixed(3)}</b> <span className={"tag " + TL[c.timeline_class][0]}>{TL[c.timeline_class][1]}</span> {c.flags.map((f: string) => <span key={f} className="tag t-amber" style={{ marginLeft: 4 }}>{f.replace(/_/g, " ").toLowerCase()}</span>)}</span>
            <Status v={c.status === "REVIEW" ? "PENDING" : c.status === "CONFIRMED_DUP" ? "DISPUTED" : c.status === "DISMISSED" ? "WITHDRAWN" : "VERIFIED"} label={c.status.replace(/_/g, " ")} /></h3>
          <div className="cols3">
            <div><b>A</b> · {c.institution_a}<div className="hint">{c.programme_a} · {c.year_a} · {c.status_a.toLowerCase()}</div></div>
            <div><b>B</b> · {c.institution_b}<div className="hint">{c.programme_b} · {c.year_b} · {c.status_b.toLowerCase()}</div></div>
            <div>{bar(c.field_scores.name, "name")}{bar(c.field_scores.dob, "DOB")}{bar(c.field_scores.parent, "parent")}</div>
          </div>
          <div className="row" style={{ marginTop: 8 }}>
            {c.status === "REVIEW" && can("duplicates:review") ? <><button className="btn small" onClick={() => { setSel(c); setDecision("CONFIRM_DUP"); }}>Confirm duplicate</button><button className="btn small ghost" onClick={() => { setSel(c); setDecision("DISMISS"); }}>Dismiss</button></> : c.decision_reason ? <span className="hint">Decision: {c.decision_reason}</span> : null}
            {can("students:reveal") ? <button className="btn small ghost" onClick={() => setRev(c)}>Reveal identity…</button> : null}</div>
        </div>))}
      {q.data && !q.data.items.length ? <Empty>No pairs in this view.</Empty> : null}
      <div className="pager"><span>{q.data?.total ?? "…"} pairs</span><span className="row"><button className="btn small ghost" disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</button><button className="btn small ghost" disabled={!q.data || (page + 1) * 15 >= q.data.total} onClick={() => setPage(page + 1)}>Next</button></span></div>
      {rev ? <RevealDialog cand={rev} onClose={() => setRev(null)} /> : null}
      {sel ? <Modal title={decision === "CONFIRM_DUP" ? "Confirm duplicate" : "Dismiss pair"} onClose={() => setSel(null)}>
        <p>{decision === "CONFIRM_DUP" ? "This opens a case and raises a finding for both institutions. The decision is stored as a label for the matching model." : "A dismissed pair is never flagged again."}</p>
        <Field label="Reason (recorded in the audit trail)"><textarea rows={2} value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
        <div className="row" style={{ marginTop: 12 }}><button className={"btn " + (decision === "CONFIRM_DUP" ? "danger" : "")} disabled={reason.length < 5 || decide.isPending} onClick={() => decide.mutate()}>{decision === "CONFIRM_DUP" ? "Confirm" : "Dismiss"}</button></div></Modal> : null}
    </>
  );
}

/* ------------------------------------------------------------------ AICTE submissions */
export function AicteSubmissions() {
  const { can } = useAuth(); const toast = useToast(); const qc = useQueryClient(); const askStepUp = useStepUp();
  const [status, setStatus] = useState(""); const [year, setYear] = useState("2026-27");
  const list = useQuery({ queryKey: ["asubs", status, year], queryFn: () => api<any>(`/submissions?year=${year}` + (status ? `&status=${status}` : "")), refetchInterval: POLL });
  const [sid, setSid] = useState<string | null>(null); const [notes, setNotes] = useState(""); const [ver, setVer] = useState<any>(null);
  const d = useQuery({ queryKey: ["asub", sid], enabled: !!sid, queryFn: () => api<any>(`/submissions/${sid}`) });
  const refresh = () => { qc.invalidateQueries({ queryKey: ["asubs"] }); qc.invalidateQueries({ queryKey: ["asub"] }); };
  const act = useMutation({ mutationFn: async ({ path, body, step }: any) => api(path, { body: body ?? {}, headers: step ? { "X-Step-Up-Token": await askStepUp() } : undefined }), onSuccess: () => { toast("Done"); refresh(); }, onError: fail(toast) });
  const verify = useMutation({ mutationFn: () => api<any>(`/submissions/${sid}/commitment/verify`, { body: {} }), onSuccess: setVer, onError: fail(toast) });
  return (
    <>
      <PageHead title="Submissions" sub="Review institution submissions in your scope, accept or ask for a review, then countersign the signed cut-off commitment.">
        <select value={year} onChange={(e) => setYear(e.target.value)} aria-label="Academic year"><option>2026-27</option><option>2025-26</option></select>
        <select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Status filter"><option value="">All statuses</option>{["DRAFT", "VALIDATED", "SUBMITTED", "UNDER_REVIEW", "CORRECTED", "ACCEPTED", "LOCKED"].map((s) => <option key={s}>{s}</option>)}</select></PageHead>
      {list.error ? <ErrorBox error={list.error} retry={() => list.refetch()} /> : null}
      <div className="tablewrap"><table><thead><tr><th>Institution</th><th>State</th><th>Status</th><th>Submitted</th><th>Late</th></tr></thead><tbody>
        {list.isLoading ? <tr><td colSpan={5}><Skeleton /></td></tr> : list.data.items.map((s: any) => <tr key={s.id} className="click" onClick={() => { setSid(s.id); setVer(null); }}><td>{s.institution_name}</td><td>{s.state}</td>
          <td><Status v={s.status === "LOCKED" || s.status === "ACCEPTED" ? "VERIFIED" : s.status === "UNDER_REVIEW" ? "PENDING_FIX" : s.status === "SUBMITTED" ? "PENDING" : "QUEUED"} label={s.status.replace(/_/g, " ")} /></td><td>{fmtDate(s.submitted_at)}</td><td>{s.late_flag ? "Yes" : ""}</td></tr>)}
        {list.data && !list.data.items.length ? <tr><td colSpan={5} className="empty">No submissions in this view yet.</td></tr> : null}
      </tbody></table></div>
      {sid ? <Modal title={d.data ? d.data.institution_name : "Submission"} drawer onClose={() => setSid(null)}>
        {!d.data ? <Skeleton /> : <>
          <p><Status v={d.data.status === "SUBMITTED" ? "PENDING" : "VERIFIED"} label={d.data.status.replace(/_/g, " ")} /> {d.data.late_flag ? <span className="tag t-amber">Late (extension)</span> : null}</p>
          <div className="tablewrap"><table><thead><tr><th>Programme</th><th>Approved</th><th>Rows</th><th>Declared</th></tr></thead><tbody>{d.data.readiness.programmes.map((p: any) => <tr key={p.programme_code}><td>{p.name}</td><td>{p.approved}</td><td>{p.live}</td><td>{p.declared ?? "-"} {p.ok ? "✓" : "✗"}</td></tr>)}</tbody></table></div>
          {d.data.status === "SUBMITTED" && can("submissions:decide") ? <>
            <Field label="Notes (mandatory to request a review)"><textarea rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} /></Field>
            <div className="row" style={{ marginTop: 8 }}><button className="btn" onClick={() => act.mutate({ path: `/submissions/${sid}/decide`, body: { decision: "ACCEPT", notes } })}>Accept</button>
              <button className="btn ghost" disabled={notes.length < 5} onClick={() => act.mutate({ path: `/submissions/${sid}/decide`, body: { decision: "REQUEST_REVIEW", notes } })}>Request review</button></div></> : null}
          {d.data.status === "ACCEPTED" ? <p className="hint">{d.data.commitment ? "Waiting for your countersignature." : "Waiting for the institution to sign its cut-off commitment."}</p> : null}
          {d.data.commitment?.status === "PENDING_COUNTERSIGN" && can("commitments:countersign") ? <button className="btn" onClick={() => act.mutate({ path: `/submissions/${sid}/commitment/countersign`, step: true })}>Countersign commitment</button> : null}
          {d.data.commitment ? <div className="card" style={{ marginTop: 14 }}><h3>Commitment v{d.data.commitment.version} <Status v={d.data.commitment.status === "COMPLETE" ? "VERIFIED" : "PENDING"} label={d.data.commitment.status.replace(/_/g, " ")} /></h3>
            <p className="mono" style={{ wordBreak: "break-all" }}>{d.data.commitment.merkle_root}</p><p className="hint">{d.data.commitment.leaf_count} enrolments · RFC 6962 Merkle tree · keys are software keys <Simulated what="a KMS / HSM" /></p>
            {can("audit:read") ? <button className="btn small ghost" onClick={() => verify.mutate()}>Verify commitment against live records</button> : null}
            {ver ? <div className={"alert " + (ver.matches ? "ok" : "err")} style={{ marginTop: 8 }}><b>{ver.matches ? "Records still match the signed root." : "Records changed after the cut-off."}</b>
              <div>Institution signature {ver.institution_signature_valid ? "valid" : "INVALID"} · AICTE countersignature {ver.aicte_countersignature_valid ? "valid" : "pending/invalid"}</div>
              {!ver.matches ? <div>{ver.changed_count} enrolment record(s) differ; recomputed root {ver.recomputed_root.slice(0, 16)}… ≠ signed root {ver.signed_root.slice(0, 16)}…</div> : null}</div> : null}</div> : null}
          <h4>History</h4><ul className="timeline" style={{ padding: 0 }}>{d.data.history.map((h: any, i: number) => <li key={i}><b>{h.to_status.replace(/_/g, " ")}</b> · {fmtDateTime(h.at)} · {h.actor || "system"}{h.note ? <div className="hint">{h.note}</div> : null}</li>)}</ul></>}
      </Modal> : null}
    </>
  );
}

/* ------------------------------------------------------------------ reviews: exceptions, extensions, blocking findings */
export function Reviews() {
  const { can } = useAuth(); const toast = useToast(); const qc = useQueryClient(); const stepUp = useStepUp();
  const [tab, setTab] = useState("exceptions");
  const onb = useQuery({ queryKey: ["onb"], queryFn: () => api<any>("/aicte/onboarding"), enabled: can("onboarding:decide"), refetchInterval: POLL });
  const cor = useQuery({ queryKey: ["aicte-corr"], queryFn: () => api<any>("/aicte/corrections"), enabled: can("corrections:decide"), refetchInterval: POLL });
  const dsr = useQuery({ queryKey: ["aicte-dsr"], queryFn: () => api<any>("/aicte/dsr"), enabled: can("dsr:decide"), refetchInterval: POLL });
  const [dec, setDec] = useState<any>(null); const [why, setWhy] = useState("");
  const decideAny = useMutation({ mutationFn: async (approve: boolean) => {
    const h = { "X-Step-Up-Token": await stepUp() };
    if (dec.kind === "onb") return api<any>(`/aicte/onboarding/${dec.id}/decide`, { body: { approve, reason: why }, headers: h });
    if (dec.kind === "cor") return api<any>(`/aicte/corrections/${dec.id}/decide`, { body: { approve, reason: why }, headers: h });
    return api<any>(`/aicte/dsr/${dec.id}/decide`, { body: { decision: approve ? "FULFIL" : "REJECT", note: why }, headers: h });
  }, onSuccess: (r: any) => { toast(r.admin_username ? `Approved. Administrator ${r.admin_username} created.` : "Decision recorded"); setDec(null); setWhy(""); qc.invalidateQueries({ queryKey: ["onb"] }); qc.invalidateQueries({ queryKey: ["aicte-corr"] }); qc.invalidateQueries({ queryKey: ["aicte-dsr"] }); }, onError: fail(toast) });
  const ex = useQuery({ queryKey: ["exceptions", "all"], queryFn: () => api<any>("/seat-exceptions"), refetchInterval: POLL });
  const ext = useQuery({ queryKey: ["extensions"], queryFn: () => api<any>("/window-extensions"), refetchInterval: POLL });
  const fin = useQuery({ queryKey: ["aicte-findings"], queryFn: () => api<any>("/aicte/findings?limit=100"), refetchInterval: POLL });
  const [sel, setSel] = useState<any>(null); const [reason, setReason] = useState(""); const [seats, setSeats] = useState(""); const [code, setCode] = useState("DATA_VERIFIED");
  const decideEx = useMutation({ mutationFn: (approve: boolean) => api(`/seat-exceptions/${sel.id}/decide`, { body: { approve, reason, approved_seats: seats ? Number(seats) : null } }), onSuccess: () => { toast("Decision recorded"); setSel(null); setReason(""); qc.invalidateQueries({ queryKey: ["exceptions"] }); }, onError: fail(toast) });
  const decideExt = useMutation({ mutationFn: (approve: boolean) => api(`/window-extensions/${sel.id}/decide`, { body: { approve, reason } }), onSuccess: () => { toast("Decision recorded"); setSel(null); setReason(""); qc.invalidateQueries({ queryKey: ["extensions"] }); }, onError: fail(toast) });
  const waive = useMutation({ mutationFn: () => api(`/findings/${sel.id}/waive`, { body: { reason_code: code, note: reason } }), onSuccess: () => { toast("Finding waived"); setSel(null); setReason(""); qc.invalidateQueries({ queryKey: ["aicte-findings"] }); }, onError: fail(toast) });
  const tabs = [["exceptions", "Seat exceptions"], ["extensions", "Window extensions"], ["findings", "Blocking findings"], ...(can("onboarding:decide") ? [["onboarding", "Onboarding"]] : []), ...(can("corrections:decide") ? [["corrections", "Corrections"]] : []), ...(can("dsr:decide") ? [["dsr", "Data requests"]] : [])];
  return (
    <>
      <PageHead title="Reviews" sub="Decisions within your regional scope: exceptions, extensions, blocking findings, onboarding, student corrections and data requests." />
      <div className="row" style={{ marginBottom: 10 }}>{tabs.map(([k, l]) => <button key={k} className={"btn small " + (tab === k ? "" : "ghost")} onClick={() => setTab(k)}>{l}</button>)}</div>
      {tab === "exceptions" ? <div className="tablewrap"><table><thead><tr><th>Institution</th><th>Programme / bucket</th><th>Seats</th><th>Justification</th><th>Status</th><th></th></tr></thead><tbody>
        {ex.data?.items.map((x: any) => <tr key={x.id}><td>{x.institution_name}<div className="hint">{x.state}</div></td><td>{x.programme_name} · {x.bucket_type}</td><td>{x.extra_seats}{x.approved_seats ? ` → ${x.approved_seats}` : ""}</td><td>{x.justification}</td><td><Status v={x.status} /></td>
          <td>{x.status === "PENDING" && can("seat_exceptions:decide") ? <button className="btn small" onClick={() => { setSel({ ...x, kind: "ex" }); setSeats(String(x.extra_seats)); }}>Decide</button> : x.decision_reason}</td></tr>)}
        {ex.data && !ex.data.items.length ? <tr><td colSpan={6} className="empty">No exception requests in your scope.</td></tr> : null}</tbody></table></div> : null}
      {tab === "extensions" ? <div className="tablewrap"><table><thead><tr><th>Institution</th><th>New closing date</th><th>Reason</th><th>Status</th><th></th></tr></thead><tbody>
        {ext.data?.items.map((x: any) => <tr key={x.id}><td>{x.institution_name}<div className="hint">{x.state}</div></td><td>{fmtDate(x.new_closes_at)}</td><td>{x.reason}</td><td><Status v={x.status} /></td>
          <td>{x.status === "PENDING" && can("extensions:decide") ? <button className="btn small" onClick={() => setSel({ ...x, kind: "ext" })}>Decide</button> : x.decision_reason}</td></tr>)}
        {ext.data && !ext.data.items.length ? <tr><td colSpan={5} className="empty">No extension requests in your scope.</td></tr> : null}</tbody></table></div> : null}
      {tab === "findings" ? <><p className="hint">Open Blocker and Error findings stop an institution's submission. A reviewer can waive one with a reason code; every waiver is audited. No student details are shown.</p><div className="tablewrap"><table><thead><tr><th>Institution</th><th>Rule</th><th>Finding</th><th>Claim</th><th></th></tr></thead><tbody>
        {fin.data?.items.map((f: any) => <tr key={f.id}><td>{f.institution_name}<div className="hint">{f.state}</div></td><td><Status v={f.severity} /> <code>{f.rule_code}</code></td><td>{f.message}</td><td><code>{f.enrolment_ref || "-"}</code></td>
          <td>{can("findings:waive") ? <button className="btn small ghost" onClick={() => setSel({ ...f, kind: "fin" })}>Waive</button> : null}</td></tr>)}
        {fin.data && !fin.data.items.length ? <tr><td colSpan={5} className="empty">No open blocking findings in your scope.</td></tr> : null}</tbody></table></div></> : null}
      {tab === "onboarding" ? <><p className="hint">{onb.data?.label}. Approving creates the institution's first administrator.</p><div className="tablewrap"><table><thead><tr><th>Institution</th><th>Nodal officer</th><th>Identity check</th><th>Status</th><th></th></tr></thead><tbody>
        {onb.data?.items.map((x: any) => <tr key={x.id}><td>{x.institution_name}<div className="hint">{x.state} · AICTE ID {x.aicte_id}</div></td><td>{x.officer_name}, {x.designation}<div className="hint">{x.email}</div></td><td><Status v={x.identity_check === "MATCH" ? "PASSED" : "Warning"} label={x.identity_check} /><div className="hint">{x.identity_note}</div></td><td><Status v={x.status} />{x.reason ? <div className="hint">{x.reason}</div> : null}</td>
          <td>{x.status === "PENDING" ? <button className="btn small" onClick={() => setDec({ kind: "onb", id: x.id, title: x.institution_name })}>Decide</button> : null}</td></tr>)}
        {onb.data && !onb.data.items.length ? <tr><td colSpan={5} className="empty">No applications in your scope.</td></tr> : null}</tbody></table></div></> : null}
      {tab === "corrections" ? <><p className="hint">{cor.data?.privacy}</p><div className="tablewrap"><table><thead><tr><th>Institution</th><th>Enrolment</th><th>Field</th><th>Student's note</th><th>Status</th><th></th></tr></thead><tbody>
        {cor.data?.items.map((x: any) => <tr key={x.id}><td>{x.institution_name}<div className="hint">{x.state}</div></td><td><code>{x.enrolment_ref}</code></td><td>{x.field}</td><td>{x.note || "-"}</td><td><Status v={x.status} />{x.reason ? <div className="hint">{x.reason}</div> : null}</td>
          <td>{x.status === "PENDING" ? <button className="btn small" onClick={() => setDec({ kind: "cor", id: x.id, title: `Correction of ${x.field}` })}>Decide</button> : null}</td></tr>)}
        {cor.data && !cor.data.items.length ? <tr><td colSpan={6} className="empty">No correction requests in your scope.</td></tr> : null}</tbody></table></div></> : null}
      {tab === "dsr" ? <><p className="hint">Erasure is refused while the student holds a live enrolment (lawful retention); otherwise personal details are erased and only counts and audit entries remain.</p><div className="tablewrap"><table><thead><tr><th>Student</th><th>Type</th><th>Details</th><th>Live enrolment</th><th>Status</th><th></th></tr></thead><tbody>
        {dsr.data?.items.map((x: any) => <tr key={x.id}><td><code>{x.student_ref}</code></td><td>{x.type}</td><td>{x.details || "-"}</td><td>{x.has_live_enrolment ? "Yes" : "No"}</td><td><Status v={x.status} />{x.decision_note ? <div className="hint">{x.decision_note}</div> : null}</td>
          <td>{["OPEN", "IN_REVIEW"].includes(x.status) ? <button className="btn small" onClick={() => setDec({ kind: "dsr", id: x.id, title: `${x.type} request` })}>Decide</button> : null}</td></tr>)}
        {dsr.data && !dsr.data.items.length ? <tr><td colSpan={6} className="empty">No data requests in your scope.</td></tr> : null}</tbody></table></div></> : null}
      {dec ? <Modal title={dec.title} onClose={() => setDec(null)}>
        <Field label="Reason or note (recorded in the audit trail, min 5 characters)"><textarea rows={3} value={why} onChange={(e) => setWhy(e.target.value)} /></Field>
        <p className="hint">You will be asked for your password to sign the decision.</p>
        <div className="row" style={{ marginTop: 12 }}><button className="btn" disabled={why.length < 5 || decideAny.isPending} onClick={() => decideAny.mutate(true)}>{dec.kind === "dsr" ? "Fulfil" : "Approve"}</button><button className="btn danger" disabled={why.length < 5 || decideAny.isPending} onClick={() => decideAny.mutate(false)}>Reject</button></div></Modal> : null}
      {sel ? <Modal title={sel.kind === "ex" ? "Decide seat exception" : sel.kind === "ext" ? "Decide window extension" : "Waive finding"} onClose={() => setSel(null)}>
        <p className="hint">{sel.institution_name}{sel.justification ? " — " + sel.justification : sel.reason && sel.kind === "ext" ? " — " + sel.reason : sel.message ? " — " + sel.message : ""}</p>
        {sel.kind === "ex" ? <Field label="Seats to approve"><input type="number" min={1} max={sel.extra_seats} value={seats} onChange={(e) => setSeats(e.target.value)} /></Field> : null}
        {sel.kind === "fin" ? <Field label="Reason code"><select value={code} onChange={(e) => setCode(e.target.value)}>{["DATA_VERIFIED", "LEGACY_RECORD", "POLICY_EXCEPTION", "FALSE_POSITIVE"].map((c) => <option key={c}>{c}</option>)}</select></Field> : null}
        <Field label="Reason (recorded in the audit trail)"><textarea rows={2} value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
        <div className="row" style={{ marginTop: 12 }}>
          {sel.kind === "fin" ? <button className="btn" disabled={reason.length < 5} onClick={() => waive.mutate()}>Waive</button>
            : <><button className="btn" disabled={reason.length < 5} onClick={() => (sel.kind === "ex" ? decideEx : decideExt).mutate(true)}>Approve</button><button className="btn danger" disabled={reason.length < 5} onClick={() => (sel.kind === "ex" ? decideEx : decideExt).mutate(false)}>Reject</button></>}</div></Modal> : null}
    </>
  );
}


function RevealDialog({ cand, onClose }: { cand: any; onClose: () => void }) {
  const toast = useToast(); const [people, setPeople] = useState<any>(null); const [needGrant, setNeed] = useState(false);
  const [caseId, setCaseId] = useState(""); const [just, setJust] = useState(""); const [mins, setMins] = useState(30);
  const cases = useQuery({ queryKey: ["cases-pick"], enabled: needGrant, queryFn: () => api<any>("/cases?limit=100") });
  const reveal = useMutation({ mutationFn: () => api<any>(`/duplicates/${cand.id}/reveal`, { body: {} }), onSuccess: (r) => { setPeople(r); setNeed(false); }, onError: (e) => { if ((e as ApiProblem).code === "ELEVATION_REQUIRED") setNeed(true); else fail(toast)(e); } });
  const grant = useMutation({ mutationFn: () => api("/me/elevations", { body: { case_id: caseId, justification: just, duration_minutes: mins } }), onSuccess: () => { toast("Elevation granted"); reveal.mutate(); }, onError: fail(toast) });
  return (
    <Modal title="Reveal identity" onClose={onClose}>
      <p className="hint">Names are decrypted only for a case-linked, time-limited grant. The reveal is audited and appears in the student's access log.</p>
      {!people && !needGrant ? <button className="btn" onClick={() => reveal.mutate()}>Try to reveal</button> : null}
      {needGrant ? <><div className="alert warn">A case-linked elevation is required.</div>
        <Field label="Case (one of the two institutions)"><select value={caseId} onChange={(e) => setCaseId(e.target.value)}><option value="">Select…</option>{cases.data?.items.filter((c: any) => c.status !== "CLOSED").map((c: any) => <option key={c.id} value={c.id}>{c.case_no} · {c.institution_name}</option>)}</select></Field>
        <Field label="Justification (min 30 characters)"><textarea rows={3} value={just} onChange={(e) => setJust(e.target.value)} /></Field>
        <Field label="Minutes (max 240)"><input type="number" min={5} max={240} value={mins} onChange={(e) => setMins(Number(e.target.value))} /></Field>
        <button className="btn" disabled={!caseId || just.length < 30 || grant.isPending} onClick={() => grant.mutate()}>Grant elevation and reveal</button></> : null}
      {people ? <div className="alert warn"><b>Personal data until {fmtDateTime(people.grant_expires_at)}</b>{people.people.map((p: any) => <div key={p.enrolment_ref} style={{ marginTop: 6 }}><code>{p.enrolment_ref}</code> {p.name} · born {p.dob} · parent {p.parent_name || "-"} · {p.mobile_masked || "no mobile"}<div className="hint">{p.institution}, {p.programme} ({p.year})</div></div>)}</div> : null}
    </Modal>
  );
}
