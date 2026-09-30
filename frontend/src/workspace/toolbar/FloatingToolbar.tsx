import {
  Fragment,
  type ReactNode,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type RefObject,
} from 'react';
import { isDrawKind } from '../../lib/annotations';
import { TOOL_MENUS, toolForShortcut } from '../tools/registry';
import type { ToolDefinition, ToolId, ToolMenu } from '../tools/types';
import { Icon, ToolIcon } from './icons';
import {
  HistoryControls,
  PROPERTY_GROUPS,
  PropertiesBar,
  propertyGroupsFor,
  type PropertiesBarProps,
  type PropertyGroupId,
} from './PropertiesBar';
import { EDGE_GAP_PX, useFloatingPanel } from './useFloatingPanel';

/** F1a: where an open drop-down list is drawn (`position: fixed`, viewport px). */
interface MenuPlacement {
  menu: ToolMenu;
  left: number;
  top: number;
  maxHeight: number;
  /** Which side of its button it opened on (a data attribute, for the checks). */
  side: 'below' | 'above' | 'right' | 'left';
}

/** A list keeps this far from the viewport's edges, and this far from its button. */
const MENU_MARGIN_PX = 8;
const MENU_GAP_PX = 4;
/** Never squeeze a list below this; it scrolls instead. */
const MENU_MIN_HEIGHT_PX = 96;

/**
 * Place a list of `width` × `height` next to `anchor`, inside the viewport:
 * below the button (above when there is more room there -- the ≤ 640 px bottom
 * bar), or beside a vertical panel (to the left on the right edge). The list
 * is clamped to the viewport and gets a max height (it scrolls past it).
 */
function placeMenu(
  anchor: DOMRect,
  width: number,
  height: number,
  view: { width: number; height: number },
  vertical: false | 'left' | 'right',
): Omit<MenuPlacement, 'menu'> {
  const clamp = (value: number, lo: number, hi: number) => Math.max(lo, Math.min(value, Math.max(lo, hi)));
  const room = Math.max(MENU_MIN_HEIGHT_PX, view.height - 2 * MENU_MARGIN_PX);
  if (vertical) {
    const rightSide = anchor.right + MENU_GAP_PX;
    const leftSide = anchor.left - MENU_GAP_PX - width;
    const fitsRight = rightSide + width <= view.width - MENU_MARGIN_PX;
    const fitsLeft = leftSide >= MENU_MARGIN_PX;
    const side = vertical === 'right' ? (fitsLeft || !fitsRight ? 'left' : 'right') : fitsRight || !fitsLeft ? 'right' : 'left';
    const left = clamp(side === 'right' ? rightSide : leftSide, MENU_MARGIN_PX, view.width - MENU_MARGIN_PX - width);
    const shown = Math.min(height, room);
    const top = clamp(anchor.top, MENU_MARGIN_PX, view.height - MENU_MARGIN_PX - shown);
    return { left, top, maxHeight: room, side };
  }
  const below = view.height - MENU_MARGIN_PX - (anchor.bottom + MENU_GAP_PX);
  const above = anchor.top - MENU_GAP_PX - MENU_MARGIN_PX;
  const down = height <= below || below >= above;
  const maxHeight = Math.max(MENU_MIN_HEIGHT_PX, Math.min(room, down ? below : above));
  const shown = Math.min(height, maxHeight);
  const top = clamp(
    down ? anchor.bottom + MENU_GAP_PX : anchor.top - MENU_GAP_PX - shown,
    MENU_MARGIN_PX,
    view.height - MENU_MARGIN_PX - shown,
  );
  const left = clamp(anchor.left, MENU_MARGIN_PX, view.width - MENU_MARGIN_PX - width);
  return { left, top, maxHeight, side: down ? 'below' : 'above' };
}

/** One button (or drop-down) of the tools row. */
type RowItem =
  | { type: 'tool'; tool: ToolDefinition }
  | { type: 'menu'; menu: ToolMenu; tools: ToolDefinition[] };

/** Roving-focus keys: a tool id, a drop-down's main button, or its ▾. */
const menuKey = (menu: ToolMenu) => `menu:${menu}`;
const toggleKey = (menu: ToolMenu) => `menu:${menu}:toggle`;

