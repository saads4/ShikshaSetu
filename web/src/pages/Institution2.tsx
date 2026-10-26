import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiProblem, daysLeft, fmtDate, fmtDateTime, getToken } from "../api";
import { useAuth } from "../auth";
import { Empty, ErrorBox, Field, Modal, PageHead, Simulated, Skeleton, Status, useStepUp, useToast } from "../ui";

const BASE = (import.meta.env.VITE_API_URL as string) || "http://localhost:8000/api/v1";
const POLL = 15000;

async function download(path: string, filename: string) {
  const r = await fetch(BASE + path, { headers: { Authorization: `Bearer ${getToken()}` } });
  const blob = await r.blob(); const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = filename; a.click(); URL.revokeObjectURL(a.href);
}

/* ------------------------------------------------------------------ uploads */
const STEPS = ["UPLOADING", "SCANNING", "PARSING", "VALIDATING", "READY_TO_COMMIT", "COMMITTING", "COMPLETED"];

export function Uploads() {
  const { can } = useAuth(); const nav = useNavigate();
  const q = useQuery({ queryKey: ["uploads"], queryFn: () => api<any>("/upload-jobs"), refetchInterval: 3000 });
  const [open, setOpen] = useState(false);
  return (
    <>
      <PageHead title="Bulk uploads" sub="Upload a CSV, review the dry-run, then a different admin commits it. Nothing is claimed until commit.">
        {can("uploads:write") ? <button className="btn" onClick={() => setOpen(true)}>New upload</button> : null}
        <button className="btn ghost" onClick={() => download("/templates/enrolment/latest.csv", "enrolment-template-v1.csv")}>Download template</button></PageHead>
      {q.error ? <ErrorBox error={q.error} retry={() => q.refetch()} /> : null}
      <div className="tablewrap"><table><thead><tr><th>File</th><th>Status</th><th>Rows</th><th>Accepted</th><th>Held</th><th>Rejected</th><th>No-op</th><th>Committed</th><th>By</th><th>When</th></tr></thead><tbody>
        {q.isLoading ? <tr><td colSpan={10}><Skeleton /></td></tr> : q.data.items.map((j: any) => (
          <tr key={j.id} className="click" onClick={() => nav(`/institution/uploads/${j.id}`)}><td>{j.filename}</td><td><Status v={j.status === "READY_TO_COMMIT" ? "PENDING" : j.status === "COMPLETED" ? "VERIFIED" : j.status === "PARTIAL" ? "UNCONFIRMED" : j.status === "FAILED" ? "FAILED" : "QUEUED"} label={j.status.replace(/_/g, " ")} /></td>
            <td>{j.total_rows}</td><td>{j.accepted}</td><td>{j.held}</td><td>{j.rejected}</td><td>{j.noop}</td><td>{j.committed}</td><td>{j.created_by_name}</td><td>{fmtDateTime(j.created_at)}</td></tr>))}
        {q.data && !q.data.items.length ? <tr><td colSpan={10} className="empty">No uploads yet. Download the template, fill it in and start a new upload.</td></tr> : null}
      </tbody></table></div>
      {open ? <NewUpload onClose={() => setOpen(false)} onDone={(id) => nav(`/institution/uploads/${id}`)} /> : null}
    </>
  );
}

