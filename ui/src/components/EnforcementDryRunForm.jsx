import { useEffect, useState } from 'react';
import { runEnforce } from '../api/enforce';
import { fetchPolicies } from '../api/policies';
import EnforcementResultPanel from './EnforcementResultPanel';
import RecentRunsPanel from './RecentRunsPanel';
import SuggestedIntentsPanel from './SuggestedIntentsPanel';

const styles = {
  panel: {
    border: '1px solid #ddd',
    borderRadius: 6,
    padding: 24,
    marginBottom: 24,
    background: '#fafafa',
  },
  panelTitle: {
    fontSize: 15,
    fontWeight: 600,
    marginBottom: 20,
    color: '#1a1a1a',
  },
  fieldset: {
    border: '1px solid #e8e8e8',
    borderRadius: 4,
    padding: '16px 20px',
    marginBottom: 16,
  },
  legend: {
    fontSize: 13,
    fontWeight: 600,
    color: '#555',
    padding: '0 6px',
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
    color: '#333',
  },
  input: {
    fontSize: 13,
    padding: '6px 10px',
    border: '1px solid #ccc',
    borderRadius: 4,
    fontFamily: 'inherit',
    background: '#fff',
  },
  select: {
    fontSize: 13,
    padding: '6px 10px',
    border: '1px solid #ccc',
    borderRadius: 4,
    fontFamily: 'inherit',
    background: '#fff',
  },
  textarea: {
    fontSize: 13,
    padding: '6px 10px',
    border: '1px solid #ccc',
    borderRadius: 4,
    fontFamily: 'monospace',
    background: '#fff',
    minHeight: 80,
    resize: 'vertical',
  },
  footer: {
    display: 'flex',
    gap: 10,
    justifyContent: 'flex-end',
    marginTop: 20,
  },
  submitButton: {
    fontSize: 13,
    padding: '7px 18px',
    border: 'none',
    borderRadius: 4,
    background: '#1a1a1a',
    color: '#fff',
    cursor: 'pointer',
    fontFamily: 'inherit',
  },
  submitButtonDisabled: {
    fontSize: 13,
    padding: '7px 18px',
    border: 'none',
    borderRadius: 4,
    background: '#aaa',
    color: '#fff',
    cursor: 'not-allowed',
    fontFamily: 'inherit',
  },
  clearButton: {
    fontSize: 13,
    padding: '7px 18px',
    border: '1px solid #ccc',
    borderRadius: 4,
    background: '#fff',
    color: '#333',
    cursor: 'pointer',
    fontFamily: 'inherit',
  },
  errorText: {
    fontSize: 13,
    color: '#c0392b',
    marginTop: 10,
  },
  inlineError: {
    fontSize: 12,
    color: '#c0392b',
    marginTop: 2,
  },
  hint: {
    display: 'block',
    fontSize: '11px',
    color: '#888',
    marginTop: '3px',
    lineHeight: '1.4',
  },
  labelRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
  },
  modeToggle: {
    fontSize: 11,
    color: '#888',
    cursor: 'pointer',
    userSelect: 'none',
    marginLeft: 'auto',
  },
  modeToggleActive: {
    fontWeight: 700,
    color: '#1a1a1a',
  },
  inputInvalid: {
    fontSize: 13,
    padding: '6px 10px',
    border: '1px solid #c0392b',
    borderRadius: 4,
    fontFamily: 'inherit',
    background: '#fff',
    resize: 'vertical',
    minHeight: 72,
  },
};

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
      <div style={{ display: 'flex', gap: 28, alignItems: 'flex-start' }}>
      <div style={{ flex: '1 1 0', minWidth: 0 }}>
      <form onSubmit={handleSubmit} noValidate>

        <fieldset style={styles.fieldset}>
          <legend style={styles.legend}>Identity</legend>
          <div style={styles.grid}>
            <div style={styles.field}>
              <label style={styles.label}>Event Type *</label>
              <select style={styles.select} value={formState.eventType} onChange={(e) => setField('eventType', e.target.value)}>
                <option value="tool_call">tool_call</option>
                <option value="reasoning">reasoning</option>
              </select>
              <small style={styles.hint}>'tool_call' — an agent invoking a tool or API. 'reasoning' — an internal reasoning step being evaluated.</small>
            </div>
            <div style={styles.field}>
              <label style={styles.label}>Actor Type *</label>
              <select style={styles.select} value={formState.actorType} onChange={(e) => setField('actorType', e.target.value)}>
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
                style={styles.input}
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
                style={styles.input}
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
                style={styles.input}
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
                style={styles.input}
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
          <small style={{ ...styles.hint, marginBottom: 12 }}>These fields describe the action a = (op, t, p). They are embedded as semantic vectors and compared against policy match anchors.</small>
          <div style={styles.grid}>
            <div style={styles.field}>
              <div style={styles.labelRow}>
                <label style={styles.label}>Operation *</label>
                <span style={styles.modeToggle}>
                  <span
                    style={!jsonMode.op ? styles.modeToggleActive : {}}
                    onClick={() => jsonMode.op && toggleJsonMode('op')}
                  >NL</span>
                  {' | '}
                  <span
                    style={jsonMode.op ? styles.modeToggleActive : {}}
                    onClick={() => !jsonMode.op && toggleJsonMode('op')}
                  >JSON</span>
                </span>
              </div>
              {jsonMode.op ? (
                <textarea
                  style={jsonErrors.op ? styles.inputInvalid : { ...styles.textarea }}
                  value={formState.op}
                  onChange={(e) => handleAnchorChange('op', e.target.value)}
                  placeholder='e.g. {"action": "read", "scope": "users"}'
                />
              ) : (
                <input
                  style={styles.input}
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
                  <span
                    style={!jsonMode.t ? styles.modeToggleActive : {}}
                    onClick={() => jsonMode.t && toggleJsonMode('t')}
                  >NL</span>
                  {' | '}
                  <span
                    style={jsonMode.t ? styles.modeToggleActive : {}}
                    onClick={() => !jsonMode.t && toggleJsonMode('t')}
                  >JSON</span>
                </span>
              </div>
              {jsonMode.t ? (
                <textarea
                  style={jsonErrors.t ? styles.inputInvalid : { ...styles.textarea }}
                  value={formState.t}
                  onChange={(e) => handleAnchorChange('t', e.target.value)}
                  placeholder='e.g. {"tool": "postgres", "table": "users"}'
                />
              ) : (
                <input
                  style={styles.input}
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
                  <span
                    style={!jsonMode.p ? styles.modeToggleActive : {}}
                    onClick={() => jsonMode.p && toggleJsonMode('p')}
                  >NL</span>
                  {' | '}
                  <span
                    style={jsonMode.p ? styles.modeToggleActive : {}}
                    onClick={() => !jsonMode.p && toggleJsonMode('p')}
                  >JSON</span>
                </span>
              </div>
              {jsonMode.p ? (
                <textarea
                  style={jsonErrors.p ? styles.inputInvalid : { ...styles.textarea }}
                  value={formState.p}
                  onChange={(e) => handleAnchorChange('p', e.target.value)}
                  placeholder='e.g. {"columns": ["email", "name"]}'
                />
              ) : (
                <input
                  style={styles.input}
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
          <small style={{ ...styles.hint, marginBottom: 12 }}>Session context ctx provides accumulated signals that influence risk-sensitive policies (context_allow, context_deny, context_defer).</small>
          <div style={styles.grid}>
            <div style={styles.field}>
              <label style={styles.label}>Risk Context (NL)</label>
              <input
                style={styles.input}
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
                style={styles.input}
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
                style={styles.input}
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
              style={styles.textarea}
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
