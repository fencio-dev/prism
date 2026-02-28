import { useState, useEffect, useCallback } from 'react';
import { Database, FileSearch, MousePointerClick } from 'lucide-react';
import { fetchCalls, deleteCalls, fetchCallDetail } from '../api/telemetry';
import { getPolicy } from '../api/policies';
import RunAnchorComparisonPanel from './RunAnchorComparisonPanel';
import PrismEmptyState from './PrismEmptyState';
import { Slider } from './ui/slider';

const BADGE_COLORS = {
  ALLOW: 'border-green-600/30 bg-green-100 text-green-700',
  DENY: 'border-red-500/35 bg-red-100 text-red-700',
  MODIFY: 'border-amber-600/30 bg-amber-100 text-amber-700',
  STEP_UP: 'border-sky-600/30 bg-sky-100 text-sky-700',
  DEFER: 'border-stone-400/40 bg-stone-100 text-stone-600',
};

const DEFAULT_BADGE_CLASS = 'border-stone-400/40 bg-stone-100 text-stone-600';

function decisionBadgeClass(decision) {
  return BADGE_COLORS[decision] ?? DEFAULT_BADGE_CLASS;
}

function buttonClass(enabled) {
  if (!enabled) {
    return 'rounded border border-[var(--prism-border-default)] px-3 py-1.5 text-xs font-medium text-[var(--prism-text-muted)] opacity-40 cursor-not-allowed pointer-events-none';
  }
  return 'rounded border border-[var(--prism-border-default)] px-3 py-1.5 text-xs font-medium text-[var(--prism-text-primary)] transition-colors hover:bg-[var(--prism-accent-subtle)] hover:text-[var(--prism-text-primary)] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[var(--prism-accent)]/40';
}

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
    <div className="flex h-full min-h-0 overflow-hidden rounded border border-[var(--prism-border-default)] bg-[var(--prism-bg-surface)] shadow-sm">
      {/* Left panel — run list */}
      <div className="flex h-full w-[300px] shrink-0 flex-col border-r border-[var(--prism-border-default)] bg-[var(--prism-bg-base)]">
        <div className="flex items-center justify-between border-b border-[var(--prism-border-default)] px-3 py-2.5">
          <span className="text-sm font-semibold text-[var(--prism-text-primary)]">
            Recent Calls <span className="ml-1 text-xs font-normal text-[var(--prism-text-muted)]">{totalCount}</span>
          </span>
          <button
            onClick={handleClearAll}
            className="rounded border border-[var(--prism-border-default)] px-2.5 py-1 text-xs font-medium text-[var(--prism-text-primary)] transition-colors hover:bg-[var(--prism-accent-subtle)] hover:text-[var(--prism-text-primary)] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[var(--prism-accent)]/40"
          >
            Clear All
          </button>
        </div>

        {calls.length === 0 ? (
          <div className="h-full px-4">
            <PrismEmptyState
              icon={Database}
              title="No recent calls"
              description="Run an enforcement check to generate data for dataset feedback."
              actionLabel="Refresh"
              onAction={poll}
              fullHeight
            />
          </div>
        ) : (
          <div className="prism-scrollbar min-h-0 flex-1 overflow-y-auto">
            {calls.map((c) => {
              const isSelected = selectedSummary?.call_id === c.call_id;
              return (
                <button
                  key={c.call_id}
                  onClick={() => handleRowClick(c)}
                  className={`w-full border-b border-[var(--prism-border-subtle)] px-3 py-2.5 text-left transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[var(--prism-accent)]/40 ${
                    isSelected
                      ? 'border-l-2 border-l-[var(--prism-accent)] bg-[var(--prism-accent-subtle)]'
                      : 'border-l-2 border-l-transparent hover:bg-[rgba(201,100,66,0.08)]'
                  }`}
                >
                  <div className="mb-1.5 flex items-center gap-2 text-[11px] text-[var(--prism-text-muted)]">
                    <span>{formatTs(c.ts_ms)}</span>
                    {c.is_dry_run && (
                      <span className="rounded-sm border border-[var(--prism-accent)]/35 bg-[var(--prism-accent-subtle)] px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-[var(--prism-accent)]">
                        dry run
                      </span>
                    )}
                  </div>
                  <div className="mb-1 truncate font-mono text-sm text-[var(--prism-text-primary)]">{truncate(c.op, 36)}</div>
                  <div className="mb-2 truncate font-mono text-sm text-[var(--prism-text-secondary)]">{truncate(c.t, 36)}</div>
                  <span className={`inline-flex rounded-full border px-2 py-0.5 text-[11px] font-medium ${decisionBadgeClass(c.decision)}`}>
                    {c.decision ?? '—'}
                  </span>
                </button>
              );
            })}
          </div>
        )}
      </div>

      {/* Right panel — details or placeholder */}
      <div className="prism-scrollbar h-full min-h-0 flex-1 overflow-y-auto overflow-x-auto bg-[var(--prism-bg-surface)] px-6 py-5">
        {selectedCall === null ? (
          <PrismEmptyState
            icon={MousePointerClick}
            title="Select a call"
            description="Choose a run from the left panel to compare intent slices and export feedback."
            fullHeight
          />
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

  function handleSliderChange(raw) {
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
    ? '#8a8779'
    : sliderDisplayValue > 0
      ? '#4ade80'
      : '#c24141';

  return (
    <td
      className={`border-b border-r border-[var(--prism-border-subtle)] px-3 py-2.5 align-top text-[var(--prism-text-primary)] ${
        isWorst ? 'border-t-2 border-t-amber-600' : 'border-t border-t-[var(--prism-border-subtle)]'
      }`}
      style={{ wordBreak: 'break-word', overflowWrap: 'break-word' }}
    >
      <RunAnchorComparisonPanel intentValue={intent} policyAnchorValue={anchor} />
      <div className="mt-2 flex items-center gap-2 text-xs">
        <span className="font-semibold text-[var(--prism-text-primary)]">
          {similarity.toFixed(2)}
        </span>
        <span className="text-[var(--prism-text-muted)]">
          / {threshold.toFixed(2)}
        </span>
        <span className={`text-sm font-bold ${passes ? 'text-green-700' : 'text-red-700'}`}>
          {passes ? '✓' : '✗'}
        </span>
      </div>

      <hr className="my-2.5 border-[var(--prism-border-default)]" />

      <div>
        <div className="mb-2">
          <Slider
            min={-100}
            max={100}
            step={1}
            value={[sliderDisplayValue]}
            onValueChange={(value) => handleSliderChange(value[0] ?? 0)}
            className="w-full"
          />
        </div>
        <div className="mb-2 flex items-center gap-2">
          <input
            type="number"
            min="-1"
            max="1"
            step="0.01"
            value={numberInputValue}
            onChange={handleNumberChange}
            onBlur={handleNumberBlur}
            className="h-7 w-16 rounded-sm border border-[var(--prism-border-default)] bg-[var(--prism-bg-elevated)] px-2 text-right font-mono text-sm text-[var(--prism-text-primary)] outline-none transition-colors placeholder:text-[var(--prism-text-muted)] focus:border-[var(--prism-accent)]/60 focus:ring-1 focus:ring-[var(--prism-accent)]/20"
          />
          <span className="min-w-10 text-right text-xs font-semibold" style={{ color: scoreLabelColor }}>
            {scoreLabel}
          </span>
        </div>
        {sliderDisplayValue !== 0 && (
          <textarea
            placeholder="Why? (optional)"
            value={currentRationale}
            onChange={handleRationaleChange}
            className="h-12 w-full resize-none rounded-sm border border-[var(--prism-border-default)] bg-[var(--prism-bg-elevated)] p-2 font-mono text-xs text-[var(--prism-text-primary)] outline-none transition-colors placeholder:text-[var(--prism-text-muted)] focus:border-[var(--prism-accent)]/60 focus:ring-1 focus:ring-[var(--prism-accent)]/20"
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
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3 border-b border-[var(--prism-border-default)] pb-3">
        <div className="flex flex-wrap items-center gap-2.5">
          <span className="text-xs text-[var(--prism-text-muted)]">
            {call.ts_ms ? new Date(call.ts_ms).toLocaleString() : '—'}
          </span>
          <span className={`inline-flex rounded-full border px-2 py-0.5 text-xs font-medium ${decisionBadgeClass(call.decision)}`}>
            {call.decision ?? '—'}
          </span>
          {call.is_dry_run && (
            <span className="rounded-sm border border-[var(--prism-accent)]/35 bg-[var(--prism-accent-subtle)] px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-[var(--prism-accent)]">
              dry run
            </span>
          )}
          {snap.op && (
            <span className="rounded-sm border border-[var(--prism-border-default)] bg-[var(--prism-bg-elevated)] px-2 py-0.5 font-mono text-sm text-[var(--prism-text-primary)]">
              {snap.op}
            </span>
          )}
          {snap.t && (
            <span className="rounded-sm border border-[var(--prism-border-default)] bg-[var(--prism-bg-elevated)] px-2 py-0.5 font-mono text-sm text-[var(--prism-text-secondary)]">
              {snap.t}
            </span>
          )}
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => setFeedback({})}
            disabled={!hasFeedback}
            className={buttonClass(hasFeedback)}
          >
            Reset All
          </button>
          <button
            onClick={() => exportFeedbackAsJSONL(call, evidence, policies, snap, feedback)}
            disabled={!hasFeedback}
            className={buttonClass(hasFeedback)}
          >
            Export Dataset
          </button>
        </div>
      </div>

      {loading ? (
        <div className="py-6 text-sm text-[var(--prism-text-secondary)]">
          Loading policies...
        </div>
      ) : evidence.length === 0 ? (
        <PrismEmptyState
          icon={FileSearch}
          title="No evidence for this run"
          description="This enforcement result has no policy evidence to score yet."
          className="py-10"
        />
      ) : (
        <table className="w-full min-w-[980px] table-fixed border-collapse text-sm">
          <thead>
            <tr className="bg-[var(--prism-bg-base)] text-xs font-medium uppercase tracking-wider text-[var(--prism-text-secondary)]">
              <th className="w-[220px] border border-[var(--prism-border-subtle)] border-b-[var(--prism-border-default)] px-3 py-2.5 text-left">
                Policy
              </th>
              {SLICE_NAMES.map(name => (
                <th key={name} className="border border-[var(--prism-border-subtle)] border-b-[var(--prism-border-default)] px-3 py-2.5 text-left">
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
                  <td
                    className="border border-[var(--prism-border-subtle)] px-3 py-2.5 align-top"
                    style={{ whiteSpace: 'normal', wordBreak: 'break-word' }}
                  >
                    <div className="mb-1 font-medium text-[var(--prism-text-primary)]">
                      {item.boundary_name || item.boundary_id || '—'}
                    </div>
                    <div className="text-xs text-[var(--prism-text-muted)]">
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