function NewUpload({ onClose, onDone }: { onClose: () => void; onDone: (id: string) => void }) {
  const [file, setFile] = useState<File | null>(null); const [pre, setPre] = useState<any>(null); const [err, setErr] = useState<any>(null); const [busy, setBusy] = useState(false);
  const tpl = useQuery({ queryKey: ["tpl"], queryFn: () => api<any>("/templates/enrolment/latest") });
  const pick = async (f: File | null) => {
    setFile(f); setPre(null); setErr(null);
    if (!f) return;
    const txt = await f.text(); const lines = txt.replace(/^﻿/, "").split(/\r?\n/).filter((l) => l.trim());
    const header = (lines[0] || "").split(",").map((h) => h.trim());
    const required = tpl.data?.columns.filter((c: any) => c.required).map((c: any) => c.name) || [];
    const missing = required.filter((c: string) => !header.includes(c));
    setPre({ rows: Math.max(lines.length - 1, 0), missing, unknown: header.filter((h) => tpl.data && !tpl.data.columns.some((c: any) => c.name === h)), name: f.name });
  };
  const go = async () => {
    if (!file) return; setBusy(true); setErr(null);
    try {
      const j = await api<any>("/upload-jobs", { body: { filename: file.name } });
      const r = await fetch(`${BASE}/upload-jobs/${j.id}/content`, { method: "PUT", headers: { Authorization: `Bearer ${getToken()}`, "Content-Type": "text/csv" }, body: await file.arrayBuffer() });
      if (!r.ok) throw new ApiProblem(r.status, await r.json().catch(() => ({})));
      onDone(j.id);
    } catch (e) { setErr(e); setBusy(false); }
  };
  return (
    <Modal title="New upload" onClose={onClose}>
      <ol style={{ paddingLeft: 18 }}><li>Download the template and fill it in (CSV only in this prototype; spreadsheets must be exported as CSV, UTF-8).</li><li>Choose the file. A quick check runs in your browser; the server validates again.</li><li>Upload, review the dry-run, then ask a different admin to commit.</li></ol>
      <input type="file" accept=".csv,text/csv" onChange={(e) => pick(e.target.files?.[0] || null)} aria-label="CSV file" />
      {pre ? <div className={"alert " + (pre.missing.length ? "err" : "ok")}>{pre.rows} data rows in <b>{pre.name}</b>.{pre.missing.length ? <> Missing required columns: <b>{pre.missing.join(", ")}</b>.</> : " Required columns are present."}
        {pre.unknown.length ? <div className="hint">Unknown columns will be ignored: {pre.unknown.join(", ")}</div> : null}</div> : null}
      {err ? <ErrorBox error={err} /> : null}
      <p className="hint">Malware scanning is <b>not performed</b> in this prototype (simulated gap). Cells starting with = + - @ are neutralised.</p>
      <div className="row"><button className="btn" disabled={!file || busy || !!pre?.missing.length} onClick={go}>{busy ? "Uploading…" : "Upload and validate"}</button><button className="btn ghost" onClick={onClose}>Cancel</button></div>
    </Modal>
  );
}

