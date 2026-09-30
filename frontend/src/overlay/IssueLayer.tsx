import { FieldHighlighter } from '../components/FieldHighlighter';
import type { PageIssue } from '../types/comparison';

interface Props {
  issues: PageIssue[];
  scale: number;
  activeFieldId: string | null;
  onSelect: (issue: PageIssue) => void;
}

/**
 * The review's marks on one page: every positioned issue that is NOT a slot,
 * as a `.highlight`. Slots are drawn once, as `.slot`, by `SlotLayer` (W1) --
 * `FieldHighlighter` skips them.
 */
export function IssueLayer(props: Props) {
  return <FieldHighlighter {...props} />;
}
