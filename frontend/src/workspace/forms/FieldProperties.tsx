import { useEffect, useState } from 'react';
import {
  FIELD_SIZE_MAX_PT,
  FIELD_SIZE_MIN_PT,
  MAX_FIELD_OPTIONS,
  MAX_FIELD_VALUE_CHARS,
  MAX_OPTION_CHARS,
  MAX_TOOLTIP_CHARS,
  type FieldDef,
} from '../../api/forms';
import {
  FIELD_FONTS,
  FIELD_TYPE_LABEL,
  FONT_TYPES,
  TEXT_TYPES,
  charsetMessage,
  freshExportValue,
  isValidExport,
  nameProblem,
  radioGroup,
  suggestRename,
  takenNames,
  winAnsiProblem,
  type FieldRecord,
  type FormState,
} from './fieldModel';

/**
 * F2b: the «Form field» panel (EDITOR-V2-PLAN §7.4 `FieldProperties.tsx`):
 * name, required, read-only, tooltip, default, the dropdown's options, the
 * radio group and its value, font / size, max length, Duplicate, Delete.
 *
 * Every committed change is ONE step of the edit session's op list (a text
 * box commits on Enter / leaving it, a tick or a choice at once). A name that
 * is already taken is NOT applied: the panel says so and offers «Rename to
 * X_2» -- except a radio button, which joins the radio group of that name.
 * Text a widget cannot draw (non-WinAnsi) is refused before anything is sent.
 */

export type FieldPatch = Partial<
  Pick<FieldDef, 'name' | 'required' | 'readonly' | 'tooltip' | 'default' | 'options' | 'export_value' | 'font' | 'size_pt' | 'max_len'>
>;

interface Props {
  record: FieldRecord;
  state: FormState;
  onPatch: (patch: FieldPatch, label: string) => Promise<boolean>;
  onDuplicate: () => void;
  onDelete: () => void;
  onClose: () => void;
}

/** A text input that commits on Enter / blur and goes back on Esc. */
function CommitInput({
  value,
  onCommit,
  testId,
  label,
  disabled,
  maxLength,
  placeholder,
  type = 'text',
}: {
  value: string;
  onCommit: (value: string) => void;
  testId: string;
  label: string;
  disabled?: boolean;
  maxLength?: number;
  placeholder?: string;
  type?: 'text' | 'number';
}) {
  const [text, setText] = useState(value);
  useEffect(() => setText(value), [value]);
  return (
    <input
      type={type}
      className="fm-props__input"
      data-testid={testId}
      aria-label={label}
      value={text}
      disabled={disabled}
      maxLength={maxLength}
      placeholder={placeholder}
      onChange={(event) => setText(event.target.value)}
      onBlur={() => {
        if (text !== value) onCommit(text);
      }}
      onKeyDown={(event) => {
        if (event.key === 'Enter') {
          event.preventDefault();
          if (text !== value) onCommit(text);
        } else if (event.key === 'Escape') {
          event.preventDefault();
          setText(value);
        }
      }}
    />
  );
}

