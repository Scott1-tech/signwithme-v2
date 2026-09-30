import { useRef } from 'react';
import type { PdfSearchState, SearchHit } from '../hooks/usePdfSearch';

/**
 * The search control in the viewer toolbar.
 *
 * All of the state lives in `hooks/usePdfSearch.ts`; this is the keyboard
 * contract and the counter, nothing else:
 *
 * * **Enter** steps to the next match, **Shift+Enter** to the previous one --
 *   the pair every reader already knows from a browser's own find bar.
 * * **Escape** empties the query, which drops every mark, and keeps the focus
 *   in the field so a second search can simply be typed.
 * * The counter is the one place the reader learns whether the search is still
 *   running (`Searching… 12 / 66`), found nothing (`0`), or found something
 *   (`3 / 17`).
 */

interface Props {
  search: PdfSearchState;
  /**
   * «Highlight all matches» (W3): one highlight annotation per match, from
   * the matches' own boxes. Offered once the search has finished and found
   * something; absent (no button) where the viewer is read-only.
   */
  onHighlightAll?: (hits: SearchHit[]) => void;
}

export function PdfSearchBox({ search, onHighlightAll }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const { query, hits, activeIndex, searching, progress, truncated } = search;

  const typed = query.trim().length > 0;
  const total = hits.length;
  const position = activeIndex >= 0 ? activeIndex + 1 : 0;

  const onKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      if (event.shiftKey) search.previous();
      else search.next();
      return;
    }
    if (event.key === 'Escape') {
      event.preventDefault();
      // Stop here: the pages behind have their own Escape handling (closing a
      // slot toolbar), and clearing the search is what the reader asked for.
      event.stopPropagation();
      search.clear();
      inputRef.current?.focus();
    }
  };

  return (
    <div className="pdf-search" role="search">
      <input
        ref={inputRef}
        type="search"
        className="pdf-search__input"
        data-testid="pdf-search-input"
        aria-label="Find in document"
        placeholder="Find in document"
        value={query}
        onChange={(event) => search.setQuery(event.target.value)}
        onKeyDown={onKeyDown}
        // `type="search"` is what this field is, for assistive technology.
        // Chromium's own ✕ that comes with it is hidden in `viewer.css`: it
        // would sit beside ours and leave the focus nowhere.
        autoComplete="off"
        spellCheck={false}
      />

      {typed && (
        <span
          className={`pdf-search__count${total === 0 && !searching ? ' pdf-search__count--empty' : ''}`}
          data-testid="pdf-search-count"
          role="status"
          aria-live="polite"
        >
          {searching
            ? `Searching… ${progress.done} / ${progress.total}`
            : total === 0
              ? '0'
              : `${position} / ${total}${truncated ? '+' : ''}`}
        </span>
      )}

      <button
        type="button"
        data-testid="pdf-search-prev"
        aria-label="Previous match"
        title="Previous match (Shift+Enter)"
        onClick={search.previous}
        disabled={total === 0}
      >
        <span aria-hidden="true">↑</span>
      </button>
      <button
        type="button"
        data-testid="pdf-search-next"
        aria-label="Next match"
        title="Next match (Enter)"
        onClick={search.next}
        disabled={total === 0}
      >
        <span aria-hidden="true">↓</span>
      </button>
      <button
        type="button"
        data-testid="pdf-search-clear"
        aria-label="Clear search"
        title="Clear search (Escape)"
        onClick={() => {
          search.clear();
          inputRef.current?.focus();
        }}
        disabled={!typed}
      >
        <span aria-hidden="true">✕</span>
      </button>
      {onHighlightAll && (
        <button
          type="button"
          className="pdf-search__highlight-all"
          data-testid="pdf-search-highlight-all"
          aria-label={
            total > 0
              ? `Highlight all ${total}${truncated ? '+' : ''} matches`
              : 'Highlight all matches'
          }
          title={
            searching
              ? 'Wait for the search to finish'
              : 'Highlight every match (one undo step)'
          }
          onClick={() => onHighlightAll(hits)}
          disabled={total === 0 || searching}
        >
          <span className="pdf-search__highlight-swatch" aria-hidden="true" />
          Highlight all
        </button>
      )}
    </div>
  );
}
