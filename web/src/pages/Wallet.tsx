import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiProblem, download, fmtDate, fmtDateTime, VERIFY_BASE } from "../api";
import { useAuth } from "../auth";
import { tr } from "../i18n";
import { ConfirmButton, Empty, ErrorBox, Field, Modal, PageHead, Skeleton, Status, useToast } from "../ui";

const FIELD_LABEL: Record<string, string> = { institution_name: "Institution", programme_name: "Programme", programme_code: "Programme code", academic_year: "Academic year", enrolment_status: "Enrolment status", approved_seat: "Approved seat" };
const PURPOSES: Record<string, string> = { EMPLOYMENT_VERIFICATION: "Employment verification", LOAN_ELIGIBILITY: "Loan eligibility", VISA_SUPPORT: "Visa support", ADMISSION_TRANSFER: "Admission transfer", REGULATORY_CHECK: "Regulatory check" };
const fail = (toast: any) => (e: unknown) => toast(`${(e as ApiProblem).title}${(e as ApiProblem).detail ? ": " + (e as ApiProblem).detail : ""}`, true);

function useActAs() {
  const links = useQuery({ queryKey: ["guardian-links"], queryFn: () => api<any>("/student/guardian-links") });
  const [actAs, setActAs] = useState("");
  const guardianOf = links.data?.items?.filter((l: any) => l.status === "VERIFIED") || [];
  const picker = guardianOf.length ? <div><label htmlFor="act">Guardian mode</label><select id="act" value={actAs} onChange={(e) => setActAs(e.target.value)}><option value="">My own record</option>{guardianOf.map((l: any) => <option key={l.student_id} value={l.student_id}>Linked minor {l.student_ref}</option>)}</select></div> : null;
  return { actAs: actAs || undefined, picker };
}

function FieldPicker({ fields, value, onChange }: { fields: string[]; value: string[]; onChange: (v: string[]) => void }) {
  return <div className="row" role="group" aria-label="Fields to share">{fields.map((f) => <label key={f} style={{ display: "flex", gap: 6, alignItems: "center", fontWeight: 400 }}>
    <input type="checkbox" style={{ width: "auto" }} checked={value.includes(f)} onChange={(e) => onChange(e.target.checked ? [...value, f] : value.filter((x) => x !== f))} />{FIELD_LABEL[f] || f}</label>)}</div>;
}

