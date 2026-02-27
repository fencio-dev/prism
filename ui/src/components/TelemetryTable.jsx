import { useState, useEffect, useCallback } from 'react';
import { fetchCalls, fetchCallDetail, deleteCalls } from '../api/telemetry';
import { getPolicy } from '../api/policies';
import EnforcementResultPanel from './EnforcementResultPanel';
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
  ALLOW:   { background: '#d4edda', color: '#155724' },
  DENY:    { background: '#f8d7da', color: '#721c24' },
  MODIFY:  { background: '#fff3cd', color: '#856404' },
  STEP_UP: { background: '#cce5ff', color: '#004085' },
  DEFER:   { background: '#e2e3e5', color: '#383d41' },
};

const SLICE_COMPARISON_CONFIG = [
  { label: 'Action', intentKey: 'op', anchorKey: 'op' },
  { label: 'Resource', intentKey: 't', anchorKey: 't' },
  { label: 'Data', intentKey: 'p', anchorKey: 'p' },
  { label: 'Risk', intentKey: 'ctx', anchorKey: 'ctx' },
];

const styles = {
  header: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: 16,
  },
  heading: {
    fontSize: 15,
    fontWeight: 600,
    color: '#1a1a1a',
    display: 'flex',
    alignItems: 'center',
    gap: 10,
  },
  totalCount: {
    fontSize: 13,
    color: '#888',
    fontWeight: 400,
  },
  statusArea: {
    display: 'flex',
    alignItems: 'center',
    gap: 6,
    fontSize: 13,
    color: '#555',
  },
  dot: (color) => ({
    display: 'inline-block',
    width: 8,
    height: 8,
    borderRadius: '50%',
    background: color,
    flexShrink: 0,
  }),
  offlineBanner: {
    background: '#fff3cd',
    border: '1px solid #ffc107',
    borderRadius: 4,
    padding: '10px 14px',
    fontSize: 13,
    color: '#856404',
    marginBottom: 16,
  },
  tableWrapper: {
    overflowX: 'auto',
    overflowY: 'auto',
    height: '100%',
  },
  layout: (isNarrow) => ({
    display: 'flex',
    flexDirection: isNarrow ? 'column' : 'row',
    gap: 16,
  }),
  leftPanel: (isNarrow) => ({
    width: isNarrow ? '100%' : '50%',
    height: isNarrow ? 360 : 'calc(100vh - 240px)',
    border: '1px solid #e6e6e6',
    borderRadius: 6,
    overflow: 'hidden',
    background: '#fff',
  }),
  rightPanel: (isNarrow) => ({
    width: isNarrow ? '100%' : '50%',
    height: isNarrow ? 'auto' : 'calc(100vh - 240px)',
    border: '1px solid #e6e6e6',
    borderRadius: 6,
    padding: 12,
    overflowY: 'auto',
    background: '#fff',
  }),
  panelTitle: {
    fontSize: 13,
    fontWeight: 600,
    color: '#333',
    marginBottom: 10,
  },
  panelMessage: {
    fontSize: 12,
    color: '#666',
    lineHeight: 1.4,
  },
  comparisonCard: {
    border: '1px solid #eee',
    borderRadius: 6,
    padding: 10,
    marginBottom: 10,
    background: '#fafafa',
  },
  comparisonHeader: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 8,
    marginBottom: 8,
  },
  comparisonName: {
    fontSize: 12,
    fontWeight: 600,
    color: '#222',
  },
  comparisonStatus: {
    fontSize: 11,
    color: '#666',
  },
  sliceCard: {
    borderTop: '1px solid #ececec',
    paddingTop: 8,
    marginTop: 8,
  },
  sliceLabel: {
    fontSize: 11,
    color: '#777',
    textTransform: 'uppercase',
    letterSpacing: 0.5,
    marginBottom: 4,
  },
  sliceMetrics: {
    marginTop: 4,
    fontSize: 11,
    color: '#666',
  },
  sliceMetricsRow: {
    marginTop: 4,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 8,
  },
  sliceCheck: (passes) => ({
    fontSize: 14,
    fontWeight: 700,
    color: passes ? '#166534' : '#991b1b',
    lineHeight: 1,
  }),
  sectionDivider: {
    borderTop: '1px solid #ececec',
    marginTop: 14,
    paddingTop: 10,
  },
  table: {
    width: '100%',
    borderCollapse: 'collapse',
    fontSize: 13,
  },
  th: {
    textAlign: 'left',
    padding: '8px 12px',
    background: '#f5f5f5',
    borderBottom: '1px solid #ddd',
    fontWeight: 600,
    color: '#555',
    fontSize: 12,
  },
  td: {
    padding: '8px 12px',
    borderBottom: '1px solid #eee',
    color: '#1a1a1a',
    fontSize: 12,
    fontFamily: 'monospace',
  },
  badge: {
    display: 'inline-block',
    fontSize: 11,
    fontWeight: 700,
    padding: '2px 8px',
    borderRadius: 4,
    letterSpacing: 0.5,
  },
  emptyState: {
    textAlign: 'center',
    color: '#888',
    fontSize: 13,
    fontStyle: 'italic',
    padding: '24px 0',
  },
  dryRunPill: {
    display: 'inline-block',
    fontSize: 10,
    fontWeight: 600,
    padding: '1px 6px',
    borderRadius: 3,
    background: '#e8f0fe',
    color: '#1a56db',
    marginLeft: 6,
    letterSpacing: 0.3,
  },
};

