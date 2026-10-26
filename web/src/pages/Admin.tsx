import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiProblem, download, fmtDate, fmtDateTime } from "../api";
import { useAuth } from "../auth";
import { ConfirmButton, Empty, ErrorBox, Field, Modal, PageHead, Simulated, Skeleton, Status, useStepUp, useToast } from "../ui";
import { SiteHeader } from "./Public";

const fail = (toast: any) => (e: unknown) => toast(`${(e as ApiProblem).title}${(e as ApiProblem).detail ? ": " + (e as ApiProblem).detail : ""}`, true);
const Tabs = ({ tabs, tab, set }: { tabs: string[][]; tab: string; set: (k: string) => void }) => <div className="row" style={{ marginBottom: 10 }}>{tabs.map(([k, l]) => <button key={k} className={"btn small " + (tab === k ? "" : "ghost")} onClick={() => set(k)}>{l}</button>)}</div>;
const Secret = ({ title, value, label, onClose }: { title: string; value: string; label: string; onClose: () => void }) => {
  const toast = useToast();
  return <Modal title={title} onClose={onClose}><div className="alert warn">{label} It is shown only once.</div><p className="mono" style={{ wordBreak: "break-all" }}>{value}</p>
    <div className="row"><button className="btn" onClick={() => { navigator.clipboard?.writeText(value); toast("Copied"); }}>Copy</button><button className="btn ghost" onClick={onClose}>Done</button></div></Modal>;
};

/* ---------------------------------------------------------------- institution: credentials */
export function InstCredentials() {
  const toast = useToast(); const qc = useQueryClient(); const stepUp = useStepUp(); const { can } = useAuth();
  const [status, setStatus] = useState("");
  const q = useQuery({ queryKey: ["i-creds", status], queryFn: () => api<any>(`/credentials?limit=100${status ? "&status=" + status : ""}`) });
  const issueAll = useMutation({ mutationFn: async () => api<any>("/credentials/issue-all-verified", { body: {}, headers: { "X-Step-Up-Token": await stepUp() } }), onSuccess: (r) => { toast(`${r.issued} credentials issued`); qc.invalidateQueries({ queryKey: ["i-creds"] }); }, onError: fail(toast) });
  const supersede = useMutation({ mutationFn: async (c: any) => api(`/credentials/${c.id}/supersede`, { body: { reason: "Superseded by the institution after a record change." }, headers: { "X-Step-Up-Token": await stepUp() } }), onSuccess: () => { toast("Credential superseded"); qc.invalidateQueries({ queryKey: ["i-creds"] }); }, onError: fail(toast) });
  const pdf = async (c: any) => { try { await download(`/credentials/${c.id}/certificate.pdf`, `certificate-${c.enrolment_ref}.pdf`); } catch (e) { fail(toast)(e); } };
  return (
    <>
      <PageHead title="Credentials" sub="A credential can be issued only for an ADMITTED enrolment whose three locks agree. Ending an enrolment revokes its credential automatically.">
        {can("credentials:issue") ? <ConfirmButton label={`Issue all eligible (${q.data?.eligible_to_issue ?? 0})`} consequence="Signs a credential for every VERIFIED, admitted enrolment that does not have one yet. You will be asked for your password." onConfirm={() => issueAll.mutate()} disabled={!q.data?.eligible_to_issue} className="" /> : null}
      </PageHead>
      <div className="row" style={{ marginBottom: 10 }}><select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Status" style={{ width: 180 }}><option value="">All statuses</option><option value="VALID">Valid</option><option value="REVOKED">Revoked</option><option value="SUPERSEDED">Superseded</option></select><span className="hint">{q.data?.total ?? 0} credentials · {q.data?.eligible_to_issue ?? 0} enrolments eligible</span></div>
      {q.isLoading ? <Skeleton rows={5} /> : q.error ? <ErrorBox error={q.error} retry={() => q.refetch()} /> : (
        <div className="tablewrap"><table><thead><tr><th>Enrolment</th><th>Programme</th><th>Credential ref</th><th>Issued</th><th>Status</th><th></th></tr></thead><tbody>
          {q.data.items.map((c: any) => <tr key={c.id}><td><code>{c.enrolment_ref}</code><div className="hint">{c.institution_student_id}</div></td><td>{c.programme_name}</td><td><code>{c.credential_ref}</code></td><td>{fmtDate(c.issued_at)}</td>
            <td><Status v={c.status === "VALID" ? "VERIFIED" : "REJECTED"} label={c.status} />{c.revoked_reason ? <div className="hint">{c.revoked_reason}</div> : null}</td>
            <td className="row"><button className="btn small ghost" onClick={() => pdf(c)}>Certificate</button>{c.status === "VALID" && can("credentials:revoke") ? <ConfirmButton label="Supersede" consequence="Replaces this credential: its status bit is set, so every verifier sees it as no longer valid. Issue a new one afterwards." onConfirm={() => supersede.mutate(c)} /> : null}</td></tr>)}
          {!q.data.items.length ? <tr><td colSpan={6} className="empty">No credentials yet.</td></tr> : null}</tbody></table></div>
      )}
    </>
  );
}

