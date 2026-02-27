import { getAuthHeaders } from './headers';

export async function runEnforce(intentEvent) {
  const response = await fetch('/api/v2/enforce?dry_run=1', {
    method: 'POST',
    headers: getAuthHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify(intentEvent),
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(`Enforce failed (${response.status}): ${text}`);
  }

  return response.json();
}
