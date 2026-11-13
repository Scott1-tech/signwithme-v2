import { useState, type FormEvent } from 'react';
import { api } from '../api/client';

/** Change your own password (POST /api/auth/me/password). */
export function Account() {
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [repeat, setRepeat] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    setDone(false);
    if (next !== repeat) {
      setError('The two new passwords are not the same.');
      return;
    }
    setBusy(true);
    try {
      await api.post('/api/auth/me/password', { current_password: current, new_password: next });
      setDone(true);
      setCurrent('');
      setNext('');
      setRepeat('');
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not change the password');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="page">
      <form className="card auth__card" onSubmit={submit}>
        <h1>Change password</h1>
        <label className="field">
          <span>Current password</span>
          <input
            type="password"
            autoComplete="current-password"
            value={current}
            onChange={(e) => setCurrent(e.target.value)}
            required
          />
        </label>
        <label className="field">
          <span>New password</span>
          <input
            type="password"
            autoComplete="new-password"
            minLength={8}
            maxLength={72}
            value={next}
            onChange={(e) => setNext(e.target.value)}
            required
          />
        </label>
        <label className="field">
          <span>New password again</span>
          <input
            type="password"
            autoComplete="new-password"
            minLength={8}
            maxLength={72}
            value={repeat}
            onChange={(e) => setRepeat(e.target.value)}
            required
          />
        </label>
        {error && <p className="alert alert--error">{error}</p>}
        {done && <p className="alert alert--good">Password changed.</p>}
        <button type="submit" className="button button--primary" disabled={busy}>
          {busy ? 'Saving…' : 'Change password'}
        </button>
      </form>
    </div>
  );
}
