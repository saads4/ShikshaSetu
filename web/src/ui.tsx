import { createContext, useCallback, useContext, useEffect, useState, ReactNode } from "react";
import { ApiProblem, fmtDateTime } from "./api";

/* ---------- toasts ---------- */
const ToastCtx = createContext<(msg: string, err?: boolean) => void>(() => {});
export const useToast = () => useContext(ToastCtx);
export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<{ id: number; msg: string; err?: boolean }[]>([]);
  const push = useCallback((msg: string, err?: boolean) => {
    const id = Date.now() + Math.random();
    setItems((x) => [...x, { id, msg, err }]);
    setTimeout(() => setItems((x) => x.filter((i) => i.id !== id)), 4500);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="toasts" aria-live="polite">{items.map((i) => <div key={i.id} className={"toast" + (i.err ? " err" : "")}>{i.msg}</div>)}</div>
    </ToastCtx.Provider>
  );
}

/* ---------- status vocabulary: one component maps enum -> colour (PRD 10.2) ---------- */
const COLORS: Record<string, [string, string]> = {
  VERIFIED: ["t-green", "Verified"], PASSED: ["t-green", "Passed"], CONFIRMED: ["t-green", "Confirmed"], SIGNED: ["t-green", "Signed"], ACTIVE: ["t-green", "Active"],
  APPROVED: ["t-green", "Approved"], ACKNOWLEDGED: ["t-green", "Acknowledged"], RESOLVED: ["t-green", "Resolved"], SUCCEEDED: ["t-green", "Succeeded"],
  PENDING: ["t-blue", "Pending"], CLAIMED: ["t-blue", "Claimed"], SENT: ["t-blue", "Sent"], OPEN: ["t-blue", "Open"], QUEUED: ["t-blue", "Queued"],
  PENDING_FIX: ["t-orange", "Pending fix"], FAILED: ["t-orange", "Failed"], Error: ["t-orange", "Error"],
  PENDING_VERIFICATION: ["t-purple", "Pending registry"], PROVISIONAL_OK: ["t-purple", "Provisional ok"], NOGEN: ["t-purple", "No APAAR (provisional)"],
  CONSENT_REFUSED: ["t-purple", "APAAR consent refused"], PROBABLE_MATCH: ["t-purple", "Probable match"],
  UNCONFIRMED: ["t-amber", "Unconfirmed"], EXPIRED: ["t-amber", "Expired"], Warning: ["t-amber", "Warning"], REJECTED: ["t-red", "Rejected"],
  DISPUTED: ["t-red", "Disputed"], Blocker: ["t-red", "Blocker"], DEAD: ["t-red", "Dead"], DOWN: ["t-red", "Down"],
  WITHDRAWN: ["t-grey", "Withdrawn"], TRANSFERRED_OUT: ["t-grey", "Transferred out"], ADMITTED: ["t-green", "Admitted"], Info: ["t-blue", "Info"],
  RUNNING: ["t-blue", "Running"], UP: ["t-green", "Up"], SLOW: ["t-amber", "Slow"], CLOSED: ["t-grey", "Closed"], OPEN_BREAKER: ["t-red", "Open"],
};
export function Status({ v, label }: { v?: string | null; label?: string }) {
  if (!v) return <span className="tag t-grey">-</span>;
  const [cls, text] = COLORS[v] || ["t-grey", v.replace(/_/g, " ").toLowerCase().replace(/^./, (c) => c.toUpperCase())];
  return <span className={"tag " + cls}>{label || text}</span>;
}

export function Simulated({ what }: { what: string }) {
  return <span className="sim" title={`SIMULATED: stands in for ${what}. No real system or person is involved.`}>SIMULATED</span>;
}

export function AsOf({ at, onRefresh }: { at?: string | null; onRefresh?: () => void }) {
  const [, tick] = useState(0);
  useEffect(() => { const t = setInterval(() => tick((x) => x + 1), 10000); return () => clearInterval(t); }, []);
  if (!at) return <span className="asof" onClick={onRefresh}>as of: waiting</span>;
  const s = Math.max(0, Math.round((Date.now() - new Date(at).getTime()) / 1000));
  const txt = s < 60 ? `${s}s ago` : s < 3600 ? `${Math.round(s / 60)} min ago` : fmtDateTime(at);
  return <button className={"asof" + (s > 60 ? " stale" : "")} onClick={onRefresh} title="Time of the newest projected event. Click to refresh." style={{ font: "inherit" }}>as of {txt}</button>;
}

