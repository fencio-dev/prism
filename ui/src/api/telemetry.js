import { getAuthHeaders } from './headers';

const BASE = '/api/v2/telemetry';

export async function fetchSessions({ limit = 50, offset = 0 } = {}) {
  const url = `${BASE}/sessions?limit=${limit}&offset=${offset}`;
  const res = await fetch(url, { headers: getAuthHeaders() });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function fetchSessionDetail(sessionId) {
  const res = await fetch(`${BASE}/sessions/${sessionId}`, { headers: getAuthHeaders() });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function fetchCalls({ limit = 50, offset = 0, agentId, decision, startMs, endMs } = {}) {
  const params = new URLSearchParams({ limit, offset });
  if (agentId) params.set('agent_id', agentId);
  if (decision) params.set('decision', decision);
  if (startMs != null) params.set('start_ms', startMs);
  if (endMs != null) params.set('end_ms', endMs);
  const res = await fetch(`${BASE}/calls?${params}`, { headers: getAuthHeaders() });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function fetchCallDetail(callId) {
  const res = await fetch(`${BASE}/calls/${callId}`, { headers: getAuthHeaders() });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function deleteCalls() {
  const res = await fetch(`${BASE}/calls`, { method: 'DELETE', headers: getAuthHeaders() });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}
