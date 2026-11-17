import { lazy, Suspense } from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';
import { Layout } from './components/Layout';
import { useAuth } from './hooks/useAuth';
import { Dashboard } from './pages/Dashboard';
import { Login } from './pages/Login';

// Sign-in and the dashboard open at once; every other page -- above all the
// contract workspace with the PDF engine -- is downloaded when it is opened.
const page = <M, K extends keyof M>(load: () => Promise<M>, name: K) =>
  lazy(async () => ({ default: (await load())[name] as unknown as () => JSX.Element }));

const Account = page(() => import('./pages/Account'), 'Account');
const ContractUpload = page(() => import('./pages/ContractUpload'), 'ContractUpload');
const ContractWorkspace = page(() => import('./pages/ContractWorkspace'), 'ContractWorkspace');
const WorkspaceDefaultRedirect = page(() => import('./pages/ContractWorkspace'), 'WorkspaceDefaultRedirect');
const FinalContract = page(() => import('./pages/FinalContract'), 'FinalContract');
const MySignatures = page(() => import('./pages/MySignatures'), 'MySignatures');
const ReviewQueue = page(() => import('./pages/ReviewQueue'), 'ReviewQueue');
const SharedForm = page(() => import('./pages/SharedForm'), 'SharedForm');
const TemplateManager = page(() => import('./pages/TemplateManager'), 'TemplateManager');

function RequireAuth({ children, role }: { children: JSX.Element; role?: 'reviewer' | 'admin' }) {
  const { user, loading, isAtLeast } = useAuth();
  if (loading) return <p className="page">Loading…</p>;
  if (!user) return <Navigate to="/login" replace />;
  if (role && !isAtLeast(role)) return <Navigate to="/" replace />;
  return children;
}

export default function App() {
  return (
    <Suspense fallback={<p className="page">Loading…</p>}>
    <Routes>
      <Route path="/login" element={<Login />} />
      {/* F3: a share link's form -- no account, no app navigation. */}
      <Route path="/s" element={<SharedForm />} />
      <Route
        element={
          <RequireAuth>
            <Layout />
          </RequireAuth>
        }
      >
        <Route index element={<Dashboard />} />
        <Route path="contracts/new" element={<ContractUpload />} />
        {/* Static, so it outranks `:panel` below. */}
        <Route path="contracts/:contractId/final" element={<FinalContract />} />
        {/* One workspace behind /review, /sign and /edit: ONE route element, so
            switching panels never remounts it (the PDF is fetched once, scroll
            and zoom stay). Same access as signing always had; the server
            re-checks at every write and at finalize. */}
        <Route path="contracts/:contractId/:panel" element={<ContractWorkspace />} />
        <Route path="contracts/:contractId" element={<WorkspaceDefaultRedirect />} />
        <Route path="signatures" element={<MySignatures />} />
        <Route path="account" element={<Account />} />
        <Route
          path="review"
          element={
            <RequireAuth role="reviewer">
              <ReviewQueue />
            </RequireAuth>
          }
        />
        <Route
          path="templates"
          element={
            <RequireAuth role="admin">
              <TemplateManager />
            </RequireAuth>
          }
        />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
    </Suspense>
  );
}
