import { useEffect, useMemo, useRef, useState } from 'react';
import type { EditorFontFamilyId, FontOut } from '../../api/document';
import { useEditorFonts } from '../../hooks/useEditorFonts';
import {
  EDITOR_FONT_LABEL,
  editorFontCss,
  ensureEditorFont,
  isEditorFamily,
  licenceLabel,
  substituteChip,
} from '../../lib/editorFonts';

/**
 * The font list of a text edit / a text box (EDITOR-V2-PLAN §7 «FontPicker»).
 *
 * The quick list is the three Liberation families (metric-compatible with
 * Helvetica / Times / Courier) plus whatever is chosen. «More fonts…» (F2)
 * opens a searchable dialog over `GET /api/fonts` (B2): every family the
 * server embeds, its licence, what it stands in for («Calibri → Carlito
 * (substitute)») and its name set in itself. Only families that list offers
 * can be picked -- no name the server would refuse. The block's original
 * font is shown as what it is: a substitute («Helvetica → Liberation Sans
 * (substitute)»).
 */

export const EDIT_FAMILIES: readonly EditorFontFamilyId[] = ['LiberationSans', 'LiberationSerif', 'LiberationMono'];

/** The family a PDF font name is printed with (what the server does, for the chip). */
export function substituteLabel(original: string, family: EditorFontFamilyId): string | null {
  const name = original.replace(/^[A-Z]{6}\+/, '').trim();
  if (!name || /^Liberation/i.test(name)) return null;
  return `${name} → ${EDITOR_FONT_LABEL[family]} (substitute)`;
}

interface Props {
  value: EditorFontFamilyId;
  original: string;
  /** The family the server picked for the block (the substitute's). */
  substitute: EditorFontFamilyId;
  onChange: (family: EditorFontFamilyId) => void;
  /** Where the testids start (`text-font` for an edited block, `textbox-font` for a text box). */
  testid?: string;
}

export function FontPicker({ value, original, substitute, onChange, testid = 'text-font' }: Props) {
  const [open, setOpen] = useState(false);
  const [more, setMore] = useState(false);
  const [query, setQuery] = useState('');
  const rootRef = useRef<HTMLDivElement>(null);
  const fonts = useEditorFonts(true, 'text');
  const chip = original ? substituteLabel(original, substitute) : null;
  const quick = useMemo(() => {
    const list = [...EDIT_FAMILIES];
    if (!list.includes(value)) list.push(value);
    return list.filter((family) =>
      EDITOR_FONT_LABEL[family].toLowerCase().includes(query.trim().toLowerCase()),
    );
  }, [query, value]);

  useEffect(() => {
    if (!open) return undefined;
    const onDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener('pointerdown', onDown, true);
    return () => document.removeEventListener('pointerdown', onDown, true);
  }, [open]);

  const pickable = (fonts ?? []).filter((font) => font.category === 'text' && isEditorFamily(font.family));

  return (
    <div className="tb-font" ref={rootRef}>
      <button
        type="button"
        className="tb-btn tb-font__button"
        data-testid={testid}
        data-value={value}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={`Font: ${EDITOR_FONT_LABEL[value]}`}
        title="Font"
        onMouseDown={(event) => event.preventDefault()}
        onClick={() => setOpen((current) => !current)}
        style={{ fontFamily: editorFontCss(value) }}
      >
        {EDITOR_FONT_LABEL[value]} ▾
      </button>
      {open && (
        <div className="tb-font__menu" data-testid={`${testid}-menu`}>
          <input
            className="tb-font__search"
            data-testid={`${testid}-search`}
            placeholder="Search fonts"
            aria-label="Search fonts"
            value={query}
            autoFocus
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              event.stopPropagation();
              if (event.key === 'Escape') setOpen(false);
              if (event.key === 'Enter' && quick[0]) {
                onChange(quick[0]);
                setOpen(false);
              }
            }}
          />
          {chip && (
            <p className="tb-font__chip" data-testid={`${testid}-substitute`} title="The original font is not embedded in a form new text can use; new text is set in its metric-compatible substitute.">
              {chip}
            </p>
          )}
          <ul className="tb-font__list" role="listbox" aria-label="Fonts">
            {quick.map((family) => (
              <li key={family}>
                <button
                  type="button"
                  role="option"
                  aria-selected={family === value}
                  className={`tb-font__option${family === value ? ' is-current' : ''}`}
                  data-testid={`${testid}-${family}`}
                  style={{ fontFamily: editorFontCss(family) }}
                  onMouseDown={(event) => event.preventDefault()}
                  onClick={() => {
                    onChange(family);
                    setOpen(false);
                  }}
                >
                  <span className="tb-font__check" aria-hidden="true">
                    {family === value ? '✓' : ''}
                  </span>
                  {EDITOR_FONT_LABEL[family]}
                </button>
              </li>
            ))}
            {quick.length === 0 && <li className="tb-font__none">No font matches.</li>}
          </ul>
          {pickable.length > 0 && (
            <button
              type="button"
              className="tb-font__more"
              data-testid={`${testid}-more`}
              title="Every font the server can set new text in, with its licence"
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => {
                setOpen(false);
                setMore(true);
              }}
            >
              More fonts…
            </button>
          )}
        </div>
      )}
      {more && (
        <MoreFontsDialog
          fonts={pickable}
          value={value}
          onPick={(family) => {
            setMore(false);
            onChange(family);
          }}
          onClose={() => setMore(false)}
        />
      )}
    </div>
  );
}

