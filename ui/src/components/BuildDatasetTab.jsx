import { useState, useEffect, useCallback } from 'react';
import { fetchCalls, deleteCalls, fetchCallDetail } from '../api/telemetry';
import { getPolicy } from '../api/policies';
import RunAnchorComparisonPanel from './RunAnchorComparisonPanel';

const BADGE_COLORS = {
  ALLOW:   { background: '#d4edda', color: '#155724' },
  DENY:    { background: '#f8d7da', color: '#721c24' },
  MODIFY:  { background: '#fff3cd', color: '#856404' },
  STEP_UP: { background: '#cce5ff', color: '#004085' },
  DEFER:   { background: '#e2e3e5', color: '#383d41' },
};

function truncate(str, max) {
  if (!str) return '';
  return str.length > max ? str.slice(0, max) + '…' : str;
}

function formatTs(ts) {
  if (!ts) return '—';
  try {
    return new Date(ts).toLocaleString();
  } catch {
    return ts;
  }
}

export default function BuildDatasetTab() {
  const [calls, setCalls] = useState([]);
  const [totalCount, setTotalCount] = useState(0);
  const [selectedCall, setSelectedCall] = useState(null);
  const [selectedSummary, setSelectedSummary] = useState(null);

  const poll = useCallback(async () => {
    try {
      const data = await fetchCalls({ limit: 50, offset: 0 });
      setCalls(data?.calls ?? []);
      setTotalCount(data?.total_count ?? 0);
    } catch {
      // fail soft — keep existing data
    }
  }, []);

  useEffect(() => {
    poll();
    const id = setInterval(poll, 5000);
    return () => clearInterval(id);
  }, [poll]);

  async function handleRowClick(c) {
    setSelectedSummary(c);
    setSelectedCall(null);
    try {
      const detail = await fetchCallDetail(c.call_id);
      // Merge summary fields (including is_dry_run) with the detail's call + enforcement_result
      setSelectedCall({ ...detail.call, enforcement_result: detail.enforcement_result });
    } catch {
      // fall back to summary with no evidence
      setSelectedCall(c);
    }
  }

  async function handleClearAll() {
    if (!window.confirm('Delete all calls? This cannot be undone.')) return;
    await deleteCalls();
    setCalls([]);
    setTotalCount(0);
    setSelectedCall(null);
    setSelectedSummary(null);
    poll();
  }

  return (
    <div style={{
      height: 'calc(100vh - 120px)',
      display: 'flex',
      overflow: 'hidden',
      borderRadius: 8,
      fontFamily: 'monospace',
    }}>
      {/* Left panel — run list */}
      <div style={{
        width: 260,
        flexShrink: 0,
        borderRight: '1px solid #e0e0e0',
        overflowY: 'auto',
        height: '100%',
        background: '#fafafa',
        display: 'flex',
        flexDirection: 'column',
      }}>
        <div style={{
          padding: '12px 14px 10px',
          borderBottom: '1px solid #e0e0e0',
          fontSize: 13,
          fontWeight: 600,
          color: '#333',
          letterSpacing: 0.2,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
        }}>
          <span>Recent Calls <span style={{ fontWeight: 400, color: '#999', fontSize: 11 }}>{totalCount}</span></span>
          <button
            onClick={handleClearAll}
            style={{ fontSize: 11, padding: '2px 8px', cursor: 'pointer', border: '1px solid #ddd', borderRadius: 3, background: '#fff', color: '#555' }}
          >
            Clear All
          </button>
        </div>

        {calls.length === 0 ? (
          <div style={{ padding: '20px 14px', fontSize: 13, color: '#999' }}>
            No recent calls found.
          </div>
        ) : (
          calls.map((c) => {
            const isSelected = selectedSummary?.call_id === c.call_id;
            const badgeColors = BADGE_COLORS[c.decision] ?? BADGE_COLORS.DEFER;
            return (
              <div
                key={c.call_id}
                onClick={() => handleRowClick(c)}
                style={{
                  padding: '9px 12px',
                  borderBottom: '1px solid #eee',
                  cursor: 'pointer',
                  background: isSelected ? '#eef4ff' : 'transparent',
                  borderLeft: isSelected ? '3px solid #2563eb' : '3px solid transparent',
                }}
              >
                <div style={{ fontSize: 11, color: '#888', marginBottom: 3, display: 'flex', alignItems: 'center', gap: 6 }}>
                  {formatTs(c.ts_ms)}
                  {c.is_dry_run && (
                    <span style={{
                      fontSize: 9,
                      fontWeight: 600,
                      padding: '1px 5px',
                      borderRadius: 3,
                      background: '#e8f0fe',
                      color: '#1a56db',
                      letterSpacing: 0.3,
                    }}>
                      dry run
                    </span>
                  )}
                </div>
                <div style={{
                  fontSize: 12,
                  color: '#1a1a1a',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                  marginBottom: 2,
                }}>
                  {truncate(c.op, 36)}
                </div>
                <div style={{
                  fontSize: 11,
                  color: '#666',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                  marginBottom: 5,
                }}>
                  {truncate(c.t, 36)}
                </div>
                <span style={{
                  fontSize: 10,
                  fontWeight: 600,
                  padding: '2px 7px',
                  borderRadius: 4,
                  letterSpacing: 0.3,
                  ...badgeColors,
                }}>
                  {c.decision ?? '—'}
                </span>
              </div>
            );
          })
        )}
      </div>

      {/* Right panel — details or placeholder */}
      <div style={{ flex: 1, overflowY: 'auto', overflowX: 'auto', height: '100%', padding: '20px 24px' }}>
        {selectedCall === null ? (
          <div style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            height: '100%',
            color: '#aaa',
            fontSize: 14,
          }}>
            Select a call to compare slices
          </div>
        ) : (
          <RunDetail call={selectedCall} />
        )}
      </div>
    </div>
  );
}

