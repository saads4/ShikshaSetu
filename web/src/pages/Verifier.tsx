import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { ApiProblem, fmtDate, fmtDateTime, VERIFY_BASE, vapi } from "../api";
import { verifyJws } from "../jws";
import { ErrorBox, Field, PageHead, Skeleton, Status, useToast } from "../ui";
import { SiteHeader } from "./Public";

const FIELD_LABEL: Record<string, string> = { institution_name: "Institution", programme_name: "Programme", programme_code: "Programme code", academic_year: "Academic year", enrolment_status: "Enrolment status", approved_seat: "Approved seat" };
const PURPOSE_LABEL: Record<string, string> = { EMPLOYMENT_VERIFICATION: "Employment verification", LOAN_ELIGIBILITY: "Loan eligibility", VISA_SUPPORT: "Visa support", ADMISSION_TRANSFER: "Admission transfer", REGULATORY_CHECK: "Regulatory check" };
const QUESTION_LABEL: Record<string, string> = { IS_ENROLLED: "Is this person currently enrolled?", PROGRAMME_MATCH: "Is the programme this code?", ENROLLED_IN_YEAR: "Was this person enrolled in this academic year?" };

/** The answer card, shared by the verifier portal and the public share-link page. */
function Answer({ a }: { a: any }) {
  const [sig, setSig] = useState<{ ok: boolean; reason?: string; expired?: boolean } | null>(null);
  const check = async () => {
    if (!a.answer_jwt) return;
    try { const jwks = await vapi<any>("/public/jwks"); setSig(await verifyJws(a.answer_jwt, jwks)); } catch (e) { setSig({ ok: false, reason: (e as ApiProblem).title }); }
  };
  if (a.result === "NEUTRAL") return <div className="alert info"><b>No verifiable answer is available for this request.</b><div className="hint">This is the same reply for an unknown reference, a missing or withdrawn consent, an expired link and anything else, so nothing can be learned from the difference.</div></div>;
  return (
    <div className="card" style={{ borderColor: a.result === "YES" ? "var(--green)" : "var(--red)" }}>
      <h2 style={{ marginTop: 0 }}>Answer <Status v={a.result === "YES" ? "VERIFIED" : "REJECTED"} label={a.result} /></h2>
      {Object.keys(a.fields || {}).length ? <table><tbody>{Object.entries(a.fields).map(([k, v]) => <tr key={k}><td>{FIELD_LABEL[k] || k}</td><td><b>{String(v)}</b></td></tr>)}</tbody></table> : <p className="hint">No fields were disclosed with this answer.</p>}
      <p className="hint">Shared {a.disclosure?.shared ?? 0} of {a.disclosure?.of ?? 40} possible fields. Nothing else about the person is disclosed.</p>
      <div className="row"><button className="btn small" onClick={check}>Verify signature in this browser</button>
        {sig ? <span className={"tag " + (sig.ok && !sig.expired ? "t-green" : "t-red")}>{sig.ok ? (sig.expired ? "Signed, but expired" : "Signature valid") : sig.reason || "Invalid"}</span> : null}</div>
      <p className="hint">Answer expires {fmtDateTime(a.expires_at ? new Date(a.expires_at * 1000).toISOString() : null)} · nonce <code>{a.nonce}</code></p>
      <p className="hint"><b>{a.boundary}</b></p>
    </div>
  );
}

