import { ptRectToPxBox } from '../lib/viewport';
import type { PageIssue } from '../types/comparison';
import type { BBox } from '../types/field';

interface Props {
  issues: PageIssue[];
  /** CSS pixels per PDF point, from the viewer. Without it, highlights drift
   *  away from the page as soon as the user zooms. */
  scale: number;
  activeFieldId?: string | null;
  onSelect?: (issue: PageIssue) => void;
}

/**
 * The review's issue marks: one `.highlight` per positioned issue, at its raw
 * bbox.
 *
 * Signature/date slots are NOT drawn here any more (W1): the workspace draws
 * every slot exactly once, as `.slot` (`overlay/SlotLayer.tsx`), standing on
 * its rule and draggable. A slot issue handed in here is skipped, so there is
 * never a second, differently-positioned box for the same field.
 */
export function FieldHighlighter({ issues, scale, activeFieldId, onSelect }: Props) {
  // Only issues carrying coordinates can be drawn; the rest live in the sidebar.
  const positioned = issues.filter(
    (issue) => issue.bbox !== null && issue.kind !== 'signature_slot',
  );

  return (
    <>
      {positioned.map((issue) => {
        const isActive = activeFieldId != null && issue.fieldId === activeFieldId;
        const className = `highlight highlight--${issue.severity}${isActive ? ' highlight--active' : ''}`;
        return (
          <button
            key={issue.key}
            type="button"
            className={className}
            style={ptRectToPxBox(issue.bbox as BBox, scale)}
            title={`${issue.label}: ${issue.detail}`}
            data-field-id={issue.fieldId ?? ''}
            onClick={(event) => {
              event.stopPropagation();
              onSelect?.(issue);
            }}
          >
            <span className="highlight__label">{issue.label}</span>
          </button>
        );
      })}
    </>
  );
}