/* ---------------------------------------------------------------- administration: users, API clients, webhooks (institution and AICTE) */
function Users({ aicte }: { aicte: boolean }) {
  const toast = useToast(); const qc = useQueryClient(); const [open, setOpen] = useState(false); const [shown, setShown] = useState<any>(null);
  const [f, setF] = useState({ username: "", display_name: "", role: aicte ? "AR" : "IO", scope_type: "NATIONAL", scope_value: "" });
  const q = useQuery({ queryKey: ["users"], queryFn: () => api<any>("/admin/users") });
  const create = useMutation({ mutationFn: () => api<any>("/admin/users", { body: { ...f, scope_type: aicte ? f.scope_type : null, scope_value: aicte && f.scope_type !== "NATIONAL" ? f.scope_value : null } }), onSuccess: (r) => { setOpen(false); qc.invalidateQueries({ queryKey: ["users"] }); setShown(r); }, onError: fail(toast) });
  const off = useMutation({ mutationFn: (id: string) => api<any>(`/admin/users/${id}/deactivate`, { body: {} }), onSuccess: (r) => { toast(`Deactivated; ${r.sessions_revoked} session(s) ended`); qc.invalidateQueries({ queryKey: ["users"] }); }, onError: fail(toast) });
  return (
    <>
      <div className="row" style={{ marginBottom: 10 }}><button className="btn small" onClick={() => setOpen(true)}>Add user</button></div>
      {q.isLoading ? <Skeleton rows={4} /> : q.error ? <ErrorBox error={q.error} retry={() => q.refetch()} /> : (
        <div className="tablewrap"><table><thead><tr><th>Username</th><th>Name</th><th>Role</th><th>Last sign-in</th><th>Status</th><th></th></tr></thead><tbody>
          {q.data.items.map((u: any) => <tr key={u.id}><td><code>{u.username}</code></td><td>{u.display_name}</td><td>{u.role_code}{u.scope_value ? ` · ${u.scope_value}` : ""}</td><td>{fmtDateTime(u.last_login_at)}</td><td><Status v={u.status === "ACTIVE" ? "ACTIVE" : "WITHDRAWN"} label={u.status} /></td>
            <td>{u.status === "ACTIVE" ? <ConfirmButton label="Deactivate" danger consequence={`${u.username} is signed out immediately and cannot sign in again.`} onConfirm={() => off.mutate(u.id)} /> : null}</td></tr>)}</tbody></table></div>
      )}
      {open ? <Modal title="Add user" onClose={() => setOpen(false)}>
        <Field label="Username"><input value={f.username} onChange={(e) => setF({ ...f, username: e.target.value })} /></Field>
        <Field label="Display name"><input value={f.display_name} onChange={(e) => setF({ ...f, display_name: e.target.value })} /></Field>
        <Field label="Role"><select value={f.role} onChange={(e) => setF({ ...f, role: e.target.value })}>{(aicte ? [["AR", "AICTE reviewer"], ["AS", "AICTE system admin"]] : [["IO", "Data operator"], ["IA", "Institution admin"]]).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></Field>
        {aicte ? <><Field label="Scope"><select value={f.scope_type} onChange={(e) => setF({ ...f, scope_type: e.target.value })}><option>NATIONAL</option><option>REGION</option><option>STATE</option></select></Field>
          {f.scope_type !== "NATIONAL" ? <Field label="Scope value" hint="e.g. SOUTH or Karnataka"><input value={f.scope_value} onChange={(e) => setF({ ...f, scope_value: e.target.value })} /></Field> : null}</> : null}
        <div className="row" style={{ marginTop: 12 }}><button className="btn" disabled={f.username.length < 3 || f.display_name.length < 2 || create.isPending} onClick={() => create.mutate()}>Create</button><button className="btn ghost" onClick={() => setOpen(false)}>Cancel</button></div></Modal> : null}
      {shown ? <Modal title="User created" onClose={() => setShown(null)}><p><code>{shown.username}</code> · {shown.role}</p><div className="alert info">{shown.note}</div>{shown.initial_password ? <p className="mono">{shown.initial_password}</p> : null}<button className="btn" onClick={() => setShown(null)}>Done</button></Modal> : null}
    </>
  );
}

function Clients() {
  const toast = useToast(); const qc = useQueryClient(); const q = useQuery({ queryKey: ["clients"], queryFn: () => api<any>("/admin/clients") });
  const [open, setOpen] = useState(false); const [name, setName] = useState(""); const [scopes, setScopes] = useState<string[]>([]); const [secret, setSecret] = useState<any>(null);
  const create = useMutation({ mutationFn: () => api<any>("/admin/clients", { body: { name, scopes } }), onSuccess: (r) => { setOpen(false); setName(""); setScopes([]); qc.invalidateQueries({ queryKey: ["clients"] }); setSecret({ id: r.client_id, secret: r.client_secret }); }, onError: fail(toast) });
  const rotate = useMutation({ mutationFn: (id: string) => api<any>(`/admin/clients/${id}/rotate`, { body: {} }), onSuccess: (r) => setSecret({ id: "new secret", secret: r.client_secret }), onError: fail(toast) });
  const disable = useMutation({ mutationFn: (id: string) => api(`/admin/clients/${id}/disable`, { body: {} }), onSuccess: () => { toast("Client disabled"); qc.invalidateQueries({ queryKey: ["clients"] }); }, onError: fail(toast) });
  return (
    <>
      <p className="hint">System clients call the API with the OAuth client-credentials grant (<code>POST /auth/token</code>). Secrets are shown once and stored only as a hash. A reference ERP script is in <code>scripts/mock_erp_client.py</code> <Simulated what="an institution ERP" />.</p>
      <div className="row" style={{ marginBottom: 10 }}><button className="btn small" onClick={() => setOpen(true)}>New client</button></div>
      {q.isLoading ? <Skeleton rows={3} /> : q.error ? <ErrorBox error={q.error} retry={() => q.refetch()} /> : (
        <div className="tablewrap"><table><thead><tr><th>Name</th><th>Client id</th><th>Scopes</th><th>Last used</th><th>Status</th><th></th></tr></thead><tbody>
          {q.data.items.map((c: any) => <tr key={c.id}><td>{c.name}</td><td><code>{c.client_id}</code></td><td>{c.scopes.join(", ")}</td><td>{fmtDateTime(c.last_used_at)}</td><td><Status v={c.status === "ACTIVE" ? "ACTIVE" : "WITHDRAWN"} label={c.status} /></td>
            <td className="row">{c.status === "ACTIVE" ? <><ConfirmButton label="Rotate secret" consequence="The old secret stops working at once." onConfirm={() => rotate.mutate(c.id)} /><ConfirmButton label="Disable" danger consequence="The client can no longer obtain tokens." onConfirm={() => disable.mutate(c.id)} /></> : null}</td></tr>)}
          {!q.data.items.length ? <tr><td colSpan={6} className="empty">No clients yet.</td></tr> : null}</tbody></table></div>
      )}
      {open ? <Modal title="New API client" onClose={() => setOpen(false)}>
        <Field label="Name"><input value={name} onChange={(e) => setName(e.target.value)} /></Field>
        <Field label="Scopes"><div className="row">{(q.data?.scopes || []).map((s: string) => <label key={s} style={{ display: "flex", gap: 6, alignItems: "center", fontWeight: 400 }}><input type="checkbox" style={{ width: "auto" }} checked={scopes.includes(s)} onChange={(e) => setScopes(e.target.checked ? [...scopes, s] : scopes.filter((x) => x !== s))} />{s}</label>)}</div></Field>
        <div className="row" style={{ marginTop: 12 }}><button className="btn" disabled={name.length < 2 || !scopes.length} onClick={() => create.mutate()}>Create</button><button className="btn ghost" onClick={() => setOpen(false)}>Cancel</button></div></Modal> : null}
      {secret ? <Secret title="Client secret" value={secret.secret} label={`Copy the secret for ${secret.id} now.`} onClose={() => setSecret(null)} /> : null}
    </>
  );
}

function Webhooks() {
  const toast = useToast(); const qc = useQueryClient();
  const q = useQuery({ queryKey: ["webhooks"], queryFn: () => api<any>("/admin/webhooks") });
  const [sel, setSel] = useState(""); const dl = useQuery({ queryKey: ["whd", sel], queryFn: () => api<any>(`/admin/webhook-deliveries${sel ? "?webhook_id=" + sel : ""}`), refetchInterval: 8000 });
  const [open, setOpen] = useState(false); const [url, setUrl] = useState(""); const [events, setEvents] = useState<string[]>([]); const [secret, setSecret] = useState<any>(null);
  const create = useMutation({ mutationFn: () => api<any>("/admin/webhooks", { body: { url, events } }), onSuccess: (r) => { setOpen(false); qc.invalidateQueries({ queryKey: ["webhooks"] }); setSecret(r.secret); }, onError: fail(toast) });
  const setStatus = useMutation({ mutationFn: ({ id, s }: any) => api(`/admin/webhooks/${id}/status?status=${s}`, { body: {} }), onSuccess: () => qc.invalidateQueries({ queryKey: ["webhooks"] }), onError: fail(toast) });
  const replay = useMutation({ mutationFn: (id: string) => api(`/admin/webhook-deliveries/${id}/replay`, { body: {} }), onSuccess: () => { toast("Queued for another attempt"); qc.invalidateQueries({ queryKey: ["whd"] }); }, onError: fail(toast) });
  return (
    <>
      <p className="hint">Deliveries are signed: <code>X-Sentinel-Signature: t=&lt;unix&gt;,v1=&lt;HMAC-SHA256(secret, t.body)&gt;</code>. They carry identifiers only, never personal data. Failed deliveries retry after 1 min, 5 min, 30 min, 2 h and 6 h, then go to the dead-letter list after 24 hours. Targets on private networks are blocked unless the local demo sink is allowed.</p>
      <div className="row" style={{ marginBottom: 10 }}><button className="btn small" onClick={() => setOpen(true)}>Add endpoint</button></div>
      <div className="tablewrap" style={{ marginBottom: 16 }}><table><thead><tr><th>Endpoint</th><th>Events</th><th>Delivered</th><th>Pending</th><th>Dead</th><th>Status</th><th></th></tr></thead><tbody>
        {q.data?.items.map((w: any) => <tr key={w.id}><td style={{ wordBreak: "break-all" }}><code>{w.url}</code></td><td>{w.events.join(", ")}</td><td>{w.delivered}</td><td>{w.pending}</td><td>{w.dead}</td><td><Status v={w.status === "ACTIVE" ? "ACTIVE" : "WITHDRAWN"} label={w.status} /></td>
          <td className="row"><button className="btn small ghost" onClick={() => setSel(sel === w.id ? "" : w.id)}>{sel === w.id ? "All" : "Deliveries"}</button><button className="btn small ghost" onClick={() => setStatus.mutate({ id: w.id, s: w.status === "ACTIVE" ? "PAUSED" : "ACTIVE" })}>{w.status === "ACTIVE" ? "Pause" : "Resume"}</button></td></tr>)}
        {q.data && !q.data.items.length ? <tr><td colSpan={7} className="empty">No endpoints yet.</td></tr> : null}</tbody></table></div>
      <h3>Delivery log</h3>
      <div className="tablewrap"><table><thead><tr><th>Event</th><th>Status</th><th>Attempts</th><th>Result</th><th>Created</th><th></th></tr></thead><tbody>
        {dl.data?.items.map((d: any) => <tr key={d.id}><td>{d.event_type}</td><td><Status v={d.status === "DELIVERED" ? "SUCCEEDED" : d.status === "DEAD_LETTERED" ? "DEAD" : d.status === "RETRYING" ? "UNCONFIRMED" : "PENDING"} label={d.status.replace("_", " ")} /></td><td>{d.attempts}</td><td>{d.last_status_code ? `HTTP ${d.last_status_code}` : d.last_error || "-"}</td><td>{fmtDateTime(d.created_at)}</td>
          <td>{d.status === "DEAD_LETTERED" ? <button className="btn small" onClick={() => replay.mutate(d.id)}>Replay</button> : null}</td></tr>)}
        {dl.data && !dl.data.items.length ? <tr><td colSpan={6} className="empty">No deliveries yet. Claim a seat to produce one.</td></tr> : null}</tbody></table></div>
      {open ? <Modal title="Add webhook endpoint" onClose={() => setOpen(false)}>
        <Field label="URL" hint="https://… (the local demo sink is allowed in demo mode)"><input value={url} onChange={(e) => setUrl(e.target.value)} /></Field>
        <Field label="Events"><div className="row">{(q.data?.events || []).map((s: string) => <label key={s} style={{ display: "flex", gap: 6, alignItems: "center", fontWeight: 400 }}><input type="checkbox" style={{ width: "auto" }} checked={events.includes(s)} onChange={(e) => setEvents(e.target.checked ? [...events, s] : events.filter((x) => x !== s))} />{s}</label>)}</div></Field>
        <div className="row" style={{ marginTop: 12 }}><button className="btn" disabled={!url || !events.length} onClick={() => create.mutate()}>Create</button><button className="btn ghost" onClick={() => setOpen(false)}>Cancel</button></div></Modal> : null}
      {secret ? <Secret title="Signing secret" value={secret} label="Use this to verify the signature on deliveries." onClose={() => setSecret(null)} /> : null}
    </>
  );
}

export function InstAdmin() {
  const [tab, setTab] = useState("users");
  return (<><PageHead title="Administration" sub="Users, system clients for your ERP, and signed webhooks. Everything here is limited to your institution." />
    <Tabs tabs={[["users", "Users"], ["clients", "API clients"], ["webhooks", "Webhooks"]]} tab={tab} set={setTab} />
    {tab === "users" ? <Users aicte={false} /> : tab === "clients" ? <Clients /> : <Webhooks />}</>);
}

export function AicteAdmin() {
  const [tab, setTab] = useState("users");
  return (<><PageHead title="AICTE administration" sub="Reviewer and system-admin accounts, and the system client that feeds the (simulated) academic liveness data." />
    <Tabs tabs={[["users", "Users"], ["clients", "Feed clients"]]} tab={tab} set={setTab} />
    {tab === "users" ? <Users aicte /> : <Clients />}</>);
}

/* ---------------------------------------------------------------- AICTE: credential lookup and verifier organisations */
export function AicteCredentials() {
  const toast = useToast(); const stepUp = useStepUp(); const { can } = useAuth(); const [ref, setRef] = useState(""); const [reason, setReason] = useState(""); const [revoke, setRevoke] = useState<any>(null);
  const [found, setFound] = useState<any>(null); const [err, setErr] = useState<unknown>(null);
  const look = async () => { try { setFound(await api(`/aicte/credentials/${encodeURIComponent(ref.trim())}`)); setErr(null); } catch (e) { setFound(null); setErr(e); } };
  const go = useMutation({ mutationFn: async () => api(`/aicte/credentials/${found.id}/revoke`, { body: { reason }, headers: { "X-Step-Up-Token": await stepUp() } }), onSuccess: () => { toast("Credential revoked; verifiers see it at once."); setRevoke(null); setReason(""); look(); }, onError: fail(toast) });
  return (
    <>
      <PageHead title="Credentials" sub="Look up a credential by its reference. You see the credential's own facts, never the student's identity." />
      <div className="row" style={{ alignItems: "end", marginBottom: 12 }}><div style={{ width: 340 }}><Field label="Credential reference"><input value={ref} onChange={(e) => setRef(e.target.value)} onKeyDown={(e) => e.key === "Enter" && ref && look()} /></Field></div><button className="btn" disabled={!ref.trim()} onClick={look}>Look up</button></div>
      {err ? <ErrorBox error={err} /> : null}
      {found ? <div className="card"><h3 style={{ marginTop: 0 }}>{found.programme_name} <Status v={found.status === "VALID" ? "VERIFIED" : "REJECTED"} label={found.status} /></h3>
        <table><tbody><tr><td>Institution</td><td>{found.institution_name}, {found.state}</td></tr><tr><td>Enrolment</td><td><code>{found.enrolment_ref}</code> · {found.academic_year}</td></tr><tr><td>Issued</td><td>{fmtDateTime(found.issued_at)} · valid until {fmtDate(found.valid_until)}</td></tr><tr><td>Issuer key</td><td><code>{found.issuer_kid}</code></td></tr>{found.revoked_reason ? <tr><td>Reason</td><td>{found.revoked_reason}</td></tr> : null}</tbody></table>
        {found.status === "VALID" && can("credentials:revoke") ? <div style={{ marginTop: 10 }}><button className="btn danger small" onClick={() => setRevoke(found)}>Revoke</button></div> : null}</div> : null}
      {revoke ? <Modal title="Revoke credential" onClose={() => setRevoke(null)}><p>Verifiers will immediately see this credential as revoked. The student is not penalised; this is recorded in the audit trail.</p>
        <Field label="Reason (min 5 characters)"><textarea rows={3} value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
        <div className="row" style={{ marginTop: 12 }}><button className="btn danger" disabled={reason.length < 5} onClick={() => go.mutate()}>Revoke</button><button className="btn ghost" onClick={() => setRevoke(null)}>Cancel</button></div></Modal> : null}
    </>
  );
}

export function VerifierOrgs() {
  const q = useQuery({ queryKey: ["vorgs"], queryFn: () => api<any>("/verifier-orgs") });
  const label: Record<string, string> = { EMPLOYMENT_VERIFICATION: "Employment", LOAN_ELIGIBILITY: "Loan", VISA_SUPPORT: "Visa", ADMISSION_TRANSFER: "Admission transfer", REGULATORY_CHECK: "Regulatory" };
  return (
    <>
      <PageHead title="Verifier organisations" sub="Who may ask what. Each organisation is limited to listed purposes, questions and fields, and to an hourly request limit." />
      {q.isLoading ? <Skeleton rows={4} /> : q.error ? <ErrorBox error={q.error} retry={() => q.refetch()} /> : (
        <div className="tablewrap"><table><thead><tr><th>Organisation</th><th>Tier</th><th>Approved purposes</th><th>Requests (7 days / hour)</th><th>Status</th></tr></thead><tbody>
          {q.data.items.map((o: any) => <tr key={o.id}><td><b>{o.name}</b><div className="hint">{o.contact_email}</div></td><td>{o.tier}</td>
            <td>{(o.scopes || []).map((s: any) => <div key={s.purpose}><b>{label[s.purpose] || s.purpose}</b>: {s.questions.join(", ")} <span className="hint">· {s.fields.length} fields · {s.hourly_limit}/h</span></div>)}</td>
            <td>{o.requests_7d} / {o.requests_1h}</td><td><Status v={o.status === "ACTIVE" ? "ACTIVE" : "REJECTED"} label={o.status} /></td></tr>)}</tbody></table></div>
      )}
    </>
  );
}

/* ---------------------------------------------------------------- AICTE: rule sets and policy simulator */
export function RuleSets() {
  const { can, user } = useAuth(); const toast = useToast(); const qc = useQueryClient(); const stepUp = useStepUp();
  const sets = useQuery({ queryKey: ["rulesets"], queryFn: () => api<any>("/rulesets"), refetchInterval: 20000 });
  const [sel, setSel] = useState<string>(""); const detail = useQuery({ queryKey: ["ruleset", sel], enabled: !!sel, queryFn: () => api<any>(`/rulesets/${sel}`) });
  const [edit, setEdit] = useState<any>(null); const [json, setJson] = useState(""); const [sev, setSev] = useState(""); const [msg, setMsg] = useState("");
  const sims = useQuery({ queryKey: ["sims", sel], enabled: !!sel, queryFn: () => api<any>(`/simulations?draft_id=${sel}`), refetchInterval: 4000 });
  const [simId, setSimId] = useState(""); const sim = useQuery({ queryKey: ["sim", simId], enabled: !!simId, queryFn: () => api<any>(`/simulations/${simId}`), refetchInterval: (q) => (q.state.data && ["DONE", "FAILED"].includes(q.state.data.status) ? false : 2000) });
  const refresh = () => { qc.invalidateQueries({ queryKey: ["rulesets"] }); qc.invalidateQueries({ queryKey: ["ruleset"] }); };
  const draft = useMutation({ mutationFn: () => api<any>("/rulesets/draft", { body: { notes: "Draft created from the console", retroactive: false } }), onSuccess: (r) => { toast(`Draft v${r.version_no} created from the active set`); setSel(r.id); refresh(); }, onError: fail(toast) });
  const save = useMutation({ mutationFn: () => { let params: any = undefined; if (json.trim()) { try { params = JSON.parse(json); } catch { throw new ApiProblem(422, { title: "Parameters are not valid JSON" }); } } return api(`/rulesets/${sel}/rules/${edit.rule_code}`, { method: "PUT", body: { params, severity: sev || undefined, message: msg || undefined } }); }, onSuccess: () => { toast("Rule updated in the draft"); setEdit(null); refresh(); }, onError: fail(toast) });
  const publish = useMutation({ mutationFn: async () => api(`/rulesets/${sel}/publish`, { body: {}, headers: { "X-Step-Up-Token": await stepUp() } }), onSuccess: (r: any) => { toast(`Published as v${r.version_no}`); refresh(); }, onError: fail(toast) });
  const rollback = useMutation({ mutationFn: async () => api(`/rulesets/${sel}/rollback`, { body: {}, headers: { "X-Step-Up-Token": await stepUp() } }), onSuccess: (r: any) => { toast(`v${r.version_no} is active again`); refresh(); }, onError: fail(toast) });
  const simulate = useMutation({ mutationFn: () => api<any>(`/rulesets/${sel}/simulate`, { body: { cycles: [cycle] } }), onSuccess: (r) => { setSimId(r.id); qc.invalidateQueries({ queryKey: ["sims"] }); }, onError: fail(toast) });
  const year = new Date().getMonth() >= 5 ? new Date().getFullYear() : new Date().getFullYear() - 1; const cycle0 = `${year}-${String(year + 1).slice(2)}`; const [cycle, setCycle] = useState(cycle0);
  const cur = sets.data?.items.find((s: any) => s.id === sel);
  const res = sim.data?.result;
  return (
    <>
      <PageHead title="Rule sets and policy simulator" sub="Rules are data. A published set is immutable; changes go through a draft, a different reviewer publishes it, and the previous set can be rolled back.">
        {can("rulesets:draft") ? <button className="btn small" onClick={() => draft.mutate()} disabled={draft.isPending}>New draft from active</button> : null}</PageHead>
      {sets.isLoading ? <Skeleton rows={3} /> : sets.error ? <ErrorBox error={sets.error} retry={() => sets.refetch()} /> : (
        <div className="tablewrap" style={{ marginBottom: 16 }}><table><thead><tr><th>Version</th><th>Name</th><th>Status</th><th>Drafted by</th><th>Published</th><th>Rules</th><th></th></tr></thead><tbody>
          {sets.data.items.map((s: any) => <tr key={s.id} style={s.id === sel ? { background: "#f0f7f3" } : undefined}><td>v{s.version_no}</td><td>{s.name}</td><td><Status v={s.status === "ACTIVE" ? "ACTIVE" : s.status === "DRAFT" ? "PENDING" : "CLOSED"} label={s.status} /></td><td>{s.drafted_by || "-"}</td><td>{s.published_at ? `${fmtDate(s.published_at)} by ${s.published_by}` : "-"}</td><td>{s.rule_count}</td><td><button className="btn small ghost" onClick={() => { setSel(s.id); setSimId(""); }}>Open</button></td></tr>)}</tbody></table></div>
      )}
      {sel && detail.data ? <>
        <div className="row" style={{ justifyContent: "space-between", marginBottom: 8 }}><h2 style={{ margin: 0 }}>v{detail.data.version_no} · {detail.data.name}</h2>
          <div className="row">
            {cur?.status === "DRAFT" && can("rulesets:publish") ? (detail.data.drafted_by === user?.id ? <span className="hint">You drafted this set, so a different reviewer must publish it.</span> : <ConfirmButton label="Publish" consequence="This set becomes the ACTIVE rule set for every new decision. Existing decisions keep the version that evaluated them. You will be asked for your password." onConfirm={() => publish.mutate()} />) : null}
            {cur?.status === "RETIRED" && can("rulesets:publish") ? <ConfirmButton label="Roll back to this version" consequence="Re-activates this set and retires the current one." onConfirm={() => rollback.mutate()} /> : null}</div></div>
        <div className="tablewrap"><table><thead><tr><th>Rule</th><th>Kind</th><th>Severity</th><th>Message</th><th>Parameters</th><th></th></tr></thead><tbody>
          {detail.data.rules.map((r: any) => <tr key={r.rule_code}><td><code>{r.rule_code}</code><div className="hint">{r.category}</div></td><td>{r.kind}</td><td><Status v={r.severity} /></td><td>{r.message_template}</td><td><code style={{ fontSize: 11 }}>{JSON.stringify(r.params)}</code></td>
            <td>{cur?.status === "DRAFT" && can("rulesets:draft") ? <button className="btn small ghost" onClick={() => { setEdit(r); setJson(JSON.stringify(r.params)); setSev(r.severity); setMsg(r.message_template); }}>Edit</button> : null}</td></tr>)}</tbody></table></div>
        {cur?.status === "DRAFT" && can("simulations:run") ? <div className="card" style={{ marginTop: 18 }}>
          <h3 style={{ marginTop: 0 }}>Policy simulator</h3>
          <p className="hint">Replays this draft over past enrolments (non-personal snapshot only) and shows how many extra findings it would raise and how much reviewer time that is. It changes no decision. Rules that need data outside the snapshot are listed as not simulated.</p>
          <div className="row" style={{ alignItems: "end" }}><div style={{ width: 160 }}><Field label="Academic year"><input value={cycle} onChange={(e) => setCycle(e.target.value)} /></Field></div><button className="btn" disabled={simulate.isPending} onClick={() => simulate.mutate()}>Run simulation</button></div>
          {sims.data?.items.length ? <div className="row" style={{ marginTop: 8 }}>{sims.data.items.slice(0, 5).map((s: any) => <button key={s.id} className={"btn small " + (s.id === simId ? "" : "ghost")} onClick={() => setSimId(s.id)}>{fmtDateTime(s.started_at)} · {s.status}</button>)}</div> : null}
          {sim.data ? <div style={{ marginTop: 12 }}>
            {sim.data.status !== "DONE" ? <div className="alert info">{sim.data.status === "FAILED" ? `Failed: ${sim.data.error}` : "Running… the worker picks this up in a few seconds."}</div> : <>
              <div className="kpis"><div className="kpi"><small>Enrolments replayed</small><b>{res.enrolments_replayed}</b></div><div className="kpi"><small>New findings</small><b>{res.new_findings_total}</b></div><div className="kpi"><small>Institutions with blocking findings</small><b>{res.institutions_that_would_be_flagged}</b></div><div className="kpi"><small>Reviewer workload</small><b>{Math.round(res.reviewer_workload_minutes / 60 * 10) / 10} h</b><small>{res.minutes_per_finding} min per finding</small></div></div>
              {Object.keys(res.by_rule).length ? <div className="tablewrap" style={{ marginTop: 10 }}><table><thead><tr><th>Rule</th><th>New findings</th></tr></thead><tbody>{Object.entries(res.by_rule).map(([k, v]) => <tr key={k}><td><code>{k}</code></td><td>{String(v)}</td></tr>)}</tbody></table></div> : <p className="hint">This draft raises no new findings.</p>}
              {res.institutions.length ? <div className="tablewrap" style={{ marginTop: 10 }}><table><thead><tr><th>Institution</th><th>New findings</th><th>Blocking</th></tr></thead><tbody>{res.institutions.slice(0, 10).map((i: any) => <tr key={i.institution}><td>{i.institution}</td><td>{i.new_findings}</td><td>{i.blocking}</td></tr>)}</tbody></table></div> : null}
              {sim.data.skipped_rules?.length ? <p className="hint">Not simulated (inputs outside the snapshot): {sim.data.skipped_rules.join(", ")}</p> : null}
              <ul className="hint">{res.notes.map((n: string) => <li key={n}>{n}</li>)}</ul></>}</div> : null}</div> : null}
      </> : sel ? <Skeleton rows={4} /> : <Empty>Select a rule set to see its rules.</Empty>}
      {edit ? <Modal title={`Edit ${edit.rule_code}`} onClose={() => setEdit(null)}>
        <Field label="Severity"><select value={sev} onChange={(e) => setSev(e.target.value)}>{["Blocker", "Error", "Warning", "Info"].map((s) => <option key={s}>{s}</option>)}</select></Field>
        <Field label="Message"><input value={msg} onChange={(e) => setMsg(e.target.value)} /></Field>
        <Field label="Parameters (JSON)" hint="Rules are interpreted from data; only known kinds and fields are accepted."><textarea rows={4} className="mono" value={json} onChange={(e) => setJson(e.target.value)} /></Field>
        <div className="row" style={{ marginTop: 12 }}><button className="btn" disabled={save.isPending} onClick={() => save.mutate()}>Save to draft</button><button className="btn ghost" onClick={() => setEdit(null)}>Cancel</button></div></Modal> : null}
    </>
  );
}

/* ---------------------------------------------------------------- public: institution onboarding */
export function Onboarding() {
  const toast = useToast(); const [f, setF] = useState({ aicte_id: "", officer_name: "", designation: "", email: "", phone: "" }); const [done, setDone] = useState<any>(null); const [err, setErr] = useState<unknown>(null);
  const [tok, setTok] = useState(""); const [status, setStatus] = useState<any>(null);
  const send = useMutation({ mutationFn: () => api<any>("/onboarding/institutions", { body: { ...f, phone: f.phone || null } }), onSuccess: (r) => { setDone(r); setErr(null); }, onError: (e) => setErr(e) });
  const check = async () => { try { setStatus(await api(`/onboarding/status/${encodeURIComponent(tok.trim())}`)); } catch (e) { toast((e as ApiProblem).detail || (e as ApiProblem).title, true); } };
  return (
    <div>
      <SiteHeader />
      <div className="main" style={{ margin: "0 auto", maxWidth: 760 }}>
        <PageHead title="Institution onboarding" sub="Apply for institution access. An AICTE reviewer checks the nodal officer against the master record and approves the application." />
        {!done ? <div className="card">
          <div className="cols2">
            <Field label="AICTE ID" hint="As printed on your approval letter"><input value={f.aicte_id} onChange={(e) => setF({ ...f, aicte_id: e.target.value })} /></Field>
            <Field label="Nodal officer name"><input value={f.officer_name} onChange={(e) => setF({ ...f, officer_name: e.target.value })} /></Field>
            <Field label="Designation"><input value={f.designation} onChange={(e) => setF({ ...f, designation: e.target.value })} /></Field>
            <Field label="Official email"><input type="email" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} /></Field>
            <Field label="Phone (optional)"><input value={f.phone} onChange={(e) => setF({ ...f, phone: e.target.value })} /></Field>
          </div>
          {err ? <ErrorBox error={err} /> : null}
          <div style={{ marginTop: 12 }}><button className="btn" disabled={send.isPending || !f.aicte_id || !f.officer_name || !f.designation || !f.email} onClick={() => send.mutate()}>Submit application</button></div>
          <p className="hint">The identity check compares your email domain with the master record. It is <Simulated what="a verified officer attestation" /> in this prototype. Demo: an application for AICTE ID <code>1-199999</code> is already waiting in the AICTE reviewer queue (Reviews, Onboarding).</p>
        </div> : <div className="card"><h2 style={{ marginTop: 0 }}>Application received for {done.institution}</h2>
          <p>Identity check: <b>{done.identity_check}</b>. Keep this status code to follow your application:</p><p className="mono">{done.status_token}</p><p className="hint">{done.label}</p></div>}
        <h2 style={{ marginTop: 24 }}>Check an application</h2>
        <div className="row" style={{ alignItems: "end" }}><div style={{ width: 320 }}><Field label="Status code"><input value={tok} onChange={(e) => setTok(e.target.value)} /></Field></div><button className="btn" disabled={!tok.trim()} onClick={check}>Check</button></div>
        {status ? <div className="alert info" style={{ marginTop: 10 }}>{status.institution}: <Status v={status.status} />{status.reason ? ` — ${status.reason}` : ""}{status.status === "APPROVED" ? <> · <Link to="/login">Sign in</Link></> : null}</div> : null}
      </div>
    </div>
  );
}
