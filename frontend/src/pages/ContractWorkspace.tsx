import { useEffect, useState } from 'react';
import { Navigate, useParams } from 'react-router-dom';
import { contractsApi } from '../api/contracts';
import { useAuth } from '../hooks/useAuth';
import { Workspace } from '../workspace/Workspace';
import { isWorkspacePanel, type WorkspacePanel } from '../workspace/useWorkspaceRoute';

/**
 * Editing a contract's PDF happens in the standalone editor page (`/editor/`),
 * not in the workspace. `/edit` only forwards there, so every link to it (the
 * dashboard's Edit, an old bookmark) lands in the editor.
 */
function OpenEditor({ contractId }: { contractId: string }) {
  useEffect(() => {
    window.location.replace(`/editor/index.html?contract=${encodeURIComponent(contractId)}`);
  }, [contractId]);
  return <p className="page">Opening the editor…</p>;
}

/**
 * `/contracts/:contractId/:panel` -- the one route behind `/review`, `/sign`
 * and `/edit` (plan §4). A thin shell: an unknown panel goes to `/review`,
 * everything else is the workspace (`/edit` forwards to the editor page).
 * Keyed by the contract, so opening a different contract starts clean, while
 * switching panels on the same one re-renders in place and never remounts.
 */
export function ContractWorkspace() {
  const { contractId, panel } = useParams<{ contractId: string; panel: string }>();
  if (!contractId) return <Navigate to="/" replace />;
  if (!isWorkspacePanel(panel)) {
    return <Navigate to={`/contracts/${contractId}/review`} replace />;
  }
  if (panel === 'edit') return <OpenEditor contractId={contractId} />;
  return <Workspace key={contractId} />;
}

/**
 * `/contracts/:contractId` with no panel: a reviewer lands on the review of a
 * pending contract, anyone lands on signing once it is approved, and the
 * review is the answer to everything else (including a failed lookup).
 */
export function WorkspaceDefaultRedirect() {
  const { contractId } = useParams<{ contractId: string }>();
  const { isAtLeast } = useAuth();
  const [target, setTarget] = useState<WorkspacePanel | null>(null);

  useEffect(() => {
    if (!contractId) return;
    let cancelled = false;
    contractsApi
      .get(contractId)
      .then((contract) => {
        if (cancelled) return;
        if (contract.review_status === 'pending' && isAtLeast('reviewer')) setTarget('review');
        else if (contract.review_status === 'approved') setTarget('sign');
        else setTarget('review');
      })
      .catch(() => {
        if (!cancelled) setTarget('review');
      });
    return () => {
      cancelled = true;
    };
  }, [contractId, isAtLeast]);

  if (!contractId) return <Navigate to="/" replace />;
  if (!target) return <p className="page">Loading…</p>;
  return <Navigate to={`/contracts/${contractId}/${target}`} replace />;
}
