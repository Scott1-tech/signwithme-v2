import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { formsApi, type FieldOut } from '../../api/document';
import { shareApi, shareableProblem, type ShareLinkOut, type SubmissionOut } from '../../api/share';
import type { EditorDocument } from '../../hooks/useContractDocument';

/**
 * F3-UI (EDITOR-V2-PLAN §8): the workspace's view of «Share for others to
 * fill» -- the current version's form fields (grouped by name, with why a
 * name cannot be shared), the contract's links and the submissions.
 *
 * Nothing is asked of the server unless the document editor is there AND the
 * user may edit the document (`capabilities.can_edit`; the share routes are
 * the same admin / reviewer / uploader rule, an email contractor gets 403).
 * The lists are re-read when the tab comes back into view, after a change
 * made here, and when a new version is current (a commit).
 */

export interface ShareFieldGroup {
  name: string;
  type: FieldOut['type'];
  /** Widgets of this name (a radio group has one per button). */
  widgets: number;
  required: boolean;
  /** null = can be shared; else `shareableProblem`'s code. */
  problem: string | null;
}

interface Options {
  contractId: string | undefined;
  editor: EditorDocument;
}

export function useShare({ contractId, editor }: Options) {
  const doc = editor.doc;
  const versionNo = doc?.current_version.version_no ?? 0;
  const enabled = Boolean(contractId && editor.status === 'ready' && doc?.capabilities.can_edit);
  const [fields, setFields] = useState<FieldOut[] | null>(null);
  const [links, setLinks] = useState<ShareLinkOut[]>([]);
  const [submissions, setSubmissions] = useState<SubmissionOut[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const liveFor = useRef<string | undefined>(contractId);
  liveFor.current = contractId;

  const reloadLists = useCallback(async () => {
    if (!enabled || !contractId) return;
    try {
      const [nextLinks, nextSubmissions] = await Promise.all([
        shareApi.list(contractId),
        shareApi.submissions(contractId),
      ]);
      if (liveFor.current !== contractId) return;
      setLinks(nextLinks);
      setSubmissions(nextSubmissions);
      setLoadError(null);
    } catch (cause) {
      if (liveFor.current !== contractId) return;
      setLoadError(cause instanceof Error ? cause.message : 'Could not load the share links');
    }
  }, [contractId, enabled]);

  useEffect(() => {
    if (!enabled || !contractId || !versionNo) {
      setFields(null);
      return;
    }
    let live = true;
    formsApi
      .fields(contractId, versionNo)
      .then((rows) => {
        if (live) setFields(rows);
      })
      .catch(() => {
        if (live) setFields([]);
      });
    return () => {
      live = false;
    };
  }, [contractId, enabled, versionNo]);

  useEffect(() => {
    if (!enabled) {
      setLinks([]);
      setSubmissions([]);
      return;
    }
    void reloadLists();
  }, [enabled, reloadLists, versionNo]);

  // Someone may have filled the form meanwhile: re-read when the tab is back.
  useEffect(() => {
    if (!enabled) return undefined;
    const onVisible = () => {
      if (document.visibilityState === 'visible') void reloadLists();
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => document.removeEventListener('visibilitychange', onVisible);
  }, [enabled, reloadLists]);

  const groups = useMemo<ShareFieldGroup[]>(() => {
    if (!fields) return [];
    const byName = new Map<string, FieldOut[]>();
    fields.forEach((row) => {
      const list = byName.get(row.name);
      if (list) list.push(row);
      else byName.set(row.name, [row]);
    });
    return [...byName.entries()].map(([name, rows]) => ({
      name,
      type: rows[0].type,
      widgets: rows.length,
      required: rows.some((row) => row.required),
      problem: shareableProblem(rows),
    }));
  }, [fields]);

  const shareableCount = groups.filter((group) => group.problem === null).length;
  /** The top bar shows «Share» when there is something to share or a link to manage. */
  const available = enabled && fields !== null && (shareableCount > 0 || links.length > 0);

  return {
    contractId,
    enabled,
    available,
    versionNo,
    fields,
    groups,
    shareableCount,
    links,
    submissions,
    loadError,
    reloadLists,
    dialogOpen,
    openDialog: () => setDialogOpen(true),
    closeDialog: () => setDialogOpen(false),
  };
}

export type ShareState = ReturnType<typeof useShare>;
