import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, apiRaw, ApiProblem, daysLeft, fmtDate, fmtDateTime, uuid } from "../api";
import { useAuth } from "../auth";
import { TrustCard } from "./Intel";
import { AsOf, ConfirmButton, Empty, ErrorBox, Field, LockTimeline, Modal, PageHead, SeatBar, Simulated, Skeleton, Status, useToast } from "../ui";

const REFRESH = 15000;

function useLookups() { return useQuery({ queryKey: ["lookups"], queryFn: () => api<any>("/ref/lookups"), staleTime: 3600000 }); }
function Tile({ v, l, s }: { v: any; l: string; s?: string }) { return <div className="kpi"><div className="v">{v}</div><div className="l">{l}</div>{s ? <div className="s">{s}</div> : null}</div>; }

/* ------------------------------------------------------------------ dashboard */
export function InstDashboard() {
  const q = useQuery({ queryKey: ["inst-dash"], queryFn: () => api<any>("/dashboards/institution"), refetchInterval: REFRESH });
  if (q.isLoading) return <Skeleton rows={8} />;
  if (q.error) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  const d = q.data, f = d.funnel || {};
  const byProg: Record<string, any[]> = {};
  d.seats.forEach((s: any) => (byProg[s.programme_name] ||= []).push(s));
  const open = d.findings.filter((x: any) => x.status === "OPEN").reduce((a: number, x: any) => a + x.count, 0);
  const seatsTotal = d.seats.reduce((a: number, s: any) => a + s.cap + s.approved_extra, 0), occ = d.seats.reduce((a: number, s: any) => a + s.occupied, 0);
  return (
    <>
      <PageHead title="Dashboard" sub={`${d.institution.name} · ${d.academic_year}`}><AsOf at={d.as_of} onRefresh={() => q.refetch()} /></PageHead>
      <div className="kpis">
        <Tile v={`${occ}/${seatsTotal}`} l="Seats occupied" s="CLAIMED or ADMITTED" />
        <Tile v={f.verified ?? 0} l="Verified" s="all three locks agree" />
        <Tile v={f.pending ?? 0} l="Awaiting confirmation" s="Lock 3 outstanding" />
        <Tile v={f.pending_fix ?? 0} l="Identity fixes due" s="with deadlines" />
        <Tile v={f.unconfirmed ?? 0} l="Unconfirmed" s="deadline passed (flag only)" />
        <Tile v={f.disputed ?? 0} l="Disputed" s="opened as cases" />
        <Tile v={f.pending_registry ?? 0} l="Pending registry" s="registry unavailable" />
        <Tile v={open} l="Open findings" s={`${d.stale_claims ?? 0} stale claims (>45 days)`} />
      </div>
      <TrustCard />
      <div className="cols2">
        <div className="card"><h3>Claims awaiting confirmation</h3>
          {d.awaiting_confirmation.length ? <div className="tablewrap"><table><thead><tr><th>Student ID</th><th>Reference</th><th>Days left</th></tr></thead><tbody>
            {d.awaiting_confirmation.map((a: any) => <tr key={a.id} className="click" onClick={() => (window.location.href = `/institution/claims/${a.id}`)}><td>{a.institution_student_id}</td><td><code>{a.enrolment_ref}</code></td><td>{daysLeft(a.deadline_at)}</td></tr>)}
          </tbody></table></div> : <Empty>Nothing is waiting on a student right now.</Empty>}
        </div>
        <div className="card"><h3>Recent notifications <Simulated what="an SMS / email gateway" /></h3>
          {d.notifications.length ? <ul style={{ margin: 0, paddingLeft: 18 }}>{d.notifications.map((n: any) => <li key={n.id}>{n.channel} · {n.template_code} · <Status v={n.status} /> · {fmtDateTime(n.created_at)}</li>)}</ul> : <Empty>No notifications yet.</Empty>}
        </div>
      </div>
      <h2 style={{ marginTop: 22 }}>Seat ledger</h2>
      <div className="tiles">{Object.entries(byProg).map(([name, list]) => (
        <div className="card" key={name}><h3>{name}</h3><div className="hint">{list[0].level}</div>
          {list.map((s: any) => <div key={s.bucket_type}><div style={{ fontSize: 12, fontWeight: 700 }}>{s.bucket_type}</div><SeatBar occupied={s.occupied} cap={s.cap} extra={s.approved_extra} cumulative={s.cumulative_admitted} /></div>)}
          <Link to={`/institution/programmes/${list[0].programme_id}`}>Open ledger →</Link></div>
      ))}</div>
      <p className="hint">Occupied = CLAIMED or ADMITTED. Cumulative admitted = ever ADMITTED and never reduced. Verified = occupied with all three locks agreeing.</p>
    </>
  );
}

