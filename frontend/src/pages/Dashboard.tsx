import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { ApiError } from '../api/client';
import { contractsApi, describeContractDeleteError } from '../api/contracts';
import { useAuth } from '../hooks/useAuth';
import { formatServerDate, formatServerDateTime, serverTimeMs } from '../lib/time';
import type { ContractListItem } from '../types/contract';
import '../styles/dashboard.css';

/* ==========================================================================
   Dashboard -- "where is every contract right now?"
   --------------------------------------------------------------------------
   Everything on this page is derived from ONE request: `GET /api/contracts`
   (`ContractListOut[]`). No per-row call is made -- a `compare` per contract
   would turn one page load into N requests -- so the only facts available here
   are the list columns:

     id, template_id, template_name, contractor_name, contractor_email, status,
     review_status, error_message, reviewed_at, review_notes, created_at,
     updated_at, critical_missing_count, critical_inconsistency_count,
     critical_fmcsa_count, can_sign, review_override,
     stored_signature_slot_count

   The last six are additive on the server, so this page is written to run
   against BOTH shapes: an older backend omits them, a contract with no stored
   comparison sends `null`, and in either case the marker simply does not
   appear. `null` is never read as "zero problems" -- see `countOf()`.

   Two numbers are deliberately NOT shown:
     * `stored_signature_slot_count` -- the unfiltered slot count, which the
       signing screen filters down by the uploaded PDF's real page count. A
       "3 signature slots" here that turns into 2 on the signing screen is
       worse than no number at all, and the only honest alternative would be
       an "approximate" caveat on every row for something a reviewer cannot
       act on from this page.
     * the per-category field ids / labels -- the server does not send them
       (counts only), and this page must not go fetch them.
   ========================================================================== */

/** One bucket per contract; the rules below are ordered and mutually exclusive,
 *  so the six counts always add up to the number of contracts. */
type GroupKey = 'review' | 'signing' | 'completed' | 'processing' | 'rejected' | 'failed';
type FilterKey = 'all' | GroupKey;
type SortKey = 'newest' | 'oldest' | 'name' | 'status' | 'trouble';

interface GroupMeta {
  key: GroupKey;
  /** Chip / badge text. */
  label: string;
  /** Badge tone class suffix (`badge--<tone>`). */
  tone: 'good' | 'warn' | 'bad' | 'info' | 'neutral';
}

/** Chip order = the order a reviewer works through the day. */
const GROUPS: readonly GroupMeta[] = [
  { key: 'review', label: 'Waiting for review', tone: 'warn' },
  { key: 'signing', label: 'Waiting for signature', tone: 'info' },
  { key: 'completed', label: 'Completed', tone: 'good' },
  { key: 'processing', label: 'Processing', tone: 'neutral' },
  { key: 'rejected', label: 'Rejected', tone: 'bad' },
  { key: 'failed', label: 'Failed', tone: 'bad' },
] as const;

const GROUP_BY_KEY = new Map(GROUPS.map((group) => [group.key, group]));

const FILTER_KEYS: readonly FilterKey[] = ['all', ...GROUPS.map((g) => g.key)] as const;
const SORT_KEYS: readonly SortKey[] = ['newest', 'oldest', 'name', 'status', 'trouble'] as const;
const TOGGLE_KEYS = ['0', '1'] as const;

/** `status` is a closed union of seven values and every one lands in a bucket. */
function groupOf(contract: ContractListItem): GroupKey {
  if (contract.status === 'completed') return 'completed';
  if (contract.status === 'failed') return 'failed';
  if (contract.review_status === 'rejected') return 'rejected';
  // `approved` before `analyzed`: an approved contract is waiting for a pen,
  // not for a second reviewer. Same condition the Sign action uses below.
  if (contract.status === 'signing' || contract.review_status === 'approved') return 'signing';
  if (contract.status === 'analyzed' || contract.status === 'reviewing') return 'review';
  return 'processing';
}

/** Human wording for the raw `status`; the raw value stays in `data-status`. */
const STATUS_LABEL: Record<string, string> = {
  uploaded: 'Uploaded',
  parsing: 'Parsing',
  analyzed: 'Analyzed',
  reviewing: 'In review',
  signing: 'Ready to sign',
  completed: 'Completed',
  failed: 'Failed',
};

