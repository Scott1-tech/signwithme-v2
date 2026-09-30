import type { ReactNode } from 'react';
import type { ToolId, ToolMenu } from '../tools/types';

/**
 * F1a: the toolbar's icons, as inline SVG (no icon package -- 0 new npm
 * dependencies). 24 × 24 view box, drawn with `currentColor`, so the Sejda
 * accent (`--sj-accent`) colours them through the button's `color`.
 *
 * Every icon is DECORATIVE (`aria-hidden`): the button around it carries the
 * accessible name (`aria-label`) and the tooltip (`title`). A tool without an
 * icon here falls back to its one-character `glyph` (registry), so a new tool
 * folder still draws something before anyone draws its icon.
 */

const PATHS: Record<string, ReactNode> = {
  select: <path d="M6 3.5l12.5 7.2-5.6 1.4-2.6 5.6z" />,
  text: (
    <>
      <path d="M5 5.5h14" />
      <path d="M12 5.5v13" />
      <path d="M9 18.5h6" />
    </>
  ),
  'edit-text': (
    <>
      <path d="M4 5h9" />
      <path d="M8.5 5v8" />
      <path d="M13.5 19.5l1-3.6 5.3-5.3a1.8 1.8 0 012.6 2.6l-5.3 5.3z" />
    </>
  ),
  date: (
    <>
      <rect x="3.5" y="5" width="17" height="15.5" rx="2" />
      <path d="M3.5 10h17" />
      <path d="M8 3v4" />
      <path d="M16 3v4" />
    </>
  ),
  check: <path d="M5 12.5l4.5 4.5L19 7" />,
  cross: (
    <>
      <path d="M6.5 6.5l11 11" />
      <path d="M17.5 6.5l-11 11" />
    </>
  ),
  image: (
    <>
      <rect x="3.5" y="4.5" width="17" height="15" rx="2" />
      <circle cx="9" cy="10" r="1.6" />
      <path d="M20.5 16l-5-5-9.5 8.5" />
    </>
  ),
  signature: (
    <>
      <path d="M3.5 16c2.5-4.5 4.5-8 6-8 2.2 0-1.6 7.5.6 7.5 1.6 0 2.6-3.5 4.2-3.5 1.4 0 .6 3 2.4 3 1 0 2-.8 3.8-2" />
      <path d="M3.5 20h17" />
    </>
  ),
  initials: (
    <>
      <path d="M4 17l3.2-9 3.2 9" />
      <path d="M5.2 13.8h4" />
      <path d="M13.5 8h3.2a2.2 2.2 0 010 4.4h-3.2z" />
      <path d="M13.5 12.4h3.8a2.3 2.3 0 010 4.6h-3.8z" />
    </>
  ),
  whiteout: (
    <>
      <path d="M7.5 19.5l-3-3a1.4 1.4 0 010-2l9.2-9.2a1.4 1.4 0 012 0l4 4a1.4 1.4 0 010 2L12 19.5z" />
      <path d="M9.5 9.5l5 5" />
      <path d="M12 19.5h8" />
    </>
  ),
  highlight: (
    <>
      <path d="M9.5 14.5l-3 3v2h4l3-3" />
      <path d="M9.5 14.5l7.8-7.8a2 2 0 012.8 2.8l-7.8 7.8z" />
      <path d="M3.5 21h7" />
    </>
  ),
  underline: (
    <>
      <path d="M7 4.5v6a5 5 0 0010 0v-6" />
      <path d="M5 20h14" />
    </>
  ),
  strikeout: (
    <>
      <path d="M4.5 12h15" />
      <path d="M16 7a4 3 0 00-4-2h-.5a3.5 3 0 00-1.5 5.8" />
      <path d="M8 17a4 3 0 004 2h.5a3.5 3 0 002.3-5" />
    </>
  ),
  ink: <path d="M3.5 16.5c2-3 3.6-7 6-7 2.8 0 1.4 7.5 4.6 7.5 2.2 0 3.6-3.5 6.4-6" />,
  note: (
    <>
      <path d="M5 4h14v11l-5 5H5z" />
      <path d="M14 20v-5h5" />
      <path d="M8.5 9h7" />
      <path d="M8.5 12.5h4" />
    </>
  ),
  rect: <rect x="4" y="6" width="16" height="12" rx="1" />,
  ellipse: <ellipse cx="12" cy="12" rx="8.5" ry="6" />,
  line: <path d="M5 19L19 5" />,
  arrow: (
    <>
      <path d="M5 19L18.5 5.5" />
      <path d="M10.5 5h8.5v8.5" />
    </>
  ),
  /* -- F2 tools ------------------------------------------------------------------ */
  textbox: (
    <>
      <rect x="3.5" y="4.5" width="17" height="15" rx="1.5" strokeDasharray="2.5 2" />
      <path d="M8 9h8" />
      <path d="M12 9v7" />
    </>
  ),
  links: (
    <>
      <path d="M10.5 13.5a4 4 0 005.7 0l3-3a4 4 0 00-5.7-5.7l-1.2 1.2" />
      <path d="M13.5 10.5a4 4 0 00-5.7 0l-3 3a4 4 0 005.7 5.7l1.2-1.2" />
    </>
  ),
  square: <rect x="6" y="6" width="12" height="12" rx="1" fill="currentColor" />,
  stamp: (
    <>
      <path d="M9.5 4.5h5l-1 7h-3z" />
      <path d="M5 14.5h14v3H5z" />
      <path d="M6.5 20.5h11" />
    </>
  ),
  'image-remove': (
    <>
      <rect x="3.5" y="4.5" width="13" height="11" rx="1.5" />
      <path d="M14.5 14.5l6 6" />
      <path d="M20.5 14.5l-6 6" />
    </>
  ),
  redact: (
    <>
      <rect x="3.5" y="8" width="17" height="8" rx="1" fill="currentColor" />
      <path d="M3.5 4.5h10" />
      <path d="M3.5 19.5h14" />
    </>
  ),
  'highlight-ink': (
    <>
      <path d="M3.5 17c2-3 3.6-7 6-7 2.8 0 1.4 7.5 4.6 7.5 2.2 0 3.6-3.5 6.4-6" strokeWidth={4} strokeOpacity={0.35} />
      <path d="M3.5 17c2-3 3.6-7 6-7 2.8 0 1.4 7.5 4.6 7.5 2.2 0 3.6-3.5 6.4-6" />
    </>
  ),
  'align-left': (
    <>
      <path d="M4 6h16" />
      <path d="M4 10h10" />
      <path d="M4 14h16" />
      <path d="M4 18h10" />
    </>
  ),
  'align-center': (
    <>
      <path d="M4 6h16" />
      <path d="M7 10h10" />
      <path d="M4 14h16" />
      <path d="M7 18h10" />
    </>
  ),
  'align-right': (
    <>
      <path d="M4 6h16" />
      <path d="M10 10h10" />
      <path d="M4 14h16" />
      <path d="M10 18h10" />
    </>
  ),
  'align-justify': (
    <>
      <path d="M4 6h16" />
      <path d="M4 10h16" />
      <path d="M4 14h16" />
      <path d="M4 18h16" />
    </>
  ),
  pages: (
    <>
      <rect x="4" y="3.5" width="11" height="14" rx="1" />
      <path d="M9 20.5h10v-14" />
    </>
  ),
  /* -- the drop-downs (the main half shows the member used last; these are
        for anything that names the menu itself) ------------------------------ */
  'menu:forms': (
    <>
      <rect x="4" y="4" width="16" height="16" rx="2" />
      <path d="M8 12.5l3 3 5-6.5" />
    </>
  ),
  'menu:shapes': (
    <>
      <rect x="3.5" y="3.5" width="10" height="10" rx="1" />
      <circle cx="15.5" cy="15.5" r="5" />
    </>
  ),
  /* -- the rest of the chrome --------------------------------------------------- */
  undo: (
    <>
      <path d="M9 14.5L4 9.5l5-5" />
      <path d="M4 9.5h10.5a5.5 5.5 0 010 11H11" />
    </>
  ),
  redo: (
    <>
      <path d="M15 14.5l5-5-5-5" />
      <path d="M20 9.5H9.5a5.5 5.5 0 000 11H13" />
    </>
  ),
  caret: <path d="M7 10l5 5 5-5" />,
  delete: (
    <>
      <path d="M4.5 6.5h15" />
      <path d="M9.5 6.5V4.5h5v2" />
      <path d="M6.5 6.5l1 13h9l1-13" />
      <path d="M10 10.5v5.5" />
      <path d="M14 10.5v5.5" />
    </>
  ),
  download: (
    <>
      <path d="M12 4v11" />
      <path d="M7 10.5l5 5 5-5" />
      <path d="M5 20h14" />
    </>
  ),
  finalize: (
    <>
      <path d="M6 3.5h8l4 4v13H6z" />
      <path d="M14 3.5v4h4" />
      <path d="M9 14l2 2 4-4.5" />
    </>
  ),
};

const MENU_ICON: Record<ToolMenu, string> = {
  text: 'text',
  forms: 'menu:forms',
  images: 'image',
  sign: 'signature',
  whiteout: 'whiteout',
  annotate: 'highlight',
  shapes: 'menu:shapes',
};

interface IconProps {
  name: string;
  /** Pixel size of the square (the button sets the colour). */
  size?: number;
  className?: string;
}

/** One icon by name; `null` when there is none (the caller falls back to a glyph). */
export function Icon({ name, size = 18, className }: IconProps) {
  const body = PATHS[name];
  if (!body) return null;
  return (
    <svg
      className={`sj-icon${className ? ` ${className}` : ''}`}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      data-icon={name}
    >
      {body}
    </svg>
  );
}

export const hasToolIcon = (id: ToolId): boolean => id in PATHS;

/** A tool's icon, or its registry glyph when it has none yet. */
export function ToolIcon({ id, glyph }: { id: ToolId; glyph: string }) {
  if (hasToolIcon(id)) return <Icon name={id} />;
  return <>{glyph}</>;
}

/** The icon that names a drop-down. */
export const MenuIcon = ({ menu }: { menu: ToolMenu }) => <Icon name={MENU_ICON[menu]} />;
