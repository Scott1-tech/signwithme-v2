import { useMemo } from 'react';

interface Props {
  value: string; // ISO yyyy-mm-dd, as the native input produces
  onChange: (value: string) => void;
  label?: string;
  /** `data-testid` of the input, so "Choose the date first" can focus it. */
  testId?: string;
}

/** Renders the ISO value the way the contract will actually print it. */
export function formatForContract(iso: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  if (!match) return iso;
  const [, year, month, day] = match;
  return `${month}/${day}/${year}`;
}

export function DatePicker({ value, onChange, label = 'Contract date', testId }: Props) {
  // Every picker starts empty (no automatic date); say so rather than printing
  // a blank preview.
  const preview = useMemo(() => (value ? formatForContract(value) : 'nothing yet'), [value]);

  return (
    <label className="date-picker">
      <span className="date-picker__label">{label}</span>
      <input
        type="date"
        className="date-picker__input"
        data-testid={testId}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
      {/* The input speaks ISO; the PDF prints MM/DD/YYYY. Showing both stops
          the reviewer being surprised by the finished document. */}
      <span className="date-picker__preview">prints as {preview}</span>
    </label>
  );
}
