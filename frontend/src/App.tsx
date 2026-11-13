import { Navigate, Route, Routes } from 'react-router-dom';
import { Layout } from './components/Layout';
import { Account } from './pages/Account';
import { useAuth } from './hooks/useAuth';
import { ContractUpload } from './pages/ContractUpload';
import { ContractWorkspace, WorkspaceDefaultRedirect } from './pages/ContractWorkspace';
import { Dashboard } from './pages/Dashboard';
import { FinalContract } from './pages/FinalContract';
import { Login } from './pages/Login';
import { MySignatures } from './pages/MySignatures';
import { ReviewQueue } from './pages/ReviewQueue';
import { SharedForm } from './pages/SharedForm';
import { TemplateManager } from './pages/TemplateManager';

function RequireAuth({ children, role }: { children: JSX.Element; role?: 'reviewer' | 'admin' }) {
  const { user, loading, isAtLeast } = useAuth();
  if (loading) return <p className="page">Loading…</p>;
  if (!user) return <Navigate to="/login" replace />;
  if (role && !isAtLeast(role)) return <Navigate to="/" replace />;
  return children;
}

export default function App() {
  return (
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
  );
}
