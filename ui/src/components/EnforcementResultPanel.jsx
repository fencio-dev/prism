const SLICE_LABELS = ['action', 'resource', 'data', 'risk'];

const BADGE_STYLES = {
  ALLOW:   { background: '#d4edda', color: '#155724' },
  DENY:    { background: '#f8d7da', color: '#721c24' },
  MODIFY:  { background: '#fff3cd', color: '#856404' },
  STEP_UP: { background: '#cce5ff', color: '#004085' },
  DEFER:   { background: '#e2e3e5', color: '#383d41' },
};

const styles = {
  panel: {
    borderTop: '2px solid #e8e8e8',
    marginTop: 28,
    paddingTop: 24,
  },
  panelNoDivider: {
    borderTop: 'none',
    marginTop: 0,
    paddingTop: 0,
  },
  panelTitle: {
    fontSize: 14,
    fontWeight: 600,
    color: '#333',
    marginBottom: 16,
  },
  badge: {
    display: 'inline-block',
    fontSize: 20,
    fontWeight: 700,
    padding: '8px 24px',
    borderRadius: 6,
    letterSpacing: 1,
    marginBottom: 16,
  },
  summaryRow: {
    display: 'flex',
    gap: 32,
    flexWrap: 'wrap',
    marginBottom: 20,
  },
  summaryItem: {
    display: 'flex',
    flexDirection: 'column',
    gap: 2,
  },
  summaryLabel: {
    fontSize: 11,
    fontWeight: 600,
    color: '#888',
    textTransform: 'uppercase',
    letterSpacing: 0.5,
  },
  summaryValue: {
    fontSize: 13,
    color: '#1a1a1a',
    fontFamily: 'monospace',
  },
  tableWrapper: {
    overflowX: 'auto',
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
    fontFamily: 'monospace',
    fontSize: 12,
  },
  sectionTitle: {
    fontSize: 13,
    fontWeight: 600,
    color: '#333',
    marginBottom: 10,
    marginTop: 20,
  },
  barLabelRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 10,
    marginBottom: 6,
  },
  barLabel: {
    fontSize: 12,
    color: '#555',
    fontFamily: 'monospace',
    width: 60,
    flexShrink: 0,
  },
  barContainer: {
    flex: 1,
    background: '#e8e8e8',
    borderRadius: 3,
    height: 10,
    overflow: 'hidden',
    position: 'relative',
  },
  barValue: {
    fontSize: 12,
    color: '#1a1a1a',
    fontFamily: 'monospace',
    width: 80,
    textAlign: 'right',
    flexShrink: 0,
  },
  preBlock: {
    background: '#f5f5f5',
    border: '1px solid #ddd',
    borderRadius: 4,
    padding: '12px 16px',
    fontSize: 12,
    fontFamily: 'monospace',
    overflowX: 'auto',
    whiteSpace: 'pre-wrap',
    wordBreak: 'break-all',
    marginTop: 8,
  },
};

function DecisionBadge({ decision }) {
  const colors = BADGE_STYLES[decision] ?? { background: '#e2e3e5', color: '#383d41' };
  return (
    <div style={{ ...styles.badge, ...colors }}>
      {decision ?? '—'}
    </div>
  );
}

function SliceBars({ similarities, thresholds }) {
  if (!Array.isArray(similarities)) return null;
  const resolvedThresholds =
    Array.isArray(thresholds) && thresholds.length === 4
      ? thresholds
      : [0, 0, 0, 0];
  return (
    <div>
      {SLICE_LABELS.map((label, i) => {
        const value = similarities[i] ?? 0;
        const threshold = resolvedThresholds[i] ?? 0;
        const pct = Math.min(Math.max(value, 0), 1) * 100;
        let barColor = '#4a90d9';
        if (threshold > 0) {
          barColor = value >= threshold ? '#2e7d32' : '#c62828';
        }
        return (
          <div key={label} style={styles.barLabelRow}>
            <span style={styles.barLabel}>{label}</span>
            <div style={styles.barContainer}>
              <div style={{ width: `${pct}%`, height: '100%', background: barColor, borderRadius: 3 }} />
              {threshold > 0 && (
                <div style={{
                  position: 'absolute',
                  left: `${Math.min(threshold, 1) * 100}%`,
                  top: 0,
                  bottom: 0,
                  width: 2,
                  background: 'rgba(0,0,0,0.4)',
                  borderRadius: 1,
                }} />
              )}
            </div>
            <span style={styles.barValue}>
              {threshold > 0 ? `${value.toFixed(2)} / ${threshold.toFixed(2)}` : value.toFixed(2)}
            </span>
          </div>
        );
      })}
    </div>
  );
}