export function UploadDetail() {
  const { id } = useParams(); const { user, can } = useAuth(); const toast = useToast(); const qc = useQueryClient(); const askStepUp = useStepUp();
  const j = useQuery({ queryKey: ["upload", id], queryFn: () => api<any>(`/upload-jobs/${id}`), refetchInterval: (q: any) => (["COMPLETED", "PARTIAL", "FAILED", "CANCELLED"].includes(q.state.data?.status) ? false : 2000) });
  const [tab, setTab] = useState("");
  const rows_ = useQuery({ queryKey: ["upload-rows", id, tab, j.data?.status], enabled: !!j.data && !["UPLOADING", "SCANNING", "FAILED"].includes(j.data.status), queryFn: () => api<any>(`/upload-jobs/${id}/rows?limit=100${tab ? "&outcome=" + tab : ""}`) });
  const [open, setOpen] = useState<number | null>(null);
  const commit = useMutation({
    mutationFn: async () => { const t = await askStepUp(); return api(`/upload-jobs/${id}/commit`, { body: {}, headers: { "X-Step-Up-Token": t } }); },
    onSuccess: () => { toast("Commit started"); qc.invalidateQueries({ queryKey: ["upload", id] }); },
    onError: (e) => toast(`${(e as ApiProblem).title}${(e as ApiProblem).detail ? ": " + (e as ApiProblem).detail : ""}`, true),
  });
  const cancel = useMutation({ mutationFn: () => api(`/upload-jobs/${id}/cancel`, { body: {} }), onSuccess: () => { toast("Cancelled"); qc.invalidateQueries({ queryKey: ["upload", id] }); }, onError: (e) => toast((e as ApiProblem).title, true) });
  if (j.isLoading) return <Skeleton rows={6} />;
  if (j.error) return <ErrorBox error={j.error} retry={() => j.refetch()} />;
  const d = j.data, idx = STEPS.indexOf(d.status === "PARTIAL" ? "COMPLETED" : d.status);
  const mine = d.created_by === user?.id;
  const tabs = [["", "All"], ["ACCEPTED", "Accepted"], ["HELD", "Held"], ["REJECTED", "Rejected"], ["NOOP_DUPLICATE", "Duplicates"], ["COMMITTED", "Committed"], ["COMMIT_FAILED", "Commit failed"]];
  return (
    <>
      <PageHead title={d.filename} sub={<>Uploaded by {d.created_by_name} · {fmtDateTime(d.created_at)}{d.committed_by_name ? ` · committed by ${d.committed_by_name}` : ""}</>}><Link to="/institution/uploads" className="btn ghost">← Uploads</Link></PageHead>
      <div className="card" aria-live="polite">
        <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>{["Upload", "Scan", "Parse", "Validate", "Ready", "Commit", "Done"].map((s, i) => <span key={s} className={"tag " + (d.status === "FAILED" ? "t-red" : i < idx ? "t-green" : i === idx ? "t-blue" : "t-grey")}>{i + 1}. {s}</span>)}</div>
        {d.status === "FAILED" ? <div className="alert err" style={{ marginTop: 10 }}><b>The file could not be processed.</b><div>{d.failure_reason}</div></div> : null}
        {d.status === "CANCELLED" ? <div className="alert warn" style={{ marginTop: 10 }}>This upload was cancelled.</div> : null}
        <div className="kpis" style={{ marginTop: 14 }}>
          {[["Rows", d.total_rows], ["Accepted", d.accepted], ["Held", d.held], ["Rejected", d.rejected], ["No-op duplicates", d.noop], ["Committed", d.committed], ["Commit failed", d.failed]].map(([l, v]) => <div className="kpi" key={l as string}><div className="v">{v as number}</div><div className="l">{l}</div></div>)}
        </div>
        <p className="hint">Dry-run uses the live claim rules and the <Simulated what="national APAAR registry" />; <b>no seat is consumed until commit</b>, and the real seat check happens at commit, so an accepted row can still fail with a conflict or a full bucket.
          Malware scan: <b>{d.malware_scan}</b>. Formula-like cells neutralised: {d.scan_result?.formula_cells_neutralised ?? 0}.</p>
        <div className="row">
          {d.status === "READY_TO_COMMIT" && can("uploads:commit") ? (mine ? <span className="alert warn" style={{ margin: 0 }}>You uploaded this file, so a different institution admin must commit it (separation of duties).</span>
            : <button className="btn" disabled={commit.isPending || !d.accepted} onClick={() => commit.mutate()}>Commit {d.accepted} accepted rows</button>) : null}
          {d.status === "READY_TO_COMMIT" && !can("uploads:commit") ? <span className="hint">Operators cannot commit. Ask an institution admin who did not upload this file.</span> : null}
          {["UPLOADING", "SCANNING", "VALIDATING", "READY_TO_COMMIT"].includes(d.status) ? <button className="btn ghost" onClick={() => cancel.mutate()}>Cancel upload</button> : null}
          {(d.rejected + d.held + d.failed) > 0 ? <button className="btn ghost" onClick={() => download(`/upload-jobs/${id}/errors.csv`, "errors.csv")}>Download errors.csv</button> : null}
        </div>
      </div>
      {rows_.data ? <>
        <div className="row" style={{ margin: "16px 0 8px" }}>{tabs.map(([k, l]) => <button key={k} className={"btn small " + (tab === k ? "" : "ghost")} onClick={() => setTab(k)}>{l}</button>)}</div>
        <div className="tablewrap"><table><thead><tr><th>Line</th><th>Student ID</th><th>Name</th><th>Programme</th><th>Outcome</th><th>Issues</th></tr></thead><tbody>
          {rows_.data.items.map((r: any) => (<>
            <tr key={r.row_no} className="click" onClick={() => setOpen(open === r.row_no ? null : r.row_no)}><td>{r.row_no}</td><td>{r.institution_student_id}</td><td>{r.name}</td><td>{r.programme_code} · {r.bucket_type}</td>
              <td><Status v={{ ACCEPTED: "VERIFIED", HELD: "UNCONFIRMED", REJECTED: "REJECTED", NOOP_DUPLICATE: "WITHDRAWN", COMMITTED: "VERIFIED", COMMIT_FAILED: "FAILED", PENDING: "QUEUED" }[r.outcome as string]} label={r.outcome.replace(/_/g, " ")} /></td>
              <td>{r.errors?.length ? `${r.errors.length} issue${r.errors.length > 1 ? "s" : ""}` : "-"}{r.diff?.status === "CHANGED" ? " · changed" : ""}</td></tr>
            {open === r.row_no ? <tr key={r.row_no + "x"}><td colSpan={6} style={{ background: "#f6fbf8" }}>
              {r.errors?.map((e: any, i: number) => <div key={i}><Status v={e.severity || "Error"} /> <b>{e.field || e.code}</b>: {e.message} {e.fix || e.fix_suggestion ? <i className="hint">Fix: {e.fix || e.fix_suggestion}</i> : null}</div>)}
              {r.diff?.status ? <div className="hint">Diff: {r.diff.status}{r.diff.fields ? " — " + Object.entries(r.diff.fields).map(([k, v]: any) => `${k}: "${v.before}" → "${v.after}"`).join("; ") : ""}</div> : null}
              {r.enrolment_id ? <Link to={`/institution/claims/${r.enrolment_id}`}>Open claim</Link> : null}</td></tr> : null}
          </>))}
          {!rows_.data.items.length ? <tr><td colSpan={6} className="empty">No rows in this view.</td></tr> : null}
        </tbody></table></div></> : null}
    </>
  );
}

