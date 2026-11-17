/*
 * Sejda-style saving. The editor's text edits become the server's text
 * operations (`text.replace` and `text.delete`). Only the lines that were edited
 * are rewritten; the rest of the page (other text, fonts, drawings) stays as it
 * was in the file.
 *
 * Pure functions: no DOM, no network. Exposed as `window.TextOps`.
 *
 * pages: [{ pageIndex, pageUid, widthPt, lines }]
 *   `lines` is every text line of the page's current version (the server's
 *   text model, flattened over its blocks): { id, blockId, text, bbox, spans,
 *   editable, reason }. `bbox` is { x_pt, y_pt, w_pt, h_pt }.
 * edits: [{ pageIndex, ptX, ptY, ptW, ptH, origText, newText, blank }]
 *   The box is where the edited thing sits, in points (top-left origin,
 *   unrotated, relative to the crop box). `origText` is what the page showed
 *   ('' for a blank). `blank` is true for a blank field (underscores) that was
 *   filled or erased.
 */
(function () {
  'use strict';

  // A point this far outside a line's box still belongs to that line.
  const TOLERANCE_PT = 3;
  // A replaced line may grow to this far from the page's right edge.
  const RIGHT_MARGIN_PT = 20;
  const MAX_RUNS = 200;
  const HEX = /^#[0-9A-Fa-f]{6}$/;

  function clamp(value, low, high) {
    return Math.min(high, Math.max(low, value));
  }

  function escapeRegExp(text) {
    return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  }

  // The words of a text in any spacing: pdf.js and the server space words differently.
  function looseTextPattern(text) {
    const words = text.trim().split(/\s+/).map(escapeRegExp);
    return new RegExp(words.join('\\s*'), 'g');
  }

  // Every place `pattern` occurs in `text`, as {index, length} (UTF-16 units).
  function occurrences(text, pattern) {
    const found = [];
    const re = new RegExp(pattern.source, 'g');
    let match = re.exec(text);
    while (match !== null) {
      if (match[0].length === 0) re.lastIndex += 1;
      else found.push({ index: match.index, length: match[0].length });
      match = re.exec(text);
    }
    return found;
  }

  // The style a span's text is written in.
  function styleOf(span) {
    const size = Number(span.size_pt);
    return {
      font: span.substitute_family || span.family || 'LiberationSans',
      size_pt: clamp(Number.isFinite(size) && size > 0 ? size : 11, 4, 144),
      color: HEX.test(span.color || '') ? span.color : '#000000',
      bold: Boolean(span.bold),
      italic: Boolean(span.italic),
    };
  }

  // One unit per UTF-16 unit of the line's text: { c, box, style }. The spans
  // give each character its box and style. Without an exact match every unit
  // takes the line's first style and no box.
  function unitsOf(line) {
    const spans = line.spans || [];
    const styles = spans.map(styleOf);
    const units = [];
    let joined = '';
    spans.forEach((span, i) => {
      for (const entry of span.chars || []) {
        const c = String(entry[0]);
        const coords = entry.slice(1, 5);
        const box = coords.length === 4 && coords.every((value) => Number.isFinite(value))
          ? { x0: coords[0], y0: coords[1], x1: coords[2], y1: coords[3] }
          : null;
        joined += c;
        for (let k = 0; k < c.length; k += 1) units.push({ c: c[k], box, style: styles[i] });
      }
    });
    if (units.length > 0 && joined === line.text) return units;
    const fallback = styles[0] || styleOf({});
    const plain = [];
    for (let k = 0; k < line.text.length; k += 1) {
      plain.push({ c: line.text[k], box: null, style: fallback });
    }
    return plain;
  }

  const textOf = (units) => units.map((unit) => unit.c).join('');

  // The line whose box holds the point, nearest to its middle; null when none does.
  function lineAt(lines, x, y) {
    let best = null;
    let bestGap = Infinity;
    for (const line of lines) {
      const b = line.bbox;
      const inside = x >= b.x_pt - TOLERANCE_PT && x <= b.x_pt + b.w_pt + TOLERANCE_PT
        && y >= b.y_pt - TOLERANCE_PT && y <= b.y_pt + b.h_pt + TOLERANCE_PT;
      if (!inside) continue;
      const gap = Math.abs(y - (b.y_pt + b.h_pt / 2));
      if (gap < bestGap) {
        best = line;
        bestGap = gap;
      }
    }
    return best;
  }

  // Where in the line's text a change belongs: the occurrence nearest to the edit.
  function locate(units, change, line) {
    const text = textOf(units);
    const pattern = change.blank ? /_+/g : looseTextPattern(change.origText);
    const found = occurrences(text, pattern);
    if (found.length === 0) return null;
    const boxed = units.length > 0 && units.every((unit) => unit.box);
    let best = null;
    let bestDistance = Infinity;
    for (const item of found) {
      let distance;
      if (boxed) {
        const part = units.slice(item.index, item.index + item.length);
        const left = Math.min(...part.map((unit) => unit.box.x0));
        const right = Math.max(...part.map((unit) => unit.box.x1));
        distance = Math.abs((left + right) / 2 - change.centerX);
      } else {
        const width = Math.max(line.bbox.w_pt, 1);
        const guess = clamp((change.centerX - line.bbox.x_pt) / width, 0, 1) * text.length;
        distance = Math.abs(item.index - guess);
      }
      if (distance < bestDistance) {
        best = item;
        bestDistance = distance;
      }
    }
    return best;
  }

  // One run per stretch of equal style, in the shape the server takes.
  function runsOf(units) {
    const runs = [];
    for (const unit of units) {
      const last = runs[runs.length - 1];
      if (last && last.style === unit.style) last.text += unit.c;
      else runs.push({ text: unit.c, style: unit.style });
    }
    const merged = runs.length <= MAX_RUNS ? runs : [{ text: textOf(units), style: units[0].style }];
    return merged.map((run) => ({ text: run.text, ...run.style }));
  }

  function rectOf(b) {
    return { x_pt: b.x_pt, y_pt: b.y_pt, w_pt: b.w_pt, h_pt: b.h_pt };
  }

  // The box the new text is set in: the line's own top-left (a moved top-left
  // would be read as a move of the whole block), with room to grow to the right.
  function boxFor(line, page) {
    const b = line.bbox;
    return {
      x_pt: b.x_pt,
      y_pt: b.y_pt,
      w_pt: Math.max(b.w_pt, page.widthPt - RIGHT_MARGIN_PT - b.x_pt),
      h_pt: b.h_pt,
    };
  }

  // The same box at the place the line was dragged to, kept on the page.
  function movedBox(line, page, move) {
    const b = line.bbox;
    const x = Math.max(0, Math.min(page.widthPt - 10, b.x_pt + move.dxPt));
    const y = Math.max(0, b.y_pt + move.dyPt);
    return {
      x_pt: x,
      y_pt: y,
      w_pt: Math.max(b.w_pt, page.widthPt - RIGHT_MARGIN_PT - x),
      h_pt: b.h_pt,
    };
  }

  function problem(code, change, page) {
    return {
      code,
      page: page ? page.pageIndex + 1 : change.pageIndex + 1,
      text: (change.origText || change.newText || '').slice(0, 60),
    };
  }

  /**
   * The text operations for the edits. Returns:
   *   ops      the operations, in the order the server applies them
   *   labels   for each op: { id, page, text } (to name the op in an error)
   *   problems the edits that cannot be written; nothing is saved while any exist
   */
  function buildTextOps({ pages, edits }) {
    const problems = [];
    const groups = new Map();
    for (const change of edits) {
      const page = pages.find((item) => item.pageIndex === change.pageIndex) || null;
      const centerX = change.ptX + change.ptW / 2;
      const centerY = change.ptY + change.ptH / 2;
      const line = page ? lineAt(page.lines, centerX, centerY) : null;
      if (!line) {
        problems.push(problem(change.blank ? 'blank_add' : 'not_found', change, page));
        continue;
      }
      if (line.editable === false) {
        problems.push({ ...problem('not_editable', change, page), detail: line.reason || '' });
        continue;
      }
      if (!(line.bbox.w_pt > 0 && line.bbox.h_pt > 0)) {
        problems.push(problem('not_found', change, page));
        continue;
      }
      if (!groups.has(line.id)) groups.set(line.id, { page, line, changes: [] });
      groups.get(line.id).changes.push({ ...change, centerX });
    }

    const ops = [];
    const labels = [];
    for (const { page, line, changes } of groups.values()) {
      const units = unitsOf(line);
      const failed = [];
      // Right to left, so a change never shifts the text of the changes still to come.
      for (const change of [...changes].sort((a, b) => b.centerX - a.centerX)) {
        const hit = locate(units, change, line);
        if (!hit) {
          failed.push(problem(change.blank ? 'blank_add' : 'text_not_found', change, page));
          continue;
        }
        const style = units[hit.index].style;
        const replacement = [];
        for (let k = 0; k < change.newText.length; k += 1) {
          replacement.push({ c: change.newText[k], box: null, style });
        }
        units.splice(hit.index, hit.length, ...replacement);
      }
      if (failed.length > 0) {
        problems.push(...failed);
        continue;
      }
      const text = textOf(units);
      const move = changes.find((change) => change.dxPt || change.dyPt) || null;
      if (text === line.text && !move) continue;
      // The file knows this text as ONE line: a part of it cannot be moved away from the rest.
      if (move && text.trim() !== '' && move.ptW < line.bbox.w_pt * 0.8) {
        problems.push(problem('partial_move', move, page));
        continue;
      }

      const id = `op${ops.length + 1}`;
      const target = {
        block_id: line.blockId,
        line_ids: [line.id],
        expect_text: line.text,
        bbox: rectOf(line.bbox),
      };
      if (text.trim() === '') {
        ops.push({ id, kind: 'text.delete', page_uid: page.pageUid, target });
      } else {
        ops.push({
          id,
          kind: 'text.replace',
          page_uid: page.pageUid,
          target,
          runs: runsOf(units),
          box: move ? movedBox(line, page, move) : boxFor(line, page),
          // A moved line keeps its size even when it now reaches past the margin.
          fit: move ? 'overflow' : 'shrink',
        });
      }
      labels.push({ id, page: page.pageIndex + 1, text: line.text.slice(0, 60) });
    }
    return { ops, labels, problems };
  }

  // A problem, in words for the person editing (Uzbek, as the rest of the editor).
  function describeProblem(item) {
    const where = `Page ${item.page}`;
    const shown = item.text ? ` ("${item.text}")` : '';
    switch (item.code) {
      case 'blank_add':
        return `${where}: new text in an empty place cannot be saved this way${shown}.`;
      case 'partial_move':
        return `${where}: part of a line cannot be moved on its own — move the whole line${shown}.`;
      case 'text_not_found':
        return `${where}: the text has changed — reload the page and edit it again${shown}.`;
      case 'not_editable':
        return `${where}: this text cannot be edited${item.detail ? ` (${item.detail})` : ''}${shown}.`;
      default:
        return `${where}: the place of the edited text was not found${shown}.`;
    }
  }

  window.TextOps = { buildTextOps, describeProblem };
})();
