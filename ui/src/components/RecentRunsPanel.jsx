import { History, Pin, PinOff, Trash2 } from 'lucide-react';
import PrismEmptyState from './PrismEmptyState';

const BADGE_COLORS = {
  ALLOW:   { background: 'rgba(34, 139, 69, 0.10)', color: '#1f8f4d', border: '1px solid rgba(34, 139, 69, 0.30)' },
  DENY:    { background: 'rgba(194, 65, 65, 0.10)', color: '#c24141', border: '1px solid rgba(194, 65, 65, 0.30)' },
  MODIFY:  { background: 'rgba(183, 121, 31, 0.12)', color: '#b7791f', border: '1px solid rgba(183, 121, 31, 0.32)' },
  STEP_UP: { background: 'rgba(37, 99, 235, 0.10)', color: '#2563eb', border: '1px solid rgba(37, 99, 235, 0.30)' },
  DEFER:   { background: 'rgba(83, 81, 70, 0.10)', color: '#6b6659', border: '1px solid rgba(83, 81, 70, 0.26)' },
};

const styles = {
  section: {
    borderTop: '1px solid var(--prism-border-default)',
    marginTop: 28,
    paddingTop: 24,
  },
  title: {
    fontSize: 14,
    fontWeight: 600,
    color: 'var(--prism-text-primary)',
    marginBottom: 12,
  },
  row: {
    display: 'flex',
    alignItems: 'center',
    gap: 12,
    padding: '8px 10px',
    borderBottom: '1px solid var(--prism-border-subtle)',
    cursor: 'pointer',
  },
  rowPinned: {
    display: 'flex',
    alignItems: 'center',
    gap: 12,
    padding: '8px 10px',
    borderBottom: '1px solid var(--prism-border-subtle)',
    cursor: 'pointer',
    background: 'var(--prism-accent-subtle)',
  },
  opText: {
    fontSize: 13,
    fontFamily: '"JetBrains Mono", monospace',
    color: 'var(--prism-text-primary)',
    flex: '0 0 auto',
    maxWidth: 280,
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
  },
  sep: {
    fontSize: 13,
    color: 'var(--prism-text-muted)',
    flex: '0 0 auto',
  },
  tText: {
    fontSize: 13,
    fontFamily: '"JetBrains Mono", monospace',
    color: 'var(--prism-text-secondary)',
    flex: '1 1 auto',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
    minWidth: 0,
  },
  badge: {
    fontSize: 11,
    fontWeight: 600,
    padding: '2px 8px',
    borderRadius: 4,
    flex: '0 0 auto',
    letterSpacing: 0.3,
  },
  pinButton: {
    background: 'none',
    border: 'none',
    cursor: 'pointer',
    padding: '0 4px',
    flex: '0 0 auto',
    lineHeight: 1,
    display: 'inline-flex',
    alignItems: 'center',
    justifyContent: 'center',
    color: 'var(--prism-text-secondary)',
  },
  pinButtonPinned: {
    color: 'var(--prism-accent)',
  },
};

function truncate(str, max) {
  if (!str) return '';
  return str.length > max ? str.slice(0, max) + '…' : str;
}

export default function RecentRunsPanel({ runs, pinnedIndex, onSelect, onPin, onClear }) {
  if (!runs || runs.length === 0) {
    return (
      <div style={styles.section}>
        <div style={styles.title}>Recent Runs</div>
        <PrismEmptyState
          icon={History}
          title="No recent runs"
          description="Dry run submissions will appear here for quick replay and pinning."
        />
      </div>
    );
  }

  // Build display list: pinned entry first (if any), then the rest in order.
  // Each item carries its original index so onPin receives the correct value.
  let displayList;
  if (pinnedIndex !== null && pinnedIndex >= 0 && pinnedIndex < runs.length) {
    const pinned = { ...runs[pinnedIndex], originalIndex: pinnedIndex, isPinned: true };
    const rest = runs
      .map((r, i) => ({ ...r, originalIndex: i, isPinned: false }))
      .filter((_, i) => i !== pinnedIndex);
    displayList = [pinned, ...rest];
  } else {
    displayList = runs.map((r, i) => ({ ...r, originalIndex: i, isPinned: false }));
  }

  return (
    <div style={styles.section}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
        <div style={{ ...styles.title, marginBottom: 0 }}>Recent Runs</div>
        {onClear && (
          <button
            style={{ background: 'none', border: 'none', cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 4, color: 'var(--prism-text-secondary)', fontSize: 12, padding: '2px 4px' }}
            title="Clear recent runs"
            onClick={onClear}
          >
            <Trash2 size={13} />
            Clear
          </button>
        )}
      </div>
      <div>
        {displayList.map((item) => {
          const badgeColors = BADGE_COLORS[item.decision] ?? BADGE_COLORS.DEFER;
          const rowStyle = item.isPinned ? styles.rowPinned : styles.row;
          const originalIndex = item.originalIndex;

          return (
            <div
              key={originalIndex}
              style={rowStyle}
              onClick={() => onSelect(item.formSnapshot)}
            >
              <span style={styles.opText} title={item.formSnapshot.op}>
                {truncate(item.formSnapshot.op, 40)}
              </span>
              <span style={styles.sep}>→</span>
              <span style={styles.tText} title={item.formSnapshot.t}>
                {truncate(item.formSnapshot.t, 30)}
              </span>
              <span style={{ ...styles.badge, ...badgeColors }}>
                {item.decision ?? '—'}
              </span>
              <button
                style={item.isPinned ? { ...styles.pinButton, ...styles.pinButtonPinned } : styles.pinButton}
                title={item.isPinned ? 'Unpin' : 'Pin'}
                onClick={(e) => {
                  e.stopPropagation();
                  onPin(originalIndex);
                }}
              >
                {item.isPinned ? <Pin size={14} /> : <PinOff size={14} />}
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
}
