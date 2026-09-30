import { useCallback } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

/** The three old screens, now three ways into the same workspace. */
export const WORKSPACE_PANELS = ['review', 'sign', 'edit'] as const;
export type WorkspacePanel = (typeof WORKSPACE_PANELS)[number];

export const isWorkspacePanel = (value: string | undefined): value is WorkspacePanel =>
  value !== undefined && (WORKSPACE_PANELS as readonly string[]).includes(value);

/**
 * `/contracts/:contractId/:panel` as state (plan §4).
 *
 * The panel segment only says which part of the right-hand panel is forced
 * open (and, on the first render, which tool starts armed). Changing it is a
 * `replace` navigation inside ONE route element, so the workspace is never
 * remounted: the PDF is not fetched again, the scroll position and zoom stay,
 * and every piece of state (dates, selection, history) survives.
 */
export function useWorkspaceRoute() {
  const { contractId, panel: raw } = useParams<{ contractId: string; panel: string }>();
  const navigate = useNavigate();
  const panel: WorkspacePanel = isWorkspacePanel(raw) ? raw : 'review';

  const setPanel = useCallback(
    (next: WorkspacePanel) => {
      if (!contractId) return;
      navigate(`/contracts/${contractId}/${next}`, { replace: true });
    },
    [contractId, navigate],
  );

  return { contractId, panel, setPanel };
}
