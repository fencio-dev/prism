import { useState } from 'react';
import { createPolicy, updatePolicy } from '../api/policies';

const INPUT_CLASS = 'h-8 w-full rounded-sm border border-[var(--prism-border-default)] bg-[var(--prism-bg-elevated)] px-3 text-sm text-[var(--prism-text-primary)] outline-none transition-colors placeholder:text-[var(--prism-text-muted)] focus:border-[var(--prism-accent)]/60 focus:ring-1 focus:ring-[var(--prism-accent)]/20';
const INPUT_INVALID_CLASS = 'h-8 w-full rounded-sm border border-red-500/50 bg-[var(--prism-bg-elevated)] px-3 text-sm text-[var(--prism-text-primary)] outline-none ring-1 ring-red-500/20 transition-colors placeholder:text-[var(--prism-text-muted)] focus:border-red-400/70 focus:ring-red-500/30';
const INPUT_MONO_CLASS = `${INPUT_CLASS} font-mono tracking-tight`;
const SELECT_CLASS = `${INPUT_CLASS} pr-8`;
const TEXTAREA_CLASS = 'min-h-[80px] w-full resize-y rounded-sm border border-[var(--prism-border-default)] bg-[var(--prism-bg-elevated)] px-3 py-2 text-sm text-[var(--prism-text-primary)] outline-none transition-colors placeholder:text-[var(--prism-text-muted)] focus:border-[var(--prism-accent)]/60 focus:ring-1 focus:ring-[var(--prism-accent)]/20';
const TEXTAREA_MONO_CLASS = `${TEXTAREA_CLASS} font-mono tracking-tight`;
const TEXTAREA_INVALID_CLASS = 'min-h-[80px] w-full resize-y rounded-sm border border-red-500/50 bg-[var(--prism-bg-elevated)] px-3 py-2 font-mono text-sm tracking-tight text-[var(--prism-text-primary)] outline-none ring-1 ring-red-500/20 transition-colors placeholder:text-[var(--prism-text-muted)] focus:border-red-400/70 focus:ring-red-500/30';
const RANGE_CLASS = 'h-1.5 w-full cursor-pointer accent-[var(--prism-accent)] disabled:cursor-not-allowed';

