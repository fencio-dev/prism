import { getAuthHeaders } from './headers';

export async function fetchPolicies({ agentId } = {}) {
  const url = agentId ? `/api/v2/policies?agent_id=${encodeURIComponent(agentId)}` : '/api/v2/policies';
  const res = await fetch(url, { headers: getAuthHeaders() });
  if (!res.ok) throw new Error(`Failed to fetch policies: ${res.status}`);
  const data = await res.json();
  return data.policies;
}

export async function deletePolicy(id) {
  const res = await fetch(`/api/v2/policies/${id}`, {
    method: 'DELETE',
    headers: getAuthHeaders(),
  });
  if (!res.ok) throw new Error(`Failed to delete policy ${id}: ${res.status}`);
}

export async function createPolicy(data) {
  const res = await fetch('/api/v2/policies', {
    method: 'POST',
    headers: getAuthHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error(`Failed to create policy: ${res.status}`);
  return res.json();
}

export async function togglePolicyStatus(id) {
  const res = await fetch(`/api/v2/policies/${id}/toggle`, {
    method: 'PATCH',
    headers: getAuthHeaders(),
  });
  if (!res.ok) throw new Error(`Failed to toggle policy ${id}: ${res.status}`);
  return res.json();
}

export async function updatePolicy(id, data) {
  const res = await fetch(`/api/v2/policies/${id}`, {
    method: 'PUT',
    headers: getAuthHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error(`Failed to update policy ${id}: ${res.status}`);
  return res.json();
}

export async function getPolicy(id) {
  const res = await fetch(`/api/v2/policies/${id}`, { headers: getAuthHeaders() });
  if (!res.ok) return null;
  return res.json();
}