/* ---------------------------------------------------------------- verifier portal */
export function VerifierHome() {
  const toast = useToast();
  const usage = useQuery({ queryKey: ["v-usage"], queryFn: () => vapi<any>("/verify/usage", { auth: true }) });
  const [mode, setMode] = useState<"ref" | "token">("ref"); const [id, setId] = useState(""); const [purpose, setPurpose] = useState("");
  const [qt, setQt] = useState("IS_ENROLLED"); const [code, setCode] = useState(""); const [year, setYear] = useState("");
  const scope = usage.data?.scopes.find((s: any) => s.purpose_code === purpose);
  useEffect(() => { if (!purpose && usage.data?.scopes?.length) setPurpose(usage.data.scopes[0].purpose_code); }, [usage.data]);
  const ask = useMutation({
    mutationFn: () => vapi<any>("/verify", { auth: true, body: { [mode === "ref" ? "credential_ref" : "share_token"]: id.trim(), purpose, question: { type: qt, ...(qt === "PROGRAMME_MATCH" ? { programme_code: code } : {}), ...(qt === "ENROLLED_IN_YEAR" ? { academic_year: year } : {}) }, nonce: crypto.randomUUID().slice(0, 18) } }),
    onSuccess: () => usage.refetch(), onError: (e) => toast(`${(e as ApiProblem).title}${(e as ApiProblem).detail ? ": " + (e as ApiProblem).detail : ""}`, true) });
  return (
    <>
      <PageHead title="Verify an enrolment" sub="Ask a yes/no question about a credential. You receive a signed answer and only the fields the student agreed to share for your purpose." />
      {usage.isLoading ? <Skeleton rows={4} /> : usage.error ? <ErrorBox error={usage.error} retry={() => usage.refetch()} /> : (
        <div className="cols2">
          <div className="card">
            <div className="alert info">{usage.data.organisation} · {usage.data.tier.toLowerCase()} · used {usage.data.used_this_hour} requests this hour{scope ? ` of ${scope.hourly_limit}` : ""}.</div>
            <div className="row" style={{ marginBottom: 8 }}>
              <button className={"btn small " + (mode === "ref" ? "" : "ghost")} onClick={() => setMode("ref")}>Credential reference</button>
              <button className={"btn small " + (mode === "token" ? "" : "ghost")} onClick={() => setMode("token")}>Share-link token</button></div>
            <Field label={mode === "ref" ? "Credential reference (from the QR code or certificate)" : "Share token given by the student"}><input value={id} onChange={(e) => setId(e.target.value)} autoComplete="off" /></Field>
            <Field label="Purpose" hint="Your organisation is approved only for the purposes listed."><select value={purpose} onChange={(e) => { setPurpose(e.target.value); setQt("IS_ENROLLED"); }}>
              {usage.data.scopes.map((s: any) => <option key={s.purpose_code} value={s.purpose_code}>{PURPOSE_LABEL[s.purpose_code] || s.purpose_code}</option>)}</select></Field>
            <Field label="Question"><select value={qt} onChange={(e) => setQt(e.target.value)}>{(scope?.allowed_questions || ["IS_ENROLLED"]).map((q: string) => <option key={q} value={q}>{QUESTION_LABEL[q] || q}</option>)}</select></Field>
            {qt === "PROGRAMME_MATCH" ? <Field label="Programme code"><input value={code} onChange={(e) => setCode(e.target.value)} placeholder="e.g. CSE" /></Field> : null}
            {qt === "ENROLLED_IN_YEAR" ? <Field label="Academic year"><input value={year} onChange={(e) => setYear(e.target.value)} placeholder="e.g. 2026-27" /></Field> : null}
            {scope ? <p className="hint">Fields you may receive for this purpose, if the student agreed: {scope.allowed_fields.map((f: string) => FIELD_LABEL[f] || f).join(", ")}.</p> : null}
            <button className="btn" disabled={!id.trim() || !purpose || ask.isPending} onClick={() => ask.mutate()}>{ask.isPending ? "Asking…" : "Ask"}</button>
          </div>
          <div>{ask.data ? <Answer a={ask.data} /> : <div className="alert info">Answers are signed by the verification service and are only valid for five minutes. A signature proves integrity and key control, not that the underlying academic fact is true.</div>}</div>
        </div>
      )}
    </>
  );
}

export function VerifierUsage() {
  const usage = useQuery({ queryKey: ["v-usage"], queryFn: () => vapi<any>("/verify/usage", { auth: true }) });
  return (
    <>
      <PageHead title="Usage and history" sub="Every request your organisation made is logged and visible to the student it concerns." />
      {usage.isLoading ? <Skeleton rows={5} /> : usage.error ? <ErrorBox error={usage.error} retry={() => usage.refetch()} /> : (
        <>
          <div className="tablewrap" style={{ marginBottom: 16 }}><table><thead><tr><th>Purpose</th><th>Questions</th><th>Fields</th><th>Hourly limit</th></tr></thead><tbody>
            {usage.data.scopes.map((s: any) => <tr key={s.purpose_code}><td>{PURPOSE_LABEL[s.purpose_code] || s.purpose_code}</td><td>{s.allowed_questions.join(", ")}</td><td>{s.allowed_fields.map((f: string) => FIELD_LABEL[f] || f).join(", ")}</td><td>{s.hourly_limit}</td></tr>)}</tbody></table></div>
          <div className="tablewrap"><table><thead><tr><th>When</th><th>Purpose</th><th>Question</th><th>Via</th><th>Result</th><th>Fields</th></tr></thead><tbody>
            {usage.data.history.map((h: any, i: number) => <tr key={i}><td>{fmtDateTime(h.created_at)}</td><td>{PURPOSE_LABEL[h.purpose_code] || h.purpose_code || "-"}</td><td>{h.question?.type}</td><td>{h.via.replace(/_/g, " ").toLowerCase()}</td><td><Status v={h.outcome === "YES" ? "VERIFIED" : h.outcome === "NO" ? "REJECTED" : "PENDING"} label={h.outcome} /></td><td>{h.fields_disclosed.length}</td></tr>)}
            {!usage.data.history.length ? <tr><td colSpan={6} className="empty">No requests yet.</td></tr> : null}</tbody></table></div>
          <p className="hint" style={{ marginTop: 10 }}>{usage.data.boundary}</p>
        </>
      )}
    </>
  );
}