const SLICE_NAMES = ['Action', 'Resource', 'Data', 'Risk'];

// Maps slice index to the formSnapshot key and policy match key
const SLICE_INTENT_KEYS = ['op', 't', 'p', 'ctxInitialRequest'];
const SLICE_ANCHOR_KEYS = ['op', 't', 'p', 'ctx'];

function worstSliceIndex(similarities, thresholds) {
  let worstIdx = 0;
  let worstMargin = Infinity;
  for (let i = 0; i < 4; i++) {
    const sim = similarities?.[i] ?? 0;
    const thr = thresholds?.[i] ?? 0;
    const margin = sim - thr;
    if (margin < worstMargin) {
      worstMargin = margin;
      worstIdx = i;
    }
  }
  return worstIdx;
}

function exportFeedbackAsJSONL(call, evidence, policies, snap, feedback) {
  const entries = [];

  for (const item of evidence) {
    const policyMatch = policies[item.boundary_id]?.match ?? {};
    const similarities = item.similarities ?? [0, 0, 0, 0];
    const thresholds = item.thresholds ?? [0, 0, 0, 0];

    for (let sliceIdx = 0; sliceIdx < 4; sliceIdx++) {
      const key = `${item.boundary_id}:${sliceIdx}`;
      const entry = feedback[key];
      if (!entry || entry.score === null || entry.score === undefined) continue;

      entries.push({
        call_id: call.call_id,
        ts: call.ts_ms,
        boundary_id: item.boundary_id,
        boundary_name: item.boundary_name,
        slice_index: sliceIdx,
        slice_label: SLICE_NAMES[sliceIdx].toLowerCase(),
        intent_text: snap[SLICE_INTENT_KEYS[sliceIdx]] || '',
        anchor_text: policyMatch[SLICE_ANCHOR_KEYS[sliceIdx]] || '',
        similarity: similarities[sliceIdx] ?? 0,
        threshold: thresholds[sliceIdx] ?? 0,
        feedback_score: entry.score,
        rationale: entry.rationale || '',
      });
    }
  }

  const jsonl = entries.map(obj => JSON.stringify(obj)).join('\n');
  const blob = new Blob([jsonl], { type: 'application/jsonl' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `guard-dataset-${Date.now()}.jsonl`;
  a.click();
  URL.revokeObjectURL(url);
}

function SliceCell({ intent, anchor, similarity, threshold, isWorst, feedbackKey, feedbackValue, onFeedbackChange }) {
  const passes = similarity >= threshold;

  const currentScore = feedbackValue?.score ?? null;
  const currentRationale = feedbackValue?.rationale ?? '';

  // Slider internal value: 0 means "not set", otherwise -100 to 100 (excluding 0)
  // Derive slider display value from stored score
  const sliderDisplayValue = currentScore !== null ? Math.round(currentScore * 100) : 0;

  // Number input display value: show current score as string, or empty if null
  const numberInputValue = currentScore !== null ? String(currentScore) : '';

  function handleSliderChange(e) {
    const raw = parseInt(e.target.value, 10);
    if (raw === 0) {
      // Treat 0 as clearing the score
      onFeedbackChange(feedbackKey, null, currentRationale);
    } else {
      onFeedbackChange(feedbackKey, raw / 100, currentRationale);
    }
  }

  function handleNumberChange(e) {
    const raw = e.target.value;
    if (raw === '' || raw === '-') {
      onFeedbackChange(feedbackKey, null, currentRationale);
      return;
    }
    const parsed = parseFloat(raw);
    if (isNaN(parsed)) {
      onFeedbackChange(feedbackKey, null, currentRationale);
    } else {
      onFeedbackChange(feedbackKey, parsed, currentRationale);
    }
  }

  function handleNumberBlur(e) {
    const raw = e.target.value;
    if (raw === '' || raw === '-') {
      onFeedbackChange(feedbackKey, null, currentRationale);
      return;
    }
    const parsed = parseFloat(raw);
    if (isNaN(parsed) || parsed === 0) {
      onFeedbackChange(feedbackKey, null, currentRationale);
      return;
    }
    // Clamp to [-1, 1], reject 0
    const clamped = Math.min(1, Math.max(-1, parsed));
    const final = clamped === 0 ? null : clamped;
    onFeedbackChange(feedbackKey, final, currentRationale);
  }

  function handleRationaleChange(e) {
    onFeedbackChange(feedbackKey, currentScore, e.target.value);
  }

  const scoreLabel = sliderDisplayValue === 0
    ? 'none'
    : (sliderDisplayValue > 0 ? `+${(sliderDisplayValue / 100).toFixed(2)}` : `${(sliderDisplayValue / 100).toFixed(2)}`);

  const scoreLabelColor = sliderDisplayValue === 0
    ? '#aaa'
    : sliderDisplayValue > 0
      ? '#166534'
      : '#991b1b';

  return (
    <td style={{
      padding: '10px 12px',
      borderTop: isWorst ? '2px solid #f59e0b' : '1px solid #eee',
      borderRight: '1px solid #eee',
      borderBottom: '1px solid #eee',
      borderLeft: '1px solid #eee',
      verticalAlign: 'top',
      background: 'transparent',
      wordBreak: 'break-word',
      overflowWrap: 'break-word',
    }}>
      <RunAnchorComparisonPanel intentValue={intent} policyAnchorValue={anchor} />
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12 }}>
        <span style={{ fontWeight: 600, color: '#1a1a1a' }}>
          {similarity.toFixed(2)}
        </span>
        <span style={{ color: '#888' }}>
          / {threshold.toFixed(2)}
        </span>
        <span style={{
          fontWeight: 700,
          fontSize: 13,
          color: passes ? '#166534' : '#991b1b',
        }}>
          {passes ? '✓' : '✗'}
        </span>
      </div>

      <hr style={{ margin: '8px 0', borderColor: '#eee' }} />

      <div>
        <div style={{ marginBottom: 4 }}>
          <input
            type="range"
            min="-100"
            max="100"
            step="1"
            value={sliderDisplayValue}
            onChange={handleSliderChange}
            style={{ width: '100%', cursor: 'pointer', display: 'block' }}
          />
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
          <input
            type="number"
            min="-1"
            max="1"
            step="0.01"
            value={numberInputValue}
            onChange={handleNumberChange}
            onBlur={handleNumberBlur}
            style={{
              width: 48,
              fontSize: 11,
              fontFamily: 'monospace',
              border: '1px solid #ddd',
              borderRadius: 3,
              padding: '2px 4px',
              textAlign: 'right',
            }}
          />
          <span style={{ fontSize: 10, color: scoreLabelColor, fontWeight: 600, minWidth: 28, textAlign: 'right' }}>
            {scoreLabel}
          </span>
        </div>
        {sliderDisplayValue !== 0 && (
          <textarea
            placeholder="Why? (optional)"
            value={currentRationale}
            onChange={handleRationaleChange}
            style={{
              width: '100%',
              height: 40,
              resize: 'none',
              borderRadius: 3,
              border: '1px solid #ddd',
              padding: '4px 6px',
              fontFamily: 'monospace',
              fontSize: 10,
              boxSizing: 'border-box',
            }}
          />
        )}
      </div>
    </td>
  );
}