function decisionBadgeStyle(decision) {
  const colors = DECISION_COLORS[decision] ?? { background: '#e2e3e5', color: '#383d41' };
  return { ...styles.badge, ...colors };
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

  return (
    <div>
      <div style={styles.header}>
        <div style={styles.heading}>
          Telemetry
          <span style={styles.totalCount}>{totalCount} calls</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <button onClick={handleClearAll} style={{ fontSize: 12, padding: '4px 10px', cursor: 'pointer' }}>
            Clear All
          </button>
          <div style={styles.statusArea}>
          {offline ? (
            <>
              <span style={styles.dot('red')} />
              Guard not running
            </>
          ) : loading ? (
            'Loading...'
          ) : (
            <>
              <span style={styles.dot('green')} />
              Live
              {lastUpdated && (
                <span style={{ color: '#aaa' }}>· last updated {formatTimeShort(lastUpdated)}</span>
              )}
            </>
          )}
          </div>
        </div>
      </div>

      {offline && (
        <div style={styles.offlineBanner}>
          Guard backend is not reachable. Retrying...
        </div>
      )}

      {error && !offline && (
        <div style={{ ...styles.offlineBanner, background: '#f8d7da', borderColor: '#f5c6cb', color: '#721c24' }}>
          Error: {error}
        </div>
      )}

      <div style={styles.layout(isNarrow)}>
        <div style={styles.leftPanel(isNarrow)}>
          <div style={styles.tableWrapper}>
            <table style={styles.table}>
              <thead>
                <tr>
                  <th style={styles.th}>Time</th>
                  <th style={styles.th}>Agent ID</th>
                  <th style={styles.th}>Decision</th>
                  <th style={styles.th}>Op</th>
                  <th style={styles.th}>Target (t)</th>
                </tr>
              </thead>
              <tbody>
                {calls.length === 0 ? (
                  <tr>
                    <td colSpan={5} style={styles.emptyState}>
                      No calls recorded yet.
                    </td>
                  </tr>
                ) : (
                  calls.map((c) => {
                    const isSelected = c.call_id === selectedCallId;
                    const decision = c.decision ?? '—';
                    const badge = decisionBadgeStyle(decision);
                    return (
                      <tr
                        key={c.call_id}
                        onClick={() => handleRowClick(c)}
                        style={{ cursor: 'pointer', background: isSelected ? '#e8f0fe' : undefined }}
                        onMouseEnter={(e) => {
                          if (!isSelected) e.currentTarget.style.background = '#f5f5f5';
                        }}
                        onMouseLeave={(e) => {
                          e.currentTarget.style.background = isSelected ? '#e8f0fe' : '';
                        }}
                      >
                        <td style={styles.td}>{formatTime(c.ts_ms)}</td>
                        <td style={styles.td}>
                          {truncate(c.agent_id)}
                          {c.is_dry_run && <span style={styles.dryRunPill}>dry run</span>}
                        </td>
                        <td style={styles.td}>
                          <span style={badge}>{decision}</span>
                        </td>
                        <td style={styles.td}>{c.op ?? '—'}</td>
                        <td style={styles.td}>{c.t ?? '—'}</td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '8px 12px', borderTop: '1px solid #eee', fontSize: 12, color: '#555' }}>
            <button onClick={() => setPage(p => Math.max(0, p - 1))} disabled={page === 0} style={{ cursor: 'pointer', padding: '2px 8px' }}>
              ← Prev
            </button>
            <span>Page {page + 1}</span>
            <button onClick={() => setPage(p => p + 1)} disabled={calls.length < 50} style={{ cursor: 'pointer', padding: '2px 8px' }}>
              Next →
            </button>
          </div>
        </div>

        <div style={styles.rightPanel(isNarrow)}>
          {!selectedCallId && !loadingDetail && (
            <div style={styles.panelMessage}>Select a call to inspect comparison details.</div>
          )}

          {loadingDetail && (
            <div style={styles.panelMessage}>Loading call detail...</div>
          )}

          {detailError && !loadingDetail && (
            <div style={{ ...styles.panelMessage, color: '#721c24' }}>Error: {detailError}</div>
          )}

          {detail && !loadingDetail && (
            <>
              <EnforcementResultPanel result={detail.enforcement_result} showTopDivider={false} />

              <div style={styles.sectionDivider}>
                {!detail.call?.intent_event ? (
                  <div style={styles.panelMessage}>
                    Detailed intent vs policy-anchor comparison is unavailable for this legacy run (missing `call.intent_event`).
                  </div>
                ) : loadingPolicies ? (
                  <div style={styles.panelMessage}>Loading policy anchors...</div>
                ) : policyError ? (
                  <div style={{ ...styles.panelMessage, color: '#721c24' }}>Error: {policyError}</div>
                ) : Array.isArray(detail.enforcement_result?.evidence) && detail.enforcement_result.evidence.length > 0 ? (
                  <>
                    <div style={styles.panelTitle}>Intent vs Policy Anchors</div>
                    {detail.enforcement_result.evidence.map((entry, idx) => {
                      const policy = policyById[entry.boundary_id];
                      const policyMatch = policy?.match ?? {};
                      const similarities = Array.isArray(entry.similarities) ? entry.similarities : [0, 0, 0, 0];
                      const thresholds = Array.isArray(entry.thresholds) ? entry.thresholds : [0, 0, 0, 0];
                      return (
                        <div key={`${entry.boundary_id || 'policy'}-${idx}`} style={styles.comparisonCard}>
                          <div style={styles.comparisonHeader}>
                            <div style={styles.comparisonName}>
                              {entry.boundary_name || policy?.name || entry.boundary_id || 'Policy'}
                            </div>
                            <div style={styles.comparisonStatus}>
                              {entry.decision === 1 ? 'matched' : 'no match'}
                            </div>
                          </div>

                          {SLICE_COMPARISON_CONFIG.map((slice, sliceIdx) => {
                            const sim = similarities[sliceIdx] ?? 0;
                            const thr = thresholds[sliceIdx] ?? 0;
                            const passes = sim >= thr;
                            return (
                              <div key={slice.label} style={styles.sliceCard}>
                                <div style={styles.sliceLabel}>{slice.label}</div>
                                <RunAnchorComparisonPanel
                                  intentValue={detail.call.intent_event?.[slice.intentKey]}
                                  policyAnchorValue={policyMatch[slice.anchorKey]}
                                />
                                <div style={styles.sliceMetricsRow}>
                                  <div style={styles.sliceMetrics}>
                                    Similarity {sim.toFixed(2)} / Threshold {thr.toFixed(2)}
                                  </div>
                                  <span style={styles.sliceCheck(passes)}>{passes ? '✓' : '✗'}</span>
                                </div>
                              </div>
                            );
                          })}
                        </div>
                      );
                    })}
                  </>
                ) : (
                  <div style={styles.panelMessage}>No policy evidence available for comparison.</div>
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