/* ------------------------------------------------------------------ submissions */
export function Submissions() {
  const { can } = useAuth(); const toast = useToast(); const qc = useQueryClient(); const askStepUp = useStepUp();
  const list = useQuery({ queryKey: ["subs"], queryFn: () => api<any>("/submissions"), refetchInterval: POLL });
  const sid = list.data?.items[0]?.id;
  const d = useQuery({ queryKey: ["sub", sid], enabled: !!sid, queryFn: () => api<any>(`/submissions/${sid}`), refetchInterval: POLL });
  const [decl, setDecl] = useState<Record<string, string>>({}); const [val, setVal] = useState<any>(null); const [ext, setExt] = useState(false);
  useEffect(() => { if (d.data) setDecl(Object.fromEntries(d.data.readiness.programmes.map((p: any) => [p.programme_code, String(d.data.declared?.[p.programme_code] ?? "")]))); }, [d.data?.id, d.data?.status]);
  const refresh = () => { qc.invalidateQueries({ queryKey: ["subs"] }); qc.invalidateQueries({ queryKey: ["sub"] }); };
  const fail = (e: unknown) => toast(`${(e as ApiProblem).title}${(e as ApiProblem).detail ? ": " + (e as ApiProblem).detail : ""}`, true);
  const create = useMutation({ mutationFn: () => api("/submissions", { body: {} }), onSuccess: refresh, onError: fail });
  const act = useMutation({ mutationFn: async ({ path, body, step, method }: any) => api(path, { method: method || "POST", body: body ?? {}, headers: step ? { "X-Step-Up-Token": await askStepUp() } : undefined }), onSuccess: (r: any, v: any) => { if (v.after) v.after(r); refresh(); }, onError: fail });
  if (list.isLoading) return <Skeleton rows={6} />;
  if (list.error) return <ErrorBox error={list.error} retry={() => list.refetch()} />;
  const w = list.data.window;
  return (
    <>
      <PageHead title="Submissions" sub="Declare your totals, pass the readiness check, submit, and sign the cut-off commitment once AICTE accepts.">
        {can("extensions:request") ? <button className="btn ghost" onClick={() => setExt(true)}>Request window extension</button> : null}</PageHead>
      <div className="card"><h3>{w.label}</h3><p style={{ margin: 0 }}>Opens {fmtDate(w.opens_at)} · closes {fmtDate(w.closes_at)}{w.effective_closes_at && w.effective_closes_at !== w.closes_at ? ` (extended to ${fmtDate(w.effective_closes_at)})` : ""} · cut-off {fmtDate(w.cutoff_at)}.
        <span className="hint"> Times are evaluated on the server in UTC and shown in your local time.</span></p></div>
      {!sid ? <div className="card" style={{ marginTop: 16 }}><Empty>No submission has been started for this window.</Empty>{can("submissions:validate") ? <div style={{ textAlign: "center" }}><button className="btn" onClick={() => create.mutate()}>Start submission</button></div> : null}</div> : d.data ? (<>
        <div className="row" style={{ margin: "16px 0 8px" }}><Status v={d.data.status === "ACCEPTED" ? "VERIFIED" : d.data.status === "LOCKED" ? "VERIFIED" : d.data.status === "UNDER_REVIEW" ? "PENDING_FIX" : "PENDING"} label={d.data.status.replace(/_/g, " ")} />
          {d.data.late_flag ? <span className="tag t-amber">Late (extension)</span> : null}<span className="hint">{d.data.blocking_findings} blocking findings · {d.data.warnings_open} warnings awaiting acknowledgement</span></div>
        {d.data.review_notes && d.data.status === "UNDER_REVIEW" ? <div className="alert warn"><b>AICTE asked for a review:</b> {d.data.review_notes}</div> : null}
        <div className="card"><h3>Declared totals vs student records</h3>
          <div className="tablewrap"><table><thead><tr><th>Programme</th><th>Approved seats</th><th>Rows on record</th><th>Declared</th><th></th></tr></thead><tbody>
            {d.data.readiness.programmes.map((p: any) => <tr key={p.programme_code}><td>{p.name}<div className="hint">{p.programme_code}</div></td><td>{p.approved}</td><td>{p.live}</td>
              <td><input type="number" min={0} style={{ width: 90 }} value={decl[p.programme_code] ?? ""} disabled={!["DRAFT", "CORRECTED", "VALIDATED"].includes(d.data.status) || !can("submissions:validate")} onChange={(e) => setDecl({ ...decl, [p.programme_code]: e.target.value })} aria-label={`Declared total for ${p.name}`} /></td>
              <td>{decl[p.programme_code] !== "" && Number(decl[p.programme_code]) === p.live ? <span className="tag t-green">matches</span> : <span className="tag t-amber">differs</span>}</td></tr>)}
          </tbody></table></div>
          <div className="row" style={{ marginTop: 10 }}>
            <button className="btn ghost" disabled={!["DRAFT", "CORRECTED", "VALIDATED"].includes(d.data.status)} onClick={() => act.mutateAsync({ path: `/submissions/${sid}/declared`, method: "PUT", body: { declared: Object.fromEntries(Object.entries(decl).filter(([, v]) => v !== "").map(([k, v]) => [k, Number(v)])) } }).then(() => toast("Declared totals saved")).catch(() => {})}>Save totals</button>
            <button className="btn" disabled={!["DRAFT", "CORRECTED", "VALIDATED"].includes(d.data.status)} onClick={() => act.mutateAsync({ path: `/submissions/${sid}/validate`, body: undefined, after: setVal }).catch(() => {})}>Run readiness check</button>
            {can("findings:ack") ? <button className="btn ghost" disabled={!d.data.warnings_open} onClick={() => api<any>("/findings/acknowledge-all", { body: {} }).then((r) => { toast(`${r.acknowledged} warnings acknowledged`); refresh(); })}>Acknowledge all warnings</button> : null}
            {can("submissions:submit") ? <button className="btn" disabled={d.data.status !== "VALIDATED"} onClick={() => act.mutate({ path: `/submissions/${sid}/submit`, body: undefined })}>Submit to AICTE</button> : null}
            {can("submissions:submit") && d.data.status === "UNDER_REVIEW" ? <button className="btn ghost" onClick={() => act.mutate({ path: `/submissions/${sid}/corrected`, body: undefined })}>Mark corrected</button> : null}
          </div>
          {val ? <div className={"alert " + (val.ok ? "ok" : "err")} style={{ marginTop: 10 }}><b>{val.ok ? "Ready to submit." : "Not ready."}</b>
            {val.blocking_findings.map((f: any) => <div key={f.id}><Status v={f.severity} /> <code>{f.rule_code}</code> {f.message}</div>)}
            {!val.ok ? <div className="hint">Blocking findings need a fix, or AICTE can waive an Error with a reason code. Warnings must be acknowledged before submitting.</div> : null}</div> : null}
        </div>
        <div className="cols2" style={{ marginTop: 16 }}>
          <div className="card"><h3>Cut-off commitment <span className="tag t-grey">Merkle root · ES256</span></h3>
            {d.data.commitment ? <>
              <p>Version {d.data.commitment.version} · {d.data.commitment.leaf_count} enrolments · <Status v={d.data.commitment.status === "COMPLETE" ? "VERIFIED" : "PENDING"} label={d.data.commitment.status.replace(/_/g, " ")} /></p>
              <p className="mono" style={{ wordBreak: "break-all" }}>{d.data.commitment.merkle_root}</p>
              <p className="hint">Signed by {d.data.commitment.institution_kid}{d.data.commitment.aicte_kid ? `, countersigned by ${d.data.commitment.aicte_kid}` : ", awaiting AICTE countersignature"} (software keys, <Simulated what="a KMS / HSM" />). <b>A signature proves integrity, not truth.</b></p></>
              : <p className="hint">Available after AICTE accepts the submission. Locked means a signed cut-off commitment, not frozen data.</p>}
            {can("commitments:sign") && d.data.status === "ACCEPTED" && !d.data.commitment ? <button className="btn" onClick={() => act.mutate({ path: `/submissions/${sid}/commitment/sign`, body: undefined, step: true })}>Sign commitment</button> : null}</div>
          <div className="card"><h3>History</h3><ul className="timeline" style={{ padding: 0 }}>{d.data.history.map((h: any, i: number) => <li key={i}><b>{h.to_status.replace(/_/g, " ")}</b> · {fmtDateTime(h.at)} · {h.actor || "system"}{h.note ? <div className="hint">{h.note}</div> : null}</li>)}</ul>
            {d.data.extensions.length ? <><h4>Window extensions</h4>{d.data.extensions.map((x: any) => <div key={x.id}><Status v={x.status} /> to {fmtDate(x.new_closes_at)} — {x.reason}</div>)}</> : null}</div>
        </div></>) : <Skeleton />}
      {ext ? <ExtensionForm onClose={() => setExt(false)} done={refresh} /> : null}
    </>
  );
}

