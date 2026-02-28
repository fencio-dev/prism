import { useState, useEffect, useCallback } from 'react';
import { Activity, History, MousePointerClick, ShieldX } from 'lucide-react';
import { fetchCalls, fetchCallDetail, deleteCalls } from '../api/telemetry';
import { getPolicy } from '../api/policies';
import EnforcementResultPanel from './EnforcementResultPanel';
import PrismEmptyState from './PrismEmptyState';
import RunAnchorComparisonPanel from './RunAnchorComparisonPanel';

function formatTime(ms) {
  if (ms == null) return '—';
  return new Date(ms).toLocaleString();
}

function formatTimeShort(ms) {
  if (ms == null) return '—';
  return new Date(ms).toLocaleTimeString();
}

function truncate(str, len = 14) {
  if (!str) return '—';
  return str.length > len ? str.slice(0, len) + '...' : str;
}

const DECISION_COLORS = {
  ALLOW: 'border-green-600/30 bg-green-100 text-green-700',
  DENY: 'border-red-500/35 bg-red-100 text-red-700',
  MODIFY: 'border-amber-600/30 bg-amber-100 text-amber-700',
  STEP_UP: 'border-sky-600/30 bg-sky-100 text-sky-700',
  DEFER: 'border-stone-400/40 bg-stone-100 text-stone-600',
};

const SLICE_COMPARISON_CONFIG = [
  { label: 'Action', intentKey: 'op', anchorKey: 'op' },
  { label: 'Resource', intentKey: 't', anchorKey: 't' },
  { label: 'Data', intentKey: 'p', anchorKey: 'p' },
  { label: 'Risk', intentKey: 'ctx', anchorKey: 'ctx' },
];

function decisionBadgeStyle(decision) {
  return DECISION_COLORS[decision] ?? DECISION_COLORS.DEFER;
}

