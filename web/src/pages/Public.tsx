import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, ApiProblem, fmtDateTime } from "../api";
import { homeFor, useAuth } from "../auth";
import { ErrorBox, Simulated, Skeleton } from "../ui";

export function SiteHeader() {
  const { user } = useAuth();
  return (
    <>
      <div className="topbanner"><span>AICTE · Education technology prototype</span><span>Demonstration environment — synthetic data. Demo credentials for local use only.</span></div>
      <header className="siteheader">
        <Link to="/" className="brand"><img src="/aicte-logo.png" alt="AICTE seal" /><div><small>AICTE · APAAR</small><b>ShikshaSetu</b></div></Link>
        <nav className="sitenav" aria-label="Site">
          <a href="/#platform">Platform</a><a href="/#how">How it works</a><a href="/#portals">Portals</a>
          <Link className="btn" to={user ? homeFor(user) : "/login"}>{user ? "Open my portal" : "Enter demo"} ↗</Link>
        </nav>
      </header>
    </>
  );
}

export function Landing() {
  return (
    <div>
      <SiteHeader />
      <section className="hero">
        <div className="hero-inner">
          <div>
            <div className="eyebrow">Trust layer for technical admissions</div>
            <h1>Every admission.<span>Accountable by design.</span></h1>
            <p className="lead">APAAR ShikshaSetu connects admission claims, approved intake, and student verification in one explainable journey — from the first seat check to a verifiable credential.</p>
            <div className="cta"><Link className="btn" to="/login">Explore the portals →</Link><a href="#platform" style={{ fontWeight: 600, color: "var(--ink)" }}>Discover the platform ↓</a></div>
            <div className="synthetic"><span className="dot" /> Synthetic records · No live registry connection</div>
          </div>
          <div className="orbit" aria-hidden="true">
            <div className="tl"><span className="dot" /> TRIPLE-LOCK VERIFICATION</div>
            <div className="ring" /><div className="ring r2" />
            <div className="seal"><img src="/aicte-logo.png" alt="" /></div>
            <div className="caption">CONNECTED TRUST LAYER</div>
            <div className="chip" style={{ left: "0%", top: "20%" }}><span className="ic">I</span><div><b>Institution</b><small>Admission claim</small></div><span className="ok">✓</span></div>
            <div className="chip" style={{ right: "0%", top: "46%" }}><span className="ic" style={{ background: "#fdeadb", color: "#c2570c" }}>A</span><div><b>APAAR</b><small>Identity validation</small></div><span className="ok">✓</span></div>
            <div className="chip" style={{ left: "6%", top: "70%" }}><span className="ic" style={{ background: "#e3eefa", color: "#1c5fa8" }}>S</span><div><b>Student</b><small>Consent &amp; confirmation</small></div><span className="ok">✓</span></div>
            <div className="chip" style={{ right: "4%", top: "82%" }}><span className="ic">✓</span><div><b>Verified record</b><small>Purpose-bound credential</small></div></div>
          </div>
        </div>
      </section>

      <section className="section" id="platform">
        <div className="eyebrow">The ShikshaSetu trust layer</div>
        <h2>Three independent locks decide what AICTE records.</h2>
        <div className="grid3">
          <div className="pcard"><h3>1 · Institution claims</h3><p>An institution claims a seat against a live ledger before admitting. At 90% a warning is raised; at 100% the gate stops unless AICTE approves an exception.</p></div>
          <div className="pcard"><h3>2 · Registry validates</h3><p>The APAAR registry check explains mismatches field by field. Students without an APAAR ID get a provisional ID — they are always counted. <Simulated what="the national APAAR registry" /></p></div>
          <div className="pcard"><h3>3 · Student confirms</h3><p>The student (or guardian for minors) confirms with a one-time code. Unconfirmed and disputed records are shown honestly, never hidden. <Simulated what="an SMS gateway" /></p></div>
        </div>
      </section>

      <section className="section" id="how" style={{ paddingTop: 0 }}>
        <h2>How it works</h2>
        <ol className="steps" style={{ padding: 0 }}>
          <li><b>Claim</b><br />A student's APAAR ID is claimed atomically; a second institution gets a CONFLICT, not student data.</li>
          <li><b>Validate</b><br />Name and date of birth are compared with fuzzy matching that tolerates initials, spelling variants and day-month swaps, with a suggested fix.</li>
          <li><b>Confirm</b><br />The student confirms; the three locks agree and the record becomes VERIFIED.</li>
          <li><b>Account</b><br />Every action is audited in a tamper-evident hash chain; dashboards are computed from the records.</li>
        </ol>
      </section>

      <section className="section" id="portals" style={{ paddingTop: 0 }}>
        <h2>Portals</h2>
        <div className="grid3">
          <div className="pcard"><h3>Institution portal</h3><p>Seat ledger, claims, identity fixes, findings, exceptions.</p><Link className="btn ghost" to="/login">Sign in as an institution</Link> <Link className="btn ghost" to="/onboarding">Apply to onboard</Link></div>
          <div className="pcard"><h3>AICTE portal</h3><p>National dashboard, utilisation, reviews, audit trail, operations.</p><Link className="btn ghost" to="/login">Sign in as AICTE</Link></div>
          <div className="pcard"><h3>Student portal</h3><p>Your enrolments, the three locks, confirmation and dispute. Guardian mode for minors.</p><Link className="btn ghost" to="/login">Sign in as a student</Link></div>
        </div>
      </section>
      <footer className="footer"><b>A signature proves integrity, not truth.</b> · APAAR is never mandatory for being counted. · Aadhaar is never stored or printed. · All data in this prototype is synthetic.</footer>
    </div>
  );
}