/* ------------------------------------------------------------------ programmes / ledger */
export function Programmes() {
  const q = useQuery({ queryKey: ["programmes"], queryFn: () => api<any>("/programmes"), refetchInterval: REFRESH });
  if (q.isLoading) return <Skeleton rows={6} />;
  if (q.error) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  return (
    <>
      <PageHead title="Seat ledger" sub={`Programmes and seat buckets for ${q.data.academic_year}`} />
      <div className="tiles">{q.data.items.map((p: any) => (
        <div className="card" key={p.id}><h3>{p.name}</h3><div className="hint">{p.code} · {p.level} · {p.mode.replace("_", " ").toLowerCase()}{p.lateral_entry ? " · lateral entry" : ""}</div>
          {p.buckets.map((b: any) => <div key={b.bucket_type}><div style={{ fontSize: 12, fontWeight: 700 }}>{b.bucket_type}</div><SeatBar occupied={b.occupied} cap={b.cap} extra={b.approved_extra} cumulative={b.cumulative_admitted} /></div>)}
          <Link to={`/institution/programmes/${p.id}`}>Ledger entries →</Link></div>
      ))}</div>
    </>
  );
}

export function ProgrammeLedger() {
  const { id } = useParams();
  const q = useQuery({ queryKey: ["ledger", id], queryFn: () => api<any>(`/programmes/${id}/ledger`), refetchInterval: REFRESH });
  const [openReq, setOpenReq] = useState(false);
  const { can } = useAuth();
  if (q.isLoading) return <Skeleton rows={6} />;
  if (q.error) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  return (
    <>
      <PageHead title="Ledger entries" sub="Append-only: occupied always equals debits minus releases."><Link to="/institution/programmes" className="btn ghost">← Seat ledger</Link>
        {can("seat_exceptions:request") ? <button className="btn" onClick={() => setOpenReq(true)}>Request exception</button> : null}</PageHead>
      <div className="row" style={{ marginBottom: 12 }}>{q.data.invariant.map((i: any) => <span key={i.bucket_type} className={"tag " + (i.ok ? "t-green" : "t-red")} title={`occupied ${i.occupied}, ledger sum ${i.ledger_sum}`}>{i.bucket_type}: {i.ok ? "invariant holds" : "MISMATCH"}</span>)}</div>
      <div className="tablewrap"><table><thead><tr><th>#</th><th>Bucket</th><th>Entry</th><th>Δ</th><th>Occupied after</th><th>Enrolment</th><th>Reason</th><th>When</th></tr></thead><tbody>
        {q.data.entries.map((e: any) => <tr key={e.id}><td>{e.id}</td><td>{e.bucket_type}</td><td><Status v={e.entry_type === "DEBIT" ? "CLAIMED" : e.entry_type === "RELEASE" ? "WITHDRAWN" : "APPROVED"} label={e.entry_type} /></td><td>{e.delta > 0 ? "+" : ""}{e.delta}</td><td>{e.occupied_after}</td><td><code>{e.enrolment_ref || "-"}</code></td><td>{e.reason || ""}</td><td>{fmtDateTime(e.created_at)}</td></tr>)}
      </tbody></table></div>
      {openReq ? <ExceptionForm onClose={() => setOpenReq(false)} /> : null}
    </>
  );
}

/* ------------------------------------------------------------------ claims list */
export function Claims() {
  const nav = useNavigate();
  const { can } = useAuth();
  const [q, setQ] = useState(""); const [status, setStatus] = useState(""); const [overall, setOverall] = useState(""); const [open, setOpen] = useState(false);
  const [cursors, setCursors] = useState<(string | null)[]>([null]);
  const params = useMemo(() => { const p = new URLSearchParams({ limit: "25", include_total: "true" }); if (q) p.set("q", q); if (status) p.set("status", status); if (overall) p.set("overall", overall); const c = cursors[cursors.length - 1]; if (c) p.set("cursor", c); return p.toString(); }, [q, status, overall, cursors]);
  const data = useQuery({ queryKey: ["claims", params], queryFn: () => api<any>("/enrolments?" + params), refetchInterval: REFRESH, placeholderData: (x: any) => x });
  useEffect(() => setCursors([null]), [q, status, overall]);
  return (
    <>
      <PageHead title="Claims" sub="Enrolments claimed by your institution this cycle.">{can("claims:write") ? <button className="btn" onClick={() => setOpen(true)}>New claim</button> : <span className="disabled-note">Operators can validate claims but not create them.</span>}
        {!can("claims:write") && can("claims:validate") ? <button className="btn ghost" onClick={() => setOpen(true)}>Validate a claim</button> : null}</PageHead>
      <div className="toolbar">
        <div className="grow"><label htmlFor="q">Search name token, student ID or reference</label><input id="q" type="text" value={q} onChange={(e) => setQ(e.target.value)} placeholder="e.g. Aarav" /></div>
        <div><label htmlFor="st">Status</label><select id="st" value={status} onChange={(e) => setStatus(e.target.value)}><option value="">All</option>{["CLAIMED", "ADMITTED", "WITHDRAWN"].map((s) => <option key={s}>{s}</option>)}</select></div>
        <div><label htmlFor="ov">Verification</label><select id="ov" value={overall} onChange={(e) => setOverall(e.target.value)}><option value="">All</option>{["PENDING", "VERIFIED", "PENDING_FIX", "UNCONFIRMED", "DISPUTED"].map((s) => <option key={s}>{s}</option>)}</select></div>
      </div>
      {data.error ? <ErrorBox error={data.error} retry={() => data.refetch()} /> : null}
      <div className="tablewrap"><table><thead><tr><th>Student</th><th>ID</th><th>Programme</th><th>Bucket</th><th>Status</th><th>Verification</th><th>Claimed</th></tr></thead><tbody>
        {data.isLoading ? <tr><td colSpan={7}><Skeleton /></td></tr> : data.data?.items.map((e: any) => (
          <tr key={e.id} className="click" onClick={() => nav(`/institution/claims/${e.id}`)}>
            <td><b>{e.name || "-"}</b><div className="hint"><code>{e.enrolment_ref}</code></div></td><td>{e.institution_student_id}</td><td>{e.programme_code}</td><td>{e.bucket_type}</td>
            <td><Status v={e.status} /></td><td><Status v={e.overall_state} />{e.identity_status === "PENDING_VERIFICATION" ? <> <Status v="PENDING_VERIFICATION" /></> : null}</td><td>{fmtDate(e.claimed_at)}</td></tr>))}
        {data.data && !data.data.items.length ? <tr><td colSpan={7} className="empty">No claims match these filters. {can("claims:write") ? "Create a new claim to get started." : ""}</td></tr> : null}
      </tbody></table></div>
      <div className="pager"><span>{data.data?.total ?? "…"} claims</span><span className="row">
        <button className="btn small ghost" disabled={cursors.length <= 1} onClick={() => setCursors((c) => c.slice(0, -1))}>Previous</button>
        <button className="btn small ghost" disabled={!data.data?.next_cursor} onClick={() => setCursors((c) => [...c, data.data.next_cursor])}>Next</button></span></div>
      {open ? <NewClaim onClose={() => setOpen(false)} /> : null}
    </>
  );
}