export function Skeleton({ rows = 4 }: { rows?: number }) {
  return <div aria-busy="true">{Array.from({ length: rows }).map((_, i) => <div key={i} className="skeleton" style={{ width: `${70 + ((i * 13) % 30)}%` }} />)}</div>;
}

export function ErrorBox({ error, retry }: { error: unknown; retry?: () => void }) {
  const e = error as ApiProblem;
  const [copied, setCopied] = useState(false);
  return (
    <div className="alert err" role="alert">
      <b>{e?.title || "Something went wrong"}</b>
      {e?.detail ? <div>{e.detail}</div> : null}
      {e?.errors?.length ? <ul>{e.errors.map((x, i) => <li key={i}><b>{x.field}</b>: {x.message}{x.fix_suggestion ? <> - <i>{x.fix_suggestion}</i></> : null}</li>)}</ul> : null}
      <div className="row" style={{ marginTop: 6 }}>
        {e?.request_id ? <button className="btn small ghost" onClick={() => { navigator.clipboard?.writeText(e.request_id); setCopied(true); }}>{copied ? "Copied" : "Copy request id"}</button> : null}
        {(e?.retryable || e?.status === 0) && retry ? <button className="btn small" onClick={retry}>Retry</button> : null}
      </div>
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) { return <div className="empty">{children}</div>; }

export function Modal({ title, onClose, children, drawer }: { title: string; onClose: () => void; children: ReactNode; drawer?: boolean }) {
  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h); return () => window.removeEventListener("keydown", h);
  }, [onClose]);
  return (
    <div className={"overlay" + (drawer ? "" : " center")} onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={drawer ? "drawer" : "modal"} role="dialog" aria-modal="true" aria-label={title}>
        <div className="row" style={{ justifyContent: "space-between" }}><h2 style={{ margin: 0 }}>{title}</h2><button className="btn small ghost" onClick={onClose} aria-label="Close">Close</button></div>
        {children}
      </div>
    </div>
  );
}

export function ConfirmButton({ label, consequence, onConfirm, danger, disabled, className }: { label: string; consequence: string; onConfirm: () => void; danger?: boolean; disabled?: boolean; className?: string }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button className={`btn small ${danger ? "danger" : "ghost"} ${className || ""}`} disabled={disabled} onClick={() => setOpen(true)}>{label}</button>
      {open && (
        <Modal title={label} onClose={() => setOpen(false)}>
          <p>{consequence}</p>
          <div className="row"><button className={"btn " + (danger ? "danger" : "")} onClick={() => { setOpen(false); onConfirm(); }}>Confirm</button><button className="btn ghost" onClick={() => setOpen(false)}>Cancel</button></div>
        </Modal>
      )}
    </>
  );
}

export function SeatBar({ occupied, cap, extra = 0, cumulative }: { occupied: number; cap: number; extra?: number; cumulative?: number }) {
  const total = cap + extra, pct = total ? (100 * occupied) / total : 0;
  const cls = pct >= 100 ? "red" : pct >= 90 ? "amber" : pct < 50 ? "neutral" : "";
  return (
    <div className="bucket" title="Occupied = CLAIMED or ADMITTED seats. Cumulative admitted is never reduced by withdrawals. Markers at 90% and 100%.">
      <div className="meta"><span>{occupied}/{total}{extra ? ` (+${extra} approved extra)` : ""}{cumulative !== undefined ? ` · ${cumulative} ever admitted` : ""}</span>
        <span>{pct >= 100 ? "FULL" : pct >= 90 ? "Nearly full" : ""} {pct.toFixed(0)}%</span></div>
      <div className={"bar " + cls}><i style={{ width: `${Math.min(pct, 100)}%` }} /></div>
    </div>
  );
}