/* ---------------------------------------------------------------- credentials + share links */
export function WalletCredentials() {
  const { user } = useAuth(); const T = (s: string) => tr(user?.locale, s);
  const toast = useToast(); const qc = useQueryClient(); const { actAs, picker } = useActAs();
  const creds = useQuery({ queryKey: ["w-creds", actAs], queryFn: () => api<any>("/student/credentials", { actAs }) });
  const links = useQuery({ queryKey: ["w-links", actAs], queryFn: () => api<any>("/student/share-links", { actAs }) });
  const [sel, setSel] = useState<any>(null); const [purpose, setPurpose] = useState("EMPLOYMENT_VERIFICATION"); const [fields, setFields] = useState<string[]>(["institution_name", "programme_name"]); const [days, setDays] = useState(7);
  const [made, setMade] = useState<any>(null);
  const create = useMutation({ mutationFn: () => api<any>("/student/share-links", { body: { credential_id: sel.id, purpose_code: purpose, fields, expires_days: days }, actAs }),
    onSuccess: (r) => { setMade(r); setSel(null); qc.invalidateQueries({ queryKey: ["w-links"] }); }, onError: fail(toast) });
  const revoke = useMutation({ mutationFn: (id: string) => api(`/student/share-links/${id}`, { method: "DELETE", actAs }), onSuccess: () => { toast("Link revoked. It stops working immediately."); qc.invalidateQueries({ queryKey: ["w-links"] }); }, onError: fail(toast) });
  const pdf = async (c: any) => { try { await download(`/student/credentials/${c.id}/certificate.pdf`, `shikshasetu-certificate-${c.credential_ref.slice(0, 8)}.pdf`, actAs); } catch (e) { fail(toast)(e); } };
  const link = made ? `${window.location.origin}/s/${made.token}` : "";
  return (
    <>
      <PageHead title={T("My wallet")} sub="Credentials issued to you once all three locks agree. You decide who may ask about them, and for which purpose.">{picker}</PageHead>
      {creds.isLoading ? <Skeleton rows={4} /> : creds.error ? <ErrorBox error={creds.error} retry={() => creds.refetch()} /> : (
        <>
          {creds.data.items.map((c: any) => (
            <div className="card" key={c.id} style={{ marginBottom: 14 }}>
              <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-start" }}>
                <div>
                  <h3 style={{ marginTop: 0 }}>{c.programme_name} <Status v={c.status === "VALID" ? "VERIFIED" : "REJECTED"} label={T(c.status === "VALID" ? "Valid" : "Revoked")} /></h3>
                  <div className="hint">{c.institution_name} · {c.academic_year} · issued {fmtDate(c.issued_at)} · valid until {fmtDate(c.valid_until)}</div>
                  <div className="hint">Reference <code>{c.credential_ref}</code></div>
                  {c.revoked_reason ? <div className="alert warn" style={{ marginTop: 8 }}>{c.status === "SUPERSEDED" ? "Replaced after a correction." : "Revoked."} {c.revoked_reason}</div> : null}
                  <div className="row" style={{ marginTop: 10 }}>
                    <button className="btn small" disabled={c.status !== "VALID"} onClick={() => { setSel(c); setMade(null); }}>{T("Create share link")}</button>
                    <button className="btn small ghost" onClick={() => pdf(c)}>{T("Download certificate")}</button>
                    <a className="btn small ghost" href={`/v/${c.credential_ref}`} target="_blank" rel="noreferrer">Public status page</a>
                  </div>
                </div>
                <img src={`${VERIFY_BASE}/qr/${encodeURIComponent(c.credential_ref)}`} alt="QR code that opens the public status page" width={110} height={110} />
              </div>
            </div>
          ))}
          {!creds.data.items.length ? <Empty>No credential yet. One is issued after your admission is VERIFIED and your institution issues it.</Empty> : null}
          <p className="hint">{creds.data.boundary}</p>
        </>
      )}
      <h2 style={{ marginTop: 24 }}>{T("Share links")}</h2>
      <div className="tablewrap"><table><thead><tr><th>Purpose</th><th>Fields</th><th>Expires</th><th>Used</th><th></th></tr></thead><tbody>
        {links.data?.items.map((l: any) => <tr key={l.id}><td>{PURPOSES[l.purpose_code] || l.purpose_code}</td><td>{l.fields.map((f: string) => FIELD_LABEL[f] || f).join(", ")}</td>
          <td>{l.revoked_at ? <Status v="WITHDRAWN" label="Revoked" /> : new Date(l.expires_at) < new Date() ? <Status v="EXPIRED" /> : fmtDate(l.expires_at)}</td><td>{l.use_count}×</td>
          <td>{!l.revoked_at && new Date(l.expires_at) > new Date() ? <ConfirmButton label={T("Revoke")} danger consequence="Anyone holding this link will get no answer from now on." onConfirm={() => revoke.mutate(l.id)} /> : null}</td></tr>)}
        {links.data && !links.data.items.length ? <tr><td colSpan={5} className="empty">You have not shared any links.</td></tr> : null}</tbody></table></div>
      {sel ? <Modal title={T("Create share link")} onClose={() => setSel(null)}>
        <p className="hint">Anyone holding the link can ask the verification service yes/no questions about this credential, but only for the purpose and fields you choose, until it expires or you revoke it.</p>
        <Field label="Purpose"><select value={purpose} onChange={(e) => setPurpose(e.target.value)}>{Object.entries(PURPOSES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></Field>
        <Field label="Fields to reveal" hint={`Out of ${links.data?.catalogue_size ?? 40} fields held about you, only these can ever be shared.`}><FieldPicker fields={links.data?.fields || []} value={fields} onChange={setFields} /></Field>
        <Field label="Expires in (days, max 30)"><input type="number" min={1} max={30} value={days} onChange={(e) => setDays(Number(e.target.value))} /></Field>
        <div className="row" style={{ marginTop: 12 }}><button className="btn" disabled={!fields.length || create.isPending} onClick={() => create.mutate()}>{T("Create share link")}</button><button className="btn ghost" onClick={() => setSel(null)}>{T("Cancel")}</button></div></Modal> : null}
      {made ? <Modal title="Your share link" onClose={() => setMade(null)}>
        <div className="alert warn">This link is shown only once. Copy it now; only a fingerprint is stored.</div>
        <p className="mono" style={{ wordBreak: "break-all" }}>{link}</p>
        <div className="row"><button className="btn" onClick={() => { navigator.clipboard?.writeText(link); toast("Link copied"); }}>Copy link</button><button className="btn ghost" onClick={() => setMade(null)}>Done</button></div></Modal> : null}
    </>
  );
}

/* ---------------------------------------------------------------- consents */
export function Consents() {
  const { user } = useAuth(); const T = (s: string) => tr(user?.locale, s);
  const toast = useToast(); const qc = useQueryClient(); const { actAs, picker } = useActAs();
  const q = useQuery({ queryKey: ["w-consents", actAs], queryFn: () => api<any>("/student/consents", { actAs }) });
  const orgs = useQuery({ queryKey: ["w-orgs"], queryFn: () => api<any>("/student/verifier-orgs") });
  const [form, setForm] = useState<{ org: any; purpose: string; fields: string[]; days: string } | null>(null);
  const refresh = () => qc.invalidateQueries({ queryKey: ["w-consents"] });
  const grant = useMutation({ mutationFn: () => api("/student/consents", { body: { verifier_org_id: form!.org.id, purpose_code: form!.purpose, fields: form!.fields, expires_days: form!.days ? Number(form!.days) : null }, actAs }), onSuccess: () => { toast("Consent recorded"); setForm(null); refresh(); }, onError: fail(toast) });
  const withdraw = useMutation({ mutationFn: (id: string) => api(`/student/consents/${id}`, { method: "DELETE", actAs }), onSuccess: (r: any) => { toast(r.note); refresh(); }, onError: fail(toast) });
  const refuse = useMutation({ mutationFn: (id: string) => api(`/student/consent-requests/${id}/refuse`, { body: {}, actAs }), onSuccess: () => { toast("Request refused"); refresh(); }, onError: fail(toast) });
  const apaar = useMutation({ mutationFn: (granted: boolean) => api("/student/consents/apaar", { body: { granted }, actAs }), onSuccess: (r: any) => { toast(r.note); refresh(); }, onError: fail(toast) });
  const openForm = (org: any, purpose?: string) => { const p = purpose || Object.keys(org.purposes || {})[0]; setForm({ org, purpose: p, fields: (org.purposes?.[p] || []).slice(0, 2), days: "90" }); };
  const hasPurpose = (r: any) => orgs.data?.items.find((o: any) => o.id === r.verifier_org_id);
  return (
    <>
      <PageHead title={T("Consents")} sub="Consent decides who may ask about your enrolment. Your enrolment is recorded under statutory authority whether or not you consent." >{picker}</PageHead>
      <div className="alert info">{T("Your enrolment is recorded under statutory authority whether or not you consent. Consent only governs sharing with verifier organisations.")}</div>
      {q.isLoading ? <Skeleton rows={4} /> : q.error ? <ErrorBox error={q.error} retry={() => q.refetch()} /> : (
        <>
          <h2>{T("Pending requests")}</h2>
          <div className="tablewrap"><table><thead><tr><th>Organisation</th><th>Purpose</th><th>Asked</th><th></th></tr></thead><tbody>
            {q.data.requests.filter((r: any) => r.status === "PENDING").map((r: any) => <tr key={r.id}><td>{r.verifier}</td><td>{PURPOSES[r.purpose_code] || r.purpose_code}</td><td>{fmtDateTime(r.created_at)}</td>
              <td className="row"><button className="btn small" onClick={() => { const o = hasPurpose(r); if (o) openForm(o, r.purpose_code); }}>{T("Grant")}</button><button className="btn small ghost" onClick={() => refuse.mutate(r.id)}>{T("Refuse")}</button></td></tr>)}
            {!q.data.requests.some((r: any) => r.status === "PENDING") ? <tr><td colSpan={4} className="empty">Nobody is waiting for your decision.</td></tr> : null}</tbody></table></div>
          <h2 style={{ marginTop: 22 }}>{T("Active consents")}</h2>
          <div className="tablewrap"><table><thead><tr><th>Organisation</th><th>Purpose</th><th>May receive</th><th>Until</th><th>Status</th><th></th></tr></thead><tbody>
            {q.data.consents.map((c: any) => <tr key={c.id}><td>{c.verifier}</td><td>{PURPOSES[c.purpose_code] || c.purpose_code}</td><td>{(c.scope?.fields || []).map((f: string) => FIELD_LABEL[f] || f).join(", ")}</td><td>{c.expires_at ? fmtDate(c.expires_at) : "No end date"}</td><td><Status v={c.status} /></td>
              <td>{c.status === "GRANTED" ? <ConfirmButton label={T("Withdraw")} danger consequence="The organisation will get no answer from the next request. Past requests stay in your access log." onConfirm={() => withdraw.mutate(c.id)} /> : null}</td></tr>)}
            {!q.data.consents.length ? <tr><td colSpan={6} className="empty">You have not granted any consent.</td></tr> : null}</tbody></table></div>
          <h2 style={{ marginTop: 22 }}>Grant a consent in advance</h2>
          <div className="row">{orgs.data?.items.map((o: any) => <button key={o.id} className="btn small ghost" onClick={() => openForm(o)}>{o.name}</button>)}</div>
          <h2 style={{ marginTop: 22 }}>APAAR use</h2>
          <div className="card"><p>{T("Refusing APAAR consent never affects whether you are counted.")} Current choice: <b>{q.data.apaar_use?.status || "not recorded"}</b>.</p>
            <div className="row"><button className="btn small" onClick={() => apaar.mutate(true)}>I agree</button><button className="btn small ghost" onClick={() => apaar.mutate(false)}>I refuse</button></div></div>
        </>
      )}
      {form ? <Modal title={`Consent for ${form.org.name}`} onClose={() => setForm(null)}>
        <Field label="Purpose"><select value={form.purpose} onChange={(e) => setForm({ ...form, purpose: e.target.value, fields: (form.org.purposes[e.target.value] || []).slice(0, 2) })}>{Object.keys(form.org.purposes || {}).map((p) => <option key={p} value={p}>{PURPOSES[p] || p}</option>)}</select></Field>
        <Field label="Fields they may receive" hint="Only fields this organisation is approved for in this purpose are listed."><FieldPicker fields={form.org.purposes[form.purpose] || []} value={form.fields} onChange={(v) => setForm({ ...form, fields: v })} /></Field>
        <Field label="Ends after (days, blank = no end date)"><input type="number" min={1} max={365} value={form.days} onChange={(e) => setForm({ ...form, days: e.target.value })} /></Field>
        <div className="row" style={{ marginTop: 12 }}><button className="btn" disabled={!form.fields.length || grant.isPending} onClick={() => grant.mutate()}>{T("Grant")}</button><button className="btn ghost" onClick={() => setForm(null)}>{T("Cancel")}</button></div></Modal> : null}
    </>
  );
}

/* ---------------------------------------------------------------- access log */
export function AccessLog() {
  const { user } = useAuth(); const T = (s: string) => tr(user?.locale, s);
  const { actAs, picker } = useActAs();
  const q = useQuery({ queryKey: ["w-log", actAs], queryFn: () => api<any>("/student/access-log", { actAs }), refetchInterval: 20000 });
  return (
    <>
      <PageHead title={T("Access log")} sub="Every verification request about you, and every time AICTE staff looked at your identity.">{picker}</PageHead>
      {q.isLoading ? <Skeleton rows={5} /> : q.error ? <ErrorBox error={q.error} retry={() => q.refetch()} /> : (
        <>
          <h2>{T("Who asked about you")}</h2>
          <div className="tablewrap"><table><thead><tr><th>When</th><th>Who</th><th>Purpose</th><th>Answer</th><th>Fields shared</th></tr></thead><tbody>
            {q.data.verifications.map((v: any, i: number) => <tr key={i}><td>{fmtDateTime(v.created_at)}</td><td>{v.verifier}</td><td>{PURPOSES[v.purpose_code] || "-"}</td>
              <td>{v.outcome === "NEUTRAL" ? <span className="hint">No answer given</span> : <Status v={v.outcome === "YES" ? "VERIFIED" : "REJECTED"} label={v.outcome} />}</td><td>{v.fields_disclosed.length} of {q.data.disclosure_of}{v.fields_disclosed.length ? <div className="hint">{v.fields_disclosed.map((f: string) => FIELD_LABEL[f] || f).join(", ")}</div> : null}</td></tr>)}
            {!q.data.verifications.length ? <tr><td colSpan={5} className="empty">Nobody has asked about you yet.</td></tr> : null}</tbody></table></div>
          <h2 style={{ marginTop: 22 }}>AICTE staff access</h2>
          <div className="tablewrap"><table><thead><tr><th>When</th><th>What</th><th>Case</th></tr></thead><tbody>
            {q.data.staff_access.map((s: any, i: number) => <tr key={i}><td>{fmtDateTime(s.at)}</td><td>{s.label} ({s.role})</td><td>{s.case_no || "-"}</td></tr>)}
            {!q.data.staff_access.length ? <tr><td colSpan={3} className="empty">No staff member has looked at your identity.</td></tr> : null}</tbody></table></div>
        </>
      )}
    </>
  );
}

/* ---------------------------------------------------------------- corrections */
export function Corrections() {
  const { user } = useAuth(); const T = (s: string) => tr(user?.locale, s);
  const toast = useToast(); const qc = useQueryClient(); const { actAs, picker } = useActAs();
  const q = useQuery({ queryKey: ["w-corr", actAs], queryFn: () => api<any>("/student/corrections", { actAs }) });
  const enr = useQuery({ queryKey: ["w-enr", actAs], queryFn: () => api<any>("/student/enrolments", { actAs }).catch(() => ({ items: [] })) });
  const [f, setF] = useState({ enrolment_id: "", field: "name", value: "", note: "" });
  const send = useMutation({ mutationFn: () => api("/student/corrections", { body: { ...f, note: f.note || null }, actAs }), onSuccess: () => { toast("Correction requested. A reviewer will decide."); setF({ ...f, value: "", note: "" }); qc.invalidateQueries({ queryKey: ["w-corr"] }); }, onError: fail(toast) });
  return (
    <>
      <PageHead title={T("Corrections")} sub={q.data?.note || "You cannot edit official fields directly. A reviewer decides each request."}>{picker}</PageHead>
      <div className="card" style={{ marginBottom: 16 }}>
        <h3 style={{ marginTop: 0 }}>{T("Request a correction")}</h3>
        <div className="cols2">
          <Field label="Enrolment"><select value={f.enrolment_id} onChange={(e) => setF({ ...f, enrolment_id: e.target.value })}><option value="">Select…</option>{enr.data?.items.map((e: any) => <option key={e.id} value={e.id}>{e.programme_name} · {e.institution_name}</option>)}</select></Field>
          <Field label="Field"><select value={f.field} onChange={(e) => setF({ ...f, field: e.target.value })}>{[["name", "Name"], ["dob", "Date of birth"], ["parent_name", "Parent or guardian name"], ["mobile", "Mobile"], ["email", "Email"]].map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></Field>
          <Field label="Correct value" hint="Stored encrypted. Reviewers see the field and your note, not the value."><input value={f.value} onChange={(e) => setF({ ...f, value: e.target.value })} /></Field>
          <Field label="Note for the reviewer (optional)"><input value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} /></Field>
        </div>
        <button className="btn" style={{ marginTop: 10 }} disabled={!f.enrolment_id || !f.value || send.isPending} onClick={() => send.mutate()}>{T("Submit")}</button>
        <p className="hint">If a correction is approved your credential is replaced, and a fresh one needs to be issued.</p>
      </div>
      {q.isLoading ? <Skeleton rows={3} /> : q.error ? <ErrorBox error={q.error} retry={() => q.refetch()} /> : (
        <div className="tablewrap"><table><thead><tr><th>Requested</th><th>Enrolment</th><th>Field</th><th>Status</th><th>Reviewer's reason</th></tr></thead><tbody>
          {q.data.items.map((c: any) => <tr key={c.id}><td>{fmtDateTime(c.created_at)}</td><td><code>{c.enrolment_ref}</code></td><td>{c.field}</td><td><Status v={c.status} /></td><td>{c.reason || "-"}</td></tr>)}
          {!q.data.items.length ? <tr><td colSpan={5} className="empty">No correction requests.</td></tr> : null}</tbody></table></div>
      )}
    </>
  );
}

/* ---------------------------------------------------------------- data requests + guardian access */
export function DataRequests() {
  const { user } = useAuth(); const T = (s: string) => tr(user?.locale, s);
  const toast = useToast(); const qc = useQueryClient(); const { actAs, picker } = useActAs();
  const q = useQuery({ queryKey: ["w-dsr", actAs], queryFn: () => api<any>("/student/dsr", { actAs }) });
  const [type, setType] = useState("ACCESS"); const [details, setDetails] = useState("");
  const [exp, setExp] = useState<any>(null);
  const [gRef, setGRef] = useState(""); const [gOtp, setGOtp] = useState(""); const [gSent, setGSent] = useState("");
  const send = useMutation({ mutationFn: () => api<any>("/student/dsr", { body: { type, details: details || null }, actAs }), onSuccess: (r) => { toast(r.status === "FULFILLED" ? "Your data export is ready below." : "Request filed. A reviewer will respond."); setDetails(""); qc.invalidateQueries({ queryKey: ["w-dsr"] }); }, onError: fail(toast) });
  const showExport = async (id: string) => { try { setExp(await api(`/student/dsr/${id}/export`, { actAs })); } catch (e) { fail(toast)(e); } };
  const askLink = useMutation({ mutationFn: () => api<any>("/student/guardian-links", { body: { student_ref: gRef }, }), onSuccess: (r) => { setGSent(r.sent_to); toast(r.note); }, onError: fail(toast) });
  const verifyLink = useMutation({ mutationFn: () => api("/student/guardian-links/verify", { body: { student_ref: gRef, otp: gOtp } }), onSuccess: () => { toast("Guardian link verified. Use Guardian mode to act for the minor."); setGOtp(""); qc.invalidateQueries({ queryKey: ["guardian-links"] }); }, onError: fail(toast) });
  const isGuardian = user?.roles.includes("ST") && !user.student_id;
  return (
    <>
      <PageHead title={T("Data requests")} sub="Ask for a copy of your data, a correction, erasure or file a grievance.">{picker}</PageHead>
      <div className="card" style={{ marginBottom: 16 }}>
        <div className="cols2">
          <Field label="Type"><select value={type} onChange={(e) => setType(e.target.value)}><option value="ACCESS">Copy of my data (instant)</option><option value="CORRECTION">Correction</option><option value="ERASURE">Erasure</option><option value="GRIEVANCE">Grievance</option></select></Field>
          <Field label="Details (optional)"><input value={details} onChange={(e) => setDetails(e.target.value)} /></Field>
        </div>
        {type === "ERASURE" ? <div className="alert info" style={{ marginTop: 8 }}>While you hold a live enrolment the law requires the record to be kept. In that case the request is closed as "lawful retention" and processing is restricted instead. After you withdraw, your personal details can be erased; counts and audit entries stay, without your identity.</div> : null}
        <button className="btn" style={{ marginTop: 10 }} disabled={send.isPending} onClick={() => send.mutate()}>{T("Request")}</button>
        <p className="hint">{q.data?.grievance_officer}</p>
      </div>
      {q.isLoading ? <Skeleton rows={3} /> : q.error ? <ErrorBox error={q.error} retry={() => q.refetch()} /> : (
        <div className="tablewrap"><table><thead><tr><th>Filed</th><th>Type</th><th>Status</th><th>Outcome</th><th></th></tr></thead><tbody>
          {q.data.items.map((d: any) => <tr key={d.id}><td>{fmtDateTime(d.created_at)}</td><td>{d.type}</td><td><Status v={d.status} /></td><td>{d.decision_note || "-"}</td><td>{d.has_export ? <button className="btn small ghost" onClick={() => showExport(d.id)}>View export</button> : null}</td></tr>)}
          {!q.data.items.length ? <tr><td colSpan={5} className="empty">No requests yet.</td></tr> : null}</tbody></table></div>
      )}
      {isGuardian ? <div className="card" style={{ marginTop: 20 }}>
        <h3 style={{ marginTop: 0 }}>Link to a student under 18</h3>
        <p className="hint">Enter the student reference. A code is sent to the number held for the student (mock SMS, see the Dev SMS Inbox).</p>
        <div className="row" style={{ alignItems: "end" }}><div style={{ width: 220 }}><Field label="Student reference"><input value={gRef} onChange={(e) => setGRef(e.target.value)} placeholder="STU-…" /></Field></div>
          <button className="btn" disabled={gRef.length < 4 || askLink.isPending} onClick={() => askLink.mutate()}>Send code</button>
          {gSent ? <><div style={{ width: 160 }}><Field label={`Code sent to ${gSent}`}><input inputMode="numeric" maxLength={6} value={gOtp} onChange={(e) => setGOtp(e.target.value.replace(/\D/g, ""))} /></Field></div><button className="btn" disabled={gOtp.length !== 6} onClick={() => verifyLink.mutate()}>Verify</button></> : null}</div></div> : null}
      {exp ? <Modal title="Your data export" onClose={() => setExp(null)} drawer><pre style={{ whiteSpace: "pre-wrap", fontSize: 12 }}>{JSON.stringify(exp, null, 2)}</pre></Modal> : null}
    </>
  );
}