function ExtensionForm({ onClose, done }: { onClose: () => void; done: () => void }) {
  const toast = useToast(); const [date, setDate] = useState(""); const [reason, setReason] = useState(""); const [err, setErr] = useState<any>(null);
  const m = useMutation({ mutationFn: () => api("/window-extensions", { body: { new_closes_at: new Date(date + "T23:59:00").toISOString(), reason } }), onSuccess: () => { toast("Extension requested"); done(); onClose(); }, onError: setErr });
  return (
    <Modal title="Request a window extension" onClose={onClose}>
      <p>AICTE decides within the reviewer's regional scope. An approved extension raises only your institution's closing time.</p>
      <Field label="New closing date"><input type="date" value={date} onChange={(e) => setDate(e.target.value)} /></Field>
      <Field label="Reason (min 10 characters)"><textarea rows={3} value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
      {err ? <ErrorBox error={err} /> : null}
      <div className="row" style={{ marginTop: 12 }}><button className="btn" disabled={!date || reason.length < 10 || m.isPending} onClick={() => m.mutate()}>Send request</button></div>
    </Modal>
  );
}

/* ------------------------------------------------------------------ transfers */
export function Transfers() {
  const q = useQuery({ queryKey: ["transfers"], queryFn: () => api<any>("/transfers"), refetchInterval: POLL });
  if (q.isLoading) return <Skeleton rows={5} />;
  if (q.error) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  return (
    <>
      <PageHead title="Transfers" sub="A transfer-out releases the seat. The receiving institution's claim for the same student matches it automatically. A missing counterpart becomes a lead, not a violation." />
      <div className="tablewrap"><table><thead><tr><th>Direction</th><th>Student</th><th>From</th><th>To</th><th>Status</th><th>Effective</th></tr></thead><tbody>
        {q.data.items.map((t: any) => <tr key={t.id}><td>{t.we_released ? "Out" : "In"}</td><td><code>{t.we_released ? t.out_ref : t.in_ref}</code></td><td>{t.out_institution}</td><td>{t.in_institution || "-"}</td>
          <td><Status v={t.status === "MATCHED" ? "VERIFIED" : t.status === "ORPHAN_LEAD" ? "UNCONFIRMED" : "PENDING"} label={t.status.replace(/_/g, " ")} />{t.status === "ORPHAN_LEAD" ? <div className="hint">No counterpart after 30 days (a lead)</div> : null}</td><td>{fmtDate(t.effective_date_out)}</td></tr>)}
        {!q.data.items.length ? <tr><td colSpan={6} className="empty">No transfers. Open an admitted student's claim and choose “Transfer out”.</td></tr> : null}
      </tbody></table></div>
    </>
  );
}
