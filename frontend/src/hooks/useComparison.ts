import { useCallback, useEffect, useMemo, useState } from 'react';
import { contractsApi } from '../api/contracts';
import { ApiError } from '../api/client';
import type { ComparisonResult, PageIssue } from '../types/comparison';
import type { ContractDetail } from '../types/contract';

interface UseComparison {
  contract: ContractDetail | null;
  result: ComparisonResult | null;
  issues: PageIssue[];
  loading: boolean;
  error: string | null;
  /** True while the background worker is still parsing the upload. */
  processing: boolean;
  refresh: () => Promise<void>;
  issuesForPage: (page: number) => PageIssue[];
}

const POLL_INTERVAL_MS = 2500;

export function useComparison(contractId: string | undefined): UseComparison {
  const [contract, setContract] = useState<ContractDetail | null>(null);
  const [result, setResult] = useState<ComparisonResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!contractId) return;
    try {
      const detail = await contractsApi.get(contractId);
      setContract(detail);
      if (detail.status === 'uploaded' || detail.status === 'parsing') {
        setResult(null);
        setError(null);
        return;
      }
      const comparison = await contractsApi.comparison(contractId);
      setResult(comparison);
      setError(null);
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 409) {
        setError(null); // still processing; the poll will pick it up
      } else {
        setError(cause instanceof Error ? cause.message : 'Failed to load comparison');
      }
    } finally {
      setLoading(false);
    }
  }, [contractId]);

  useEffect(() => {
    void load();
  }, [load]);

  const processing = contract?.status === 'uploaded' || contract?.status === 'parsing';

  useEffect(() => {
    if (!processing) return;
    const timer = window.setInterval(() => void load(), POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [processing, load]);

  /**
   * All five result buckets flattened into one page-anchored list. Cross-field
   * inconsistencies carry an explicit `pages` array from the backend, so this
   * never has to infer a page from a field-id prefix.
   */
  const issues = useMemo<PageIssue[]>(() => {
    if (!result) return [];
    const flattened: PageIssue[] = [];

    result.missing_required.forEach((missing) => {
      flattened.push({
        kind: 'missing',
        key: `missing:${missing.field_id}`,
        label: missing.label,
        detail: 'Required field is empty',
        severity: missing.severity,
        page: missing.page,
        fieldId: missing.field_id,
        bbox: missing.bbox,
      });
    });

    const pushIssue = (kind: 'inconsistency' | 'warning') =>
      (issue: ComparisonResult['inconsistencies'][number], index: number) => {
        const pages = issue.pages.length > 0 ? issue.pages : [null];
        pages.forEach((page) => {
          flattened.push({
            kind,
            key: `${kind}:${issue.type}:${index}:${page ?? 'x'}`,
            label: issue.field_id ?? issue.type.replace(/_/g, ' '),
            detail: issue.issue,
            severity: issue.severity,
            page,
            fieldId: issue.field_id ?? null,
            bbox: issue.bbox ?? null,
          });
        });
      };

    result.inconsistencies.forEach(pushIssue('inconsistency'));
    result.warnings.forEach(pushIssue('warning'));

    result.fmcsa_violations.forEach((violation, index) => {
      flattened.push({
        kind: 'fmcsa',
        key: `fmcsa:${violation.rule}:${index}`,
        label: violation.rule.replace(/_/g, ' '),
        detail: violation.issue,
        severity: violation.severity,
        page: violation.page,
        fieldId: violation.field_ids[0] ?? null,
        bbox: null,
      });
    });

    // Slots to be filled at signing. Older stored comparisons predate this
    // bucket, hence the fallback. Severity is always `info`: these never block.
    (result.signature_slots ?? []).forEach((slot) => {
      flattened.push({
        kind: 'signature_slot',
        key: `slot:${slot.field_id}`,
        label: slot.label,
        detail:
          slot.kind === 'signature' ? 'Signature required at signing' : 'Date required at signing',
        severity: 'info',
        page: slot.page,
        fieldId: slot.field_id,
        bbox: slot.bbox,
      });
    });

    return flattened;
  }, [result]);

  const issuesForPage = useCallback(
    (page: number) => issues.filter((issue) => issue.page === page),
    [issues],
  );

  return {
    contract,
    result,
    issues,
    loading,
    error,
    processing: Boolean(processing),
    refresh: load,
    issuesForPage,
  };
}