/**
 * The workspace's ONE toolbar (plan §2.6): the registered tools, the
 * PropertiesBar, and the grip that moves the whole panel.
 *
 * `data-testid="editor-toolbar"` with `role="toolbar"`. Default `docked-top`
 * (in the column's flow, as before W2); drag the grip (`toolbar-grip`) to
 * float it, drop it on the top edge to dock it again, on the left/right edge
 * to stand it up vertically there. `toolbar-collapse` folds it to the armed
 * tool alone. Placement persists in `swm.toolbar` (`useFloatingPanel`).
 *
 * KEYBOARD
 * --------
 *  * the tools are ONE tab stop (roving tabindex): arrows move between them,
 *    Home / End jump to the ends, Enter / Space arm;
 *  * Alt+T focuses the armed tool from anywhere; Esc inside the toolbar hands
 *    the focus back to the document;
 *  * one letter arms a tool (`aria-keyshortcuts`: V T D K X S for the six
 *    tools today) -- never while typing in an input / textarea / select, never
 *    with Ctrl / Alt / Meta, never while a dialog is open, never for a tool
 *    that is disabled;
 *  * on the grip: arrows move the panel 10 px (Shift 50), Enter docks/floats.
 *
 * NARROW
 * ------
 * Horizontal, when the groups do not fit, the LAST groups move into «More ▾»
 * (`toolbar-more` / `toolbar-more-menu`), measured with a ResizeObserver. In
 * the vertical panel and at ≤ 640 px (a bottom bar that scrolls sideways,
 * 44 px targets, no dragging) the PropertiesBar is a pop-over
 * (`toolbar-properties` / `toolbar-properties-popover`).
 *
 * A disabled tool says why ON the tool (title, accessible name,
 * `data-disabled-reason`) and once more in a compact badge
 * (`tool-disabled-reason`) that never wraps the panel onto a second line.
 *
 * DROP-DOWNS (W4)
 * ---------------
 * Seventeen tools do not fit one row at 1600 px next to the properties (and
 * a free signature's count), so the tools that declare a `menu` share ONE
 * button, Sejda-style: «Markup ▾» (highlight / underline / strikeout),
 * «Shapes ▾» (freehand / rectangle / ellipse / line / arrow) and «Insert ▾»
 * (note / image) -- ten buttons in the row, as before W4. The button
 * (`toolgroup-<menu>`, one roving stop) shows and arms the member used last;
 * its ▾ corner (`toolgroup-<menu>-toggle`, the mouse's way in) or Alt+↓ / F4
 * on the button opens the list (`toolgroup-<menu>-menu`, `role="menu"`),
 * whose items are the members' own `tool-<id>` buttons -- in the DOM even
 * while the list is closed (`hidden`), so every hook and `aria-keyshortcuts`
 * stays where it was. The one-letter shortcuts arm a member directly. In the
 * list: ↑ ↓ Home End move, Enter / Space arm, Esc closes (focus back on the
 * button).
 *
 * The property groups follow what the boxes describe (`propertyGroupsFor`):
 * `stroke` / `fill` only appear for a drawing that has them.
 *
 * F1a (EDITOR-V2-PLAN §7, Sejda style)
 * ------------------------------------
 * The row is the spec's: Select | Text ▼ | Forms ▼ | Images ▼ | Sign ▼ |
 * Whiteout | Annotate ▼ | Shapes ▼ | Undo | Redo («Links» joins with its tool
 * in F2). The menus come from the registry (`group` / `menu`), the icons are
 * inline SVG (`icons.tsx`), the look is `styles/sejda.css` (scoped to the
 * workspace). Undo / Redo stand right after the tools in a wide row (they are
 * never moved under «More ▾»); in the vertical / mobile pop-over they stay the
 * `history` group. An open list is `position: fixed`, placed inside the
 * viewport next to its button (`placeMenu`) and placed again on scroll, resize,
 * zoom, and whenever the panel moves.
 */

interface Props {
  /** The document column the panel floats in (`.workspace__stage`). */
  stageRef: RefObject<HTMLElement>;
  tools: readonly ToolDefinition[];
  toolId: ToolId;
  onTool: (tool: ToolId) => void;
  /** Tools that cannot be used right now, with the reason in words. */
  disabledTools: Partial<Record<ToolId, string>>;
  properties: PropertiesBarProps;
  /**
   * F2: what a drop-down holds besides its tools (Images ▾: the gallery, New
   * Image, New Stamp; Sign ▾: the saved signatures, New Signature, My
   * signatures…; Annotate ▾: Show / hide annotations). Rendered after the
   * tools; `close` shuts the list. Never a `tool-*` testid.
   */
  menuExtras?: Partial<Record<ToolMenu, (close: () => void) => ReactNode>>;
}

const MORE_FALLBACK_PX = 84;

