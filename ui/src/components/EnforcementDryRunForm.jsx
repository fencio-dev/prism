import { useEffect, useState } from 'react';
import { runEnforce } from '../api/enforce';
import { fetchPolicies } from '../api/policies';
import EnforcementResultPanel from './EnforcementResultPanel';
import RecentRunsPanel from './RecentRunsPanel';
import SuggestedIntentsPanel from './SuggestedIntentsPanel';

const styles = {
  panel: {
    border: '1px solid var(--prism-border-default)',
    borderRadius: 6,
    padding: 20,
    marginBottom: 24,
    background: 'var(--prism-bg-surface)',
  },
  panelTitle: {
    fontSize: 18,
    fontWeight: 600,
    marginBottom: 16,
    color: 'var(--prism-text-primary)',
  },
  fieldset: {
    border: '1px solid var(--prism-border-strong)',
    borderRadius: 4,
    padding: '14px 16px 16px',
    marginBottom: 14,
    background: 'linear-gradient(180deg, rgba(255, 253, 249, 0.95), rgba(246, 241, 232, 0.9))',
    boxShadow: '0 1px 2px rgba(39, 36, 30, 0.08)',
  },
  legend: {
    fontSize: 11,
    fontWeight: 500,
    color: 'var(--prism-text-secondary)',
    padding: '0 6px',
    letterSpacing: '0.08em',
    textTransform: 'uppercase',
  },
  grid: {
    display: 'grid',
    gridTemplateColumns: '1fr 1fr',
    gap: '14px 24px',
  },
  field: {
    display: 'flex',
    flexDirection: 'column',
    gap: 4,
  },
  label: {
    fontSize: 13,
    fontWeight: 500,
    color: 'var(--prism-text-primary)',
  },
  footer: {
    display: 'flex',
    gap: 10,
    justifyContent: 'flex-end',
    marginTop: 20,
  },
  submitButton: {
    fontSize: 13,
    padding: '7px 14px',
    height: 32,
    lineHeight: 1,
    border: 'none',
    borderRadius: 6,
    background: 'var(--prism-accent)',
    color: '#ffffff',
    cursor: 'pointer',
    fontFamily: 'inherit',
    fontWeight: 600,
  },
  submitButtonDisabled: {
    fontSize: 13,
    padding: '7px 14px',
    height: 32,
    lineHeight: 1,
    border: 'none',
    borderRadius: 6,
    background: 'rgba(201,100,66,0.22)',
    color: 'rgba(39,36,30,0.5)',
    cursor: 'not-allowed',
    fontFamily: 'inherit',
    fontWeight: 600,
  },
  clearButton: {
    fontSize: 13,
    padding: '7px 14px',
    height: 32,
    lineHeight: 1,
    border: '1px solid var(--prism-border-default)',
    borderRadius: 6,
    background: 'transparent',
    color: 'var(--prism-text-primary)',
    cursor: 'pointer',
    fontFamily: 'inherit',
  },
  errorText: {
    fontSize: 13,
    color: '#f87171',
    marginTop: 10,
  },
  inlineError: {
    fontSize: 12,
    color: '#f87171',
    marginTop: 2,
  },
  hint: {
    display: 'block',
    fontSize: '11px',
    color: 'var(--prism-text-secondary)',
    marginTop: '3px',
    lineHeight: '1.4',
  },
  fieldsetLead: {
    display: 'block',
    fontSize: '11px',
    color: 'var(--prism-text-secondary)',
    marginBottom: 12,
    lineHeight: '1.4',
  },
  labelRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
  },
  modeToggle: {
    display: 'inline-flex',
    alignItems: 'center',
    border: '1px solid var(--prism-border-default)',
    borderRadius: 3,
    overflow: 'hidden',
    marginLeft: 'auto',
  },
  modeToggleButton: {
    fontSize: 11,
    fontWeight: 500,
    lineHeight: 1,
    border: 'none',
    borderRight: '1px solid var(--prism-border-default)',
    padding: '4px 8px',
    background: 'transparent',
    color: 'var(--prism-text-secondary)',
    cursor: 'pointer',
  },
  modeToggleButtonLast: {
    borderRight: 'none',
  },
  modeToggleActive: {
    background: 'var(--prism-accent-subtle)',
    color: 'var(--prism-accent)',
    fontWeight: 600,
  },
  sectionLayout: {
    display: 'flex',
    gap: 28,
    alignItems: 'flex-start',
    flexWrap: 'wrap',
  },
};