export function Lock({ n, title, state, children }: { n: number; title: string; state: "ok" | "bad" | "wait"; children?: ReactNode }) {
  return <div className={"lock " + state}><div className="n">Lock {n}</div><b>{title}</b>{children}</div>;
}

/** Three-step lock visual (Institution - Registry - Student) with channel, trust and deadline (PRD 10.3). */
export function LockTimeline({ v, deadline }: { v: any; deadline?: string | null }) {
  const l2 = v.lock2_status ?? v.lock2, l3 = v.lock3_status ?? v.lock3;
  const dl = deadline ? Math.ceil((new Date(deadline).getTime() - Date.now()) / 86400000) : null;
  return (
    <div className="locks">
      <Lock n={1} title="Institution claimed" state="ok"><Status v="SIGNED" /><small>Signed by the institution's key</small></Lock>
      <Lock n={2} title="APAAR registry" state={l2 === "PASSED" || l2 === "PROVISIONAL_OK" ? "ok" : l2 === "FAILED" ? "bad" : "wait"}>
        <Status v={l2} /><small>{l2 === "PROVISIONAL_OK" ? "Provisional ID - no registry check" : "Mock registry"} <Simulated what="the national APAAR registry" /></small>
      </Lock>
      <Lock n={3} title="Student confirms" state={l3 === "CONFIRMED" ? "ok" : l3 === "DISPUTED" ? "bad" : "wait"}>
        <Status v={l3} />
        <small>{v.channel ? `via ${String(v.channel).replace(/_/g, " ").toLowerCase()}` : "no channel yet"}{v.channel_trust ? ` · ${v.channel_trust === "LOW" ? "low" : "high"} trust` : ""}</small>
        {l3 === "PENDING" && dl !== null ? <small>{dl >= 0 ? `${dl} days left` : "deadline passed"}</small> : null}
      </Lock>
    </div>
  );
}

export function Field({ label, error, hint, children }: { label: string; error?: string; hint?: string; children: ReactNode }) {
  return <div><label>{label}</label>{children}{hint && !error ? <div className="hint">{hint}</div> : null}{error ? <div className="field-err" role="alert">{error}</div> : null}</div>;
}

export function PageHead({ title, sub, children }: { title: string; sub?: ReactNode; children?: ReactNode }) {
  return <div className="pagehead"><div><h1>{title}</h1>{sub ? <p>{sub}</p> : null}</div><div className="row">{children}</div></div>;
}

/* ---------- password step-up (REQ-AUTH-05, SIMULATED MFA) ---------- */
import { api as _api } from "./api";
type Ask = () => Promise<string>;
const StepUpCtx = createContext<Ask>(() => Promise.reject(new Error("no provider")));
export const useStepUp = () => useContext(StepUpCtx);
export function StepUpProvider({ children }: { children: ReactNode }) {
  const [res, setRes] = useState<{ ok: (t: string) => void; no: () => void } | null>(null);
  const [pw, setPw] = useState(""); const [err, setErr] = useState("");
  const ask: Ask = () => new Promise((ok, no) => { setPw(""); setErr(""); setRes({ ok, no }); });
  const close = () => { res?.no(); setRes(null); };
  const go = async () => {
    try { const r = await _api<{ step_up_token: string }>("/auth/step-up", { body: { password: pw } }); res?.ok(r.step_up_token); setRes(null); }
    catch (e) { setErr((e as any).detail || (e as any).title || "Could not confirm"); }
  };
  return (
    <StepUpCtx.Provider value={ask}>
      {children}
      {res ? <Modal title="Confirm with your password" onClose={close}>
        <p>This action signs or commits records. Re-enter your password to continue. <span className="hint">(Simulated step-up: production would require a second factor.)</span></p>
        <Field label="Password" error={err}><input type="password" autoFocus value={pw} onChange={(e) => setPw(e.target.value)} onKeyDown={(e) => e.key === "Enter" && pw && go()} /></Field>
        <div className="row" style={{ marginTop: 12 }}><button className="btn" disabled={!pw} onClick={go}>Confirm</button><button className="btn ghost" onClick={close}>Cancel</button></div>
      </Modal> : null}
    </StepUpCtx.Provider>
  );
}
