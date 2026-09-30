import { useMemo, useState } from 'react';
import { ComplianceBadge } from './ComplianceBadge';
import { ProgressBar } from './ProgressBar';
import type { ComparisonResult, IssueKind, PageIssue } from '../types/comparison';

interface Props {
  result: ComparisonResult | null;
  issues: PageIssue[];
  currentPage: number;
  processing?: boolean;
  onNavigateToIssue: (issue: PageIssue) => void;
  activeFieldId?: string | null;
  /**
   * When present (reviewer + pending), each issue anchored to a real field gets
   * an inline "correct value" form. The handler owns the
   * correctField → rerun-comparison → refresh orchestration.
   */
  onCorrectField?: (issue: PageIssue, value: string) => Promise<void>;
  canCorrect?: boolean;
  correcting?: boolean;
  /** Passed straight to the badge: approved by reviewer override. */
  override?: boolean;
}

const KIND_LABELS: Record<IssueKind, string> = {
  missing: 'Missing required',
  inconsistency: 'Inconsistent',
  fmcsa: 'FMCSA',
  warning: 'Warning',
  signature_slot: 'Signature / date required',
};

const KIND_ORDER: IssueKind[] = ['fmcsa', 'missing', 'inconsistency', 'warning', 'signature_slot'];

export function ComparisonSidebar({
  result,
  issues,
  currentPage,
  processing,
  onNavigateToIssue,
  activeFieldId,
  onCorrectField,
  canCorrect,
  correcting,
  override,
}: Props) {
  const [scope, setScope] = useState<'page' | 'all'>('all');
  // Which issue's inline correction form is open, keyed by issue.key, plus the
  // draft value for that one form.
  const [editingKey, setEditingKey] = useState<string | null>(null);
  const [draft, setDraft] = useState('');

  const openEditor = (issue: PageIssue) => {
    setEditingKey(issue.key);
    setDraft('');
  };

  const submitCorrection = async (issue: PageIssue) => {
    if (!onCorrectField || !issue.fieldId) return;
    await onCorrectField(issue, draft);
    setEditingKey(null);
    setDraft('');
  };

  const visible = useMemo(
    () => (scope === 'page' ? issues.filter((issue) => issue.page === currentPage) : issues),
    [issues, scope, currentPage],
  );

  const grouped = useMemo(() => {
    const map = new Map<IssueKind, PageIssue[]>();
    KIND_ORDER.forEach((kind) => map.set(kind, []));
    visible.forEach((issue) => map.get(issue.kind)?.push(issue));
    return map;
  }, [visible]);

  return (
    <aside className="sidebar">
      <header className="sidebar__header">
        <h2>Review</h2>
        <ComplianceBadge result={result} processing={processing} override={override} />
      </header>

      {result && (
        <div className="sidebar__summary">
          <ProgressBar percentage={result.completion_percentage} />
          <p className="sidebar__counts">
            {result.filled_count} of {result.field_count} fields filled
          </p>
        </div>
      )}

      <div className="sidebar__scope">
        <button
          type="button"
          className={scope === 'all' ? 'chip chip--active' : 'chip'}
          onClick={() => setScope('all')}
        >
          All pages ({issues.length})
        </button>
        <button
          type="button"
          className={scope === 'page' ? 'chip chip--active' : 'chip'}
          onClick={() => setScope('page')}
        >
          This page ({issues.filter((issue) => issue.page === currentPage).length})
        </button>
      </div>

      {visible.length === 0 && (
        <p className="sidebar__empty">
          {processing ? 'Analysing the document…' : 'No issues in this view.'}
        </p>
      )}

      {KIND_ORDER.map((kind) => {
        const group = grouped.get(kind) ?? [];
        if (group.length === 0) return null;
        return (
          <section key={kind} className="sidebar__group">
            <h3 className="sidebar__group-title">
              {KIND_LABELS[kind]} <span className="sidebar__group-count">{group.length}</span>
            </h3>
            <ul className="issue-list">
              {group.map((issue) => (
                <li key={issue.key}>
                  <button
                    type="button"
                    className={`issue issue--${issue.severity}${
                      activeFieldId && issue.fieldId === activeFieldId ? ' issue--active' : ''
                    }`}
                    onClick={() => onNavigateToIssue(issue)}
                  >
                    <span className="issue__label">{issue.label}</span>
                    <span className="issue__detail">{issue.detail}</span>
                    <span className="issue__meta">
                      {issue.page ? `Page ${issue.page}` : 'Document-wide'}
                      {issue.bbox ? '' : ' · no highlight'}
                    </span>
                  </button>
                  {/* Slots are filled by signing, not by typing a value in. */}
                  {canCorrect && onCorrectField && issue.fieldId && issue.kind !== 'signature_slot' && (
                    editingKey === issue.key ? (
                      <div className="issue__correct">
                        <input
                          className="field__input"
                          type="text"
                          value={draft}
                          autoFocus
                          placeholder="Corrected value"
                          disabled={correcting}
                          onChange={(event) => setDraft(event.target.value)}
                        />
                        <div className="issue__correct-actions">
                          <button
                            type="button"
                            className="button button--primary button--small"
                            disabled={correcting || draft.trim() === ''}
                            onClick={() => void submitCorrection(issue)}
                          >
                            {correcting ? 'Saving…' : 'Save & re-check'}
                          </button>
                          <button
                            type="button"
                            className="button button--ghost button--small"
                            disabled={correcting}
                            onClick={() => setEditingKey(null)}
                          >
                            Cancel
                          </button>
                        </div>
                      </div>
                    ) : (
                      <button
                        type="button"
                        className="button button--link issue__correct-open"
                        disabled={correcting}
                        onClick={() => openEditor(issue)}
                      >
                        Correct value
                      </button>
                    )
                  )}
                </li>
              ))}
            </ul>
          </section>
        );
      })}
    </aside>
  );
}