const INPUT_CLASS = 'h-8 w-full rounded-sm border border-[var(--prism-border-default)] bg-[var(--prism-bg-elevated)] px-3 text-sm text-[var(--prism-text-primary)] outline-none transition-colors placeholder:text-[var(--prism-text-muted)] focus:border-[var(--prism-accent)]/60 focus:ring-1 focus:ring-[var(--prism-accent)]/20';
const INPUT_MONO_CLASS = `${INPUT_CLASS} font-mono tracking-tight`;
const SELECT_CLASS = `${INPUT_CLASS} pr-8`;
const TEXTAREA_CLASS = 'min-h-[80px] w-full resize-y rounded-sm border border-[var(--prism-border-default)] bg-[var(--prism-bg-elevated)] px-3 py-2 font-mono text-sm tracking-tight text-[var(--prism-text-primary)] outline-none transition-colors placeholder:text-[var(--prism-text-muted)] focus:border-[var(--prism-accent)]/60 focus:ring-1 focus:ring-[var(--prism-accent)]/20';
const TEXTAREA_INVALID_CLASS = 'min-h-[80px] w-full resize-y rounded-sm border border-red-500/50 bg-[var(--prism-bg-elevated)] px-3 py-2 font-mono text-sm tracking-tight text-[var(--prism-text-primary)] outline-none ring-1 ring-red-500/20 transition-colors placeholder:text-[var(--prism-text-muted)] focus:border-red-400/70 focus:ring-red-500/30';

const DEFAULT_STATE = {
  eventType: 'tool_call',
  agentId: '',
  principalId: '',
  actorType: 'agent',
  serviceAccount: '',
  roleScope: '',
  op: '',
  t: '',
  p: '',
  paramsRaw: '',
  ctxInitialRequest: '',
  ctxDataClassifications: '',
  ctxCumulativeDrift: '',
};

const FORM_SNAPSHOT_KEYS = Object.keys(DEFAULT_STATE);

function isPlainObject(value) {
  return !!value && typeof value === 'object' && !Array.isArray(value);
}

function sanitizeFormSnapshot(value) {
  if (!isPlainObject(value)) {
    return { ...DEFAULT_STATE };
  }

  const hasExactShape =
    FORM_SNAPSHOT_KEYS.every((key) => typeof value[key] === 'string')
    && Object.keys(value).every((key) => FORM_SNAPSHOT_KEYS.includes(key));

  if (!hasExactShape) {
    return { ...DEFAULT_STATE };
  }

  return {
    eventType: value.eventType,
    agentId: value.agentId,
    principalId: value.principalId,
    actorType: value.actorType,
    serviceAccount: value.serviceAccount,
    roleScope: value.roleScope,
    op: value.op,
    t: value.t,
    p: value.p,
    paramsRaw: value.paramsRaw,
    ctxInitialRequest: value.ctxInitialRequest,
    ctxDataClassifications: value.ctxDataClassifications,
    ctxCumulativeDrift: value.ctxCumulativeDrift,
  };
}