/** «More fonts…»: the searchable list of `GET /api/fonts`. */
export function MoreFontsDialog({
  fonts,
  value,
  onPick,
  onClose,
}: {
  fonts: FontOut[];
  value: EditorFontFamilyId;
  onPick: (family: EditorFontFamilyId) => void;
  onClose: () => void;
}) {
  const [query, setQuery] = useState('');
  const shown = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return fonts.filter(
      (font) =>
        !needle ||
        font.label.toLowerCase().includes(needle) ||
        font.family.toLowerCase().includes(needle) ||
        font.substitutes_for.some((name) => name.toLowerCase().includes(needle)),
    );
  }, [fonts, query]);
  useEffect(() => {
    fonts.forEach((font) => void ensureEditorFont(font.family));
  }, [fonts]);

  return (
    <div
      className="modal"
      role="dialog"
      aria-modal="true"
      aria-label="More fonts"
      data-testid="fonts-dialog"
      onPointerDown={(event) => {
        event.stopPropagation();
        if (event.target === event.currentTarget) onClose();
      }}
      onClick={(event) => event.stopPropagation()}
      onKeyDown={(event) => {
        event.stopPropagation();
        if (event.key === 'Escape') onClose();
      }}
    >
      <div className="modal__panel fonts-dialog">
        <header className="fonts-dialog__head">
          <h2>More fonts</h2>
          <button type="button" className="tb-btn" data-testid="fonts-close" aria-label="Close" title="Close" onClick={onClose}>
            ×
          </button>
        </header>
        <input
          className="tb-font__search fonts-dialog__search"
          data-testid="fonts-search"
          placeholder="Search by name or by the font it replaces (e.g. Calibri)"
          aria-label="Search fonts"
          value={query}
          autoFocus
          onChange={(event) => setQuery(event.target.value)}
        />
        <ul className="fonts-dialog__list" role="listbox" aria-label="Fonts">
          {shown.map((font) => {
            const family = font.family as EditorFontFamilyId;
            const chip = substituteChip(font);
            return (
              <li key={font.family}>
                <button
                  type="button"
                  role="option"
                  aria-selected={family === value}
                  className={`fonts-dialog__option${family === value ? ' is-current' : ''}`}
                  data-testid={`fonts-option-${font.family}`}
                  onClick={() => onPick(family)}
                >
                  <span
                    className="fonts-dialog__preview"
                    data-testid={`fonts-preview-${font.family}`}
                    style={{ fontFamily: editorFontCss(family) }}
                  >
                    {font.label}
                  </span>
                  <span className="fonts-dialog__meta">
                    <span data-testid={`fonts-license-${font.family}`}>{licenceLabel(font.license)}</span>
                    {font.scripts.length > 0 && <span> · {font.scripts.join(', ')}</span>}
                  </span>
                  {chip && (
                    <span className="tb-font__chip fonts-dialog__chip" data-testid={`fonts-substitute-${font.family}`}>
                      {chip}
                    </span>
                  )}
                </button>
              </li>
            );
          })}
          {shown.length === 0 && <li className="tb-font__none">No font matches.</li>}
        </ul>
      </div>
    </div>
  );
}