export default function TelemetryTable() {
  const [calls, setCalls] = useState([]);
  const [totalCount, setTotalCount] = useState(0);
  const [loading, setLoading] = useState(true);
  const [offline, setOffline] = useState(false);
  const [error, setError] = useState(null);
  const [lastUpdated, setLastUpdated] = useState(null);

  const [selectedCallId, setSelectedCallId] = useState(null);
  const [detail, setDetail] = useState(null);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [detailError, setDetailError] = useState(null);
  const [policyById, setPolicyById] = useState({});
  const [loadingPolicies, setLoadingPolicies] = useState(false);
  const [policyError, setPolicyError] = useState(null);
  const [isNarrow, setIsNarrow] = useState(() =>
    typeof window !== 'undefined' ? window.innerWidth < 1100 : false
  );
  const [page, setPage] = useState(0);

  const poll = useCallback(async () => {
    try {
      const data = await fetchCalls({ limit: 50, offset: page * 50 });
      setCalls(data?.calls ?? []);
      setTotalCount(data?.total_count ?? 0);
      setOffline(false);
      setError(null);
      setLastUpdated(new Date());
    } catch (err) {
      const isNetworkError = err instanceof TypeError;
      const isServerError = err.message && /HTTP 5\d\d/.test(err.message);
      if (isNetworkError || isServerError) {
        setOffline(true);
      } else {
        setError(err.message ?? 'Failed to fetch calls');
      }
    } finally {
      setLoading(false);
    }
  }, [page]);

  useEffect(() => {
    poll();
    const id = setInterval(poll, 5000);
    return () => clearInterval(id);
  }, [poll, page]);

  useEffect(() => {
    function onResize() {
      setIsNarrow(window.innerWidth < 1100);
    }

    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  useEffect(() => {
    let cancelled = false;
    const evidence = detail?.enforcement_result?.evidence;
    const intentEvent = detail?.call?.intent_event;

    if (!detail || !intentEvent || !Array.isArray(evidence) || evidence.length === 0) {
      setPolicyById({});
      setLoadingPolicies(false);
      setPolicyError(null);
      return;
    }

    async function loadPolicies() {
      setLoadingPolicies(true);
      setPolicyError(null);

      try {
        const ids = [...new Set(evidence.map((entry) => entry.boundary_id).filter(Boolean))];
        if (ids.length === 0) {
          if (!cancelled) {
            setPolicyById({});
            setLoadingPolicies(false);
          }
          return;
        }

        const results = await Promise.all(ids.map(async (id) => ({ id, policy: await getPolicy(id) })));
        if (cancelled) return;

        const next = {};
        for (const { id, policy } of results) {
          if (policy) next[id] = policy;
        }
        setPolicyById(next);
      } catch (err) {
        if (!cancelled) {
          setPolicyById({});
          setPolicyError(err.message ?? 'Failed to load policy anchors');
        }
      } finally {
        if (!cancelled) setLoadingPolicies(false);
      }
    }

    loadPolicies();
    return () => {
      cancelled = true;
    };
  }, [detail]);

  async function handleClearAll() {
    if (!window.confirm('Delete all telemetry calls? This cannot be undone.')) return;
    await deleteCalls();
    setPage(0);
    poll();
  }

  async function handleRowClick(call) {
    const callId = call.call_id;
    if (selectedCallId === callId) {
      setSelectedCallId(null);
      setDetail(null);
      setDetailError(null);
      return;
    }
    setSelectedCallId(callId);
    setDetail(null);
    setDetailError(null);
    setPolicyById({});
    setPolicyError(null);
    setLoadingDetail(true);
    try {
      const data = await fetchCallDetail(callId);
      setDetail(data);
    } catch (err) {
      setDetailError(err.message ?? 'Failed to load call detail');
    } finally {
      setLoadingDetail(false);
    }
  }

  function handleRowKeyDown(event, call) {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      handleRowClick(call);
    }
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="mb-4 flex shrink-0 flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <h2 className="text-lg font-semibold text-[var(--prism-text-primary)]">Telemetry</h2>
          <span className="text-sm text-[var(--prism-text-secondary)]">{totalCount} calls</span>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={handleClearAll}
            className="rounded border border-[var(--prism-border-default)] px-3 py-1.5 text-xs font-medium text-[var(--prism-text-primary)] transition-colors hover:bg-[var(--prism-accent-subtle)] hover:text-[var(--prism-text-primary)] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[var(--prism-accent)]/40"
          >
            Clear All
          </button>
          <div className="flex items-center gap-2 text-sm text-[var(--prism-text-secondary)]">
            {offline ? (
              <>
                <span className="h-2 w-2 rounded-full bg-red-400" />
                Prism backend unreachable
              </>
            ) : loading ? (
              'Loading...'
            ) : (
              <>
                <span className="h-2 w-2 rounded-full bg-green-400" />
                Live
                {lastUpdated && <span className="text-xs text-[var(--prism-text-muted)]">· updated {formatTimeShort(lastUpdated)}</span>}
              </>
            )}
          </div>
        </div>
      </div>

      {offline && (
        <div className="mb-4 rounded border border-amber-600/35 bg-amber-100 px-3 py-2 text-sm text-amber-700">
          Prism backend is not reachable. Retrying...
        </div>
      )}

      {error && !offline && (
        <div className="mb-4 rounded border border-red-500/35 bg-red-100 px-3 py-2 text-sm text-red-700">
          Error: {error}
        </div>
      )}

      <div className={`flex min-h-0 flex-1 gap-6 ${isNarrow ? 'flex-col overflow-y-auto' : 'flex-row overflow-hidden'}`}>
        <div className={`flex flex-col overflow-hidden rounded border border-[var(--prism-border-default)] bg-[var(--prism-bg-surface)] shadow-sm ${isNarrow ? 'h-[360px] w-full shrink-0' : 'h-full min-h-0 w-1/2'}`}>
          {calls.length === 0 && !loading ? (
            <div className="h-full px-4">
              <PrismEmptyState
                icon={Activity}
                title="No telemetry calls"
                description="Run an enforcement check to capture telemetry and inspect policy comparisons."
                actionLabel="Refresh"
                onAction={poll}
                fullHeight
              />
            </div>
          ) : (
            <>
              <div className="prism-scrollbar min-h-0 flex-1 overflow-x-auto overflow-y-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="bg-[var(--prism-bg-base)] text-xs font-medium uppercase tracking-wider text-[var(--prism-text-secondary)]">
                      <th className="border-b border-[var(--prism-border-default)] px-3 py-2.5 text-left">Time</th>
                      <th className="border-b border-[var(--prism-border-default)] px-3 py-2.5 text-left">Agent ID</th>
                      <th className="border-b border-[var(--prism-border-default)] px-3 py-2.5 text-left">Decision</th>
                      <th className="border-b border-[var(--prism-border-default)] px-3 py-2.5 text-left">Op</th>
                      <th className="border-b border-[var(--prism-border-default)] px-3 py-2.5 text-left">Target (t)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {calls.map((c) => {
                      const isSelected = c.call_id === selectedCallId;
                      const decision = c.decision ?? '—';
                      return (
                        <tr
                          key={c.call_id}
                          onClick={() => handleRowClick(c)}
                          onKeyDown={(event) => handleRowKeyDown(event, c)}
                          tabIndex={0}
                           className={`cursor-pointer border-b border-[var(--prism-border-subtle)] transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-[var(--prism-accent)]/40 ${isSelected ? 'bg-[var(--prism-accent-subtle)]' : 'hover:bg-[rgba(201,100,66,0.08)]'}`}
                        >
                          <td className="px-3 py-2.5 font-mono text-sm text-[var(--prism-text-primary)]">{formatTime(c.ts_ms)}</td>
                          <td className="px-3 py-2.5 font-mono text-sm text-[var(--prism-text-primary)]">
                            {truncate(c.agent_id)}
                            {c.is_dry_run && (
                              <span className="ml-1.5 rounded-sm border border-[var(--prism-accent)]/35 bg-[var(--prism-accent-subtle)] px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-[var(--prism-accent)]">
                                dry run
                              </span>
                            )}
                          </td>
                          <td className="px-3 py-2.5 text-[var(--prism-text-primary)]">
                            <span className={`inline-flex rounded-full border px-2 py-0.5 text-xs font-medium ${decisionBadgeStyle(decision)}`}>
                              {decision}
                            </span>
                          </td>
                          <td className="px-3 py-2.5 font-mono text-sm text-[var(--prism-text-primary)]">{c.op ?? '—'}</td>
                          <td className="px-3 py-2.5 font-mono text-sm text-[var(--prism-text-primary)]">{c.t ?? '—'}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <div className="flex items-center justify-between border-t border-[var(--prism-border-default)] px-3 py-2 text-xs text-[var(--prism-text-secondary)]">
                <button
                  onClick={() => setPage(p => Math.max(0, p - 1))}
                  disabled={page === 0}
                  className="rounded border border-[var(--prism-border-default)] px-2 py-1 text-[var(--prism-text-primary)] transition-colors hover:bg-[var(--prism-accent-subtle)] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[var(--prism-accent)]/40 disabled:cursor-not-allowed disabled:opacity-40"
                >
                  ← Prev
                </button>
                <span>Page {page + 1}</span>
                <button
                  onClick={() => setPage(p => p + 1)}
                  disabled={calls.length < 50}
                  className="rounded border border-[var(--prism-border-default)] px-2 py-1 text-[var(--prism-text-primary)] transition-colors hover:bg-[var(--prism-accent-subtle)] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[var(--prism-accent)]/40 disabled:cursor-not-allowed disabled:opacity-40"
                >
                  Next →
                </button>
              </div>
            </>
          )}
        </div>

        <div className={`${isNarrow ? 'w-full border-t border-[var(--prism-border-default)] pb-6' : 'prism-scrollbar h-full min-h-0 w-1/2 overflow-y-auto border-l border-[var(--prism-border-default)]'} bg-[var(--prism-bg-surface)] px-5 py-4`}>
          {!selectedCallId && !loadingDetail && (
            <PrismEmptyState
              icon={MousePointerClick}
              title="Select a call"
              description="Choose a telemetry row to inspect enforcement output and policy-anchor comparisons."
              fullHeight
            />
          )}

          {loadingDetail && (
            <div className="text-sm text-[var(--prism-text-secondary)]">Loading call detail...</div>
          )}

          {detailError && !loadingDetail && (
            <div className="text-sm text-red-300">Error: {detailError}</div>
          )}

          {detail && !loadingDetail && (
            <>
              <EnforcementResultPanel result={detail.enforcement_result} showTopDivider={false} />

              <div className="mt-4 border-t border-[var(--prism-border-default)] pt-3">
                {!detail.call?.intent_event ? (
                  <PrismEmptyState
                    icon={History}
                    title="Comparison unavailable"
                    description="This legacy run does not include intent-event fields required for anchor comparison."
                  />
                ) : loadingPolicies ? (
                    <div className="text-sm text-[var(--prism-text-secondary)]">Loading policy anchors...</div>
                ) : policyError ? (
                  <div className="text-sm text-red-300">Error: {policyError}</div>
                ) : Array.isArray(detail.enforcement_result?.evidence) && detail.enforcement_result.evidence.length > 0 ? (
                  <>
                    <div className="mb-3 text-sm font-semibold text-[var(--prism-text-primary)]">Intent vs Policy Anchors</div>
                    {detail.enforcement_result.evidence.map((entry, idx) => {
                      const policy = policyById[entry.boundary_id];
                      const policyMatch = policy?.match ?? {};
                      const similarities = Array.isArray(entry.similarities) ? entry.similarities : [0, 0, 0, 0];
                      const thresholds = Array.isArray(entry.thresholds) ? entry.thresholds : [0, 0, 0, 0];
                      return (
                        <div
                          key={`${entry.boundary_id || 'policy'}-${idx}`}
                           className="mb-3 rounded border border-[var(--prism-border-default)] bg-[var(--prism-bg-base)] p-4 transition-colors hover:border-[var(--prism-border-strong)] hover:bg-[var(--prism-bg-elevated)]"
                         >
                          <div className="mb-2 flex items-center justify-between gap-2">
                            <div className="text-sm font-semibold text-[var(--prism-text-primary)]">
                              {entry.boundary_name || policy?.name || entry.boundary_id || 'Policy'}
                            </div>
                            <div className="text-xs text-[var(--prism-text-secondary)]">
                              {entry.decision === 1 ? 'matched' : 'no match'}
                            </div>
                          </div>

                          {SLICE_COMPARISON_CONFIG.map((slice, sliceIdx) => {
                            const sim = similarities[sliceIdx] ?? 0;
                            const thr = thresholds[sliceIdx] ?? 0;
                            const passes = sim >= thr;
                            return (
                              <div key={slice.label} className="mt-3 border-t border-[var(--prism-border-default)] pt-3">
                                <div className="mb-1 text-xs font-medium uppercase tracking-wider text-[var(--prism-text-secondary)]">{slice.label}</div>
                                <RunAnchorComparisonPanel
                                  intentValue={detail.call.intent_event?.[slice.intentKey]}
                                  policyAnchorValue={policyMatch[slice.anchorKey]}
                                />
                                <div className="mt-2 flex items-center justify-between gap-2 text-xs text-[var(--prism-text-secondary)]">
                                  <div>Similarity {sim.toFixed(2)} / Threshold {thr.toFixed(2)}</div>
                                  <span className={`text-sm font-bold ${passes ? 'text-green-700' : 'text-red-700'}`}>{passes ? '✓' : '✗'}</span>
                                </div>
                              </div>
                            );
                          })}
                        </div>
                      );
                    })}
                  </>
                ) : (
                  <PrismEmptyState
                    icon={ShieldX}
                    title="No policy evidence"
                    description="No evidence records are available for intent vs policy-anchor comparison."
                  />
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