/* ---------------------------------------------------------------- public status page (/v/:ref) */
const KEY = (ref: string) => `ss_status_${ref}`;
export function StatusPage() {
  const { ref = "" } = useParams();
  const [state, setState] = useState<{ data?: any; offline?: boolean; err?: any } | null>(null);
  const load = async () => {
    try {
      const data = await vapi<any>(`/v/${encodeURIComponent(ref)}`);
      try { localStorage.setItem(KEY(ref), JSON.stringify(data)); } catch { /* storage may be blocked */ }
      setState({ data });
    } catch (e) {
      let last: any = null;
      try { last = JSON.parse(localStorage.getItem(KEY(ref)) || "null"); } catch { /* ignore */ }
      if ((e as ApiProblem).status === 0 && last) setState({ data: last, offline: true }); else setState({ err: e });
    }
  };
  useEffect(() => { load(); }, [ref]);
  const d = state?.data;
  const word: Record<string, string> = { VALID: "This credential is valid.", REVOKED: "This credential has been revoked.", SUPERSEDED: "This credential was replaced by a corrected one.", EXPIRED: "This credential has expired.", NEUTRAL: "No verifiable status is available for this reference." };
  return (
    <div>
      <SiteHeader />
      <div className="main" style={{ margin: "0 auto", maxWidth: 720 }}>
        <PageHead title="Credential status" sub="A public check. It shows status only and never any personal detail." />
        {!state ? <Skeleton rows={3} /> : state.err ? <ErrorBox error={state.err} retry={load} /> : (
          <div className="card">
            {state.offline ? <div className="alert warn">You are offline. This is the last status this browser saw, checked {fmtDateTime(d.checked_at)}. It may be out of date.</div> : null}
            <h2 style={{ marginTop: 0 }}><Status v={d.result === "VALID" ? "VERIFIED" : d.result === "NEUTRAL" ? "PENDING" : "REJECTED"} label={d.result} /> {word[d.result] || d.result}</h2>
            {d.issuer ? <table><tbody><tr><td>Issued by</td><td><b>{d.issuer}</b></td></tr><tr><td>Issued</td><td>{fmtDateTime(d.issued_at)}</td></tr><tr><td>Valid until</td><td>{fmtDate(d.valid_until)}</td></tr><tr><td>Checked</td><td>{fmtDateTime(d.checked_at)}</td></tr></tbody></table> : null}
            <p className="hint"><b>{d.boundary}</b></p>
            {d.result === "VALID" ? <img src={`${VERIFY_BASE}/qr/${encodeURIComponent(ref)}`} alt="QR code for this status page" width={120} height={120} /> : null}
            <p className="hint">To ask a question (for example, "is this person enrolled?") you need either the student's share link or an approved verifier account.</p>
          </div>
        )}
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------- public share-link page (/s/:token) */
export function SharePage() {
  const { token = "" } = useParams();
  const toast = useToast();
  const [qt, setQt] = useState("IS_ENROLLED"); const [code, setCode] = useState(""); const [year, setYear] = useState("");
  const ask = useMutation({ mutationFn: () => vapi<any>("/public/verify", { body: { token, question: { type: qt, ...(qt === "PROGRAMME_MATCH" ? { programme_code: code } : {}), ...(qt === "ENROLLED_IN_YEAR" ? { academic_year: year } : {}) } } }),
    onError: (e) => toast(`${(e as ApiProblem).title}${(e as ApiProblem).detail ? ": " + (e as ApiProblem).detail : ""}`, true) });
  return (
    <div>
      <SiteHeader />
      <div className="main" style={{ margin: "0 auto", maxWidth: 720 }}>
        <PageHead title="Verify an enrolment" sub="A student shared this link with you. You can ask the questions below; you will see only the fields they chose to share." />
        <div className="card">
          <Field label="Question"><select value={qt} onChange={(e) => setQt(e.target.value)}>{Object.entries(QUESTION_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></Field>
          {qt === "PROGRAMME_MATCH" ? <Field label="Programme code"><input value={code} onChange={(e) => setCode(e.target.value)} placeholder="e.g. CSE" /></Field> : null}
          {qt === "ENROLLED_IN_YEAR" ? <Field label="Academic year"><input value={year} onChange={(e) => setYear(e.target.value)} placeholder="e.g. 2026-27" /></Field> : null}
          <div style={{ marginTop: 10 }}><button className="btn" disabled={ask.isPending} onClick={() => ask.mutate()}>Ask</button></div>
        </div>
        {ask.data ? <div style={{ marginTop: 14 }}><Answer a={ask.data} /></div> : null}
      </div>
    </div>
  );
}
