import { useState } from 'react';
import { Shield, Play, Database, Activity } from 'lucide-react';
import PolicyList from './components/PolicyList';
import EnforcementDryRunForm from './components/EnforcementDryRunForm';
import BuildDatasetTab from './components/BuildDatasetTab';
import TelemetryTable from './components/TelemetryTable';

const NAV_ITEMS = [
  { label: 'Policies',      icon: Shield   },
  { label: 'Dry Run',       icon: Play     },
  { label: 'Build Dataset', icon: Database },
  { label: 'Telemetry',     icon: Activity },
];

export default function App() {
  const [activeTab, setActiveTab] = useState('Policies');
  const [tenantId, setTenantId] = useState(
    () => localStorage.getItem('guardTenantId') || ''
  );
  const usesPanelLayout = activeTab === 'Build Dataset' || activeTab === 'Telemetry';

  function handleTenantChange(e) {
    const val = e.target.value;
    setTenantId(val);
    localStorage.setItem('guardTenantId', val);
  }

  function renderNavButton({ label, icon: Icon }) {
    const isActive = activeTab === label;

    return (
      <button
        key={label}
        type="button"
        onClick={() => setActiveTab(label)}
        aria-current={isActive ? 'page' : undefined}
        aria-label={label}
        title={label}
        className={[
          'flex h-9 w-full items-center gap-3 rounded px-3 text-left text-sm font-medium transition-colors',
          'focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[var(--prism-accent)]/40',
          isActive
            ? 'border-l-2 border-[var(--prism-accent)] bg-[var(--prism-accent-subtle)] pl-2 text-[var(--prism-accent)]'
            : 'border-l-2 border-transparent text-[var(--prism-text-secondary)] hover:bg-[var(--prism-accent-subtle)] hover:text-[var(--prism-text-primary)]',
        ].join(' ')}
      >
        <Icon size={16} strokeWidth={1.75} className="shrink-0" />
        <span className="hidden sm:inline">{label}</span>
      </button>
    );
  }

  return (
    <div className="flex h-screen overflow-hidden bg-[var(--prism-bg-base)]">
      <aside className="flex w-14 shrink-0 flex-col border-r border-[var(--prism-border-default)] bg-[var(--prism-bg-surface)] sm:w-60">
        <div className="flex h-14 shrink-0 items-center justify-center border-b border-[var(--prism-border-default)] px-3 sm:justify-start sm:px-4">
          <img
            src="/prism.mark.png"
            alt="Prism"
            className="h-6 w-6 shrink-0 object-contain"
          />
          <span className="ml-2.5 hidden text-sm font-semibold tracking-wide text-[var(--prism-text-primary)] sm:inline">
            Prism
          </span>
        </div>

        <nav aria-label="Primary" className="flex flex-col gap-1 p-2 sm:p-3">
          {NAV_ITEMS.map(renderNavButton)}
        </nav>

        <div className="flex-1" />

        <div className="hidden border-t border-[var(--prism-border-default)] p-3 sm:flex sm:flex-col sm:gap-1.5">
          <label
            htmlFor="tenant-id-input"
            className="text-xs font-medium uppercase tracking-wider text-[var(--prism-text-secondary)]"
          >
            Tenant ID
          </label>
          <input
            id="tenant-id-input"
            type="text"
            value={tenantId}
            onChange={handleTenantChange}
            placeholder="your-tenant-id"
            spellCheck={false}
            className="h-8 w-full rounded-sm border border-[var(--prism-border-default)] bg-[var(--prism-bg-elevated)] px-2.5 font-mono text-sm tracking-tight text-[var(--prism-text-primary)] placeholder:text-[var(--prism-text-muted)] focus:outline-none focus:ring-1 focus:ring-[var(--prism-accent)]/40"
          />
        </div>
      </aside>

      <main
        className={[
          'flex-1 bg-[var(--prism-bg-base)]',
          usesPanelLayout ? 'flex min-h-0 flex-col overflow-hidden' : 'overflow-y-auto',
        ].join(' ')}
      >
        <div className="border-b border-[var(--prism-border-default)] p-3 sm:hidden">
          <label
            htmlFor="tenant-id-input-mobile"
            className="block text-xs font-medium uppercase tracking-wider text-[var(--prism-text-secondary)]"
          >
            Tenant ID
          </label>
          <input
            id="tenant-id-input-mobile"
            type="text"
            value={tenantId}
            onChange={handleTenantChange}
            placeholder="your-tenant-id"
            spellCheck={false}
            className="mt-1 h-8 w-full rounded-sm border border-[var(--prism-border-default)] bg-[var(--prism-bg-elevated)] px-2.5 font-mono text-sm tracking-tight text-[var(--prism-text-primary)] placeholder:text-[var(--prism-text-muted)] focus:outline-none focus:ring-1 focus:ring-[var(--prism-accent)]/40"
          />
        </div>
        {activeTab === 'Policies'      && <PolicyList />}
        {activeTab === 'Dry Run'       && <EnforcementDryRunForm />}
        {activeTab === 'Build Dataset' && (
          <div className="min-h-0 flex-1 p-6">
            <BuildDatasetTab />
          </div>
        )}
        {activeTab === 'Telemetry' && (
          <div className="min-h-0 flex-1 p-6">
            <TelemetryTable />
          </div>
        )}
      </main>
    </div>
  );
}
