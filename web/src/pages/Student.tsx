import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiProblem, fmtDate, fmtDateTime } from "../api";
import { ErrorBox, Field, LockTimeline, Modal, PageHead, Simulated, Skeleton, Status, useToast } from "../ui";

const enc = new TextEncoder();
const hex = (b: ArrayBuffer | Uint8Array) => Array.from(new Uint8Array(b as ArrayBuffer)).map((x) => x.toString(16).padStart(2, "0")).join("");
const unhex = (h: string) => Uint8Array.from(h.match(/.{2}/g)!.map((x) => parseInt(x, 16)));
const sha = async (...parts: Uint8Array[]) => { const all = new Uint8Array(parts.reduce((n, p) => n + p.length, 0)); let o = 0; parts.forEach((p) => { all.set(p, o); o += p.length; }); return new Uint8Array(await crypto.subtle.digest("SHA-256", all)); };
const canon = (v: any): string => v === null || typeof v !== "object" ? JSON.stringify(v) : Array.isArray(v) ? "[" + v.map(canon).join(",") + "]" : "{" + Object.keys(v).sort().map((k) => JSON.stringify(k) + ":" + canon(v[k])).join(",") + "}";

function Proof({ id, actAs }: { id: string; actAs?: string }) {
  const [p, setP] = useState<any>(null); const [res, setRes] = useState<string>(""); const [err, setErr] = useState<any>(null);
  const load = async () => { try { setP(await api<any>(`/student/enrolments/${id}/proof`, { actAs })); setErr(null); } catch (e) { setErr(e); } };
  const verify = async () => {
    // RFC 6962: leaf = SHA-256(0x00 || canonical JSON), node = SHA-256(0x01 || left || right); computed here with the browser's WebCrypto.
    let h = await sha(Uint8Array.of(0), enc.encode(canon(p.leaf)));
    const leafOk = hex(h) === p.leaf_hash;
    for (const s of p.path) { const sib = unhex(s.hash); h = s.side === "R" ? await sha(Uint8Array.of(1), h, sib) : await sha(Uint8Array.of(1), sib, h); }
    setRes(leafOk && hex(h) === p.root ? "VERIFIED: your enrolment record is part of the cohort that your institution signed and AICTE countersigned." : "FAILED: this proof does not match the signed root.");
  };
  return (
    <div style={{ marginTop: 10 }}>
      {!p ? <button className="btn link" onClick={load}>Show my cohort inclusion proof</button> : <div className="card" style={{ background: "#f8fbf9" }}>
        <b>Inclusion proof</b> <span className="hint">(commitment v{p.commitment_version}, {p.leaf_count} enrolments in the cohort)</span>
        <p className="mono" style={{ wordBreak: "break-all" }}>root {p.root}</p>
        <p className="hint">{p.hashing}. {p.note} A signature proves integrity, not truth.</p>
        <button className="btn small" onClick={verify}>Verify in my browser</button>{res ? <div className={"alert " + (res.startsWith("VERIFIED") ? "ok" : "err")}>{res}</div> : null}</div>}
      {err ? <div className="hint">{(err as any).detail || "No proof is available yet. A proof exists once AICTE has countersigned your institution's cut-off commitment."}</div> : null}
    </div>
  );
}