export function FieldProperties({ record, state, onPatch, onDuplicate, onDelete, onClose }: Props) {
  const def = record.def;
  const type = def.type;
  const locked = record.lockedReason;
  const [nameIssue, setNameIssue] = useState<{ wanted: string; kind: 'invalid' | 'taken' } | null>(null);
  const [exportIssue, setExportIssue] = useState<string | null>(null);
  const [textIssue, setTextIssue] = useState<string | null>(null);
  const [newOption, setNewOption] = useState('');

  useEffect(() => {
    setNameIssue(null);
    setExportIssue(null);
    setTextIssue(null);
  }, [record.key]);

  const charsetOk = (text: string, multiline = false) => {
    const bad = winAnsiProblem(text, multiline);
    setTextIssue(bad ? charsetMessage(bad) : null);
    return !bad;
  };

  const rename = (wanted: string) => {
    const name = wanted.trim();
    if (!name || name === def.name) {
      setNameIssue(null);
      return;
    }
    const problem = nameProblem(state, record, name);
    if (problem) {
      setNameIssue({ wanted: name, kind: problem });
      return;
    }
    setNameIssue(null);
    void onPatch({ name }, type === 'radio' ? 'Rename radio group' : 'Rename form field');
  };

  const setExport = (wanted: string) => {
    const value = wanted.trim();
    if (type === 'checkbox' && value === '') {
      setExportIssue(null);
      void onPatch({ export_value: null }, 'Change checkbox value');
      return;
    }
    if (!isValidExport(value)) {
      setExportIssue('Use letters, digits, _ . - (at most 64).');
      return;
    }
    if (type === 'radio') {
      const taken = radioGroup(state, def.name).filter((item) => item !== record).map((item) => item.def.export_value ?? '');
      if (taken.includes(value)) {
        setExportIssue(`Another button of «${def.name}» already has the value «${value}».`);
        return;
      }
    }
    setExportIssue(null);
    void onPatch({ export_value: value }, type === 'radio' ? 'Change radio value' : 'Change checkbox value');
  };

  const options = def.options ?? [];
  const setOptions = (next: string[], label: string) => {
    const bad = next.map((option) => winAnsiProblem(option)).find(Boolean);
    if (bad) {
      setTextIssue(charsetMessage(bad));
      return;
    }
    if (next.length === 0) {
      setTextIssue('A dropdown needs at least one option.');
      return;
    }
    if (new Set(next).size !== next.length) {
      setTextIssue('The options must be different from each other.');
      return;
    }
    setTextIssue(null);
    const keepDefault = typeof def.default === 'string' && next.includes(def.default);
    void onPatch({ options: next, ...(keepDefault ? {} : { default: null }) }, label);
  };

  const suggested = nameIssue?.kind === 'taken' ? suggestRename(nameIssue.wanted, takenNames(state, record)) : null;
  const radioSiblings = type === 'radio' ? radioGroup(state, def.name).length : 0;

  return (
    <section className="fm-props" data-testid="field-properties" data-key={record.key} data-type={type} aria-label="Form field properties">
      <header className="fm-props__head">
        <strong className="fm-props__title">{FIELD_TYPE_LABEL[type]}</strong>
        <span className="fm-props__source" data-testid="field-source">
          {record.origin ? (record.origin.source === 'original' ? 'in the uploaded PDF' : 'saved') : 'new in this draft'}
        </span>
        <button type="button" className="fm-props__close" aria-label="Close the field panel" title="Close" onClick={onClose}>
          ×
        </button>
      </header>
      {locked && (
        <p className="fm-props__locked" role="status" data-testid="field-locked">
          {locked}
        </p>
      )}

      <label className="fm-props__row">
        <span>{type === 'radio' ? 'Group name' : 'Name'}</span>
        <CommitInput
          value={def.name}
          testId="field-name"
          label={type === 'radio' ? 'Radio group name' : 'Field name'}
          disabled={Boolean(locked)}
          maxLength={64}
          onCommit={rename}
        />
      </label>
      {nameIssue?.kind === 'invalid' && (
        <p className="fm-props__issue" role="alert" data-testid="field-name-invalid">
          A field name uses letters, digits, “_”, “.” and “-” only (at most 64) — «{nameIssue.wanted}» was not applied.
        </p>
      )}
      {nameIssue?.kind === 'taken' && suggested && (
        <p className="fm-props__issue" role="alert" data-testid="field-name-taken">
          A field named «{nameIssue.wanted}» already exists; two fields cannot share a name.{' '}
          <button
            type="button"
            className="sj-btn fm-props__fix"
            data-testid="field-rename-suggest"
            onClick={() => {
              setNameIssue(null);
              void onPatch({ name: suggested }, 'Rename form field');
            }}
          >
            Rename to {suggested}
          </button>
        </p>
      )}
      {type === 'radio' && (
        <p className="fm-props__note" data-testid="field-radio-group">
          {radioSiblings} button{radioSiblings === 1 ? '' : 's'} in this group. Give another radio button the same group name
          to add it to the group.
        </p>
      )}

      {(type === 'radio' || type === 'checkbox') && (
        <label className="fm-props__row">
          <span>{type === 'radio' ? 'Value of this button' : 'Value when checked'}</span>
          <CommitInput
            value={def.export_value ?? ''}
            testId="field-export-value"
            label={type === 'radio' ? 'Value of this radio button' : 'Value when checked'}
            disabled={Boolean(locked)}
            maxLength={64}
            placeholder={type === 'checkbox' ? 'Yes' : undefined}
            onCommit={setExport}
          />
        </label>
      )}
      {exportIssue && (
        <p className="fm-props__issue" role="alert" data-testid="field-export-issue">
          {exportIssue}{' '}
          {type === 'radio' && (
            <button
              type="button"
              className="sj-btn fm-props__fix"
              data-testid="field-export-suggest"
              onClick={() => {
                const value = freshExportValue(new Set(radioGroup(state, def.name).map((item) => item.def.export_value ?? '')));
                setExportIssue(null);
                void onPatch({ export_value: value }, 'Change radio value');
              }}
            >
              Use a free value
            </button>
          )}
        </p>
      )}

      <div className="fm-props__checks">
        <label>
          <input
            type="checkbox"
            data-testid="field-required"
            checked={Boolean(def.required)}
            disabled={Boolean(locked)}
            onChange={(event) => void onPatch({ required: event.target.checked }, 'Change «Required»')}
          />{' '}
          Required
        </label>
        <label>
          <input
            type="checkbox"
            data-testid="field-readonly"
            checked={Boolean(def.readonly)}
            disabled={Boolean(locked)}
            onChange={(event) => void onPatch({ readonly: event.target.checked }, 'Change «Read-only»')}
          />{' '}
          Read-only
        </label>
      </div>

      <label className="fm-props__row">
        <span>Tooltip</span>
        <CommitInput
          value={def.tooltip ?? ''}
          testId="field-tooltip"
          label="Tooltip (what a PDF reader shows on hover)"
          disabled={Boolean(locked)}
          maxLength={MAX_TOOLTIP_CHARS}
          onCommit={(value) => {
            if (!charsetOk(value)) return;
            void onPatch({ tooltip: value.trim() || null }, 'Change tooltip');
          }}
        />
      </label>

      {TEXT_TYPES.has(type) && (
        <label className="fm-props__row">
          <span>Default text</span>
          <CommitInput
            value={typeof def.default === 'string' ? def.default : ''}
            testId="field-default"
            label="Default text"
            disabled={Boolean(locked)}
            maxLength={def.max_len ?? MAX_FIELD_VALUE_CHARS}
            onCommit={(value) => {
              if (!charsetOk(value, type === 'multiline')) return;
              void onPatch({ default: value || null }, 'Change default text');
            }}
          />
        </label>
      )}
      {type === 'dropdown' && (
        <label className="fm-props__row">
          <span>Default choice</span>
          <select
            className="fm-props__input"
            data-testid="field-default"
            aria-label="Default choice"
            value={typeof def.default === 'string' ? def.default : ''}
            disabled={Boolean(locked)}
            onChange={(event) => void onPatch({ default: event.target.value || null }, 'Change default choice')}
          >
            <option value="">(none)</option>
            {options.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </label>
      )}
      {(type === 'checkbox' || type === 'radio') && (
        <label className="fm-props__check">
          <input
            type="checkbox"
            data-testid="field-default"
            checked={def.default === true}
            disabled={Boolean(locked)}
            onChange={(event) =>
              void onPatch({ default: event.target.checked }, type === 'radio' ? 'Change default choice' : 'Change default')
            }
          />{' '}
          {type === 'radio' ? 'This button is chosen by default' : 'Checked by default'}
        </label>
      )}

      {type === 'dropdown' && (
        <fieldset className="fm-props__options" data-testid="field-options" disabled={Boolean(locked)}>
          <legend>Options ({options.length})</legend>
          <ol>
            {options.map((option, index) => (
              <li key={`${index}-${option}`} data-testid="field-option">
                <CommitInput
                  value={option}
                  testId={`field-option-${index}`}
                  label={`Option ${index + 1}`}
                  maxLength={MAX_OPTION_CHARS}
                  onCommit={(value) => {
                    const trimmed = value.trim();
                    if (!trimmed) return;
                    setOptions(options.map((item, at) => (at === index ? trimmed : item)), 'Change option');
                  }}
                />
                <button
                  type="button"
                  className="fm-props__mini"
                  aria-label={`Move option ${index + 1} up`}
                  title="Move up"
                  disabled={index === 0}
                  onClick={() => {
                    const next = [...options];
                    [next[index - 1], next[index]] = [next[index], next[index - 1]];
                    setOptions(next, 'Reorder options');
                  }}
                >
                  ↑
                </button>
                <button
                  type="button"
                  className="fm-props__mini"
                  aria-label={`Move option ${index + 1} down`}
                  title="Move down"
                  disabled={index === options.length - 1}
                  onClick={() => {
                    const next = [...options];
                    [next[index + 1], next[index]] = [next[index], next[index + 1]];
                    setOptions(next, 'Reorder options');
                  }}
                >
                  ↓
                </button>
                <button
                  type="button"
                  className="fm-props__mini"
                  data-testid={`field-option-remove-${index}`}
                  aria-label={`Remove option ${index + 1}`}
                  title="Remove"
                  disabled={options.length <= 1}
                  onClick={() => setOptions(options.filter((_item, at) => at !== index), 'Remove option')}
                >
                  ×
                </button>
              </li>
            ))}
          </ol>
          <div className="fm-props__add">
            <input
              type="text"
              className="fm-props__input"
              data-testid="field-option-new"
              aria-label="New option"
              placeholder="New option"
              value={newOption}
              maxLength={MAX_OPTION_CHARS}
              onChange={(event) => setNewOption(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') {
                  event.preventDefault();
                  const value = newOption.trim();
                  if (value && options.length < MAX_FIELD_OPTIONS) {
                    setOptions([...options, value], 'Add option');
                    setNewOption('');
                  }
                }
              }}
            />
            <button
              type="button"
              className="sj-btn"
              data-testid="field-option-add"
              disabled={!newOption.trim() || options.length >= MAX_FIELD_OPTIONS}
              onClick={() => {
                const value = newOption.trim();
                if (!value) return;
                setOptions([...options, value], 'Add option');
                setNewOption('');
              }}
            >
              Add
            </button>
          </div>
        </fieldset>
      )}

      {FONT_TYPES.has(type) && (
        <div className="fm-props__pair">
          <label className="fm-props__row">
            <span>Font</span>
            <select
              className="fm-props__input"
              data-testid="field-font"
              aria-label="Font"
              value={def.font ?? ''}
              disabled={Boolean(locked)}
              onChange={(event) => void onPatch({ font: (event.target.value || null) as FieldDef['font'] }, 'Change font')}
            >
              <option value="">Auto (Helvetica)</option>
              {FIELD_FONTS.map((font) => (
                <option key={font.id} value={font.id}>
                  {font.label}
                </option>
              ))}
            </select>
          </label>
          <label className="fm-props__row">
            <span>Size (pt)</span>
            <CommitInput
              type="number"
              value={def.size_pt ? String(def.size_pt) : ''}
              testId="field-size"
              label={`Font size in points, ${FIELD_SIZE_MIN_PT}–${FIELD_SIZE_MAX_PT}; empty = auto`}
              placeholder="Auto"
              disabled={Boolean(locked)}
              onCommit={(value) => {
                if (!value.trim()) {
                  void onPatch({ size_pt: null }, 'Change font size');
                  return;
                }
                const size = Number(value);
                if (!Number.isFinite(size)) return;
                void onPatch(
                  { size_pt: Math.min(FIELD_SIZE_MAX_PT, Math.max(FIELD_SIZE_MIN_PT, Math.round(size * 2) / 2)) },
                  'Change font size',
                );
              }}
            />
          </label>
        </div>
      )}
      {TEXT_TYPES.has(type) && (
        <label className="fm-props__row">
          <span>Max length</span>
          <CommitInput
            type="number"
            value={def.max_len ? String(def.max_len) : ''}
            testId="field-max-len"
            label="Maximum number of characters; empty = no limit"
            placeholder="No limit"
            disabled={Boolean(locked)}
            onCommit={(value) => {
              if (!value.trim()) {
                void onPatch({ max_len: null }, 'Change max length');
                return;
              }
              const max = Math.round(Number(value));
              if (!Number.isFinite(max) || max < 1) return;
              void onPatch({ max_len: Math.min(MAX_FIELD_VALUE_CHARS, max) }, 'Change max length');
            }}
          />
        </label>
      )}

      {textIssue && (
        <p className="fm-props__issue" role="alert" data-testid="field-text-issue">
          {textIssue}
        </p>
      )}

      <p className="fm-props__geometry" data-testid="field-geometry">
        {def.rect.x_pt.toFixed(1)}, {def.rect.y_pt.toFixed(1)} pt · {def.rect.w_pt.toFixed(1)} × {def.rect.h_pt.toFixed(1)} pt
      </p>
      <div className="fm-props__actions">
        <button
          type="button"
          className="sj-btn"
          data-testid="field-duplicate"
          disabled={Boolean(locked)}
          title={type === 'radio' ? 'Another button of this group, below this one' : 'A copy with a new name, below this one'}
          onClick={onDuplicate}
        >
          Duplicate
        </button>
        <button
          type="button"
          className="sj-btn sj-btn--danger"
          data-testid="field-delete"
          title="Delete the field (Delete key)"
          onClick={onDelete}
        >
          Delete
        </button>
      </div>
    </section>
  );
}