/* ------------------------------------------------------------------ new claim drawer with live validation */
const blank = { programme_code: "", bucket_type: "REGULAR", apaar_id: "", institution_student_id: "", name: "", dob: "", gender: "M", category: "GEN", citizenship_type: "INDIAN", parent_name: "", mobile: "", noApaar: false, provisional_type: "NOGEN", document_number: "", admit_now: false };

function NewClaim({ onClose }: { onClose: () => void }) {
  const { can } = useAuth();
  const qc = useQueryClient(); const toast = useToast(); const nav = useNavigate();
  const [f, setF] = useState<any>(blank);
  const [verdict, setVerdict] = useState<any>(null);
  const [preview, setPreview] = useState<any>(null);
  const progs = useQuery({ queryKey: ["programmes"], queryFn: () => api<any>("/programmes") });
  const lk = useLookups();
  const prog = progs.data?.items.find((p: any) => p.code === f.programme_code);
  const bucket = prog?.buckets.find((b: any) => b.bucket_type === f.bucket_type);
  const set = (k: string, v: any) => setF((x: any) => ({ ...x, [k]: v }));

  const body = () => ({
    programme_code: f.programme_code, bucket_type: f.bucket_type, institution_student_id: f.institution_student_id, name: f.name, dob: f.dob, gender: f.gender,
    category: f.category, citizenship_type: f.citizenship_type, parent_name: f.parent_name || null, mobile: f.mobile || null, admit_now: f.admit_now,
    ...(f.noApaar ? { provisional: { type: f.provisional_type, document_number: f.document_number || null } } : { apaar_id: f.apaar_id || null }),
  });
  const ready = f.programme_code && f.institution_student_id && f.name && f.dob && (f.noApaar || f.apaar_id.replace(/\D/g, "").length >= 12);
  const aadhaarHint = !f.noApaar && /^\s*\d{4}\s?\d{4}\s?\d{4}\s*$/.test(f.apaar_id) && !f.apaar_id.replace(/\s/g, "").startsWith("99");

  useEffect(() => {              // debounced live validate panel (REQ-GATE-13): no write, no seat consumed
    if (!ready) { setPreview(null); return; }
    const t = setTimeout(async () => {
      const r = await apiRaw("/claims:validate", body()).catch(() => null);
      setPreview(r);
    }, 600);
    return () => clearTimeout(t);
  }, [JSON.stringify(f)]);

  const submit = useMutation({
    mutationFn: async () => apiRaw("/claims", body(), { "Idempotency-Key": uuid() }),
    onSuccess: (r) => { setVerdict(r); qc.invalidateQueries({ queryKey: ["claims"] }); qc.invalidateQueries({ queryKey: ["programmes"] }); qc.invalidateQueries({ queryKey: ["inst-dash"] }); },
  });

  const fieldErr = (field: string) => (preview?.data?.errors || []).find((e: any) => e.field === field)?.message;
  const outcomeLine = () => {
    if (!preview) return ready ? "Checking…" : "Fill in the required fields to preview the outcome.";
    const d = preview.data;
    if (preview.status === 200 && d.outcome === "DUPLICATE_SAME_INSTITUTION") return "This student already has a live claim at your institution.";
    if (preview.status === 200) return d.verification.overall === "PENDING_FIX" ? "Will be claimed, but identity fixes are required." : d.identity_status === "PENDING_VERIFICATION" ? "Will be claimed; the registry is unavailable so verification is pending." : "Will claim a seat.";
    if (preview.status === 409 && d.code === "CLAIM_CONFLICT") return "Conflict: another institution holds this student this cycle.";
    if (preview.status === 409) return "Bucket is full.";
    if (preview.status === 403) return "Your role cannot validate claims.";
    return d.title || "Needs attention.";
  };

  if (verdict) {
    const { status, data } = verdict;
    return (
      <Modal title={status === 201 ? "Seat claimed" : status === 200 ? "Already claimed" : status === 409 ? (data.code === "SEAT_LIMIT" ? "Bucket is full" : "Conflict") : "Needs attention"} onClose={onClose}>
        {status === 201 && <>
          <div className="alert ok">{data.message}</div>
          <LockTimeline v={{ lock2: data.verification.lock2, lock3: data.verification.lock3, channel: null }} deadline={data.verification.deadline_at} />
          {data.warnings?.map((w: any) => <div key={w.code} className="alert warn">{w.message}</div>)}
          {data.fix_list?.length ? <div className="alert warn"><b>Identity fixes needed</b><ul>{data.fix_list.map((x: any, i: number) => <li key={i}><b>{x.field}</b>: {x.message} <i>{x.fix}</i></li>)}</ul></div> : null}
          {data.provisional_token ? <div className="alert info">Provisional ID: <code>{data.provisional_token}</code> (kept until the student's real APAAR ID is added).</div> : null}
          <p className="hint">The registry and the confirmation SMS are <Simulated what="national systems" />. See the code in the Dev SMS Inbox.</p>
          <div className="row"><button className="btn" onClick={() => { onClose(); nav(`/institution/claims/${data.enrolment_id}`); }}>Open claim</button><button className="btn ghost" onClick={() => { setVerdict(null); setF(blank); }}>Claim another</button></div></>}
        {status === 200 && <><div className="alert info">{data.message}</div><button className="btn" onClick={() => { onClose(); nav(`/institution/claims/${data.enrolment_id}`); }}>Open the existing claim</button></>}
        {status === 409 && data.code === "CLAIM_CONFLICT" && <>
          <div className="alert err">{data.detail}</div><p>Resolution reference: <code>{data.resolution_ref}</code></p>
          {data.other_institution_contact ? <p>Contact the other institution's public nodal contact: <b>{data.other_institution_contact.contact}</b>, {data.other_institution_contact.institution} · {data.other_institution_contact.email} · {data.other_institution_contact.phone}</p> : null}
          <p className="hint">No student details are shared across institutions.</p></>}
        {status === 409 && data.code === "SEAT_LIMIT" && <>
          <div className="alert err">{data.bucket} bucket: {data.occupied}/{data.cap + data.approved_extra} seats occupied. No seat was consumed.</div>
          {can("seat_exceptions:request") ? <button className="btn" onClick={() => { onClose(); nav("/institution/exceptions?new=1"); }}>Request an exception</button> : null}</>}
        {status >= 400 && status !== 409 && <ErrorBox error={new ApiProblem(status, data)} />}
        {status >= 400 && <button className="btn ghost" style={{ marginTop: 10 }} onClick={() => setVerdict(null)}>Back to the form</button>}
      </Modal>
    );
  }

  return (
    <Modal title={can("claims:write") ? "New claim" : "Validate a claim"} onClose={onClose} drawer>
      <p className="hint">The validation panel below runs the full claim logic without consuming a seat.</p>
      <div className="cols2">
        <Field label="Programme"><select value={f.programme_code} onChange={(e) => { set("programme_code", e.target.value); set("bucket_type", "REGULAR"); }}><option value="">Select…</option>{progs.data?.items.map((p: any) => <option key={p.code} value={p.code}>{p.name}</option>)}</select></Field>
        <Field label="Seat bucket" hint={bucket ? `${bucket.occupied}/${bucket.cap + bucket.approved_extra} occupied${bucket.pct >= 90 ? " — nearly full" : ""}` : ""}>
          <select value={f.bucket_type} onChange={(e) => set("bucket_type", e.target.value)}>{(prog?.buckets || [{ bucket_type: "REGULAR" }]).map((b: any) => <option key={b.bucket_type}>{b.bucket_type}</option>)}</select></Field>
      </div>
      <label style={{ display: "flex", gap: 8, alignItems: "center", fontWeight: 600 }}><input type="checkbox" style={{ width: "auto" }} checked={f.noApaar} onChange={(e) => set("noApaar", e.target.checked)} /> No APAAR ID (use a provisional ID)</label>
      {f.noApaar ? <div className="cols2">
        <Field label="Provisional type"><select value={f.provisional_type} onChange={(e) => set("provisional_type", e.target.value)}>{["NOGEN", "REFUSED", "FOREIGN"].map((t) => <option key={t}>{t}</option>)}</select></Field>
        <Field label="Passport / OCI number (foreign nationals)" hint="Stored only as a keyed hash plus last four characters."><input value={f.document_number} onChange={(e) => set("document_number", e.target.value)} /></Field></div>
        : <Field label="APAAR ID" error={aadhaarHint ? "Aadhaar-like value — do not enter. Aadhaar must never be typed here." : fieldErr("apaar_id")} hint="12 digits. Spaces and hyphens are ignored."><input value={f.apaar_id} onChange={(e) => set("apaar_id", e.target.value)} inputMode="numeric" /></Field>}
      <div className="cols2">
        <Field label="Institution student ID" error={fieldErr("institution_student_id")}><input value={f.institution_student_id} onChange={(e) => set("institution_student_id", e.target.value)} /></Field>
        <Field label="Full name (as on certificate)"><input value={f.name} onChange={(e) => set("name", e.target.value)} /></Field>
        <Field label="Date of birth" error={fieldErr("dob")}><input type="date" value={f.dob} onChange={(e) => set("dob", e.target.value)} /></Field>
        <Field label="Parent / guardian name"><input value={f.parent_name} onChange={(e) => set("parent_name", e.target.value)} /></Field>
        <Field label="Gender" error={fieldErr("gender")}><select value={f.gender} onChange={(e) => set("gender", e.target.value)}>{["M", "F", "O"].map((g) => <option key={g}>{g}</option>)}</select></Field>
        <Field label="Category" error={fieldErr("category")}><select value={f.category} onChange={(e) => set("category", e.target.value)}>{(lk.data?.category || ["GEN"]).map((g: string) => <option key={g}>{g}</option>)}</select></Field>
        <Field label="Citizenship"><select value={f.citizenship_type} onChange={(e) => set("citizenship_type", e.target.value)}>{(lk.data?.citizenship_type || ["INDIAN"]).map((g: string) => <option key={g}>{g}</option>)}</select></Field>
        <Field label="Mobile (optional)" hint="Used only if the registry holds no number (low-trust channel)."><input value={f.mobile} onChange={(e) => set("mobile", e.target.value)} inputMode="tel" /></Field>
      </div>
      {can("claims:write") ? <label style={{ display: "flex", gap: 8, alignItems: "center", fontWeight: 600 }}><input type="checkbox" style={{ width: "auto" }} checked={f.admit_now} onChange={(e) => set("admit_now", e.target.checked)} /> Admit now (claim and admit in one step)</label> : null}
      <div className={"alert " + (preview?.status === 200 && preview.data.outcome !== "DUPLICATE_SAME_INSTITUTION" ? "ok" : preview && preview.status >= 400 ? "err" : "info")} aria-live="polite" style={{ marginTop: 14 }}>
        <b>Live check:</b> {outcomeLine()}
        {preview?.data?.errors?.length ? <ul>{preview.data.errors.map((e: any, i: number) => <li key={i}><b>{e.field}</b>: {e.message} <i>{e.fix_suggestion}</i></li>)}</ul> : null}
        {preview?.status === 200 && preview.data.fix_list?.length ? <ul>{preview.data.fix_list.map((e: any, i: number) => <li key={i}><b>{e.field}</b>: {e.message} <i>{e.fix}</i></li>)}</ul> : null}
        {preview?.data?.warnings?.map((w: any) => <div key={w.code}>⚠ {w.message}</div>)}
      </div>
      <div className="row" style={{ marginTop: 12 }}>
        {can("claims:write") ? <button className="btn" disabled={!ready || submit.isPending} onClick={() => submit.mutate()}>{submit.isPending ? "Claiming…" : "Claim seat"}</button> : null}
        <button className="btn ghost" onClick={onClose}>Cancel</button></div>
      {submit.error ? <ErrorBox error={submit.error} /> : null}
    </Modal>
  );
}

/* ------------------------------------------------------------------ claim detail */
export function ClaimDetail() {
  const { id } = useParams();
  const { can } = useAuth(); const toast = useToast(); const qc = useQueryClient();
  const q = useQuery({ queryKey: ["claim", id], queryFn: () => api<any>(`/enrolments/${id}`), refetchInterval: REFRESH });
  const [modal, setModal] = useState<string | null>(null);
  const [form, setForm] = useState<any>({});
  const act = useMutation({
    mutationFn: ({ path, body }: { path: string; body?: any }) => api(`/enrolments/${id}/${path}`, { body: body ?? {} }),
    onSuccess: (_d, v) => { toast("Done: " + v.path.replace(/\//g, " ")); setModal(null); qc.invalidateQueries({ queryKey: ["claim", id] }); qc.invalidateQueries({ queryKey: ["claims"] }); qc.invalidateQueries({ queryKey: ["fixes"] }); },
    onError: (e) => toast((e as ApiProblem).title + (((e as ApiProblem).detail) ? ": " + (e as ApiProblem).detail : ""), true),
  });
  if (q.isLoading) return <Skeleton rows={8} />;
  if (q.error) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  const d = q.data, v = d.verification, live = d.status === "CLAIMED" || d.status === "ADMITTED";
  return (
    <>
      <PageHead title={d.student.name || "Claim"} sub={<><code>{d.enrolment_ref}</code> · {d.programme.name} · {d.bucket_type} · {d.academic_year}</>}>
        <Status v={d.status} /><Status v={v.overall_state} /><Link to="/institution/claims" className="btn ghost">← Claims</Link></PageHead>
      <div className="card"><h3>Triple-Lock verification</h3><LockTimeline v={v} deadline={v.deadline_at} />
        {d.confirmation ? <p className="hint">Code sent to {d.confirmation.target_masked} ({String(d.confirmation.channel).replace(/_/g, " ").toLowerCase()}) <Simulated what="an SMS gateway" /> · attempts used {d.confirmation.attempts}/5 · {d.confirmation.for_guardian ? "guardian must confirm (minor)" : ""}</p> : <p className="hint">No confirmation code was sent (no mobile on record) — use assisted confirmation.</p>}
        {live ? <div className="row">
          {can("enrolments:manage") && d.status === "CLAIMED" ? <ConfirmButton label="Admit" consequence="Marks this student as ADMITTED today and counts towards cumulative admitted." onConfirm={() => act.mutate({ path: "admit" })} /> : null}
          {can("confirmation:resend") && v.lock3_status !== "CONFIRMED" ? <button className="btn small ghost" onClick={() => act.mutate({ path: "confirmation/resend" })}>Resend code</button> : null}
          {can("confirmation:assist") && v.lock3_status !== "CONFIRMED" ? <button className="btn small ghost" onClick={() => setModal("assist")}>Assisted confirmation</button> : null}
          {can("enrolments:manage") ? <button className="btn small ghost" onClick={() => { setForm({ name: d.student.name, dob: d.student.dob, apaar_id: "", reason: "" }); setModal("correct"); }}>Edit identity &amp; revalidate</button> : null}
          {can("transfers:write") && d.status === "ADMITTED" ? <ConfirmButton label="Transfer out" consequence="Releases this seat and records a transfer-out. If the student is claimed by another institution it is matched automatically; otherwise it becomes a lead after 30 days." onConfirm={() => act.mutate({ path: "transfer-out" })} /> : null}
          {can("enrolments:manage") ? <button className="btn small danger" onClick={() => { setForm({ reason_code: "STUDENT_WITHDREW", note: "" }); setModal("withdraw"); }}>Withdraw</button> : null}
        </div> : null}
      </div>
      <div className="cols2" style={{ marginTop: 16 }}>
        <div className="card"><h3>Student record <span className="tag t-grey">decrypted for your institution · audited</span></h3>
          <table><tbody>{[["Name", d.student.name], ["Date of birth", d.student.dob], ["Parent / guardian", d.student.parent_name || "-"], ["Mobile", d.student.mobile_masked || "-"], ["Email", d.student.email || "-"], ["District", d.student.district || "-"], ["Gender / category", `${d.student.gender} / ${d.student.category}`], ["Minor", d.student.is_minor ? "Yes — guardian confirms" : "No"], ["Student ID", d.institution_student_id], ["Concurrency class", d.concurrency_class]].map(([k, val]) => <tr key={k as string}><td style={{ color: "var(--muted)" }}>{k}</td><td>{val}</td></tr>)}</tbody></table>
          {d.provisional ? <div className="alert info">Provisional ID <code>{d.provisional.token}</code> ({d.provisional.type}) — compliance deadline {fmtDate(d.provisional.compliance_deadline)}.</div> : null}
        </div>
        <div className="card"><h3>Identity diagnosis <Simulated what="the national APAAR registry" /></h3>
          <Status v={v.identity_status} />
          {d.diagnoses.length ? <ul>{d.diagnoses.map((x: any, i: number) => <li key={i}><b>{x.field}</b>: {x.message}<div className="hint">Suggested fix: {x.suggested_fix?.fix}</div></li>)}</ul> : <p className="hint">No field mismatches.</p>}
          {v.fix_deadline_at && v.overall_state === "PENDING_FIX" ? <div className="alert warn">Fix by {fmtDate(v.fix_deadline_at)} ({(daysLeft(v.fix_deadline_at) ?? 0) < 0 ? `overdue by ${Math.abs(daysLeft(v.fix_deadline_at) ?? 0)} days` : `${daysLeft(v.fix_deadline_at)} days left`}).</div> : null}
          <h4 style={{ margin: "10px 0 4px" }}>Registry checks</h4>
          <table><tbody>{d.registry_checks.map((c: any, i: number) => <tr key={i}><td>{fmtDateTime(c.checked_at)}</td><td>{c.source}</td><td>{c.verdict}</td><td>{c.name_score ?? ""}</td></tr>)}</tbody></table>
        </div>
      </div>
      <div className="cols2" style={{ marginTop: 16 }}>
        <div className="card"><h3>Findings</h3>{d.findings.length ? <ul>{d.findings.map((f: any) => <li key={f.id}><Status v={f.severity} /> {f.rule_code} — {f.message}<div className="hint">Fix: {f.suggested_fix?.fix}</div></li>)}</ul> : <Empty>No findings.</Empty>}</div>
        <div className="card"><h3>Event history <span className="tag t-grey">append-only</span></h3><ul className="timeline" style={{ padding: 0 }}>{d.events.map((e: any) => <li key={e.seq}><b>{e.event_type.replace(/_/g, " ")}</b> · {fmtDateTime(e.occurred_at)}{e.reason_code ? ` · ${e.reason_code}` : ""}{e.reason_text ? <div className="hint">{e.reason_text}</div> : null}</li>)}</ul></div>
      </div>

      {modal === "withdraw" && <Modal title="Withdraw enrolment" onClose={() => setModal(null)}>
        <p>This releases the seat. Cumulative admitted is not reduced, and the student can then be claimed elsewhere.</p>
        <Field label="Reason"><select value={form.reason_code} onChange={(e) => setForm({ ...form, reason_code: e.target.value })}>{["STUDENT_WITHDREW", "ADMISSION_CANCELLED", "FEE_NOT_PAID", "DATA_ERROR", "OTHER"].map((r) => <option key={r}>{r}</option>)}</select></Field>
        <Field label="Note"><textarea rows={2} value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })} /></Field>
        <div className="row" style={{ marginTop: 12 }}><button className="btn danger" onClick={() => act.mutate({ path: "withdraw", body: form })}>Withdraw</button><button className="btn ghost" onClick={() => setModal(null)}>Cancel</button></div></Modal>}
      {modal === "assist" && <Modal title="Assisted confirmation" onClose={() => setModal(null)}>
        <p>For a student without a phone. This is recorded as <b>low trust</b>, audited with your witness note, and shown separately on dashboards.</p>
        <Field label="Reason"><select value={form.reason_code || "NO_PHONE"} onChange={(e) => setForm({ ...form, reason_code: e.target.value })}>{["NO_PHONE", "PHONE_LOST", "ACCESSIBILITY", "OTHER"].map((r) => <option key={r}>{r}</option>)}</select></Field>
        <Field label="Witness note (min 5 characters)"><textarea rows={2} value={form.witness_note || ""} onChange={(e) => setForm({ ...form, witness_note: e.target.value })} /></Field>
        <div className="row" style={{ marginTop: 12 }}><button className="btn" onClick={() => act.mutate({ path: "confirmation/assisted", body: { reason_code: form.reason_code || "NO_PHONE", witness_note: form.witness_note || "" } })}>Record confirmation</button></div></Modal>}
      {modal === "correct" && <Modal title="Edit identity and revalidate" onClose={() => setModal(null)}>
        <p>Corrections are stored as a new encrypted version, written as a CORRECT event, and the registry check is re-run.</p>
        <Field label="Name"><input value={form.name || ""} onChange={(e) => setForm({ ...form, name: e.target.value })} /></Field>
        <Field label="Date of birth"><input type="date" value={form.dob || ""} onChange={(e) => setForm({ ...form, dob: e.target.value })} /></Field>
        <Field label="APAAR ID (only if it was mistyped)"><input value={form.apaar_id || ""} onChange={(e) => setForm({ ...form, apaar_id: e.target.value })} /></Field>
        <Field label="Reason (min 5 characters)"><input value={form.reason || ""} onChange={(e) => setForm({ ...form, reason: e.target.value })} /></Field>
        <div className="row" style={{ marginTop: 12 }}><button className="btn" onClick={() => act.mutate({ path: "identity/correct", body: { name: form.name, dob: form.dob || null, apaar_id: form.apaar_id || null, reason: form.reason } })}>Save and revalidate</button></div></Modal>}
    </>
  );
}

/* ------------------------------------------------------------------ fix-it list, findings, exceptions */
export function Fixes() {
  const q = useQuery({ queryKey: ["fixes"], queryFn: () => api<any>("/fixes"), refetchInterval: REFRESH });
  const nav = useNavigate();
  if (q.isLoading) return <Skeleton rows={5} />;
  if (q.error) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  return (
    <>
      <PageHead title="Fix-it list" sub="Identity problems with a suggested fix. Claims are never rejected for an identity problem; they get a deadline." />
      <div className="tablewrap"><table><thead><tr><th>Student ID</th><th>Status</th><th>What to fix</th><th>Deadline</th><th></th></tr></thead><tbody>
        {q.data.items.map((i: any) => <tr key={i.id}><td>{i.institution_student_id}<div className="hint"><code>{i.enrolment_ref}</code></div></td><td><Status v={i.identity_status === "PENDING_VERIFICATION" ? "PENDING_VERIFICATION" : i.overall_state} /></td>
          <td>{(i.fixes || []).map((f: any, k: number) => <div key={k}><b>{f.field}</b>: {f.message} <i className="hint">{f.fix}</i></div>)}{i.identity_status === "PENDING_VERIFICATION" ? "The registry was unavailable; it will be re-checked automatically." : ""}</td>
          <td>{i.fix_deadline_at ? <>{fmtDate(i.fix_deadline_at)}<div className="hint">{(daysLeft(i.fix_deadline_at) ?? 0) < 0 ? `overdue by ${Math.abs(daysLeft(i.fix_deadline_at) ?? 0)} days` : `${daysLeft(i.fix_deadline_at)} days left`}</div></> : "-"}</td>
          <td><button className="btn small" onClick={() => nav(`/institution/claims/${i.id}`)}>Edit &amp; revalidate</button></td></tr>)}
        {!q.data.items.length ? <tr><td colSpan={5} className="empty">No identity fixes are outstanding.</td></tr> : null}
      </tbody></table></div>
    </>
  );
}

export function Findings() {
  const { can } = useAuth(); const qc = useQueryClient(); const toast = useToast();
  const [status, setStatus] = useState("OPEN"); const [severity, setSeverity] = useState("");
  const q = useQuery({ queryKey: ["findings", status, severity], queryFn: () => api<any>(`/findings?status=${status}${severity ? "&severity=" + severity : ""}&limit=200`), refetchInterval: REFRESH });
  const ack = useMutation({ mutationFn: (id: string) => api(`/findings/${id}/acknowledge`, { body: {} }), onSuccess: () => { toast("Finding acknowledged"); qc.invalidateQueries({ queryKey: ["findings"] }); }, onError: (e) => toast((e as ApiProblem).title, true) });
  return (
    <>
      <PageHead title="Findings" sub="Rule findings from the active rule set. Warnings can be acknowledged; AICTE may waive them." />
      <div className="toolbar"><div><label>Status</label><select value={status} onChange={(e) => setStatus(e.target.value)}>{["OPEN", "ACKNOWLEDGED", "RESOLVED", "WAIVED"].map((s) => <option key={s}>{s}</option>)}</select></div>
        <div><label>Severity</label><select value={severity} onChange={(e) => setSeverity(e.target.value)}><option value="">All</option>{["Blocker", "Error", "Warning", "Info"].map((s) => <option key={s}>{s}</option>)}</select></div></div>
      {q.error ? <ErrorBox error={q.error} retry={() => q.refetch()} /> : null}
      <div className="tablewrap"><table><thead><tr><th>Severity</th><th>Rule</th><th>Finding and suggested fix</th><th>Claim</th><th>Raised</th><th></th></tr></thead><tbody>
        {q.isLoading ? <tr><td colSpan={6}><Skeleton /></td></tr> : q.data.items.map((f: any) => <tr key={f.id}><td><Status v={f.severity} /></td><td><code>{f.rule_code}</code></td><td>{f.message}<div className="hint">{f.suggested_fix?.fix}</div></td>
          <td>{f.enrolment_id ? <Link to={`/institution/claims/${f.enrolment_id}`}><code>{f.enrolment_ref}</code></Link> : "-"}</td><td>{fmtDate(f.created_at)}</td>
          <td>{can("findings:ack") && f.status === "OPEN" && (f.severity === "Warning" || f.severity === "Info") ? <button className="btn small ghost" onClick={() => ack.mutate(f.id)}>Acknowledge</button> : null}</td></tr>)}
        {q.data && !q.data.items.length ? <tr><td colSpan={6} className="empty">No findings in this state.</td></tr> : null}
      </tbody></table></div>
    </>
  );
}

function ExceptionForm({ onClose }: { onClose: () => void }) {
  const progs = useQuery({ queryKey: ["programmes"], queryFn: () => api<any>("/programmes") });
  const qc = useQueryClient(); const toast = useToast();
  const [f, setF] = useState<any>({ programme_code: "", bucket_type: "REGULAR", extra_seats: 5, justification: "" });
  const [err, setErr] = useState<any>(null);
  const m = useMutation({ mutationFn: () => api("/seat-exceptions", { body: { ...f, extra_seats: Number(f.extra_seats) } }), onSuccess: () => { toast("Request sent to AICTE"); qc.invalidateQueries({ queryKey: ["exceptions"] }); onClose(); }, onError: setErr });
  return (
    <Modal title="Request a seat exception" onClose={onClose}>
      <p>AICTE decides within your region's scope. The bucket must be at least 90% full.</p>
      <Field label="Programme"><select value={f.programme_code} onChange={(e) => setF({ ...f, programme_code: e.target.value })}><option value="">Select…</option>{progs.data?.items.map((p: any) => <option key={p.code} value={p.code}>{p.name}</option>)}</select></Field>
      <div className="cols2"><Field label="Bucket"><input value={f.bucket_type} onChange={(e) => setF({ ...f, bucket_type: e.target.value })} /></Field>
        <Field label="Extra seats"><input type="number" min={1} value={f.extra_seats} onChange={(e) => setF({ ...f, extra_seats: e.target.value })} /></Field></div>
      <Field label="Justification (min 20 characters)"><textarea rows={3} value={f.justification} onChange={(e) => setF({ ...f, justification: e.target.value })} /></Field>
      {err ? <ErrorBox error={err} /> : null}
      <div className="row" style={{ marginTop: 12 }}><button className="btn" disabled={!f.programme_code || f.justification.length < 20 || m.isPending} onClick={() => m.mutate()}>Send request</button></div>
    </Modal>
  );
}

export function Exceptions() {
  const { can } = useAuth();
  const q = useQuery({ queryKey: ["exceptions"], queryFn: () => api<any>("/seat-exceptions"), refetchInterval: REFRESH });
  const [open, setOpen] = useState(new URLSearchParams(window.location.search).has("new"));
  return (
    <>
      <PageHead title="Seat exceptions" sub="Requests for extra seats when a bucket is full. AICTE approves or rejects with a reason.">{can("seat_exceptions:request") ? <button className="btn" onClick={() => setOpen(true)}>Request exception</button> : null}</PageHead>
      {q.error ? <ErrorBox error={q.error} retry={() => q.refetch()} /> : null}
      <div className="tablewrap"><table><thead><tr><th>Programme</th><th>Bucket</th><th>Seats</th><th>Status</th><th>Justification</th><th>Decision</th></tr></thead><tbody>
        {q.data?.items.map((x: any) => <tr key={x.id}><td>{x.programme_name}</td><td>{x.bucket_type}</td><td>{x.extra_seats}{x.approved_seats ? ` → ${x.approved_seats}` : ""}</td><td><Status v={x.status} /></td><td>{x.justification}</td><td>{x.decision_reason || "-"}</td></tr>)}
        {q.data && !q.data.items.length ? <tr><td colSpan={6} className="empty">No requests yet.</td></tr> : null}
      </tbody></table></div>
      {open ? <ExceptionForm onClose={() => setOpen(false)} /> : null}
    </>
  );
}
