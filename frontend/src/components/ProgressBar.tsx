interface Props {
  percentage: number;
  label?: string;
}

export function ProgressBar({ percentage, label }: Props) {
  const clamped = Math.max(0, Math.min(100, percentage));
  const tone = clamped >= 95 ? 'good' : clamped >= 70 ? 'warn' : 'bad';

  return (
    <div className="progress">
      <div className="progress__header">
        <span>{label ?? 'Completion'}</span>
        <strong>{clamped.toFixed(1)}%</strong>
      </div>
      <div
        className="progress__track"
        role="progressbar"
        aria-valuenow={Math.round(clamped)}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div className={`progress__fill progress__fill--${tone}`} style={{ width: `${clamped}%` }} />
      </div>
    </div>
  );
}