const REVIEW_TONE: Record<string, 'good' | 'warn' | 'bad' | 'neutral'> = {
  approved: 'good',
  pending: 'neutral',
  rejected: 'bad',
};

/** A contract that has not moved for this long while still "processing" is
 *  almost certainly stuck on a worker, so it is worth a visible marker. */
const STALE_PROCESSING_MS = 60 * 60 * 1000;

/** Stable per-marker id: the React key, the `data-flag` hook and the reason a
 *  marker can only ever be added once per row. */
type FlagKey =
  | 'error'
  | 'fmcsa'
  | 'cannot-sign'
  | 'mismatch'
  | 'empty'
  | 'rejected'
  | 'note'
  | 'override'
  | 'stale';

interface Flag {
  key: FlagKey;
  tone: 'bad' | 'warn';
  text: string;
  /** Long form for the tooltip when the text is clipped, and the place where a
   *  derived count explains what it means and whether it can be waived. */
  title?: string;
  /** Extra modifier class (`dash__flag--<mark>`) for a marker that must not
   *  read as "one more warning among five". */
  mark?: 'fmcsa' | 'override';
  /** `false` = a fact about the contract, not something still to fix, so it
   *  does not put the row into "needs attention". Only the override marker. */
  attention?: boolean;
}

/** A contract plus everything the list/sort/search needs, computed once. */
interface Row {
  contract: ContractListItem;
  group: GroupKey;
  /** Lower-cased searchable text, precomputed so typing stays O(n) on strings. */
  haystack: string;
  created: number;
  nameKey: string;
  flags: Flag[];
  /** At least one marker that still has to be dealt with. */
  hasTrouble: boolean;
  /** Ordering weight for "most problems first"; see `troubleScore()`. */
  trouble: number;
}

const STORE_FILTER = 'swm.dash.filter';
const STORE_SORT = 'swm.dash.sort';
const STORE_TROUBLE = 'swm.dash.trouble';

function readStored<T extends string>(key: string, allowed: readonly T[], fallback: T): T {
  try {
    const raw = window.localStorage.getItem(key);
    return (allowed as readonly string[]).includes(raw ?? '') ? (raw as T) : fallback;
  } catch {
    // Private mode / disabled storage: the page still works, it just forgets.
    return fallback;
  }
}

function writeStored(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    /* ignore */
  }
}

function shortId(value: string): string {
  return value.length > 8 ? value.slice(0, 8) : value;
}

function hoursSince(iso: string, now: number): number {
  const then = serverTimeMs(iso);
  if (!Number.isFinite(then)) return 0;
  return Math.max(0, Math.floor((now - then) / 3_600_000));
}

const plural = (count: number, one: string, many: string) => (count === 1 ? one : many);

/**
 * A derived count, or `null` for "not known".
 *
 * Three different payloads land here and only one of them is a number:
 * the key is absent (a backend from before `ContractListOut`), the key is
 * `null` (no stored comparison yet), or it is a count. Anything that is not a
 * finite, non-negative number is treated as "not known" rather than as `0`,
 * because a marker that silently means "we did not look" would be a lie.
 */
function countOf(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0
    ? Math.trunc(value)
    : null;
}

interface CriticalCounts {
  missing: number | null;
  inconsistency: number | null;
  fmcsa: number | null;
}

function criticalCounts(contract: ContractListItem): CriticalCounts {
  return {
    missing: countOf(contract.critical_missing_count),
    inconsistency: countOf(contract.critical_inconsistency_count),
    fmcsa: countOf(contract.critical_fmcsa_count),
  };
}

/**
 * Weight for "most problems first". Each bucket is worth more than every
 * bucket below it can add up to in practice (a contract does not carry a
 * hundred critical inconsistencies), so one FMCSA violation always outranks a
 * page full of empty fields, and the count inside a bucket only orders rows
 * that are otherwise equally bad. Rows with nothing wrong score 0 and land at
 * the bottom, newest first.
 */
function troubleScore(contract: ContractListItem, counts: CriticalCounts): number {
  let score = 0;
  if (contract.error_message || contract.status === 'failed') score += 1_000_000;
  score += (counts.fmcsa ?? 0) * 10_000;
  if (contract.review_status === 'rejected') score += 5_000;
  score += (counts.inconsistency ?? 0) * 100;
  score += counts.missing ?? 0;
  return score;
}

