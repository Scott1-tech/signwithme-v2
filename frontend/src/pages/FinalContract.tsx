import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { contractsApi } from '../api/contracts';
import { tokenStore } from '../api/client';
import { formatServerDateTime } from '../lib/time';
import type { ContractDetail } from '../types/contract';

interface AuditEntry {
  action: string;
  actor_email: string | null;
  actor_role: string | null;
  details: Record<string, unknown>;
  timestamp: string;
}

export function FinalContract() {
  const { contractId } = useParams<{ contractId: string }>();
  const [contract, setContract] = useState<ContractDetail | null>(null);
  const [audit, setAudit] = useState<AuditEntry[]>([]);
  const [verification, setVerification] = useState<{ intact: boolean; current_hash: string } | null>(
    null,
  );
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!contractId) return;
    contractsApi
      .get(contractId)
      .then(setContract)
      .catch((cause) => setError(cause instanceof Error ? cause.message : 'Failed to load'));
    contractsApi.auditTrail(contractId).then(setAudit).catch(() => undefined);
    contractsApi.verifyFinal(contractId).then(setVerification).catch(() => undefined);
  }, [contractId]);

  const download = async () => {
    if (!contractId) return;
    // The download endpoint is authenticated, so the bytes are fetched with the
    // bearer token and handed to the browser as a blob.
    const response = await fetch(`/api/download/${contractId}/final`, {
      headers: { Authorization: `Bearer ${tokenStore.get() ?? ''}` },
    });
    if (!response.ok) {
      setError(`Download failed (${response.status})`);
      return;
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `contract-${contractId}-signed.pdf`;
    anchor.click();
    URL.revokeObjectURL(url);
  };

  return (
    <section className="page">
      <header className="page__header">
        <h1>Final contract</h1>
        <Link to="/">Back to dashboard</Link>
      </header>

      {error && <p className="alert alert--error">{error}</p>}

      {contract && (
        <div className="card">
          <h2>{contract.contractor_name ?? contract.id}</h2>
          <p>
            Status: <strong>{contract.status}</strong> · Review:{' '}
            <strong>{contract.review_status}</strong>
          </p>

          {contract.final_available ? (
            <>
              <button type="button" className="button button--primary" onClick={download}>
                Download signed PDF
              </button>
              {verification && (
                <p className={verification.intact ? 'hint' : 'alert alert--error'}>
                  {verification.intact
                    ? 'Integrity verified: the stored document matches the hash recorded at signing.'
                    : 'Warning: the stored document does not match the hash recorded at signing.'}
                  <br />
                  <code>{verification.current_hash}</code>
                </p>
              )}
            </>
          ) : (
            <p className="hint">This contract has not been finalised yet.</p>
          )}
        </div>
      )}

      {audit.length > 0 && (
        <div className="card">
          <h2>Audit trail</h2>
          <table className="table table--compact">
            <thead>
              <tr>
                <th>When</th>
                <th>Action</th>
                <th>Actor</th>
              </tr>
            </thead>
            <tbody>
              {audit.map((entry, index) => (
                <tr key={`${entry.action}-${index}`}>
                  <td>{formatServerDateTime(entry.timestamp)}</td>
                  <td>{entry.action.replace(/_/g, ' ')}</td>
                  <td>
                    {entry.actor_email ?? 'system'}
                    {entry.actor_role ? ` (${entry.actor_role})` : ''}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
