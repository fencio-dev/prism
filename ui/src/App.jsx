import { useState } from 'react';
import PolicyList from './components/PolicyList';
import EnforcementDryRunForm from './components/EnforcementDryRunForm';
import BuildDatasetTab from './components/BuildDatasetTab';
import TelemetryTable from './components/TelemetryTable';

const TABS = ['Policies', 'Dry Run', 'Build Dataset', 'Telemetry'];

export default function App() {
  const [activeTab, setActiveTab] = useState('Policies');
  const [tenantId, setTenantId] = useState(
    () => localStorage.getItem('guardTenantId') || ''
  );
  const [tenantTipOpen, setTenantTipOpen] = useState(false);

  function handleTenantChange(e) {
    const val = e.target.value;
    setTenantId(val);
    localStorage.setItem('guardTenantId', val);
  }

  return (
    <div className="container">
      <header className="header">
        <span className="header-title">Guard</span>
        <nav className="tab-bar">
          {TABS.map((tab) => (
            <button
              key={tab}
              className={`tab-button${activeTab === tab ? ' active' : ''}`}
              onClick={() => setActiveTab(tab)}
            >
              {tab}
            </button>
          ))}
        </nav>
        <div className="header-tenant">
          <label htmlFor="tenant-id-input" style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
            Tenant ID
            <span style={{ position: 'relative', display: 'inline-flex' }}>
              <span
                title="Enter your tenant ID, or use 'demo-tenant' for the developer environment."
                onClick={() => setTenantTipOpen(o => !o)}
                style={{ cursor: 'pointer', color: '#999', fontSize: 12, lineHeight: 1, userSelect: 'none' }}
              >ⓘ</span>
              {tenantTipOpen && (
                <span style={{
                  position: 'absolute',
                  top: '100%',
                  right: 0,
                  marginTop: 6,
                  background: '#1a1a1a',
                  color: '#fff',
                  fontSize: 12,
                  padding: '7px 10px',
                  borderRadius: 6,
                  whiteSpace: 'nowrap',
                  zIndex: 100,
                  boxShadow: '0 2px 8px rgba(0,0,0,0.2)',
                  lineHeight: 1.5,
                }}>
                  Enter your tenant ID, or use{' '}
                  <code style={{ background: '#333', borderRadius: 3, padding: '1px 4px' }}>demo-tenant</code>
                  {' '}for the developer environment.
                </span>
              )}
            </span>
          </label>
          <input
            id="tenant-id-input"
            type="text"
            value={tenantId}
            onChange={handleTenantChange}
            placeholder="your-tenant-id"
            spellCheck={false}
          />
        </div>
      </header>
      <main className="content">
        <div className="tab-panel">
          {activeTab === 'Policies' && <PolicyList />}
          {activeTab === 'Dry Run' && <EnforcementDryRunForm />}
          {activeTab === 'Build Dataset' && <BuildDatasetTab />}
          {activeTab === 'Telemetry' && <TelemetryTable />}
        </div>
      </main>
    </div>
  );
}
