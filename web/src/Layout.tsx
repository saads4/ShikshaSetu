import { NavLink, Outlet, Navigate, useLocation, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import { homeFor, useAuth } from "./auth";
import { tr } from "./i18n";
import { Skeleton } from "./ui";

type NavItem = { to?: string; label: string; off?: boolean; note?: string };
const NAV: Record<string, { title: string; groups: { h: string; items: NavItem[] }[] }> = {
  INSTITUTION: {
    title: "Institution portal",
    groups: [
      { h: "Admissions", items: [{ to: "/institution", label: "Dashboard" }, { to: "/institution/programmes", label: "Seat ledger" }, { to: "/institution/claims", label: "Claims" }, { to: "/institution/fixes", label: "Fix-it list" }, { to: "/institution/findings", label: "Findings" }, { to: "/institution/exceptions", label: "Seat exceptions" }] },
      { h: "Submissions", items: [{ to: "/institution/uploads", label: "Bulk uploads" }, { to: "/institution/submissions", label: "Submissions" }, { to: "/institution/transfers", label: "Transfers" }, { to: "/institution/cases", label: "Cases" }, { to: "/institution/reports", label: "Reports" }] },
      { h: "Credentials and admin", items: [{ to: "/institution/credentials", label: "Credentials" }, { to: "/institution/admin", label: "Admin" }] },
    ],
  },
  AICTE: {
    title: "AICTE portal",
    groups: [
      { h: "Oversight", items: [{ to: "/aicte", label: "National dashboard" }, { to: "/aicte/utilisation", label: "Seat utilisation" }, { to: "/aicte/trust", label: "Trust Index" }, { to: "/aicte/alerts", label: "Alerts" }, { to: "/aicte/cases", label: "Cases" }, { to: "/aicte/duplicates", label: "Duplicate queue" }, { to: "/aicte/submissions", label: "Submissions" }, { to: "/aicte/reviews", label: "Reviews" }, { to: "/aicte/reports", label: "Reports" }, { to: "/aicte/audit", label: "Audit trail" }, { to: "/aicte/ops", label: "Operations" }] },
      { h: "Policy and credentials", items: [{ to: "/aicte/rulesets", label: "Rule sets and simulator" }, { to: "/aicte/credentials", label: "Credentials" }, { to: "/aicte/verifier-orgs", label: "Verifier orgs" }, { to: "/aicte/admin", label: "Users and feeds" }] },
    ],
  },
  STUDENT: { title: "Student portal", groups: [{ h: "My records", items: [{ to: "/student", label: "My enrolments" }, { to: "/student/wallet", label: "Credentials" }, { to: "/student/consents", label: "Consents" }, { to: "/student/access-log", label: "Access log" }, { to: "/student/corrections", label: "Corrections" }, { to: "/student/requests", label: "Data requests" }] }] },
  VERIFIER: { title: "Verifier portal", groups: [{ h: "Verification", items: [{ to: "/verifier", label: "Verify" }, { to: "/verifier/usage", label: "Usage and history" }] }] },
};

export function RoleSwitcher() {
  const { user, login, logout } = useAuth();
  const nav = useNavigate();
  const accounts = useQuery({ queryKey: ["demo-accounts"], queryFn: () => api<any>("/auth/demo-accounts"), retry: false, staleTime: 600000 });
  if (!user?.demo || !accounts.data) return null;
  const feat = accounts.data.items.filter((a: any) => a.description);
  return (
    <select aria-label="Switch demo account" style={{ width: 190 }} value="" onChange={async (e) => {
      const name = e.target.value; if (!name) return;
      await logout();                                         // signs out, then signs in through the normal /auth/login (no bypass)
      const u = await login(name, accounts.data.password_hint); nav(homeFor(u));
    }}>
      <option value="">Switch demo account…</option>
      {feat.map((a: any) => <option key={a.username} value={a.username}>{a.username} — {a.role_code}</option>)}
    </select>
  );
}

export function Shell({ utype }: { utype: "INSTITUTION" | "AICTE" | "STUDENT" | "VERIFIER" }) {
  const { user, loading, logout, can, setLocale } = useAuth();
  const loc = useLocation();
  const nav = useNavigate();
  if (loading) return <div className="main"><Skeleton rows={6} /></div>;
  if (!user) return <Navigate to={`/login?next=${encodeURIComponent(loc.pathname)}`} replace />;
  if (user.utype !== utype) return <Navigate to={homeFor(user)} replace />;
  const cfg = NAV[utype];
  const scope = user.institution ? `${user.institution.name}` : user.scope ? `${user.scope.type}${user.scope.value ? " · " + user.scope.value : ""}` : "";
  const groups = cfg.groups.map((g) => ({ ...g, items: g.items.filter((i) => !(utype === "AICTE" && i.to === "/aicte/demo")) }));
  return (
    <>
      <div className="topbanner"><span>Demo environment — synthetic data. Demo credentials for local use only.</span><span><a href="/dev/sms-inbox" target="_blank" rel="noreferrer" style={{ color: "#ffe9b0" }}>Dev SMS Inbox (simulated)</a></span></div>
      <a className="skip" href="#main">Skip to content</a>
      <div className="shell">
        <aside className="side" aria-label="Navigation">
          <a className="brand" href="/"><img src="/aicte-logo.png" alt="AICTE seal" /><div><small>AICTE · APAAR</small><b>ShikshaSetu</b></div></a>
          <div style={{ padding: "0 12px 8px", fontSize: 12, color: "var(--muted)" }}>{cfg.title}</div>
          <nav>
            {groups.map((g) => (
              <div key={g.h}><h4>{g.h}</h4>
                {g.items.map((i) => i.off ? <span key={i.label} className="off" title="Not available in this prototype yet (planned for a later phase)">{i.label}</span>
                  : <NavLink key={i.to} to={i.to!} end={i.to === `/${utype.toLowerCase()}`} className={({ isActive }) => (isActive ? "active" : "")}>{tr(user.locale, i.label)}</NavLink>)}
              </div>
            ))}
            {user.permissions.includes("demo:tools") ? <div><h4>Demo</h4><NavLink to="/aicte/demo" className={({ isActive }) => (isActive ? "active" : "")}>Demo tools</NavLink></div> : null}
          </nav>
        </aside>
        <div className="mainwrap">
          <header className="topbar">
            <div className="row"><RoleSwitcher />{utype === "STUDENT" ? <select aria-label={tr(user.locale, "Language")} style={{ width: 120 }} value={user.locale === "hi" ? "hi" : "en"} onChange={(e) => setLocale(e.target.value as "en" | "hi")}><option value="en">English</option><option value="hi">हिन्दी</option></select> : null}</div>
            <div className="userchip">
              <div className="who"><b>{user.display_name}</b><small>{scope}</small></div>
              <span className="rolechip">{user.roles.join(", ")}</span>
              <button className="btn small ghost" onClick={async () => { await logout(); nav("/"); }}>Sign out</button>
            </div>
          </header>
          <main id="main" className="main"><Outlet /></main>
          {utype === "STUDENT" ? <footer className="footer" style={{ marginTop: "auto" }}>APAAR is never mandatory for being counted. Aadhaar is never stored or printed. Grievance officer: placeholder role holder (prototype).</footer> : null}
        </div>
      </div>
    </>
  );
}