export function Login() {
  const { login, user } = useAuth();
  const nav = useNavigate();
  const [sp] = useSearchParams();
  const [u, setU] = useState(""); const [p, setP] = useState(""); const [err, setErr] = useState<unknown>(null); const [busy, setBusy] = useState(false);
  const [q, setQ] = useState("");
  const accounts = useQuery({ queryKey: ["demo-accounts"], queryFn: () => api<any>("/auth/demo-accounts"), retry: false });
  useEffect(() => { if (user) nav(sp.get("next") || homeFor(user), { replace: true }); }, [user]);

  const go = async (name: string, pw: string) => {
    setBusy(true); setErr(null);
    try { const usr = await login(name, pw); nav(sp.get("next") || homeFor(usr), { replace: true }); } catch (e) { setErr(e); } finally { setBusy(false); }
  };
  const items: any[] = accounts.data?.items || [];
  const groups: Record<string, any[]> = {};
  const label: Record<string, string> = { IA: "Institution admin", IO: "Institution operator", AR: "AICTE reviewer", AS: "AICTE system admin", ST: "Student or guardian", VU: "Verifier organisation user" };
  items.filter((a) => a.description && (!q || (a.username + a.display_name + (a.institution || "")).toLowerCase().includes(q.toLowerCase()))).forEach((a) => { (groups[a.role_code] ||= []).push(a); });
  const others = items.filter((a) => !a.description && (a.username + a.institution).toLowerCase().includes(q.toLowerCase()));

  return (
    <div>
      <SiteHeader />
      <div className="loginwrap">
        <div>
          <div className="card">
            <h2>Sign in</h2>
            <form onSubmit={(e) => { e.preventDefault(); go(u, p); }}>
              <label htmlFor="un">Username</label><input id="un" autoComplete="username" value={u} onChange={(e) => setU(e.target.value)} />
              <label htmlFor="pw">Password</label><input id="pw" type="password" autoComplete="current-password" value={p} onChange={(e) => setP(e.target.value)} />
              {err ? <ErrorBox error={err} /> : null}
              <div style={{ marginTop: 14 }}><button className="btn" disabled={busy || !u || !p}>{busy ? "Signing in…" : "Sign in"}</button></div>
            </form>
            <p className="hint">Demo password for every account: <code>{accounts.data?.password_hint || "1234"}</code>. <b>Demo credentials are for local demonstration only.</b></p>
          </div>
          <div className="alert info" style={{ marginTop: 14 }}>APAAR is never mandatory for being counted. Aadhaar is never stored or printed. Registry and SMS in this prototype are <Simulated what="national systems" />.</div>
        </div>
        <div>
          <h2>Sign in as…</h2>
          {accounts.isLoading ? <Skeleton rows={6} /> : accounts.error ? <ErrorBox error={accounts.error} retry={() => accounts.refetch()} /> : (
            <>
              <input type="text" placeholder="Search accounts or institutions" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search accounts" style={{ marginBottom: 12 }} />
              {Object.entries(groups).map(([role, list]) => (
                <section key={role}>
                  <h3 style={{ fontSize: 14, marginTop: 14 }}>{label[role] || role}</h3>
                  <div className="accgrid">{list.map((a) => (
                    <button key={a.username} className="account" onClick={() => go(a.username, accounts.data.password_hint)}><b>{a.username}</b><small>{a.display_name}</small><small>{a.description}</small></button>
                  ))}</div>
                </section>
              ))}
              {others.length ? <details><summary>Other institutions ({others.length} accounts)</summary><div className="accgrid" style={{ marginTop: 8 }}>{others.slice(0, 60).map((a) => (
                <button key={a.username} className="account" onClick={() => go(a.username, accounts.data.password_hint)}><b>{a.username}</b><small>{a.institution} · {a.state}</small></button>))}</div></details> : null}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

export function DevSms() {
  const q = useQuery({ queryKey: ["dev-sms"], queryFn: () => api<any>("/dev/sms"), refetchInterval: 5000, retry: false });
  return (
    <div>
      <SiteHeader />
      <div className="main" style={{ margin: "0 auto" }}>
        <div className="pagehead"><div><h1>Dev SMS Inbox <Simulated what="an SMS gateway" /></h1><p>Messages the mock gateway “sent”, including one-time codes. No real SMS is ever sent. Refreshes every 5 seconds.</p></div></div>
        {q.isLoading ? <Skeleton /> : q.error ? <ErrorBox error={q.error} retry={() => q.refetch()} /> : (
          <div className="tablewrap"><table><thead><tr><th>When</th><th>To (masked)</th><th>Template</th><th>Lang</th><th>One-time code</th><th>Message</th></tr></thead><tbody>
            {q.data.items.map((m: any) => <tr key={m.id}><td>{fmtDateTime(m.created_at)}</td><td>{m.recipient_masked}</td><td>{m.template_code}</td><td>{m.language}</td><td><code>{m.otp || "-"}</code></td><td>{m.body}</td></tr>)}
            {!q.data.items.length ? <tr><td colSpan={6} className="empty">No messages yet. Claim a seat or request a code to see one here.</td></tr> : null}
          </tbody></table></div>
        )}
      </div>
    </div>
  );
}

export function NotFound() { return <div><SiteHeader /><div className="main" style={{ margin: "0 auto" }}><h1>Page not found</h1><p>That address does not exist. <Link to="/">Back to the start</Link>.</p></div></div>; }
export function Forbidden() { return <div className="alert err"><b>Not permitted</b><div>Your role cannot open this page. Use the role switcher to sign in as a different demo account.</div></div>; }