export function StudentHome() {
  const toast = useToast(); const qc = useQueryClient();
  const links = useQuery({ queryKey: ["guardian-links"], queryFn: () => api<any>("/student/guardian-links") });
  const [actAs, setActAs] = useState<string>("");
  const q = useQuery({ queryKey: ["my-enrolments", actAs], queryFn: async () => {
    try { return await api<any>("/student/enrolments", { actAs: actAs || undefined }); }
    catch (e) { if ((e as ApiProblem).status === 404 && !actAs) return { items: [], acting_for_minor: false, noRecord: true }; throw e; }
  }, refetchInterval: 15000 });
  const [otp, setOtp] = useState<Record<string, string>>({}); const [dispute, setDispute] = useState<any>(null); const [note, setNote] = useState("");
  const refresh = () => qc.invalidateQueries({ queryKey: ["my-enrolments"] });
  const confirm = useMutation({ mutationFn: ({ id }: any) => api(`/student/enrolments/${id}/confirm`, { body: { otp: otp[id] }, actAs: actAs || undefined }),
    onSuccess: (r: any) => { toast(r.state === "VERIFIED" ? "Confirmed. Your enrolment is verified." : `Confirmed. Overall status: ${r.state}`); refresh(); },
    onError: (e) => toast(`${(e as ApiProblem).title}${(e as ApiProblem).detail ? " — " + (e as ApiProblem).detail : ""}`, true) });
  const resend = useMutation({ mutationFn: (id: string) => api(`/student/enrolments/${id}/resend`, { body: {}, actAs: actAs || undefined }), onSuccess: () => toast("A new code was sent (see the Dev SMS Inbox)"), onError: (e) => toast((e as ApiProblem).title, true) });
  const disp = useMutation({ mutationFn: (id: string) => api(`/student/enrolments/${id}/dispute`, { body: { note }, actAs: actAs || undefined }), onSuccess: () => { toast("Your dispute was recorded and AICTE was notified."); setDispute(null); refresh(); }, onError: (e) => toast((e as ApiProblem).title, true) });

  const guardianOf = links.data?.items?.filter((l: any) => l.status === "VERIFIED") || [];
  return (
    <>
      <PageHead title="My enrolments" sub="Your admission is only VERIFIED when your institution, the registry and you all agree.">
        {guardianOf.length ? <div><label htmlFor="act">Guardian mode</label><select id="act" value={actAs} onChange={(e) => setActAs(e.target.value)}><option value="">My own record</option>{guardianOf.map((l: any) => <option key={l.student_id} value={l.student_id}>Linked minor {l.student_ref} ({l.relationship.toLowerCase()})</option>)}</select></div> : null}
      </PageHead>
      {actAs ? <div className="alert info">You are acting as the verified guardian of a minor. Confirmations and disputes are recorded as guardian actions.</div> : null}
      <div className="alert info">Refusing APAAR consent never affects whether you are counted. The registry and SMS here are <Simulated what="national systems" /> — find the one-time code in the <a href="/dev/sms-inbox" target="_blank" rel="noreferrer">Dev SMS Inbox</a>.</div>
      {q.isLoading ? <Skeleton rows={6} /> : q.error ? <ErrorBox error={q.error} retry={() => q.refetch()} /> : q.data.noRecord ? <div className="alert warn">This account is a guardian account. Choose a linked minor above.</div> : null}
      {q.data?.items.map((e: any) => (
        <div className="card" key={e.id} style={{ marginBottom: 16 }}>
          <h3>{e.programme_name} <span className="row"><Status v={e.status} /><Status v={e.overall_state} /></span></h3>
          <div className="hint">{e.institution_name}, {e.state} · {e.academic_year} · reference <code>{e.enrolment_ref}</code> · claimed {fmtDate(e.claimed_at)}</div>
          <div style={{ marginTop: 12 }}><LockTimeline v={e} deadline={e.deadline_at} /></div>
          <ul style={{ margin: "12px 0" }}>
            <li><b>Your institution claimed this seat.</b> Signed with the institution's key.</li>
            <li><b>The APAAR registry {e.lock2_status === "PASSED" ? "confirmed" : e.lock2_status === "PROVISIONAL_OK" ? "is not needed for a provisional ID" : e.lock2_status === "FAILED" ? "found a mismatch in" : "has not yet checked"} your details.</b>{e.lock2_status === "FAILED" ? " Your institution has been asked to fix the record." : ""}</li>
            <li><b>You confirm that you are enrolled.</b> {e.lock3_status === "CONFIRMED" ? "Done." : e.can_confirm ? "Enter the code sent to " + (e.target_masked || "your registered number") + "." : e.needs_guardian ? "A guardian confirms for students under 18." : ""}</li>
          </ul>
          {e.can_confirm && e.otp_status === "SENT" ? <div className="row" style={{ alignItems: "end" }}>
            <div style={{ width: 200 }}><Field label={`One-time code · ${e.attempts_left ?? 5} attempts left`}><input type="text" inputMode="numeric" maxLength={6} value={otp[e.id] || ""} onChange={(x) => setOtp({ ...otp, [e.id]: x.target.value.replace(/\D/g, "") })} aria-label="One-time code" /></Field></div>
            <button className="btn" disabled={(otp[e.id] || "").length !== 6 || confirm.isPending} onClick={() => confirm.mutate({ id: e.id })}>Confirm enrolment</button>
            <button className="btn ghost" onClick={() => resend.mutate(e.id)}>Send a new code</button></div>
            : e.can_confirm ? <div className="row"><span className="hint">{e.otp_status === "EXPIRED" || e.otp_status === "FAILED" ? "The last code can no longer be used." : "No code is active."}</span><button className="btn ghost" onClick={() => resend.mutate(e.id)}>Send a new code</button></div> : null}
          {e.status !== "WITHDRAWN" && e.lock3_status !== "DISPUTED" ? <div style={{ marginTop: 10 }}><button className="btn link" onClick={() => setDispute(e)}>I did not enrol here</button></div> : null}
          {e.lock3_status === "DISPUTED" ? <div className="alert err">You disputed this enrolment. AICTE has opened a case; you do not need to do anything else.</div> : null}
          {e.status !== "WITHDRAWN" ? <Proof id={e.id} actAs={actAs || undefined} /> : null}
          {e.channel_trust === "LOW" ? <div className="hint">Confirmed through a lower-trust channel ({String(e.channel).replace(/_/g, " ").toLowerCase()}); this is reported separately.</div> : null}
        </div>
      ))}
      {q.data && !q.data.items.length && !q.data.noRecord ? <div className="alert info">No enrolments are linked to your account.</div> : null}
      {dispute ? <Modal title="I did not enrol here" onClose={() => setDispute(null)}>
        <p>Tell AICTE that you did not apply to <b>{dispute.institution_name}</b>. The enrolment is marked DISPUTED and a case is opened automatically. No penalty follows from a dispute.</p>
        <Field label="Note (optional)"><textarea rows={3} value={note} onChange={(e) => setNote(e.target.value)} /></Field>
        <div className="row" style={{ marginTop: 12 }}><button className="btn danger" onClick={() => disp.mutate(dispute.id)}>Submit dispute</button><button className="btn ghost" onClick={() => setDispute(null)}>Cancel</button></div></Modal> : null}
    </>
  );
}