export function FloatingToolbar({ stageRef, tools, toolId, onTool, disabledTools, properties, menuExtras }: Props) {
  const panelRef = useRef<HTMLDivElement>(null);
  const mainRef = useRef<HTMLDivElement>(null);
  const moreRef = useRef<HTMLSpanElement>(null);
  const propsRef = useRef<HTMLSpanElement>(null);
  /** Every focusable stop of the tools row, by roving key. */
  const toolRefs = useRef(new Map<string, HTMLButtonElement>());
  /** The menu items, by tool id (focus inside an open drop-down). */
  const itemRefs = useRef(new Map<ToolId, HTMLButtonElement>());
  const menuRefs = useRef(new Map<ToolMenu, HTMLSpanElement>());
  /** The property groups that apply right now (W4: `stroke` / `fill` come and go). */
  const groups = useMemo(
    () => propertyGroupsFor(properties.kind, Boolean(properties.editor)),
    [properties.kind, properties.editor],
  );
  const floating = useFloatingPanel(stageRef, panelRef);
  const { placement, dragPosition, narrow, stageSize } = floating;

  const collapsed = placement.collapsed;
  const mode = narrow ? 'mobile' : dragPosition ? 'floating' : placement.mode;
  const vertical =
    !narrow && placement.mode === 'floating' && placement.orientation === 'vertical';
  /** The PropertiesBar sits in the bar itself only when the bar is a wide row. */
  const inlineProps = !narrow && !vertical && !collapsed;
  /**
   * F1a: the groups the ROW measures and may move under «More ▾». In a wide
   * row Undo / Redo are not among them: they stand right after the tools.
   */
  const barGroups = useMemo(
    () => (inlineProps ? groups.filter((id) => id !== 'history') : groups),
    [groups, inlineProps],
  );
  const groupsKey = barGroups.join('|');

  const reasons = useMemo(
    () =>
      Array.from(
        new Set(Object.values(disabledTools).filter((reason): reason is string => Boolean(reason))),
      ),
    [disabledTools],
  );
  const reasonText = reasons.join(' · ');

  /* -- «More ▾»: how many property groups fit in the row -------------------- */

  /**
   * Each group's width, measured in the mode it was drawn in: a compact row
   * also hides the property labels ("Font", "Size", "Date"), so a width taken
   * compact must never decide whether the FULL row fits -- or the row would
   * flip between the two for ever.
   */
  const widths = useRef({
    full: new Map<PropertyGroupId, number>(),
    compact: new Map<PropertyGroupId, number>(),
  });
  /** The fixed part (grip, tools, badge, collapse) with its labels, and what the labels take. */
  const mainFull = useRef(0);
  const labelsWidth = useRef(0);
  /** The same part once it really was drawn without labels (the estimate is only a first guess). */
  const mainCompact = useRef<number | null>(null);
  const [visibleCount, setVisibleCount] = useState<number>(PROPERTY_GROUPS.length);
  /** Step one of fitting a narrow row: the tools drop their labels (glyph + title stay). */
  const [compact, setCompact] = useState(false);
  const [moreOpen, setMoreOpen] = useState(false);
  const [propsOpen, setPropsOpen] = useState(false);

  /**
   * What the width of the two groups whose CONTENT changes depends on. A group
   * that sits under «More ▾» cannot be measured, so when what it shows changes
   * its remembered width is dropped and it is measured again in the row: the
   * status group is ~100 px while the signatures are still loading ("2
   * signatures", before the slots say those are slot signatures) and ~40 px
   * after, and a stale 100 px would keep it under «More ▾» for good.
   */
  const contentKeys = useRef({ status: '', history: '', appearance: '' });
  const statusKey = `${properties.signatureCount > 0}|${String(properties.count).length}|${properties.signatureCount}`;
  const historyKey = `${String(properties.undoDepth).length}|${String(properties.redoDepth).length}`;
  // W4: a drawing's appearance group is its colour alone -- a different width.
  const appearanceKey = isDrawKind(properties.kind) ? 'draw' : 'text';

  useLayoutEffect(() => {
    const panel = panelRef.current;
    const main = mainRef.current;
    if (!inlineProps || !panel || !main) return;
    if (contentKeys.current.status !== statusKey) {
      contentKeys.current.status = statusKey;
      widths.current.full.delete('status');
      widths.current.compact.delete('status');
    }
    if (contentKeys.current.history !== historyKey) {
      contentKeys.current.history = historyKey;
      widths.current.full.delete('history');
      widths.current.compact.delete('history');
    }
    if (contentKeys.current.appearance !== appearanceKey) {
      contentKeys.current.appearance = appearanceKey;
      widths.current.full.delete('appearance');
      widths.current.compact.delete('appearance');
    }
    panel.querySelectorAll<HTMLElement>(':scope > [data-group]').forEach((element) => {
      (compact ? widths.current.compact : widths.current.full).set(
        element.dataset.group as PropertyGroupId,
        element.offsetWidth,
      );
    });
    const style = getComputedStyle(panel);
    const gap = parseFloat(style.columnGap) || 0;
    const padding = (parseFloat(style.paddingLeft) || 0) + (parseFloat(style.paddingRight) || 0);
    const border = (parseFloat(style.borderLeftWidth) || 0) + (parseFloat(style.borderRightWidth) || 0);
    // Docked: the row is the column. Floating: what is left between the panel's
    // own left edge and the column's right edge (it is not re-flowed while the
    // grip is being dragged, only where it was dropped).
    const floatingLeft = Math.max(EDGE_GAP_PX, placement.x);
    const available =
      mode === 'docked-top'
        ? panel.clientWidth - padding
        : Math.max(0, (stageRef.current?.clientWidth ?? 0) - floatingLeft - EDGE_GAP_PX) -
          padding -
          border;
    if (!compact) {
      mainFull.current = main.offsetWidth;
      let labels = 0;
      main.querySelectorAll<HTMLElement>('.editor__tool-label').forEach((label) => {
        const parentGap = parseFloat(getComputedStyle(label.parentElement as Element).columnGap) || 0;
        labels += label.offsetWidth + parentGap;
      });
      labelsWidth.current = labels;
    } else {
      mainCompact.current = main.offsetWidth;
    }
    const moreWidth = moreRef.current?.offsetWidth || MORE_FALLBACK_PX;
    const total = barGroups.length;
    const fitWith = (mainWidth: number, asCompact: boolean) => {
      const own = asCompact ? widths.current.compact : widths.current.full;
      const other = asCompact ? widths.current.full : widths.current.compact;
      const widthOf = (index: number) =>
        own.get(barGroups[index]) ?? other.get(barGroups[index]) ?? 0;
      // A group never drawn in the compact row (it went under «More ▾» while
      // the row was still full-width) has no compact width yet, and its full
      // width -- with the words -- would keep it under «More ▾» for good. So
      // draw every group once (still inside this layout pass, before paint):
      // the next pass measures them and decides for real. Converges: the
      // widths are cached after that one pass.
      if (asCompact && barGroups.some((id) => !own.has(id))) return total;
      let all = mainWidth;
      for (let index = 0; index < total; index += 1) all += gap + widthOf(index);
      if (all <= available + 0.5) return total;
      // Not all of them: the ones that fit next to «More ▾».
      let used = mainWidth + gap + moreWidth;
      let fit = 0;
      for (let index = 0; index < total; index += 1) {
        if (used + gap + widthOf(index) > available + 0.5) break;
        used += gap + widthOf(index);
        fit = index + 1;
      }
      return fit;
    };
    // Labels first, «More ▾» only when even the glyphs alone do not leave room.
    const full = fitWith(mainFull.current, false);
    const nextCompact = full < total;
    const fit = nextCompact
      ? fitWith(mainCompact.current ?? mainFull.current - labelsWidth.current, true)
      : full;
    setCompact((current) => (current === nextCompact ? current : nextCompact));
    setVisibleCount((current) => (current === fit ? current : fit));
  }, [
    compact,
    tools.length,
    inlineProps,
    mode,
    placement.x,
    stageSize.width,
    visibleCount,
    reasonText,
    statusKey,
    historyKey,
    appearanceKey,
    groupsKey,
    stageRef,
  ]);

  const shownGroups = inlineProps ? barGroups.slice(0, visibleCount) : [];
  const overflowGroups = inlineProps ? barGroups.slice(visibleCount) : [];
  useEffect(() => {
    if (overflowGroups.length === 0) setMoreOpen(false);
  }, [overflowGroups.length]);
  useEffect(() => {
    if (inlineProps) setPropsOpen(false);
  }, [inlineProps]);

  // A click anywhere else closes the menu / the pop-over.
  useEffect(() => {
    if (!moreOpen && !propsOpen) return undefined;
    const onDown = (event: MouseEvent) => {
      const target = event.target as Node;
      if (moreOpen && moreRef.current && !moreRef.current.contains(target)) setMoreOpen(false);
      if (propsOpen && propsRef.current && !propsRef.current.contains(target)) setPropsOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [moreOpen, propsOpen]);

  /* -- the tools: drop-downs (W4) and roving tabindex ------------------------- */

  /** The row: plain tools, and ONE item per drop-down, at its first member's place. */
  const rowItems = useMemo<RowItem[]>(() => {
    const items: RowItem[] = [];
    // F2: «Whiteout ▾ Redact» -- without Redact (no editor) Whiteout stays F1a's plain button.
    const members = new Map<ToolMenu, number>();
    tools.forEach((tool) => {
      if (tool.menu) members.set(tool.menu, (members.get(tool.menu) ?? 0) + 1);
    });
    tools.forEach((tool) => {
      if (!tool.menu || (tool.menu === 'whiteout' && members.get(tool.menu) === 1)) {
        items.push({ type: 'tool', tool });
        return;
      }
      const existing = items.find(
        (item): item is Extract<RowItem, { type: 'menu' }> => item.type === 'menu' && item.menu === tool.menu,
      );
      if (existing) existing.tools.push(tool);
      else items.push({ type: 'menu', menu: tool.menu, tools: [tool] });
    });
    return items;
  }, [tools, menuExtras]);

  /** The member each drop-down shows (and its main half arms): the one used last. */
  const [lastInMenu, setLastInMenu] = useState<Partial<Record<ToolMenu, ToolId>>>({});
  const armedTool = tools.find((tool) => tool.id === toolId);
  useEffect(() => {
    const menu = armedTool?.menu;
    if (menu) setLastInMenu((current) => (current[menu] === toolId ? current : { ...current, [menu]: toolId }));
  }, [armedTool, toolId]);
  const currentOf = (item: Extract<RowItem, { type: 'menu' }>): ToolDefinition =>
    item.tools.find((tool) => tool.id === lastInMenu[item.menu]) ?? item.tools[0];

  const [openMenu, setOpenMenu] = useState<ToolMenu | null>(null);
  useEffect(() => {
    if (collapsed) setOpenMenu(null);
  }, [collapsed]);
  // A click outside the open drop-down closes it.
  useEffect(() => {
    if (!openMenu) return undefined;
    const onDown = (event: MouseEvent) => {
      const root = menuRefs.current.get(openMenu);
      if (root && !root.contains(event.target as Node)) setOpenMenu(null);
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [openMenu]);

  /* -- F1a: the open list is placed inside the viewport -------------------------- */

  /** The lists themselves, by menu (measured to be placed). */
  const listRefs = useRef(new Map<ToolMenu, HTMLSpanElement>());
  const [menuPlace, setMenuPlace] = useState<MenuPlacement | null>(null);
  const verticalEdge: false | 'left' | 'right' = vertical ? (placement.edge === 'right' ? 'right' : 'left') : false;
  const placeOpenMenu = useCallback(() => {
    if (!openMenu) {
      setMenuPlace((current) => (current ? null : current));
      return;
    }
    const root = menuRefs.current.get(openMenu);
    const list = listRefs.current.get(openMenu);
    if (!root || !list) return;
    const main = root.firstElementChild as HTMLElement | null;
    // The whole split button (main + ▾) is the anchor; left-aligned with its main half.
    const anchor = root.getBoundingClientRect();
    const start = main?.getBoundingClientRect().left ?? anchor.left;
    // The ≤ 640 px bottom bar: the list stands on the BAR's top edge, never over it.
    const barTop = narrow ? (panelRef.current?.getBoundingClientRect().top ?? anchor.top) : anchor.top;
    const box = new DOMRect(start, barTop, anchor.right - start, anchor.bottom - barTop);
    const next = placeMenu(
      box,
      list.offsetWidth,
      list.scrollHeight,
      { width: document.documentElement.clientWidth, height: window.innerHeight },
      verticalEdge,
    );
    setMenuPlace((current) =>
      current &&
      current.menu === openMenu &&
      Math.abs(current.left - next.left) < 0.5 &&
      Math.abs(current.top - next.top) < 0.5 &&
      Math.abs(current.maxHeight - next.maxHeight) < 0.5 &&
      current.side === next.side
        ? current
        : { menu: openMenu, ...next },
    );
  }, [openMenu, verticalEdge, narrow]);
  // Before paint: the list never flashes at the wrong place.
  useLayoutEffect(() => {
    placeOpenMenu();
  }, [
    placeOpenMenu,
    placement.x,
    placement.y,
    placement.mode,
    dragPosition,
    stageSize.width,
    stageSize.height,
    narrow,
    compact,
    visibleCount,
  ]);
  // Scroll (any scroller: the page list, the window), resize, browser zoom and
  // pinch zoom move the button under a `position: fixed` list: place it again.
  useEffect(() => {
    if (!openMenu) return undefined;
    let frame = 0;
    const schedule = () => {
      if (frame) return;
      frame = window.requestAnimationFrame(() => {
        frame = 0;
        placeOpenMenu();
      });
    };
    window.addEventListener('resize', schedule);
    window.addEventListener('scroll', schedule, true);
    const visual = window.visualViewport;
    visual?.addEventListener('resize', schedule);
    visual?.addEventListener('scroll', schedule);
    return () => {
      if (frame) window.cancelAnimationFrame(frame);
      window.removeEventListener('resize', schedule);
      window.removeEventListener('scroll', schedule, true);
      visual?.removeEventListener('resize', schedule);
      visual?.removeEventListener('scroll', schedule);
    };
  }, [openMenu, placeOpenMenu]);

  /** The roving key a tool id lives under (a drop-down member: its main half). */
  const stopOf = useCallback(
    (id: ToolId): string => {
      const menu = tools.find((tool) => tool.id === id)?.menu;
      return menu && !collapsed ? menuKey(menu) : id;
    },
    [tools, collapsed],
  );

  const shownItems: RowItem[] = collapsed
    ? tools.filter((tool) => tool.id === toolId).map((tool) => ({ type: 'tool' as const, tool }))
    : rowItems;
  const enabledIds: string[] = [];
  shownItems.forEach((item) => {
    if (item.type === 'tool') {
      if (!disabledTools[item.tool.id]) enabledIds.push(item.tool.id);
      return;
    }
    // ONE stop per drop-down: its main half (Alt+↓ there opens the list; the
    // ▾ corner is for the mouse).
    if (!disabledTools[currentOf(item).id]) enabledIds.push(menuKey(item.menu));
  });
  const [rovingId, setRovingId] = useState<string>(() => stopOf(toolId));
  useEffect(() => setRovingId(stopOf(toolId)), [toolId, stopOf]);
  const tabStop = enabledIds.includes(rovingId) ? rovingId : (enabledIds[0] ?? stopOf(toolId));

  const focusTool = useCallback((id: string) => {
    setRovingId(id);
    toolRefs.current.get(id)?.focus();
  }, []);

  const pickFromMenu = (menu: ToolMenu, id: ToolId) => {
    // The focus goes back to the drop-down's button (the item is about to be
    // hidden) -- NOW, not after a timer: a timer could fire after the user's
    // next click on the page and take the focus from what that click opened
    // (a note's text box would lose it and close).
    focusTool(menuKey(menu));
    setOpenMenu(null);
    onTool(id);
  };

  /** Keys inside an open drop-down: ↑ ↓ Home End between its items, Esc closes. */
  const onMenuKeyDown = (menu: ToolMenu, members: ToolDefinition[]) => (event: ReactKeyboardEvent<HTMLElement>) => {
    const enabled = members.filter((tool) => !disabledTools[tool.id]).map((tool) => tool.id);
    const at = enabled.findIndex((id) => itemRefs.current.get(id) === document.activeElement);
    let next: ToolId | null = null;
    switch (event.key) {
      case 'ArrowDown':
      case 'ArrowRight':
        next = enabled[(at + 1) % enabled.length] ?? null;
        break;
      case 'ArrowUp':
      case 'ArrowLeft':
        next = enabled[(at - 1 + enabled.length) % enabled.length] ?? null;
        break;
      case 'Home':
        next = enabled[0] ?? null;
        break;
      case 'End':
        next = enabled[enabled.length - 1] ?? null;
        break;
      case 'Escape':
        event.preventDefault();
        event.stopPropagation();
        setOpenMenu(null);
        focusTool(menuKey(menu));
        return;
      case 'Tab':
        setOpenMenu(null);
        return;
      default:
        return;
    }
    // Neither the row's roving nor the window's nudge may hear these.
    event.preventDefault();
    event.stopPropagation();
    if (next) itemRefs.current.get(next)?.focus();
  };

  const openDropDown = (menu: ToolMenu, focusFirst: boolean) => {
    setOpenMenu(menu);
    if (!focusFirst) return;
    window.setTimeout(() => {
      const item = rowItems.find(
        (entry): entry is Extract<RowItem, { type: 'menu' }> => entry.type === 'menu' && entry.menu === menu,
      );
      const target = item ? (item.tools.find((tool) => tool.id === toolId) ?? currentOf(item)) : null;
      if (target) itemRefs.current.get(target.id)?.focus();
    }, 0);
  };

  const onToolsKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (enabledIds.length === 0) return;
    const index = Math.max(0, enabledIds.indexOf(tabStop));
    let next: string | null = null;
    switch (event.key) {
      case 'ArrowRight':
      case 'ArrowDown':
        next = enabledIds[(index + 1) % enabledIds.length];
        break;
      case 'ArrowLeft':
      case 'ArrowUp':
        next = enabledIds[(index - 1 + enabledIds.length) % enabledIds.length];
        break;
      case 'Home':
        next = enabledIds[0];
        break;
      case 'End':
        next = enabledIds[enabledIds.length - 1];
        break;
      default:
        return;
    }
    // Never let the arrows reach the window: there they would nudge a
    // selected annotation.
    event.preventDefault();
    event.stopPropagation();
    if (next) focusTool(next);
  };

  /** Esc anywhere inside the panel: close what is open, give the focus back to the page. */
  const onPanelKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (event.key !== 'Escape') return;
    event.preventDefault();
    event.stopPropagation();
    setMoreOpen(false);
    setPropsOpen(false);
    setOpenMenu(null);
    const stage = document.querySelector<HTMLElement>('.pdf-stage');
    if (stage) {
      if (!stage.hasAttribute('tabindex')) stage.setAttribute('tabindex', '-1');
      stage.focus({ preventScroll: true });
    } else {
      (document.activeElement as HTMLElement | null)?.blur?.();
    }
  };

  /* -- window keys: Alt+T and the one-letter shortcuts ------------------------ */

  const latest = useRef({ toolId, onTool, disabledTools, tabStop, focusTool, tools });
  latest.current = { toolId, onTool, disabledTools, tabStop, focusTool, tools };
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.defaultPrevented) return;
      const current = latest.current;
      if (event.altKey && !event.ctrlKey && !event.metaKey && event.code === 'KeyT') {
        event.preventDefault();
        current.focusTool(current.tabStop);
        return;
      }
      if (event.ctrlKey || event.metaKey || event.altKey) return;
      const target = event.target as HTMLElement | null;
      if (target?.closest?.('input, textarea, select, [contenteditable="true"]')) return;
      if (document.querySelector('.modal')) return;
      // F2: only the tools on THIS toolbar (the server decides which exist).
      const tool = toolForShortcut(event.key, current.tools);
      if (!tool || current.disabledTools[tool.id]) return;
      event.preventDefault();
      current.onTool(tool.id);
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, []);

  /* -- where it is drawn --------------------------------------------------------- */

  let style: CSSProperties | undefined;
  if (!narrow) {
    if (dragPosition) style = { left: dragPosition.x, top: dragPosition.y };
    else if (placement.mode === 'floating') {
      style =
        vertical && placement.edge === 'right'
          ? { right: EDGE_GAP_PX, top: placement.y }
          : { left: placement.x, top: placement.y };
    }
  }

  const classes = [
    'editor__toolbar',
    'ws-toolbar',
    `ws-toolbar--${mode}`,
    vertical ? 'ws-toolbar--vertical' : 'ws-toolbar--horizontal',
    collapsed ? 'ws-toolbar--collapsed' : '',
    compact && inlineProps ? 'ws-toolbar--compact' : '',
    dragPosition ? 'is-dragging' : '',
  ]
    .filter(Boolean)
    .join(' ');

  return (
    <div
      ref={panelRef}
      className={classes}
      style={style}
      data-testid="editor-toolbar"
      data-mode={mode}
      data-orientation={vertical ? 'vertical' : 'horizontal'}
      data-edge={vertical ? (placement.edge ?? 'left') : undefined}
      data-collapsed={collapsed ? 'true' : 'false'}
      data-overflow={overflowGroups.length}
      role="toolbar"
      aria-label="Editor tools"
      aria-orientation={vertical ? 'vertical' : 'horizontal'}
      onKeyDown={onPanelKeyDown}
    >
      <div className="ws-toolbar__main" ref={mainRef}>
        <button
          type="button"
          className="ws-toolbar__grip"
          data-testid="toolbar-grip"
          aria-label={
            narrow
              ? 'Toolbar grip — the toolbar stays at the bottom on a small screen'
              : 'Move the toolbar: drag, or arrow keys (Shift: faster); Enter docks or floats it'
          }
          title={narrow ? 'Pinned to the bottom on a small screen' : 'Drag to move the toolbar'}
          disabled={narrow}
          {...floating.gripBindings}
        >
          <span aria-hidden="true">⠿</span>
        </button>

        <div
          className="editor__group ws-toolbar__tools"
          role="group"
          aria-label="Tools"
          onKeyDown={onToolsKeyDown}
        >
          {shownItems.map((row) => {
            if (row.type === 'tool') {
              const item = row.tool;
              const active = item.id === toolId;
              const reason = disabledTools[item.id];
              const hint = reason ?? item.hint;
              return (
                <button
                  key={item.id}
                  ref={(element) => {
                    if (element) toolRefs.current.set(item.id, element);
                    else toolRefs.current.delete(item.id);
                  }}
                  type="button"
                  className={`editor__tool${active ? ' is-active' : ''}`}
                  data-testid={`tool-${item.id}`}
                  data-menu={item.menu}
                  aria-pressed={active}
                  aria-label={`${item.label} — ${hint}`}
                  aria-keyshortcuts={item.shortcut}
                  title={item.shortcut ? `${hint} (${item.shortcut})` : hint}
                  disabled={Boolean(reason)}
                  data-disabled-reason={reason}
                  tabIndex={item.id === tabStop ? 0 : -1}
                  onFocus={() => setRovingId(item.id)}
                  onClick={() => onTool(item.id)}
                >
                  <span className="editor__tool-glyph" aria-hidden="true">
                    <ToolIcon id={item.id} glyph={item.glyph} />
                  </span>
                  <span className="editor__tool-label">{item.label}</span>
                </button>
              );
            }
            // A drop-down (W4): main half = the member used last, ▾ = the list.
            const { menu, tools: members } = row;
            const current = currentOf(row);
            const meta = TOOL_MENUS[menu];
            const armed = members.some((tool) => tool.id === toolId);
            const currentReason = disabledTools[current.id];
            const open = openMenu === menu;
            return (
              <span
                key={`menu-${menu}`}
                className={`ws-toolgroup${open ? ' is-open' : ''}${armed ? ' is-armed' : ''}`}
                data-menu={menu}
                ref={(element) => {
                  if (element) menuRefs.current.set(menu, element);
                  else menuRefs.current.delete(menu);
                }}
              >
                <button
                  ref={(element) => {
                    if (element) toolRefs.current.set(menuKey(menu), element);
                    else toolRefs.current.delete(menuKey(menu));
                  }}
                  type="button"
                  className={`editor__tool ws-toolgroup__main${armed ? ' is-active' : ''}`}
                  data-testid={`toolgroup-${menu}`}
                  data-tool={current.id}
                  aria-pressed={armed}
                  aria-label={`${meta.label}: ${current.label} — ${currentReason ?? current.hint}. Alt+Down lists ${members
                    .filter((tool) => tool.id !== current.id)
                    .map((tool) => tool.label)
                    .join(', ')}`}
                  aria-keyshortcuts="Alt+ArrowDown"
                  title={`${meta.label}: ${currentReason ?? current.hint}${current.shortcut ? ` (${current.shortcut})` : ''} — ▾ or Alt+↓ for the others`}
                  disabled={Boolean(currentReason)}
                  tabIndex={menuKey(menu) === tabStop ? 0 : -1}
                  onFocus={() => setRovingId(menuKey(menu))}
                  onClick={() => onTool(current.id)}
                  onKeyDown={(event) => {
                    if ((event.altKey && event.key === 'ArrowDown') || event.key === 'F4') {
                      event.preventDefault();
                      event.stopPropagation();
                      openDropDown(menu, true);
                    }
                  }}
                >
                  <span className="editor__tool-glyph" aria-hidden="true">
                    <ToolIcon id={current.id} glyph={current.glyph} />
                  </span>
                  <span className="editor__tool-label">{meta.label}</span>
                </button>
                <button
                  ref={(element) => {
                    if (element) toolRefs.current.set(toggleKey(menu), element);
                    else toolRefs.current.delete(toggleKey(menu));
                  }}
                  type="button"
                  className="ws-toolgroup__toggle"
                  data-testid={`toolgroup-${menu}-toggle`}
                  aria-haspopup="menu"
                  aria-expanded={open}
                  aria-controls={`ws-toolmenu-${menu}`}
                  aria-label={`${meta.label} tools: ${members.map((tool) => tool.label).join(', ')}`}
                  title={`${meta.label} — ${meta.hint}`}
                  // Not a stop of its own: the main half is (Alt+↓ opens the list from there).
                  tabIndex={-1}
                  onClick={() => (open ? setOpenMenu(null) : openDropDown(menu, false))}
                  onKeyDown={(event) => {
                    if (event.key === 'ArrowDown' && !open) {
                      event.preventDefault();
                      event.stopPropagation();
                      openDropDown(menu, true);
                    } else if ((event.key === 'Enter' || event.key === ' ') && !open) {
                      event.preventDefault();
                      event.stopPropagation();
                      openDropDown(menu, true);
                    }
                  }}
                >
                  <Icon name="caret" size={14} />
                </button>
                <span
                  id={`ws-toolmenu-${menu}`}
                  ref={(element) => {
                    if (element) listRefs.current.set(menu, element);
                    else listRefs.current.delete(menu);
                  }}
                  className="ws-toolgroup__menu"
                  role="menu"
                  aria-label={meta.label}
                  data-testid={`toolgroup-${menu}-menu`}
                  data-placed={open && menuPlace?.menu === menu ? menuPlace.side : undefined}
                  style={
                    open && menuPlace?.menu === menu
                      ? { left: menuPlace.left, top: menuPlace.top, maxHeight: menuPlace.maxHeight }
                      : undefined
                  }
                  hidden={!open}
                  onKeyDown={onMenuKeyDown(menu, members)}
                >
                  {members.map((item, index) => {
                    const active = item.id === toolId;
                    const reason = disabledTools[item.id];
                    const hint = reason ?? item.hint;
                    const section =
                      item.menuSection && item.menuSection !== members[index - 1]?.menuSection
                        ? item.menuSection
                        : null;
                    return (
                      <Fragment key={item.id}>
                      {section && (
                        <span className="ws-toolmenu__section" role="presentation" aria-hidden="true">
                          {section}
                        </span>
                      )}
                      <button
                        ref={(element) => {
                          if (element) itemRefs.current.set(item.id, element);
                          else itemRefs.current.delete(item.id);
                        }}
                        type="button"
                        role="menuitemradio"
                        aria-checked={active}
                        className={`ws-toolmenu__item${active ? ' is-active' : ''}`}
                        data-testid={`tool-${item.id}`}
                        data-menu={menu}
                        aria-pressed={active}
                        aria-label={`${item.label} — ${hint}`}
                        aria-keyshortcuts={item.shortcut}
                        title={item.shortcut ? `${hint} (${item.shortcut})` : hint}
                        disabled={Boolean(reason)}
                        data-disabled-reason={reason}
                        tabIndex={-1}
                        onClick={() => pickFromMenu(menu, item.id)}
                      >
                        <span className="editor__tool-glyph" aria-hidden="true">
                          <ToolIcon id={item.id} glyph={item.glyph} />
                        </span>
                        <span className="ws-toolmenu__label">{item.label}</span>
                        {item.shortcut && (
                          <kbd className="ws-toolmenu__key" aria-hidden="true">
                            {item.shortcut}
                          </kbd>
                        )}
                      </button>
                      </Fragment>
                    );
                  })}
                  {menuExtras?.[menu] && (
                    <span className="ws-toolmenu__extras" data-testid={`menu-extras-${menu}`}>
                      {menuExtras[menu]!(() => {
                        setOpenMenu(null);
                        focusTool(menuKey(menu));
                      })}
                    </span>
                  )}
                </span>
              </span>
            );
          })}
        </div>

        {/* F1a: «… | Shapes ▼ | Undo | Redo» -- in a wide row they follow the
            tools and never go under «More ▾»; elsewhere they are the pop-over's
            `history` group, as before. */}
        {inlineProps && (
          <div
            className="editor__group ws-props__group ws-props__group--history ws-toolbar__history"
            role="group"
            aria-label="History"
            data-history="row"
          >
            <HistoryControls {...properties} />
          </div>
        )}

        {reasons.length > 0 && (
          <span
            className="ws-toolbar__reason"
            role="status"
            data-testid="tool-disabled-reason"
            title={reasonText}
          >
            <span className="ws-toolbar__reason-icon" aria-hidden="true">
              ⚠
            </span>
            <span className="ws-toolbar__reason-text">{reasonText}</span>
          </span>
        )}

        <button
          type="button"
          className="ws-toolbar__collapse"
          data-testid="toolbar-collapse"
          aria-expanded={!collapsed}
          aria-label={collapsed ? 'Expand the toolbar' : 'Collapse the toolbar'}
          title={collapsed ? 'Expand the toolbar' : 'Collapse the toolbar'}
          onClick={() => floating.setCollapsed(!collapsed)}
        >
          <span aria-hidden="true">{collapsed ? (vertical ? '▾' : '▸') : vertical ? '▴' : '◂'}</span>
        </button>

        {!inlineProps && !collapsed && (
          <span className="ws-toolbar__props" ref={propsRef}>
            <button
              type="button"
              className="editor__button ws-toolbar__props-button"
              data-testid="toolbar-properties"
              aria-haspopup="true"
              aria-expanded={propsOpen}
              aria-label="Properties: font, size, date, colour, undo"
              title="Properties"
              onClick={() => setPropsOpen((value) => !value)}
            >
              <span aria-hidden="true">⚙</span>
            </button>
            {propsOpen && (
              <div className="ws-toolbar__popover" data-testid="toolbar-properties-popover">
                <PropertiesBar groups={groups} variant="stack" {...properties} />
              </div>
            )}
          </span>
        )}
      </div>

      {shownGroups.length > 0 && (
        <PropertiesBar groups={shownGroups} {...properties} />
      )}

      {overflowGroups.length > 0 && (
        <span className="ws-toolbar__more" ref={moreRef}>
          <button
            type="button"
            className="editor__button"
            data-testid="toolbar-more"
            aria-haspopup="true"
            aria-expanded={moreOpen}
            aria-label={`More tools: ${overflowGroups.join(', ')}`}
            onClick={() => setMoreOpen((value) => !value)}
          >
            More ▾
          </button>
          {moreOpen && (
            <div className="ws-toolbar__popover ws-toolbar__popover--end" data-testid="toolbar-more-menu">
              <PropertiesBar groups={overflowGroups} variant="stack" {...properties} />
            </div>
          )}
        </span>
      )}
    </div>
  );
}