function EvidenceTable({ evidence, policyMap }) {
  if (!Array.isArray(evidence) || evidence.length === 0) return null;
  return (
    <div style={styles.tableWrapper}>
      <table style={styles.table}>
        <thead>
          <tr>
            <th style={styles.th}>Policy Name</th>
            <th style={styles.th}>Effect</th>
            <th style={styles.th}>Match</th>
            <th style={styles.th}>Triggering Slice</th>
            <th style={styles.th}>Scoring Mode</th>
            <th style={styles.th}>Similarities / Thresholds</th>
          </tr>
        </thead>
        <tbody>
          {evidence.map((entry, i) => {
            const sims = Array.isArray(entry.similarities) ? entry.similarities : [];
            const thresholds = Array.isArray(entry.thresholds) && entry.thresholds.length === 4
              ? entry.thresholds
              : [0, 0, 0, 0];
            const policyName = policyMap?.[entry.boundary_id];
            const scoringMode = typeof entry.scoring_mode === 'string' ? entry.scoring_mode.trim() : '';
            return (
              <tr key={i}>
                <td style={styles.td}>
                  {policyName && (
                    <div>{policyName}</div>
                  )}
                  {entry.boundary_name && (
                    <div style={{ color: '#999', fontSize: 11, marginTop: policyName ? 2 : 0 }}>
                      {entry.boundary_name}
                    </div>
                  )}
                  {!policyName && !entry.boundary_name && (entry.boundary_id || '—')}
                </td>
                <td style={styles.td}>{entry.effect ?? '—'}</td>
                <td style={styles.td}>{entry.decision === 1 ? 'matched' : 'no match'}</td>
                <td style={styles.td}>{entry.triggering_slice ?? '—'}</td>
                <td style={styles.td}>
                  {scoringMode ? (
                    <span style={{
                      display: 'inline-block',
                      fontSize: 10,
                      fontFamily: 'monospace',
                      fontWeight: 600,
                      padding: '2px 6px',
                      borderRadius: 3,
                      background: '#f0f0f0',
                      color: '#555',
                      whiteSpace: 'nowrap',
                      letterSpacing: 0.2,
                    }}>
                      {scoringMode}
                    </span>
                  ) : '—'}
                </td>
                <td style={{ ...styles.td, minWidth: 160 }}>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
                    {SLICE_LABELS.map((label, idx) => {
                      const sim = sims[idx] != null ? sims[idx] : null;
                      const thr = thresholds[idx] ?? 0;
                      const pass = sim !== null && thr > 0 && sim >= thr;
                      const fail = sim !== null && thr > 0 && sim < thr;
                      return (
                        <div key={label} style={{ display: 'flex', alignItems: 'center', gap: 6, whiteSpace: 'nowrap' }}>
                          <span style={{ fontSize: 10, color: '#999', width: 52, flexShrink: 0 }}>{label}</span>
                          <span style={{ fontSize: 11, fontFamily: 'monospace', color: '#1a1a1a' }}>
                            {sim !== null ? sim.toFixed(2) : '—'}
                            {thr > 0 && <span style={{ color: '#bbb' }}> / {thr.toFixed(2)}</span>}
                          </span>
                          {pass && <span style={{ fontSize: 10, color: '#2e7d32', fontWeight: 700 }}>✓</span>}
                          {fail && <span style={{ fontSize: 10, color: '#c62828', fontWeight: 700 }}>✗</span>}
                        </div>
                      );
                    })}
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export default function EnforcementResultPanel({ result, policies, showTopDivider = true }) {
  if (!result) return null;

  const {
    decision,
    drift_score,
    drift_triggered,
    slice_similarities,
    evidence,
    modified_params,
  } = result;

  const policyMap = Array.isArray(policies)
    ? Object.fromEntries(policies.map((p) => [p.id, p.name]))
    : {};

  return (
    <div style={showTopDivider ? styles.panel : styles.panelNoDivider}>
      <div style={styles.panelTitle}>Result</div>

      <DecisionBadge decision={decision} />

      <div style={styles.summaryRow}>
        <div style={styles.summaryItem}>
          <span style={styles.summaryLabel}>Drift Score</span>
          <span style={styles.summaryValue}>
            {drift_score != null ? drift_score.toFixed(4) : '—'}
          </span>
        </div>
        <div style={styles.summaryItem}>
          <span style={styles.summaryLabel}>Drift Triggered</span>
          <span style={styles.summaryValue}>{drift_triggered ? 'yes' : 'no'}</span>
        </div>
      </div>

      <div style={styles.sectionTitle}>Slice Similarities (aggregate)</div>
      <SliceBars similarities={slice_similarities} />

      <div style={styles.sectionTitle}>Evidence (per policy)</div>
      <EvidenceTable evidence={evidence} policyMap={policyMap} />

      {modified_params != null && (
        <>
          <div style={styles.sectionTitle}>Modified Params</div>
          <pre style={styles.preBlock}>{JSON.stringify(modified_params, null, 2)}</pre>
        </>
      )}
    </div>
  );
}
