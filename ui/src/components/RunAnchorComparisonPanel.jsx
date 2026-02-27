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
    <div style={{ marginBottom: 6 }}>
      <span style={{ fontSize: 10, color: '#999', display: 'block', marginBottom: 2 }}>{label}:</span>
      <div style={{ minHeight: 36 }}>
        {formatted ? (
          isJson ? (
            <pre style={{
              margin: 0,
              fontSize: 11,
              color: tone,
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
              overflowWrap: 'break-word',
              fontFamily: 'monospace',
              lineHeight: 1.35,
            }}>
              {formatted}
            </pre>
          ) : (
            <span style={{ fontSize: 11, color: tone, wordBreak: 'break-word' }}>
              {formatted}
            </span>
          )
        ) : (
          <span style={{ fontSize: 11, color: tone, wordBreak: 'break-word' }}>-</span>
        )}
      </div>
    </div>
  );
}

export default function RunAnchorComparisonPanel({ intentValue, policyAnchorValue }) {
  return (
    <>
      <ComparisonValue label="intent" value={intentValue} tone="#333" />
      <ComparisonValue label="anchor" value={policyAnchorValue} tone="#555" />
    </>
  );
}
