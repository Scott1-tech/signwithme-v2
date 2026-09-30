import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { contractsApi } from '../api/contracts';
import { formatServerDate } from '../lib/time';
import type { Contract } from '../types/contract';

export function ReviewQueue() {
  const [contracts, setContracts] = useState<Contract[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    contractsApi
      .reviewQueue()
      .then(setContracts)
      .catch((cause) => setError(cause instanceof Error ? cause.message : 'Failed to load queue'));
  }, []);

  return (
    <section className="page">
      <header className="page__header">
        <h1>Review queue</h1>
      </header>
      {error && <p className="alert alert--error">{error}</p>}
      {contracts.length === 0 && <p className="empty">Nothing is waiting for review.</p>}
      <ul className="list">
        {contracts.map((contract) => (
          <li key={contract.id} className="list__item">
            <span>
              {contract.contractor_name ?? contract.id} · uploaded{' '}
              {formatServerDate(contract.created_at)}
            </span>
            <Link to={`/contracts/${contract.id}/review`}>Open</Link>
          </li>
        ))}
      </ul>
    </section>
  );
}
