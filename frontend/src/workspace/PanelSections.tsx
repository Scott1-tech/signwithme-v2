import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import type { WorkspacePanel } from './useWorkspaceRoute';

/** The right-hand panel's sections, top to bottom (plan §2.7). */
export const SECTION_IDS = ['review', 'sign', 'layout', 'objects'] as const;
export type SectionId = (typeof SECTION_IDS)[number];

export const SECTION_TITLE: Record<SectionId, string> = {
  review: 'Review',
  sign: 'Signature & date',
  layout: 'Placement',
  objects: 'Objects',
};

/** What each URL segment forces open. `/sign` opens Layout too: the slots'
 *  positions are part of signing (and `open_sign` waits for `layout-panel`). */
export const FORCED_SECTIONS: Record<WorkspacePanel, SectionId[]> = {
  review: ['review'],
  sign: ['sign', 'layout'],
  edit: ['objects'],
};

export const SECTIONS_KEY = 'swm.panel.sections';

function readStored(): SectionId[] | null {
  try {
    const raw = localStorage.getItem(SECTIONS_KEY);
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return null;
    return parsed.filter((id): id is SectionId => (SECTION_IDS as readonly unknown[]).includes(id));
  } catch {
    return null;
  }
}

function writeStored(open: ReadonlySet<SectionId>): void {
  try {
    localStorage.setItem(SECTIONS_KEY, JSON.stringify(SECTION_IDS.filter((id) => open.has(id))));
  } catch {
    /* private mode: the choice simply does not persist */
  }
}

/**
 * Which sections are open. The URL segment forces its own open (and scrolls
 * the first of them into view) every time it changes; what the user opened or
 * closed by hand is remembered in `swm.panel.sections`. A closed section
 * renders nothing but its header, so nothing inside it is in the DOM twice.
 */
export function usePanelSections(panel: WorkspacePanel) {
  const [open, setOpen] = useState<ReadonlySet<SectionId>>(
    () => new Set<SectionId>([...(readStored() ?? []), ...FORCED_SECTIONS[panel]]),
  );
  const refs = useRef(new Map<SectionId, HTMLElement>());
  const firstRun = useRef(true);

  useEffect(() => {
    const forced = FORCED_SECTIONS[panel];
    setOpen((current) => {
      if (forced.every((id) => current.has(id))) return current;
      const next = new Set(current);
      forced.forEach((id) => next.add(id));
      return next;
    });
    // The page just opened on this panel: it is already where it should be.
    if (firstRun.current) {
      firstRun.current = false;
      return;
    }
    const frame = window.requestAnimationFrame(() => {
      refs.current.get(forced[0])?.scrollIntoView({ block: 'nearest' });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [panel]);

  const toggle = useCallback((id: SectionId) => {
    setOpen((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      writeStored(next);
      return next;
    });
  }, []);

  const register = useCallback((id: SectionId, element: HTMLElement | null) => {
    if (element) refs.current.set(id, element);
    else refs.current.delete(id);
  }, []);

  return { open, toggle, register };
}

export type PanelSectionsState = ReturnType<typeof usePanelSections>;

interface SectionProps {
  id: SectionId;
  sections: PanelSectionsState;
  /** A short status beside the title (e.g. "3 issues"). */
  badge?: ReactNode;
  children: ReactNode;
}

/** One accordion section: a real `<button>` header with `aria-expanded`. */
export function PanelSection({ id, sections, badge, children }: SectionProps) {
  const isOpen = sections.open.has(id);
  const bodyId = `workspace-section-${id}`;
  return (
    <section
      className={`workspace-section${isOpen ? ' is-open' : ''}`}
      data-testid={`section-${id}`}
      data-open={isOpen ? 'true' : 'false'}
      ref={(element) => sections.register(id, element)}
    >
      <h2 className="workspace-section__heading">
        <button
          type="button"
          className="workspace-section__toggle"
          data-testid={`section-toggle-${id}`}
          aria-expanded={isOpen}
          aria-controls={bodyId}
          onClick={() => sections.toggle(id)}
        >
          <span className="workspace-section__chevron" aria-hidden="true">
            {isOpen ? '▾' : '▸'}
          </span>
          <span className="workspace-section__title">{SECTION_TITLE[id]}</span>
          {badge !== undefined && badge !== null && (
            <span className="workspace-section__badge">{badge}</span>
          )}
        </button>
      </h2>
      {isOpen && (
        <div className="workspace-section__body" id={bodyId}>
          {children}
        </div>
      )}
    </section>
  );
}
