function tryFormatJson(value) {
  if (value === null || value === undefined) return null;

  if (typeof value === 'object') {
    try {
      return JSON.stringify(value, null, 2);
    } catch {
      return String(value);
    }
  }

  if (typeof value !== 'string') {
    return String(value);
  }

  const trimmed = value.trim();
  if (!trimmed) return null;

  try {
    const parsed = JSON.parse(trimmed);
    if (parsed !== null && typeof parsed === 'object') {
      return JSON.stringify(parsed, null, 2);
    }
  } catch {
    // Not JSON, treat as plain text.
  }

  return value;
}

function ComparisonValue({ label, value, tone }) {
  const formatted = tryFormatJson(value);
  const isJson = typeof formatted === 'string' && formatted.includes('\n');

  return (
    <div className="mb-2.5">
      <span className="mb-1 block text-xs font-medium uppercase tracking-wider text-[var(--prism-text-secondary)]">{label}</span>
      <div className="min-h-9">
        {formatted ? (
          isJson ? (
            <pre
              className="m-0 overflow-x-auto rounded-sm border border-[var(--prism-border-default)] bg-[var(--prism-bg-elevated)] p-3 font-mono text-sm leading-[1.35]"
              style={{ color: tone, whiteSpace: 'pre-wrap', wordBreak: 'break-word', overflowWrap: 'break-word' }}
            >
              {formatted}
            </pre>
          ) : (
            <span className="block rounded-sm border border-[var(--prism-border-default)] bg-[var(--prism-bg-elevated)] p-2.5 font-mono text-sm" style={{ color: tone, wordBreak: 'break-word' }}>
              {formatted}
            </span>
          )
        ) : (
          <span className="block rounded-sm border border-[var(--prism-border-default)] bg-[var(--prism-bg-elevated)] p-2.5 font-mono text-sm" style={{ color: tone, wordBreak: 'break-word' }}>-</span>
        )}
      </div>
    </div>
  );
}

export default function RunAnchorComparisonPanel({ intentValue, policyAnchorValue }) {
  return (
    <div className="space-y-1.5">
      <ComparisonValue label="intent" value={intentValue} tone="#3a352d" />
      <ComparisonValue label="anchor" value={policyAnchorValue} tone="#6b6659" />
    </div>
  );
}