export default function PolicyForm({ onSuccess, onCancel, policy = null }) {
  const isEdit = policy !== null;

  const [name, setName] = useState(isEdit ? policy.name : '');
  const [tenantId, setTenantId] = useState(isEdit ? policy.tenant_id : '');
  const [status, setStatus] = useState(isEdit ? policy.status : 'active');
  const [policyType, setPolicyType] = useState(isEdit ? policy.policy_type : 'forbidden');
  const [priority, setPriority] = useState(isEdit ? policy.priority : 0);

  const [matchOp, setMatchOp] = useState(isEdit ? (policy.match?.op ?? '') : '');
  const [matchT, setMatchT] = useState(isEdit ? (policy.match?.t ?? '') : '');
  const [matchP, setMatchP] = useState(isEdit ? (policy.match?.p ?? '') : '');
  const [matchCtx, setMatchCtx] = useState(isEdit ? (policy.match?.ctx ?? '') : '');

  const [thresholds, setThresholds] = useState(
    isEdit && policy.thresholds
      ? { action: 0.85, resource: 0.85, data: 0.85, risk: 0.85, ...policy.thresholds }
      : { action: 0.85, resource: 0.85, data: 0.85, risk: 0.85 }
  );
  const [scoringMode, setScoringMode] = useState(
    isEdit && policy.weights ? 'weighted-avg' : 'min'
  );
  const [weights, setWeights] = useState(
    isEdit && policy.weights
      ? { action: 1.0, resource: 1.0, data: 1.0, risk: 1.0, ...policy.weights }
      : { action: 1.0, resource: 1.0, data: 1.0, risk: 1.0 }
  );

  const [driftThreshold, setDriftThreshold] = useState(
    isEdit && policy.drift_threshold != null ? String(policy.drift_threshold) : ''
  );
  const [notes, setNotes] = useState(isEdit ? (policy.notes ?? '') : '');

  const [jsonMode, setJsonMode] = useState({ op: false, t: false, p: false, ctx: false });
  const [jsonErrors, setJsonErrors] = useState({ op: null, t: null, p: null, ctx: null });

  const [saving, setSaving] = useState(false);
  const [submitError, setSubmitError] = useState(null);
  const [fieldErrors, setFieldErrors] = useState({});

  function setThreshold(key, value) {
    setThresholds((prev) => ({ ...prev, [key]: parseFloat(value) }));
  }

  function setWeight(key, value) {
    setWeights((prev) => ({ ...prev, [key]: parseFloat(value) }));
  }

  function toggleJsonMode(field) {
    setJsonMode((prev) => ({ ...prev, [field]: !prev[field] }));
    setJsonErrors((prev) => ({ ...prev, [field]: null }));
  }

  function handleAnchorChange(field, value, setter) {
    setter(value);
    if (jsonMode[field]) {
      try {
        JSON.parse(value);
        setJsonErrors((prev) => ({ ...prev, [field]: null }));
      } catch {
        setJsonErrors((prev) => ({ ...prev, [field]: 'Invalid JSON' }));
      }
    }
  }

  function validate() {
    const errors = {};
    if (!name.trim()) errors.name = 'Name is required.';
    if (!tenantId.trim()) errors.tenantId = 'Tenant ID is required.';
    if (!matchOp.trim()) errors.matchOp = 'Operation is required.';
    if (!matchT.trim()) errors.matchT = 'Target / Tool is required.';
    return errors;
  }

  async function handleSubmit(e) {
    e.preventDefault();
    setSubmitError(null);

    const errors = validate();
    if (Object.keys(errors).length > 0) {
      setFieldErrors(errors);
      return;
    }
    setFieldErrors({});

    const now = Date.now() / 1000;

    const match = {
      op: matchOp.trim(),
      t: matchT.trim(),
    };
    if (matchP.trim()) match.p = matchP.trim();
    if (matchCtx.trim()) match.ctx = matchCtx.trim();

    const payload = isEdit
      ? {
          id: policy.id,
          name: name.trim(),
          tenant_id: tenantId.trim(),
          status,
          policy_type: policyType,
          priority,
          match,
          thresholds: { ...thresholds },
          scoring_mode: scoringMode,
          weights: scoringMode === 'weighted-avg' ? { ...weights } : null,
          drift_threshold: driftThreshold !== '' ? parseFloat(driftThreshold) : null,
          notes: notes.trim() || null,
          created_at: policy.created_at,
          updated_at: now,
        }
      : {
          id: crypto.randomUUID(),
          name: name.trim(),
          tenant_id: tenantId.trim(),
          status,
          policy_type: policyType,
          priority,
          match,
          thresholds: { ...thresholds },
          scoring_mode: scoringMode,
          weights: scoringMode === 'weighted-avg' ? { ...weights } : null,
          drift_threshold: driftThreshold !== '' ? parseFloat(driftThreshold) : null,
          notes: notes.trim() || null,
          created_at: now,
          updated_at: now,
        };

    setSaving(true);
    try {
      if (isEdit) {
        await updatePolicy(policy.id, payload);
      } else {
        await createPolicy(payload);
      }
      onSuccess();
    } catch (err) {
      setSubmitError(err.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="mb-6 rounded border border-[var(--prism-border-default)] bg-[var(--prism-bg-surface)] p-5 shadow-sm">
      <div className="mb-5 text-lg font-semibold text-[var(--prism-text-primary)]">{isEdit ? 'Edit Policy' : 'New Policy'}</div>
      <form onSubmit={handleSubmit} noValidate>

        <fieldset className="mb-4 rounded border border-[var(--prism-border-default)] bg-[var(--prism-bg-base)] p-4 shadow-sm">
          <legend className="px-1.5 text-xs font-medium uppercase tracking-wider text-[var(--prism-text-secondary)]">Basic Info</legend>
          <div className="grid grid-cols-1 gap-x-6 gap-y-3 md:grid-cols-2">
            <div className="flex flex-col gap-1">
              <label htmlFor="policy-name" className="text-sm font-medium text-[var(--prism-text-primary)]">Name</label>
              <input
                id="policy-name"
                className={fieldErrors.name ? INPUT_INVALID_CLASS : INPUT_CLASS}
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Policy name"
              />
              {fieldErrors.name && <span className="text-xs text-red-400">{fieldErrors.name}</span>}
              <small className="text-xs leading-snug text-[var(--prism-text-secondary)]">A human-readable label for this policy boundary.</small>
            </div>
            <div className="flex flex-col gap-1">
              <label htmlFor="policy-tenant-id" className="text-sm font-medium text-[var(--prism-text-primary)]">Tenant ID</label>
              <input
                id="policy-tenant-id"
                className={fieldErrors.tenantId ? `${INPUT_INVALID_CLASS} font-mono tracking-tight` : INPUT_MONO_CLASS}
                type="text"
                value={tenantId}
                onChange={(e) => setTenantId(e.target.value)}
                placeholder="tenant-id"
              />
              {fieldErrors.tenantId && <span className="text-xs text-red-400">{fieldErrors.tenantId}</span>}
              <small className="text-xs leading-snug text-[var(--prism-text-secondary)]">The tenant this policy applies to. Must match the tenant_id in incoming intent events.</small>
            </div>
            <div className="flex flex-col gap-1">
              <label htmlFor="policy-status" className="text-sm font-medium text-[var(--prism-text-primary)]">Status</label>
              <select id="policy-status" className={SELECT_CLASS} value={status} onChange={(e) => setStatus(e.target.value)}>
                <option value="active">active</option>
                <option value="disabled">disabled</option>
              </select>
              <small className="text-xs leading-snug text-[var(--prism-text-secondary)]">Active policies are evaluated during enforcement. Disabled policies are stored but skipped.</small>
            </div>
            <div className="flex flex-col gap-1">
              <label htmlFor="policy-type" className="text-sm font-medium text-[var(--prism-text-primary)]">Policy Type</label>
              <select id="policy-type" className={SELECT_CLASS} value={policyType} onChange={(e) => setPolicyType(e.target.value)}>
                <option value="forbidden">forbidden</option>
                <option value="context_allow">context_allow</option>
                <option value="context_deny">context_deny</option>
                <option value="context_defer">context_defer</option>
              </select>
              <small className="text-xs leading-snug text-[var(--prism-text-secondary)]">forbidden: always blocks regardless of context. context_allow: denied by default, allowed when context confirms intent. context_deny: allowed by default, blocked when context signals risk. context_defer: triggers DEFER when action is ambiguous or context is insufficient.</small>
            </div>
            <div className="flex flex-col gap-1">
              <label htmlFor="policy-priority" className="text-sm font-medium text-[var(--prism-text-primary)]">Priority</label>
              <input
                id="policy-priority"
                className={INPUT_CLASS}
                type="number"
                value={priority}
                onChange={(e) => setPriority(parseInt(e.target.value, 10) || 0)}
                step={1}
              />
              <small className="text-xs leading-snug text-[var(--prism-text-secondary)]">Lower number = higher priority. When multiple policies match, the lowest priority number wins.</small>
            </div>
          </div>
        </fieldset>

        <fieldset className="mb-4 rounded border border-[var(--prism-border-default)] bg-[var(--prism-bg-base)] p-4 shadow-sm">
          <legend className="px-1.5 text-xs font-medium uppercase tracking-wider text-[var(--prism-text-secondary)]">Match Anchors</legend>
          <small className="mb-3 block text-xs leading-snug text-[var(--prism-text-secondary)]">Natural language descriptions of the action pattern this policy should match. The engine embeds these as semantic vectors and compares them against incoming intent events.</small>
          <div className="grid grid-cols-1 gap-y-3">
            <div className="flex flex-col gap-1">
              <div className="flex items-center gap-2">
                 <label htmlFor="policy-match-op" className="text-sm font-medium text-[var(--prism-text-primary)]">Operation</label>
                 <span className="ml-auto inline-flex overflow-hidden rounded-sm border border-[var(--prism-border-default)]" role="group" aria-label="Operation input mode">
                  <button
                    type="button"
                     className={`h-6 border-r border-[var(--prism-border-default)] px-2 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[var(--prism-accent)]/40 ${jsonMode.op ? 'bg-transparent text-[var(--prism-text-secondary)] hover:bg-[var(--prism-accent-subtle)]' : 'bg-[var(--prism-accent-subtle)] text-[var(--prism-accent)]'}`}
                    onClick={() => jsonMode.op && toggleJsonMode('op')}
                    aria-pressed={!jsonMode.op}
                  >
                    NL
                  </button>
                  <button
                    type="button"
                     className={`h-6 px-2 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[var(--prism-accent)]/40 ${jsonMode.op ? 'bg-[var(--prism-accent-subtle)] text-[var(--prism-accent)]' : 'bg-transparent text-[var(--prism-text-secondary)] hover:bg-[var(--prism-accent-subtle)]'}`}
                    onClick={() => !jsonMode.op && toggleJsonMode('op')}
                    aria-pressed={jsonMode.op}
                  >
                    JSON
                  </button>
                </span>
              </div>
              {jsonMode.op ? (
                <textarea
                  id="policy-match-op"
                  className={jsonErrors.op ? TEXTAREA_INVALID_CLASS : TEXTAREA_MONO_CLASS}
                  value={matchOp}
                  onChange={(e) => handleAnchorChange('op', e.target.value, setMatchOp)}
                  placeholder='e.g. {"action": "read", "scope": "users"}'
                />
              ) : (
                <input
                  id="policy-match-op"
                  className={fieldErrors.matchOp ? INPUT_INVALID_CLASS : INPUT_CLASS}
                  type="text"
                  value={matchOp}
                  onChange={(e) => setMatchOp(e.target.value)}
                  placeholder="e.g. read user records from database"
                />
              )}
              {fieldErrors.matchOp && <span className="text-xs text-red-400">{fieldErrors.matchOp}</span>}
              {jsonErrors.op && <span className="text-xs text-red-400">{jsonErrors.op}</span>}
               <small className="text-xs leading-snug text-[var(--prism-text-secondary)]">Describe the action being performed. E.g. 'query a database', 'send an email', 'read a file'.</small>
            </div>
            <div className="flex flex-col gap-1">
              <div className="flex items-center gap-2">
                 <label htmlFor="policy-match-target" className="text-sm font-medium text-[var(--prism-text-primary)]">Target / Tool</label>
                 <span className="ml-auto inline-flex overflow-hidden rounded-sm border border-[var(--prism-border-default)]" role="group" aria-label="Target input mode">
                  <button
                    type="button"
                     className={`h-6 border-r border-[var(--prism-border-default)] px-2 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[var(--prism-accent)]/40 ${jsonMode.t ? 'bg-transparent text-[var(--prism-text-secondary)] hover:bg-[var(--prism-accent-subtle)]' : 'bg-[var(--prism-accent-subtle)] text-[var(--prism-accent)]'}`}
                    onClick={() => jsonMode.t && toggleJsonMode('t')}
                    aria-pressed={!jsonMode.t}
                  >
                    NL
                  </button>
                  <button
                    type="button"
                     className={`h-6 px-2 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[var(--prism-accent)]/40 ${jsonMode.t ? 'bg-[var(--prism-accent-subtle)] text-[var(--prism-accent)]' : 'bg-transparent text-[var(--prism-text-secondary)] hover:bg-[var(--prism-accent-subtle)]'}`}
                    onClick={() => !jsonMode.t && toggleJsonMode('t')}
                    aria-pressed={jsonMode.t}
                  >
                    JSON
                  </button>
                </span>
              </div>
              {jsonMode.t ? (
                <textarea
                  id="policy-match-target"
                  className={jsonErrors.t ? TEXTAREA_INVALID_CLASS : TEXTAREA_MONO_CLASS}
                  value={matchT}
                  onChange={(e) => handleAnchorChange('t', e.target.value, setMatchT)}
                  placeholder='e.g. {"tool": "postgres", "table": "users"}'
                />
              ) : (
                <input
                  id="policy-match-target"
                  className={fieldErrors.matchT ? INPUT_INVALID_CLASS : INPUT_CLASS}
                  type="text"
                  value={matchT}
                  onChange={(e) => setMatchT(e.target.value)}
                  placeholder="e.g. postgres users table"
                />
              )}
              {fieldErrors.matchT && <span className="text-xs text-red-400">{fieldErrors.matchT}</span>}
              {jsonErrors.t && <span className="text-xs text-red-400">{jsonErrors.t}</span>}
               <small className="text-xs leading-snug text-[var(--prism-text-secondary)]">Describe the resource or tool being accessed. E.g. 'postgres users table', 'Gmail API', 'S3 bucket'.</small>
            </div>
            <div className="flex flex-col gap-1">
              <div className="flex items-center gap-2">
                 <label htmlFor="policy-match-params" className="text-sm font-medium text-[var(--prism-text-primary)]">Parameters - optional</label>
                 <span className="ml-auto inline-flex overflow-hidden rounded-sm border border-[var(--prism-border-default)]" role="group" aria-label="Parameters input mode">
                  <button
                    type="button"
                     className={`h-6 border-r border-[var(--prism-border-default)] px-2 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[var(--prism-accent)]/40 ${jsonMode.p ? 'bg-transparent text-[var(--prism-text-secondary)] hover:bg-[var(--prism-accent-subtle)]' : 'bg-[var(--prism-accent-subtle)] text-[var(--prism-accent)]'}`}
                    onClick={() => jsonMode.p && toggleJsonMode('p')}
                    aria-pressed={!jsonMode.p}
                  >
                    NL
                  </button>
                  <button
                    type="button"
                     className={`h-6 px-2 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[var(--prism-accent)]/40 ${jsonMode.p ? 'bg-[var(--prism-accent-subtle)] text-[var(--prism-accent)]' : 'bg-transparent text-[var(--prism-text-secondary)] hover:bg-[var(--prism-accent-subtle)]'}`}
                    onClick={() => !jsonMode.p && toggleJsonMode('p')}
                    aria-pressed={jsonMode.p}
                  >
                    JSON
                  </button>
                </span>
              </div>
              {jsonMode.p ? (
                <textarea
                  id="policy-match-params"
                  className={jsonErrors.p ? TEXTAREA_INVALID_CLASS : TEXTAREA_MONO_CLASS}
                  value={matchP}
                  onChange={(e) => handleAnchorChange('p', e.target.value, setMatchP)}
                  placeholder='e.g. {"columns": ["email", "name"]}'
                />
              ) : (
                <input
                  id="policy-match-params"
                  className={INPUT_CLASS}
                  type="text"
                  value={matchP}
                  onChange={(e) => setMatchP(e.target.value)}
                  placeholder="e.g. query includes email and name columns"
                />
              )}
              {jsonErrors.p && <span className="text-xs text-red-400">{jsonErrors.p}</span>}
               <small className="text-xs leading-snug text-[var(--prism-text-secondary)]">Optional. Describe the parameter pattern to match. E.g. 'queries containing personal identifiers'. Leave blank to match any parameters.</small>
            </div>
            <div className="flex flex-col gap-1">
              <div className="flex items-center gap-2">
                 <label htmlFor="policy-match-ctx" className="text-sm font-medium text-[var(--prism-text-primary)]">Risk Context - optional</label>
                 <span className="ml-auto inline-flex overflow-hidden rounded-sm border border-[var(--prism-border-default)]" role="group" aria-label="Risk context input mode">
                  <button
                    type="button"
                     className={`h-6 border-r border-[var(--prism-border-default)] px-2 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[var(--prism-accent)]/40 ${jsonMode.ctx ? 'bg-transparent text-[var(--prism-text-secondary)] hover:bg-[var(--prism-accent-subtle)]' : 'bg-[var(--prism-accent-subtle)] text-[var(--prism-accent)]'}`}
                    onClick={() => jsonMode.ctx && toggleJsonMode('ctx')}
                    aria-pressed={!jsonMode.ctx}
                  >
                    NL
                  </button>
                  <button
                    type="button"
                     className={`h-6 px-2 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[var(--prism-accent)]/40 ${jsonMode.ctx ? 'bg-[var(--prism-accent-subtle)] text-[var(--prism-accent)]' : 'bg-transparent text-[var(--prism-text-secondary)] hover:bg-[var(--prism-accent-subtle)]'}`}
                    onClick={() => !jsonMode.ctx && toggleJsonMode('ctx')}
                    aria-pressed={jsonMode.ctx}
                  >
                    JSON
                  </button>
                </span>
              </div>
              {jsonMode.ctx ? (
                <textarea
                  id="policy-match-ctx"
                  className={jsonErrors.ctx ? TEXTAREA_INVALID_CLASS : TEXTAREA_MONO_CLASS}
                  value={matchCtx}
                  onChange={(e) => handleAnchorChange('ctx', e.target.value, setMatchCtx)}
                  placeholder='e.g. {"signal": "pii_access", "window_minutes": 5}'
                />
              ) : (
                <input
                  id="policy-match-ctx"
                  className={INPUT_CLASS}
                  type="text"
                  value={matchCtx}
                  onChange={(e) => setMatchCtx(e.target.value)}
                  placeholder="e.g. accessed PII in the last 5 minutes"
                />
              )}
              {jsonErrors.ctx && <span className="text-xs text-red-400">{jsonErrors.ctx}</span>}
               <small className="text-xs leading-snug text-[var(--prism-text-secondary)]">Optional. Describe the session context signal this policy reacts to. E.g. 'requests involving financial data'. Leave blank to ignore context.</small>
            </div>
          </div>
        </fieldset>

        <fieldset className="mb-4 rounded border border-[var(--prism-border-default)] bg-[var(--prism-bg-base)] p-4 shadow-sm">
          <legend className="px-1.5 text-xs font-medium uppercase tracking-wider text-[var(--prism-text-secondary)]">Thresholds &amp; Scoring</legend>

          <div className="mb-4 flex flex-col gap-1">
            <label htmlFor="policy-scoring-mode" className="text-sm font-medium text-[var(--prism-text-primary)]">Scoring Mode</label>
            <select id="policy-scoring-mode" className={SELECT_CLASS} value={scoringMode} onChange={(e) => setScoringMode(e.target.value)}>
              <option value="min">min</option>
              <option value="weighted-avg">weighted-avg</option>
            </select>
            <small className="text-xs leading-snug text-[var(--prism-text-secondary)]">
              'min': each slice must independently exceed its own threshold — one failure blocks the match.<br />
              'weighted-avg': similarities and thresholds are both weight-averaged into single scores, then compared — slices with higher weight have more influence on the outcome.
            </small>
          </div>

          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 md:gap-6">

            <div className="rounded border border-[var(--prism-border-default)] bg-[var(--prism-bg-elevated)] p-3">
              <div className="mb-2 text-xs font-medium uppercase tracking-wider text-[var(--prism-text-secondary)]">Thresholds</div>
              <small className="mb-3 block text-xs leading-snug text-[var(--prism-text-secondary)]">Minimum similarity per slice (0.0-1.0). Active in both modes - in weighted-avg they are averaged together with the weights.</small>
              <div className="flex flex-col gap-2.5">
                {['action', 'resource', 'data', 'risk'].map((key) => (
                  <div key={key} className="rounded border border-[var(--prism-border-subtle)] bg-[var(--prism-bg-base)] px-2.5 py-2">
                    <label className="text-sm font-medium text-[var(--prism-text-primary)]">Threshold: {key}</label>
                    <div className="mt-1 flex items-center gap-2.5">
                      <input
                        className={RANGE_CLASS}
                        type="range"
                        min={0}
                        max={1}
                        step={0.01}
                        value={thresholds[key]}
                        onChange={(e) => setThreshold(key, e.target.value)}
                      />
                      <span className="w-10 text-right font-mono text-sm tracking-tight text-[var(--prism-text-primary)]">{thresholds[key].toFixed(2)}</span>
                    </div>
                    <small className="mt-1 block text-xs leading-snug text-[var(--prism-text-secondary)]">{key === 'action' ? 'Operation/action slice.' : key === 'resource' ? 'Target/resource slice.' : key === 'data' ? 'Parameters/data slice.' : 'Risk context slice.'}</small>
                  </div>
                ))}
              </div>
            </div>

            <div className={`rounded border border-[var(--prism-border-default)] bg-[var(--prism-bg-elevated)] p-3 ${scoringMode === 'min' ? 'pointer-events-none opacity-40' : ''}`}>
              <div className="mb-2 text-xs font-medium uppercase tracking-wider text-[var(--prism-text-secondary)]">
                Weights {scoringMode === 'min' && <span className="font-normal normal-case tracking-normal">(not used in min mode)</span>}
              </div>
              <small className="mb-3 block text-xs leading-snug text-[var(--prism-text-secondary)]">Relative influence of each slice in weighted-avg mode. Higher weight = that slice pulls the combined score and threshold more.</small>
              <div className="flex flex-col gap-2.5">
                {['action', 'resource', 'data', 'risk'].map((key) => (
                  <div key={key} className="rounded border border-[var(--prism-border-subtle)] bg-[var(--prism-bg-base)] px-2.5 py-2">
                    <label className="text-sm font-medium text-[var(--prism-text-primary)]">Weight: {key}</label>
                    <div className="mt-1 flex items-center gap-2.5">
                      <input
                        className={RANGE_CLASS}
                        type="range"
                        min={0}
                        max={2}
                        step={0.1}
                        value={weights[key]}
                        onChange={(e) => setWeight(key, e.target.value)}
                      />
                      <span className="w-10 text-right font-mono text-sm tracking-tight text-[var(--prism-text-primary)]">{weights[key].toFixed(1)}</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>

          </div>
        </fieldset>

        <fieldset className="mb-4 rounded border border-[var(--prism-border-default)] bg-[var(--prism-bg-base)] p-4 shadow-sm">
          <legend className="px-1.5 text-xs font-medium uppercase tracking-wider text-[var(--prism-text-secondary)]">Advanced</legend>
          <div className="grid grid-cols-1 gap-x-6 gap-y-3 md:grid-cols-2">
            <div className="flex flex-col gap-1">
              <label htmlFor="policy-drift-threshold" className="text-sm font-medium text-[var(--prism-text-primary)]">Drift Threshold - optional (0.0-1.0)</label>
              <input
                id="policy-drift-threshold"
                className={`${INPUT_CLASS} font-mono tracking-tight`}
                type="number"
                value={driftThreshold}
                onChange={(e) => setDriftThreshold(e.target.value)}
                placeholder="e.g. 0.3"
                min={0}
                max={1}
                step={0.01}
              />
              <small className="text-xs leading-snug text-[var(--prism-text-secondary)]">Optional. If the semantic distance between the agent's current action and the user's original request exceeds this value (0.0-1.0), the policy triggers a DEFER or STEP_UP. Leave blank to disable drift enforcement for this policy.</small>
            </div>
          </div>
          <div className="mt-3 grid grid-cols-1 gap-y-3">
            <div className="flex flex-col gap-1">
              <label htmlFor="policy-notes" className="text-sm font-medium text-[var(--prism-text-primary)]">Notes - optional</label>
              <textarea
                id="policy-notes"
                className={TEXTAREA_CLASS}
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                placeholder="Additional context or notes about this policy"
              />
              <small className="text-xs leading-snug text-[var(--prism-text-secondary)]">Optional free-text notes for your own reference. Not used during enforcement.</small>
            </div>
          </div>
        </fieldset>

        {submitError && <p className="mt-2 text-sm text-red-400">{submitError}</p>}

        <div className="mt-5 flex justify-end gap-2.5">
          <button
            type="button"
            className="inline-flex h-8 items-center justify-center rounded border border-[var(--prism-border-default)] px-3 text-sm text-[var(--prism-text-primary)] transition-colors hover:bg-[var(--prism-accent-subtle)] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[var(--prism-accent)]/40 active:bg-[rgba(201,100,66,0.2)] disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-40"
            onClick={onCancel}
            disabled={saving}
          >
            Cancel
          </button>
          <button
            type="submit"
            className="inline-flex h-8 items-center justify-center rounded border border-[var(--prism-accent)]/40 bg-[var(--prism-accent)] px-3 text-sm font-semibold text-white transition-colors hover:bg-[#b75a3b] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[var(--prism-accent)]/40 active:bg-[#a65135] disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-40"
            disabled={saving}
          >
            {saving ? 'Saving...' : isEdit ? 'Update Policy' : 'Create Policy'}
          </button>
        </div>
      </form>
    </div>
  );
}