export default function EnforcementDryRunForm() {
  const [formState, setFormState] = useState(DEFAULT_STATE);

  const [jsonMode, setJsonMode] = useState({ op: false, t: false, p: false });
  const [jsonErrors, setJsonErrors] = useState({ op: null, t: null, p: null });

  const [running, setRunning] = useState(false);
  const [result, setResult] = useState(null);
  const [submitError, setSubmitError] = useState(null);
  const [fieldErrors, setFieldErrors] = useState({});
  const [runs, setRuns] = useState([]);
  const [pinnedIndex, setPinnedIndex] = useState(null);
  const [policies, setPolicies] = useState([]);

  useEffect(() => {
    fetchPolicies().then(setPolicies).catch(() => {});
  }, []);

  function setField(key, value) {
    setFormState(s => ({ ...s, [key]: value }));
  }

  function toggleJsonMode(field) {
    setJsonMode((prev) => ({ ...prev, [field]: !prev[field] }));
    setJsonErrors((prev) => ({ ...prev, [field]: null }));
  }

  function handleAnchorChange(field, value) {
    setField(field, value);
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
    if (!formState.agentId.trim()) errors.agentId = 'Agent ID is required.';
    if (!formState.principalId.trim()) errors.principalId = 'Principal ID is required.';
    if (!formState.op.trim()) errors.op = 'Operation is required.';
    if (!formState.t.trim()) errors.t = 'Target / Tool is required.';
    if (formState.paramsRaw.trim()) {
      try {
        JSON.parse(formState.paramsRaw);
      } catch {
        errors.paramsRaw = 'Invalid JSON.';
      }
    }
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

    const identity = {
      agent_id: formState.agentId.trim(),
      principal_id: formState.principalId.trim(),
      actor_type: formState.actorType,
    };
    if (formState.serviceAccount.trim()) identity.service_account = formState.serviceAccount.trim();
    if (formState.roleScope.trim()) identity.role_scope = formState.roleScope.trim();

    let params = null;
    if (formState.paramsRaw.trim()) {
      params = JSON.parse(formState.paramsRaw);
    }

    let ctx = null;
    const classifications = formState.ctxDataClassifications.trim()
      ? formState.ctxDataClassifications.split(',').map((s) => s.trim()).filter(Boolean)
      : [];
    const driftVal = formState.ctxCumulativeDrift !== '' ? parseFloat(formState.ctxCumulativeDrift) : null;
    const hasCtx =
      formState.ctxInitialRequest.trim() || classifications.length > 0 || driftVal !== null;

    if (hasCtx) {
      ctx = {};
      if (formState.ctxInitialRequest.trim()) ctx.initial_request = formState.ctxInitialRequest.trim();
      if (classifications.length > 0) ctx.data_classifications = classifications;
      if (driftVal !== null) ctx.cumulative_drift = driftVal;
    }

    const payload = {
      event_type: formState.eventType,
      id: crypto.randomUUID(),
      ts: Date.now() / 1000,
      identity,
      op: formState.op.trim(),
      t: formState.t.trim(),
      params,
      ctx,
    };
    if (formState.p.trim()) payload.p = formState.p.trim();

    setRunning(true);
    try {
      const data = await runEnforce(payload);
      setResult(data);

      const entry = { formSnapshot: { ...formState }, decision: data.decision, ts: Date.now(), result: data };
      const next = [entry, ...runs];

      let newPinnedIndex = pinnedIndex !== null ? pinnedIndex + 1 : null;

      if (next.length > 10) {
        let dropIndex = next.length - 1;
        while (dropIndex >= 0 && dropIndex === newPinnedIndex) {
          dropIndex--;
        }
        if (dropIndex >= 0) {
          next.splice(dropIndex, 1);
          if (newPinnedIndex !== null && newPinnedIndex > dropIndex) {
            newPinnedIndex--;
          }
        }
      }

      setRuns(next);
      setPinnedIndex(newPinnedIndex);
    } catch (err) {
      setSubmitError(err.message);
    } finally {
      setRunning(false);
    }
  }

  function handlePinRun(index) {
    setPinnedIndex(prev => (prev === index ? null : index));
  }

  function handleSelectRun(item) {
    setFormState(sanitizeFormSnapshot(item.formSnapshot));
    setResult(item.result ?? null);
    setSubmitError(null);
    setFieldErrors({});
  }

  function handleClear() {
    setResult(null);
    setSubmitError(null);
    setFieldErrors({});
    setFormState(DEFAULT_STATE);
  }

  return (
    <div style={styles.panel}>
      <div style={styles.panelTitle}>Enforcement Dry Run</div>
      <div style={styles.sectionLayout}>
      <div style={{ flex: '1 1 0', minWidth: 0 }}>
      <form onSubmit={handleSubmit} noValidate>

        <fieldset style={styles.fieldset}>
          <legend style={styles.legend}>Identity</legend>
          <div style={styles.grid}>
            <div style={styles.field}>
              <label style={styles.label}>Event Type *</label>
              <select className={SELECT_CLASS} value={formState.eventType} onChange={(e) => setField('eventType', e.target.value)}>
                <option value="tool_call">tool_call</option>
                <option value="reasoning">reasoning</option>
              </select>
              <small style={styles.hint}>'tool_call' — an agent invoking a tool or API. 'reasoning' — an internal reasoning step being evaluated.</small>
            </div>
            <div style={styles.field}>
              <label style={styles.label}>Actor Type *</label>
              <select className={SELECT_CLASS} value={formState.actorType} onChange={(e) => setField('actorType', e.target.value)}>
                <option value="user">user</option>
                <option value="service">service</option>
                <option value="llm">llm</option>
                <option value="agent">agent</option>
              </select>
              <small style={styles.hint}>The type of entity making this request. 'user' = human; 'llm' = model acting autonomously; 'agent' = orchestrated agent; 'service' = background service.</small>
            </div>
            <div style={styles.field}>
              <label style={styles.label}>Agent ID *</label>
              <input
                className={INPUT_MONO_CLASS}
                type="text"
                value={formState.agentId}
                onChange={(e) => setField('agentId', e.target.value)}
                placeholder="agent-id"
              />
              {fieldErrors.agentId && <span style={styles.inlineError}>{fieldErrors.agentId}</span>}
              <small style={styles.hint}>Identifier for the agent instance. Used to track session context and cumulative drift across multiple calls.</small>
            </div>
            <div style={styles.field}>
              <label style={styles.label}>Principal ID *</label>
              <input
                className={INPUT_MONO_CLASS}
                type="text"
                value={formState.principalId}
                onChange={(e) => setField('principalId', e.target.value)}
                placeholder="principal-id"
              />
              {fieldErrors.principalId && <span style={styles.inlineError}>{fieldErrors.principalId}</span>}
              <small style={styles.hint}>The human or service principal on whose behalf the agent is acting.</small>
            </div>
            <div style={styles.field}>
              <label style={styles.label}>Service Account</label>
              <input
                className={INPUT_MONO_CLASS}
                type="text"
                value={formState.serviceAccount}
                onChange={(e) => setField('serviceAccount', e.target.value)}
                placeholder="optional"
              />
              <small style={styles.hint}>Optional. The service account used by this agent, if applicable.</small>
            </div>
            <div style={styles.field}>
              <label style={styles.label}>Role Scope</label>
              <input
                className={INPUT_MONO_CLASS}
                type="text"
                value={formState.roleScope}
                onChange={(e) => setField('roleScope', e.target.value)}
                placeholder="optional"
              />
              <small style={styles.hint}>Optional. The privilege scope or role active for this request. E.g. 'read-only', 'admin', 'data-analyst'.</small>
            </div>
          </div>
        </fieldset>

        <fieldset style={styles.fieldset}>
          <legend style={styles.legend}>Intent</legend>
          <small style={styles.fieldsetLead}>These fields describe the action a = (op, t, p). They are embedded as semantic vectors and compared against policy match anchors.</small>
          <div style={styles.grid}>
            <div style={styles.field}>
              <div style={styles.labelRow}>
                <label style={styles.label}>Operation *</label>
                <span style={styles.modeToggle}>
                  <button
                    type="button"
                    style={{ ...styles.modeToggleButton, ...(!jsonMode.op ? styles.modeToggleActive : {}) }}
                    onClick={() => jsonMode.op && toggleJsonMode('op')}
                  >
                    NL
                  </button>
                  <button
                    type="button"
                    style={{ ...styles.modeToggleButton, ...styles.modeToggleButtonLast, ...(jsonMode.op ? styles.modeToggleActive : {}) }}
                    onClick={() => !jsonMode.op && toggleJsonMode('op')}
                  >
                    JSON
                  </button>
                </span>
              </div>
              {jsonMode.op ? (
                <textarea
                  className={jsonErrors.op ? TEXTAREA_INVALID_CLASS : TEXTAREA_CLASS}
                  value={formState.op}
                  onChange={(e) => handleAnchorChange('op', e.target.value)}
                  placeholder='e.g. {"action": "read", "scope": "users"}'
                />
              ) : (
                <input
                  className={INPUT_CLASS}
                  type="text"
                  value={formState.op}
                  onChange={(e) => setField('op', e.target.value)}
                  placeholder="e.g. read from users table"
                />
              )}
              {fieldErrors.op && <span style={styles.inlineError}>{fieldErrors.op}</span>}
              {jsonErrors.op && <span style={styles.inlineError}>{jsonErrors.op}</span>}
              <small style={styles.hint}>What is the agent trying to do? Use natural language. E.g. 'query user records', 'send a summary email', 'write to S3'.</small>
            </div>
            <div style={styles.field}>
              <div style={styles.labelRow}>
                <label style={styles.label}>Target / Tool *</label>
                <span style={styles.modeToggle}>
                  <button
                    type="button"
                    style={{ ...styles.modeToggleButton, ...(!jsonMode.t ? styles.modeToggleActive : {}) }}
                    onClick={() => jsonMode.t && toggleJsonMode('t')}
                  >
                    NL
                  </button>
                  <button
                    type="button"
                    style={{ ...styles.modeToggleButton, ...styles.modeToggleButtonLast, ...(jsonMode.t ? styles.modeToggleActive : {}) }}
                    onClick={() => !jsonMode.t && toggleJsonMode('t')}
                  >
                    JSON
                  </button>
                </span>
              </div>
              {jsonMode.t ? (
                <textarea
                  className={jsonErrors.t ? TEXTAREA_INVALID_CLASS : TEXTAREA_CLASS}
                  value={formState.t}
                  onChange={(e) => handleAnchorChange('t', e.target.value)}
                  placeholder='e.g. {"tool": "postgres", "table": "users"}'
                />
              ) : (
                <input
                  className={INPUT_CLASS}
                  type="text"
                  value={formState.t}
                  onChange={(e) => setField('t', e.target.value)}
                  placeholder="e.g. postgres users table"
                />
              )}
              {fieldErrors.t && <span style={styles.inlineError}>{fieldErrors.t}</span>}
              {jsonErrors.t && <span style={styles.inlineError}>{jsonErrors.t}</span>}
              <small style={styles.hint}>What resource or tool is being accessed? E.g. 'postgres users table', 'Gmail API', 'production S3 bucket'.</small>
            </div>
            <div style={styles.field}>
              <div style={styles.labelRow}>
                <label style={styles.label}>Parameters</label>
                <span style={styles.modeToggle}>
                  <button
                    type="button"
                    style={{ ...styles.modeToggleButton, ...(!jsonMode.p ? styles.modeToggleActive : {}) }}
                    onClick={() => jsonMode.p && toggleJsonMode('p')}
                  >
                    NL
                  </button>
                  <button
                    type="button"
                    style={{ ...styles.modeToggleButton, ...styles.modeToggleButtonLast, ...(jsonMode.p ? styles.modeToggleActive : {}) }}
                    onClick={() => !jsonMode.p && toggleJsonMode('p')}
                  >
                    JSON
                  </button>
                </span>
              </div>
              {jsonMode.p ? (
                <textarea
                  className={jsonErrors.p ? TEXTAREA_INVALID_CLASS : TEXTAREA_CLASS}
                  value={formState.p}
                  onChange={(e) => handleAnchorChange('p', e.target.value)}
                  placeholder='e.g. {"columns": ["email", "name"]}'
                />
              ) : (
                <input
                  className={INPUT_CLASS}
                  type="text"
                  value={formState.p}
                  onChange={(e) => setField('p', e.target.value)}
                  placeholder="optional"
                />
              )}
              {jsonErrors.p && <span style={styles.inlineError}>{jsonErrors.p}</span>}
              <small style={styles.hint}>Optional. Describe the parameters of this action. E.g. 'filtering by user_id and date range'. Leave blank if not relevant.</small>
            </div>
          </div>
        </fieldset>

        <fieldset style={styles.fieldset}>
          <legend style={styles.legend}>Context</legend>
          <small style={styles.fieldsetLead}>Session context ctx provides accumulated signals that influence risk-sensitive policies (context_allow, context_deny, context_defer).</small>
          <div style={styles.grid}>
            <div style={styles.field}>
              <label style={styles.label}>Risk Context (NL)</label>
              <input
                className={INPUT_CLASS}
                type="text"
                value={formState.ctxInitialRequest}
                onChange={(e) => setField('ctxInitialRequest', e.target.value)}
                placeholder="optional"
              />
              <small style={styles.hint}>The original user request or session framing. E.g. 'user asked to generate a monthly financial report'. Policies using context anchors compare against this.</small>
            </div>
            <div style={styles.field}>
              <label style={styles.label}>Data Classifications (comma-separated)</label>
              <input
                className={INPUT_CLASS}
                type="text"
                value={formState.ctxDataClassifications}
                onChange={(e) => setField('ctxDataClassifications', e.target.value)}
                placeholder="e.g. pii, financial"
              />
              <small style={styles.hint}>Comma-separated labels for data types accessed in this session. E.g. 'pii, financial, internal'. Influences risk scoring.</small>
            </div>
            <div style={styles.field}>
              <label style={styles.label}>Cumulative Drift (0.0–1.0)</label>
              <input
                className={INPUT_CLASS}
                type="number"
                min="0"
                max="1"
                step="0.01"
                value={formState.ctxCumulativeDrift}
                onChange={(e) => setField('ctxCumulativeDrift', e.target.value)}
                placeholder="optional"
              />
              <small style={styles.hint}>Semantic distance between the agent's current action and the original user request (0.0 = perfectly aligned, 1.0 = completely diverged). Used to trigger drift-sensitive policies.</small>
            </div>
          </div>
        </fieldset>

        <fieldset style={styles.fieldset}>
          <legend style={styles.legend}>Advanced</legend>
          <div style={styles.field}>
            <label style={styles.label}>Params (JSON for MODIFY testing)</label>
            <textarea
              className={TEXTAREA_CLASS}
              value={formState.paramsRaw}
              onChange={(e) => setField('paramsRaw', e.target.value)}
              placeholder='optional — e.g. {"limit": 100}'
            />
            {fieldErrors.paramsRaw && <span style={styles.inlineError}>{fieldErrors.paramsRaw}</span>}
            <small style={styles.hint}>Optional structured parameters for testing MODIFY-type policy enforcement. Must be valid JSON. E.g. {`{"table": "users", "limit": 100}`}. Leave blank for standard ALLOW/DENY testing.</small>
          </div>
        </fieldset>

        {submitError && <p style={styles.errorText}>{submitError}</p>}

        <div style={styles.footer}>
          <button
            type="button"
            style={styles.clearButton}
            onClick={handleClear}
          >
            Clear
          </button>
          <button
            type="submit"
            style={running ? styles.submitButtonDisabled : styles.submitButton}
            disabled={running}
          >
            {running ? 'Running...' : 'Run Enforce'}
          </button>
        </div>
      </form>

      </div>
      <SuggestedIntentsPanel onSelect={(formSnapshot) => handleSelectRun({ formSnapshot, result: null })} />
      </div>
      <EnforcementResultPanel result={result} policies={policies} />
      <RecentRunsPanel runs={runs} pinnedIndex={pinnedIndex} onSelect={(formSnapshot) => { const item = runs.find(r => r.formSnapshot === formSnapshot) ?? { formSnapshot, result: null }; handleSelectRun(item); }} onPin={handlePinRun} onClear={() => { setRuns([]); setPinnedIndex(null); }} />
    </div>
  );
}
