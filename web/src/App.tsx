import { ComponentType, lazy, Suspense } from "react";
import { Route, Routes } from "react-router-dom";
import { Shell } from "./Layout";
import { Skeleton } from "./ui";
import { DevSms, Landing, Login, NotFound } from "./pages/Public";

// Route-level code splitting: each portal loads on demand (PRD 10.6).
const lz = (loader: () => Promise<any>, name: string) => lazy(() => loader().then((m) => ({ default: m[name] as ComponentType })));
const inst = () => import("./pages/Institution");
const aicte = () => import("./pages/Aicte");
const student = () => import("./pages/Student");
const inst2 = () => import("./pages/Institution2");
const aicte2 = () => import("./pages/Aicte2");
const intel = () => import("./pages/Intel");
const InstDashboard = lz(inst, "InstDashboard"), Programmes = lz(inst, "Programmes"), ProgrammeLedger = lz(inst, "ProgrammeLedger"), Claims = lz(inst, "Claims"),
  ClaimDetail = lz(inst, "ClaimDetail"), Fixes = lz(inst, "Fixes"), Findings = lz(inst, "Findings"), Exceptions = lz(inst, "Exceptions");
const Uploads = lz(inst2, "Uploads"), UploadDetail = lz(inst2, "UploadDetail"), Submissions = lz(inst2, "Submissions"), Transfers = lz(inst2, "Transfers");
const Duplicates = lz(aicte2, "Duplicates"), AicteSubmissions = lz(aicte2, "AicteSubmissions"), Reviews = lz(aicte2, "Reviews");
const TrustBoard = lz(intel, "TrustBoard"), Alerts = lz(intel, "Alerts"), Cases = lz(intel, "Cases"), CaseWorkspace = lz(intel, "CaseWorkspace"), Reports = lz(intel, "Reports"), InstCases = lz(intel, "InstCases");
const AicteOverview = lz(aicte, "AicteOverview"), Utilisation = lz(aicte, "Utilisation"), Audit = lz(aicte, "Audit"), Ops = lz(aicte, "Ops"), Demo = lz(aicte, "Demo");
const StudentHome = lz(student, "StudentHome");
const wallet = () => import("./pages/Wallet");
const admin = () => import("./pages/Admin");
const verifier = () => import("./pages/Verifier");
const WalletCredentials = lz(wallet, "WalletCredentials"), Consents = lz(wallet, "Consents"), AccessLog = lz(wallet, "AccessLog"), Corrections = lz(wallet, "Corrections"), DataRequests = lz(wallet, "DataRequests");
const InstCredentials = lz(admin, "InstCredentials"), InstAdmin = lz(admin, "InstAdmin"), AicteAdmin = lz(admin, "AicteAdmin"), AicteCredentials = lz(admin, "AicteCredentials"), VerifierOrgs = lz(admin, "VerifierOrgs"), RuleSets = lz(admin, "RuleSets"), Onboarding = lz(admin, "Onboarding");
const VerifierHome = lz(verifier, "VerifierHome"), VerifierUsage = lz(verifier, "VerifierUsage"), StatusPage = lz(verifier, "StatusPage"), SharePage = lz(verifier, "SharePage");

const S = (C: ComponentType) => <Suspense fallback={<Skeleton rows={6} />}><C /></Suspense>;

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Landing />} />
      <Route path="/login" element={<Login />} />
      <Route path="/dev/sms-inbox" element={<DevSms />} />
      <Route path="/onboarding" element={S(Onboarding)} />
      <Route path="/v/:ref" element={S(StatusPage)} />
      <Route path="/s/:token" element={S(SharePage)} />
      <Route path="/institution" element={<Shell utype="INSTITUTION" />}>
        <Route index element={S(InstDashboard)} />
        <Route path="programmes" element={S(Programmes)} />
        <Route path="programmes/:id" element={S(ProgrammeLedger)} />
        <Route path="claims" element={S(Claims)} />
        <Route path="claims/:id" element={S(ClaimDetail)} />
        <Route path="fixes" element={S(Fixes)} />
        <Route path="findings" element={S(Findings)} />
        <Route path="exceptions" element={S(Exceptions)} />
        <Route path="uploads" element={S(Uploads)} />
        <Route path="uploads/:id" element={S(UploadDetail)} />
        <Route path="submissions" element={S(Submissions)} />
        <Route path="transfers" element={S(Transfers)} />
        <Route path="cases" element={S(InstCases)} />
        <Route path="reports" element={S(Reports)} />
        <Route path="credentials" element={S(InstCredentials)} />
        <Route path="admin" element={S(InstAdmin)} />
        <Route path="*" element={<NotFound />} />
      </Route>
      <Route path="/aicte" element={<Shell utype="AICTE" />}>
        <Route index element={S(AicteOverview)} />
        <Route path="utilisation" element={S(Utilisation)} />
        <Route path="reviews" element={S(Reviews)} />
        <Route path="duplicates" element={S(Duplicates)} />
        <Route path="trust" element={S(TrustBoard)} />
        <Route path="alerts" element={S(Alerts)} />
        <Route path="cases" element={S(Cases)} />
        <Route path="cases/:id" element={S(CaseWorkspace)} />
        <Route path="reports" element={S(Reports)} />
        <Route path="submissions" element={S(AicteSubmissions)} />
        <Route path="audit" element={S(Audit)} />
        <Route path="ops" element={S(Ops)} />
        <Route path="rulesets" element={S(RuleSets)} />
        <Route path="credentials" element={S(AicteCredentials)} />
        <Route path="verifier-orgs" element={S(VerifierOrgs)} />
        <Route path="admin" element={S(AicteAdmin)} />
        <Route path="demo" element={S(Demo)} />
        <Route path="*" element={<NotFound />} />
      </Route>
      <Route path="/student" element={<Shell utype="STUDENT" />}>
        <Route index element={S(StudentHome)} />
        <Route path="wallet" element={S(WalletCredentials)} />
        <Route path="consents" element={S(Consents)} />
        <Route path="access-log" element={S(AccessLog)} />
        <Route path="corrections" element={S(Corrections)} />
        <Route path="requests" element={S(DataRequests)} />
      </Route>
      <Route path="/verifier" element={<Shell utype="VERIFIER" />}>
        <Route index element={S(VerifierHome)} />
        <Route path="usage" element={S(VerifierUsage)} />
      </Route>
      <Route path="*" element={<NotFound />} />
    </Routes>
  );
}