function RunDetail({ call }) {
  const [policies, setPolicies] = useState({});
  const [loading, setLoading] = useState(true);
  const [feedback, setFeedback] = useState({});

  // Build a snap-like object from call fields for slice intent lookup.
  // p and ctx are not top-level DB columns — they live inside intent_event.
  const snap = {
    op: call.op ?? call.intent_event?.op ?? '',
    t:  call.t  ?? call.intent_event?.t  ?? '',
    p:  call.intent_event?.p ?? '',
    ctxInitialRequest: call.intent_event?.ctx?.initial_request ?? '',
  };
  const evidence = call.enforcement_result?.evidence ?? [];
  const badgeColors = BADGE_COLORS[call.decision] ?? BADGE_COLORS.DEFER;

  useEffect(() => {
    setPolicies({});
    setLoading(true);
    setFeedback({});

    if (evidence.length === 0) {
      setLoading(false);
      return;
    }

    const uniqueIds = [...new Set(evidence.map(e => e.boundary_id).filter(Boolean))];

    Promise.all(
      uniqueIds.map(id => getPolicy(id).then(data => ({ id, data })))
    ).then(results => {
      const map = {};
      for (const { id, data } of results) {
        map[id] = data;
      }
      setPolicies(map);
      setLoading(false);
    });
  }, [call]);

  const hasFeedback = Object.values(feedback).some(f => f?.score !== null && f?.score !== undefined);

  return (
    <div>
      {/* Header */}
      <div style={{ marginBottom: 18, display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <span style={{ fontSize: 12, color: '#888' }}>
            {call.ts_ms ? new Date(call.ts_ms).toLocaleString() : '—'}
          </span>
          <span style={{
            fontSize: 11,
            fontWeight: 600,
            padding: '2px 8px',
            borderRadius: 4,
            letterSpacing: 0.3,
            ...badgeColors,
          }}>
            {call.decision ?? '—'}
          </span>
          {call.is_dry_run && (
            <span style={{
              fontSize: 10,
              fontWeight: 600,
              padding: '1px 6px',
              borderRadius: 3,
              background: '#e8f0fe',
              color: '#1a56db',
              letterSpacing: 0.3,
            }}>
              dry run
            </span>
          )}
          {snap.op && (
            <span style={{ fontSize: 12, color: '#444', fontFamily: 'monospace', background: '#f5f5f5', padding: '2px 6px', borderRadius: 3 }}>
              {snap.op}
            </span>
          )}
          {snap.t && (
            <span style={{ fontSize: 12, color: '#666', fontFamily: 'monospace', background: '#f5f5f5', padding: '2px 6px', borderRadius: 3 }}>
              {snap.t}
            </span>
          )}
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button
            onClick={() => setFeedback({})}
            disabled={!hasFeedback}
            style={{
              border: '1px solid #e0e0e0',
              background: 'white',
              padding: '4px 10px',
              fontSize: 12,
              borderRadius: 4,
              cursor: hasFeedback ? 'pointer' : 'not-allowed',
              fontFamily: 'monospace',
              color: hasFeedback ? '#991b1b' : '#aaa',
            }}
          >
            Reset All
          </button>
          <button
            onClick={() => exportFeedbackAsJSONL(call, evidence, policies, snap, feedback)}
            disabled={!hasFeedback}
            style={{
              border: '1px solid #ccc',
              background: 'white',
              padding: '4px 10px',
              fontSize: 12,
              borderRadius: 4,
              cursor: hasFeedback ? 'pointer' : 'not-allowed',
              fontFamily: 'monospace',
              color: hasFeedback ? '#1a1a1a' : '#aaa',
            }}
          >
            Export Dataset
          </button>
        </div>
      </div>

      {loading ? (
        <div style={{ color: '#aaa', fontSize: 13, padding: '24px 0' }}>
          Loading policies...
        </div>
      ) : evidence.length === 0 ? (
        <div style={{ color: '#aaa', fontSize: 13, padding: '24px 0' }}>
          No evidence items for this run.
        </div>
      ) : (
        <table style={{ borderCollapse: 'collapse', fontSize: 12, fontFamily: 'monospace', width: '100%', tableLayout: 'fixed', minWidth: 900 }}>
          <thead>
            <tr style={{ background: '#f5f5f5' }}>
              <th style={{
                padding: '10px 12px',
                border: '1px solid #eee',
                textAlign: 'left',
                fontSize: 11,
                fontWeight: 600,
                letterSpacing: 0.8,
                color: '#555',
                textTransform: 'uppercase',
                width: 180,
              }}>
                Policy
              </th>
              {SLICE_NAMES.map(name => (
                <th key={name} style={{
                  padding: '10px 12px',
                  border: '1px solid #eee',
                  textAlign: 'left',
                  fontSize: 11,
                  fontWeight: 600,
                  letterSpacing: 0.8,
                  color: '#555',
                  textTransform: 'uppercase',
                  width: 'calc((100% - 180px) / 4)',
                }}>
                  {name}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {evidence.map((item, rowIdx) => {
              const policy = policies[item.boundary_id];
              const policyMatch = policy?.match ?? {};
              const similarities = item.similarities ?? [0, 0, 0, 0];
              const thresholds = item.thresholds ?? [0, 0, 0, 0];
              const worst = worstSliceIndex(similarities, thresholds);

              return (
                <tr key={rowIdx}>
                  <td style={{
                    padding: '10px 12px',
                    border: '1px solid #eee',
                    verticalAlign: 'top',
                    whiteSpace: 'normal',
                    wordBreak: 'break-word',
                    color: '#1a1a1a',
                    fontWeight: 500,
                  }}>
                    <div style={{ fontSize: 12, marginBottom: 3 }}>
                      {item.boundary_name || item.boundary_id || '—'}
                    </div>
                    <div style={{ fontSize: 10, color: '#888' }}>
                      {item.effect ?? '—'} · {item.decision === 1 ? 'allowed' : 'blocked'}
                    </div>
                  </td>
                  {[0, 1, 2, 3].map(sliceIdx => (
                    <SliceCell
                      key={sliceIdx}
                      intent={snap[SLICE_INTENT_KEYS[sliceIdx]] || ''}
                      anchor={policyMatch[SLICE_ANCHOR_KEYS[sliceIdx]] || ''}
                      similarity={similarities[sliceIdx] ?? 0}
                      threshold={thresholds[sliceIdx] ?? 0}
                      isWorst={sliceIdx === worst}
                      feedbackKey={`${item.boundary_id}:${sliceIdx}`}
                      feedbackValue={feedback[`${item.boundary_id}:${sliceIdx}`] ?? null}
                      onFeedbackChange={(key, score, rationale) =>
                        setFeedback(prev => ({ ...prev, [key]: { score, rationale } }))
                      }
                    />
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}