function buildFlags(
  contract: ContractListItem,
  group: GroupKey,
  counts: CriticalCounts,
  now: number,
): Flag[] {
  const flags: Flag[] = [];
  if (contract.error_message) {
    flags.push({
      key: 'error',
      tone: 'bad',
      text: contract.error_message,
      title: contract.error_message,
    });
  } else if (contract.status === 'failed') {
    flags.push({ key: 'error', tone: 'bad', text: 'Processing failed' });
  }

  // --- what the comparison engine found (counts only; absent / null = silent)
  const fmcsa = counts.fmcsa ?? 0;
  if (fmcsa > 0) {
    flags.push({
      key: 'fmcsa',
      tone: 'bad',
      mark: 'fmcsa',
      text: fmcsa === 1 ? 'FMCSA' : `FMCSA Ã—${fmcsa}`,
      title:
        `${fmcsa} critical FMCSA ${plural(fmcsa, 'violation', 'violations')}. ` +
        'This is the one thing a reviewer cannot wave through: an override covers ' +
        'empty fields and contradicting fields, never an FMCSA violation. The ' +
        'contract has to be corrected and compared again before it can be signed.',
    });
  }
  // The engine's verdict. Same rule the signing page applies: an override
  // waives a `false` for empty fields and inconsistencies, never for FMCSA
  // (`lib/gate.ts`, `overrideCovers`). A count of `null` means the
  // FMCSA side is unknown, so the override is not assumed to cover anything.
  const overrideCovers = contract.review_override === true && counts.fmcsa === 0;
  if (contract.can_sign === false && !overrideCovers) {
    flags.push({
      key: 'cannot-sign',
      tone: 'bad',
      text: 'Not ready to sign',
      title:
        'The comparison engine refuses this contract for signing: required fields ' +
        'are still empty, two fields that must agree do not, or an FMCSA rule is ' +
        'broken. Open Review to see which. A reviewer may approve it anyway (an ' +
        'override) for everything except an FMCSA violation.',
    });
  }
  const inconsistency = counts.inconsistency ?? 0;
  if (inconsistency > 0) {
    flags.push({
      key: 'mismatch',
      tone: 'bad',
      text: `${inconsistency} ${plural(inconsistency, 'mismatch', 'mismatches')}`,
      title:
        `${inconsistency} critical cross-field ${plural(inconsistency, 'inconsistency', 'inconsistencies')}: ` +
        'fields that have to agree carry different values. A reviewer can approve ' +
        'this with an override once the reason is recorded.',
    });
  }
  const empty = counts.missing ?? 0;
  if (empty > 0) {
    flags.push({
      key: 'empty',
      tone: 'warn',
      text: `${empty} empty`,
      title:
        `${empty} required ${plural(empty, 'field is', 'fields are')} still empty. ` +
        'A reviewer can approve this with an override once the reason is recorded.',
    });
  }

  if (contract.review_status === 'rejected') {
    flags.push({
      key: 'rejected',
      tone: 'bad',
      text: 'Rejected by reviewer',
      title: contract.review_notes ?? undefined,
    });
  } else if (contract.review_notes) {
    flags.push({ key: 'note', tone: 'warn', text: 'Reviewer note', title: contract.review_notes });
  }

  if (contract.review_override === true) {
    flags.push({
      key: 'override',
      tone: 'warn',
      mark: 'override',
      // A recorded decision, not an open defect: it must not pull the row into
      // the "needs attention" filter.
      attention: false,
      text: 'Approved with override',
      title:
        'A reviewer approved this contract although required fields were empty ' +
        'and/or fields contradicted each other, and recorded a reason. Open ' +
        'Review to read it. FMCSA violations are never waived this way.',
    });
  }

  if (group === 'processing') {
    const age = serverTimeMs(contract.updated_at);
    if (Number.isFinite(age) && now - age > STALE_PROCESSING_MS) {
      flags.push({
        key: 'stale',
        tone: 'warn',
        text: `No progress for ${hoursSince(contract.updated_at, now)}h`,
        title: 'Still uploading or parsing. If this does not clear, re-upload the contract.',
      });
    }
  }
  return flags;
}

export function Dashboard() {
  const { isAtLeast } = useAuth();
  const isAdmin = isAtLeast('admin');
  const [contracts, setContracts] = useState<ContractListItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  const [filter, setFilter] = useState<FilterKey>(() =>
    readStored(STORE_FILTER, FILTER_KEYS, 'all'),
  );
  const [sort, setSort] = useState<SortKey>(() => readStored(STORE_SORT, SORT_KEYS, 'newest'));
  // Persisted like the chips: a reviewer who works "problems only" wants that
  // on the next morning too, and unlike the search box it is visible as a
  // pressed control, so the page can never look empty for no reason.
  const [troubleOnly, setTroubleOnly] = useState(
    () => readStored(STORE_TROUBLE, TOGGLE_KEYS, '0') === '1',
  );
  // Deliberately not persisted: a search box that still holds yesterday's word
  // after a reload reads as "no contracts".
  const [query, setQuery] = useState('');

  useEffect(() => {
    let alive = true;
    setLoading(true);
    contractsApi
      .list()
      .then((rows) => {
        if (!alive) return;
        setContracts(rows);
        setError(null);
      })
      .catch((cause) => {
        if (!alive) return;
        setError(cause instanceof Error ? cause.message : 'Failed to load');
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [reloadKey]);

  useEffect(() => writeStored(STORE_FILTER, filter), [filter]);
  useEffect(() => writeStored(STORE_SORT, sort), [sort]);
  useEffect(() => writeStored(STORE_TROUBLE, troubleOnly ? '1' : '0'), [troubleOnly]);

  // Admin only; the server checks the role again. A completed contract gets a
  // sharper warning because its signed final PDF goes with it.
  const removeContract = async (contract: ContractListItem) => {
    const who = contract.contractor_name ?? 'this contractor';
    const question =
      contract.status === 'completed'
        ? `Delete the COMPLETED contract for ${who}?\n\n` +
          'The signed final PDF and all of its signatures will be permanently deleted. ' +
          'This cannot be undone.'
        : `Delete the contract for ${who}? This cannot be undone.`;
    if (!window.confirm(question)) return;
    setDeletingId(contract.id);
    setError(null);
    try {
      await contractsApi.remove(contract.id);
      setContracts((current) => current.filter((item) => item.id !== contract.id));
    } catch (cause) {
      setError(describeContractDeleteError(cause));
      if (cause instanceof ApiError && cause.status === 404) {
        setContracts((current) => current.filter((item) => item.id !== contract.id));
      }
    } finally {
      setDeletingId(null);
    }
  };

  // One pass over the payload. Everything downstream reads these rows, so a
  // keystroke never re-parses a date or re-builds a search string.
  const rows = useMemo<Row[]>(() => {
    const now = Date.now();
    return contracts.map((contract) => {
      const group = groupOf(contract);
      const created = serverTimeMs(contract.created_at);
      const critical = criticalCounts(contract);
      const flags = buildFlags(contract, group, critical, now);
      return {
        contract,
        group,
        haystack: [
          contract.contractor_name,
          contract.contractor_email,
          contract.id,
          contract.template_id,
          // The template's NAME, now that the list carries it: "search
          // contractor, template or id" was only two thirds true before.
          contract.template_name,
          contract.status,
          STATUS_LABEL[contract.status],
          contract.review_status,
          GROUP_BY_KEY.get(group)?.label,
          // Marker texts too, so "fmcsa", "empty" or "override" find the rows
          // that show that marker without a second control for it.
          ...flags.map((flag) => flag.text),
        ]
          .filter(Boolean)
          .join(' ')
          .toLowerCase(),
        created: Number.isFinite(created) ? created : 0,
        nameKey: (contract.contractor_name ?? '').toLowerCase(),
        flags,
        hasTrouble: flags.some((flag) => flag.attention !== false),
        trouble: troubleScore(contract, critical),
      };
    });
  }, [contracts]);

  const needle = query.trim().toLowerCase();

  /** Search first, so the chip counts describe what the search actually found. */
  const searched = useMemo(
    () => (needle ? rows.filter((row) => row.haystack.includes(needle)) : rows),
    [rows, needle],
  );

  /** How many of the searched rows the "needs attention" toggle would keep --
   *  computed before the toggle is applied, so the number on it does not
   *  collapse to itself the moment it is switched on. */
  const troubleCount = useMemo(
    () => searched.reduce((total, row) => total + (row.hasTrouble ? 1 : 0), 0),
    [searched],
  );

  /** Second narrowing, still ahead of the chips: the chip counts keep meaning
   *  "what this page is actually listing". */
  const narrowed = useMemo(
    () => (troubleOnly ? searched.filter((row) => row.hasTrouble) : searched),
    [searched, troubleOnly],
  );

  const counts = useMemo(() => {
    const tally: Record<FilterKey, number> = {
      all: narrowed.length,
      review: 0,
      signing: 0,
      completed: 0,
      processing: 0,
      rejected: 0,
      failed: 0,
    };
    for (const row of narrowed) tally[row.group] += 1;
    return tally;
  }, [narrowed]);

  const visible = useMemo(() => {
    const picked = filter === 'all' ? narrowed : narrowed.filter((row) => row.group === filter);
    const order = GROUPS.map((group) => group.key);
    const sorted = picked.slice();
    sorted.sort((a, b) => {
      switch (sort) {
        case 'oldest':
          return a.created - b.created;
        case 'name':
          // Unnamed contracts sink to the bottom instead of leading the list.
          if (!a.nameKey !== !b.nameKey) return a.nameKey ? -1 : 1;
          return a.nameKey.localeCompare(b.nameKey) || b.created - a.created;
        case 'status':
          return order.indexOf(a.group) - order.indexOf(b.group) || b.created - a.created;
        case 'trouble':
          return b.trouble - a.trouble || b.created - a.created;
        default:
          return b.created - a.created;
      }
    });
    return sorted;
  }, [narrowed, filter, sort]);

  const clearFilters = useCallback(() => {
    setFilter('all');
    setQuery('');
    setTroubleOnly(false);
  }, []);

  const hasContracts = contracts.length > 0;

  return (
    <section className="page dash">
      <header className="page__header dash__header">
        <div>
          <h1>Contracts</h1>
          {hasContracts && (
            <p className="dash__subtitle" data-testid="dash-count">
              Showing {visible.length} of {contracts.length}
            </p>
          )}
        </div>
        <nav className="dash__quick" aria-label="Quick actions">
          <Link className="button button--primary" to="/contracts/new">
            New contract
          </Link>
          {isAdmin && (
            <Link className="button" to="/templates">
              Templates
            </Link>
          )}
          <Link className="button" to="/signatures">
            My signatures
          </Link>
        </nav>
      </header>

      {error && (
        <p className="alert alert--error dash__alert" role="alert" data-testid="dash-error">
          <span>{error}</span>
          <button
            type="button"
            className="button button--small"
            onClick={() => setReloadKey((key) => key + 1)}
          >
            Try again
          </button>
        </p>
      )}

      {loading && <p className="dash__loading">Loadingâ€¦</p>}

      {!loading && hasContracts && (
        <>
          <div className="dash__filters" role="group" aria-label="Filter contracts by status">
            <FilterChip
              value="all"
              label="All"
              count={counts.all}
              active={filter === 'all'}
              tone="neutral"
              onSelect={setFilter}
            />
            {GROUPS.map((group) => (
              <FilterChip
                key={group.key}
                value={group.key}
                label={group.label}
                count={counts[group.key]}
                active={filter === group.key}
                tone={group.tone}
                onSelect={setFilter}
              />
            ))}
          </div>

          <div className="dash__controls">
            <label className="dash__search">
              <span className="visually-hidden">Search contracts</span>
              <input
                type="search"
                className="field__input"
                data-testid="dash-search"
                aria-label="Search contracts by contractor, template or id"
                placeholder="Search contractor, template or idâ€¦"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
              />
            </label>
            {/* A plain checkbox rather than a seventh chip: the six chips are
                mutually exclusive and always add up to "All", and a bucket that
                cuts across all six would quietly break that. */}
            <label
              className="dash__toggle"
              data-count={troubleCount}
              data-on={troubleOnly ? '1' : '0'}
            >
              <input
                type="checkbox"
                data-testid="dash-trouble"
                checked={troubleOnly}
                onChange={(event) => setTroubleOnly(event.target.checked)}
                // Spelled out rather than left to the label text, so the count
                // pill is not read as part of the control's name.
                aria-label={`Show only the ${troubleCount} contract${
                  troubleCount === 1 ? '' : 's'
                } that need attention`}
              />
              <span>Needs attention only</span>
              <span className="dash__toggle-count" aria-hidden="true">
                {troubleCount}
              </span>
            </label>
            <label className="dash__sort">
              <span className="visually-hidden">Sort contracts</span>
              <select
                className="field__input"
                data-testid="dash-sort"
                aria-label="Sort contracts"
                value={sort}
                onChange={(event) => setSort(event.target.value as SortKey)}
              >
                <option value="newest">Newest first</option>
                <option value="oldest">Oldest first</option>
                <option value="name">Contractor Aâ€“Z</option>
                <option value="status">By status</option>
                <option value="trouble">Most problems first</option>
              </select>
            </label>
          </div>
        </>
      )}

      {!loading && !hasContracts && !error && (
        <div className="dash__empty" data-testid="dash-empty-none">
          <h2>No contracts yet</h2>
          <p>Upload a signed or unsigned contract and it will appear here with its status.</p>
          <Link className="button button--primary" to="/contracts/new">
            Upload a contract
          </Link>
        </div>
      )}

      {!loading && hasContracts && visible.length === 0 && (
        <div className="dash__empty" data-testid="dash-empty-filtered">
          <h2>No contract matches</h2>
          <p>
            {needle ? (
              <>
                Nothing matches â€œ{query.trim()}â€
                {filter !== 'all' ? ` in ${GROUP_BY_KEY.get(filter as GroupKey)?.label}` : ''}
                {troubleOnly ? ' among the contracts that need attention' : ''}.
              </>
            ) : troubleOnly ? (
              // Reachable with `filter === 'all'` too, so the group name is a
              // suffix here rather than the subject of the sentence.
              <>
                Nothing needs attention
                {filter !== 'all' ? ` in ${GROUP_BY_KEY.get(filter as GroupKey)?.label}` : ''} right
                now.
              </>
            ) : (
              <>No contract is in {GROUP_BY_KEY.get(filter as GroupKey)?.label} right now.</>
            )}
          </p>
          <button type="button" className="button" onClick={clearFilters}>
            Clear filters
          </button>
        </div>
      )}

      {!loading && visible.length > 0 && (
        <table className="dash__table">
          <thead>
            <tr>
              <th scope="col">Contractor</th>
              <th scope="col">Status</th>
              <th scope="col">Review</th>
              <th scope="col">Needs attention</th>
              <th scope="col">Uploaded</th>
              <th scope="col">
                <span className="visually-hidden">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {visible.map((row) => (
              <ContractRow
                key={row.contract.id}
                row={row}
                isAdmin={isAdmin}
                deletingId={deletingId}
                onDelete={removeContract}
              />
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

function FilterChip({
  value,
  label,
  count,
  active,
  tone,
  onSelect,
}: {
  value: FilterKey;
  label: string;
  count: number;
  active: boolean;
  tone: GroupMeta['tone'];
  onSelect: (value: FilterKey) => void;
}) {
  return (
    <button
      type="button"
      className={`chip dash__chip dash__chip--${tone}${active ? ' chip--active' : ''}`}
      data-testid={`dash-filter-${value}`}
      data-count={count}
      aria-pressed={active}
      aria-label={`${label}: ${count} contract${count === 1 ? '' : 's'}`}
      onClick={() => onSelect(value)}
    >
      <span className="dash__chip-label">{label}</span>
      <span className="dash__chip-count">{count}</span>
    </button>
  );
}

function ContractRow({
  row,
  isAdmin,
  deletingId,
  onDelete,
}: {
  row: Row;
  isAdmin: boolean;
  deletingId: string | null;
  onDelete: (contract: ContractListItem) => void | Promise<void>;
}) {
  const { contract, group, flags } = row;
  const meta = GROUP_BY_KEY.get(group);
  const who = contract.contractor_name ?? `contract ${shortId(contract.id)}`;
  // Unchanged from the first version of this page: the Sign link appears on an
  // approved contract that is not finished, Download on a finished one.
  const canSign = contract.review_status === 'approved' && contract.status !== 'completed';
  const isFinished = contract.status === 'completed';
  // Exactly one action per row is the obvious next step; the rest stay links.
  const primary: 'final' | 'sign' | 'review' = isFinished ? 'final' : canSign ? 'sign' : 'review';

  return (
    <tr
      className="dash__row"
      data-testid="dash-row"
      data-contract-id={contract.id}
      data-group={group}
      data-status={contract.status}
      data-attention={row.hasTrouble ? '1' : '0'}
    >
      <td className="dash__cell dash__cell--name">
        <span className="dash__label">Contractor</span>
        <div className="dash__value">
          <span className="dash__name">{contract.contractor_name ?? 'â€”'}</span>
          <span className="dash__meta">
            #{shortId(contract.id)} Â·{' '}
            {/* The template's name when the server sends one; otherwise exactly
                the text this line carried before it did. */}
            <span className="dash__template" title={`Template ${contract.template_id}`}>
              {contract.template_name || `template ${shortId(contract.template_id)}`}
            </span>
          </span>
        </div>
      </td>
      <td className="dash__cell">
        <span className="dash__label">Status</span>
        <div className="dash__value">
          <span className={`badge badge--${meta?.tone ?? 'neutral'}`} title={contract.status}>
            {STATUS_LABEL[contract.status] ?? contract.status}
          </span>
        </div>
      </td>
      <td className="dash__cell">
        <span className="dash__label">Review</span>
        <div className="dash__value">
          <span className={`badge badge--${REVIEW_TONE[contract.review_status] ?? 'neutral'}`}>
            {contract.review_status}
          </span>
        </div>
      </td>
      <td className={`dash__cell dash__cell--flags${flags.length ? '' : ' dash__cell--empty'}`}>
        <span className="dash__label">Needs attention</span>
        <div className="dash__value">
          {flags.length === 0 ? (
            <span className="dash__meta">â€”</span>
          ) : (
            <ul className="dash__flags">
              {flags.map((flag) => (
                <li
                  key={flag.key}
                  className={
                    `dash__flag dash__flag--${flag.tone}` +
                    (flag.mark ? ` dash__flag--${flag.mark}` : '')
                  }
                  data-flag={flag.key}
                  title={flag.title ?? flag.text}
                >
                  {flag.text}
                </li>
              ))}
            </ul>
          )}
        </div>
      </td>
      <td className="dash__cell dash__cell--date">
        <span className="dash__label">Uploaded</span>
        <div className="dash__value">
          <time
            dateTime={contract.created_at}
            title={formatServerDateTime(contract.created_at)}
          >
            {formatServerDate(contract.created_at)}
          </time>
        </div>
      </td>
      <td className="dash__cell dash__cell--actions">
        <span className="dash__label">Actions</span>
        <div className="dash__value dash__actions">
          {isFinished && (
            <Link
              className={primary === 'final' ? 'button button--small button--primary' : 'button--link'}
              to={`/contracts/${contract.id}/final`}
              aria-label={`Final document for ${who}`}
            >
              Final document
            </Link>
          )}
          {canSign && (
            <Link
              className={primary === 'sign' ? 'button button--small button--primary' : 'button--link'}
              to={`/contracts/${contract.id}/sign`}
              aria-label={`Sign the contract for ${who}`}
            >
              Sign
            </Link>
          )}
          <Link
            className={primary === 'review' ? 'button button--small button--primary' : 'button--link'}
            to={`/contracts/${contract.id}/review`}
            aria-label={`Review the contract for ${who}`}
          >
            Review
          </Link>
          {/* The full-screen editor. Always offered and never the row's primary
              action: putting a word or a tick on the page is something a user
              goes looking for, not the next step the dashboard recommends. */}
          <Link
            className="button--link"
            data-testid="dash-open-editor"
            to={`/contracts/${contract.id}/edit`}
            aria-label={`Open the editor for ${who}`}
          >
            Open editor
          </Link>
          {isAdmin && (
            <button
              type="button"
              className="button button--link button--danger"
              disabled={deletingId !== null}
              aria-label={`Delete the contract for ${who}`}
              onClick={() => void onDelete(contract)}
            >
              {deletingId === contract.id ? 'Deletingâ€¦' : 'Delete'}
            </button>
          )}
        </div>
      </td>
    </tr>
  );
}
