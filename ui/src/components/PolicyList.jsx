import { useState, useEffect } from 'react';
import { ListFilter, ShieldPlus } from 'lucide-react';
import { fetchPolicies, deletePolicy, togglePolicyStatus } from '../api/policies';
import PolicyForm from './PolicyForm';
import PrismEmptyState from './PrismEmptyState';
import { Badge } from './ui/badge';
import { Switch } from './ui/switch';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from './ui/table';

function formatDate(timestamp) {
  return new Date(timestamp * 1000).toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  });
}

function getStatusBadgeClass(status) {
  const normalized = (status || '').toLowerCase();

  if (normalized === 'active') {
    return 'border-green-600/30 bg-green-100 text-green-700';
  }

  if (normalized === 'disabled') {
    return 'border-stone-400/40 bg-stone-100 text-stone-600';
  }

  return 'border-sky-600/30 bg-sky-100 text-sky-700';
}

export default function PolicyList() {
  const [policies, setPolicies] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [deletingIds, setDeletingIds] = useState(new Set());
  const [togglingIds, setTogglingIds] = useState(new Set());
  const [showForm, setShowForm] = useState(false);
  const [selectedPolicy, setSelectedPolicy] = useState(null);
  const [viewPolicy, setViewPolicy] = useState(null);
  const [selectedStatus, setSelectedStatus] = useState('all');

  const filteredPolicies = policies.filter((policy) => {
    if (selectedStatus === 'all') {
      return true;
    }
    return (policy.status || '').toLowerCase() === selectedStatus;
  });

  const hasFilters = selectedStatus !== 'all';
  const emptyTitle = hasFilters ? 'No matching policies' : 'No policies yet';
  const emptyDescription = hasFilters
    ? 'No policies match the selected status filter.'
    : 'Create your first policy to begin enforcing guardrails for agent actions.';
  const emptyActionLabel = hasFilters ? 'Clear Filter' : 'Add Policy';
  const emptyAction = hasFilters
    ? () => setSelectedStatus('all')
    : () => setShowForm(true);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchPolicies();
      setPolicies(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function handleToggle(id) {
    setTogglingIds((prev) => new Set(prev).add(id));
    try {
      await togglePolicyStatus(id);
      await load();
    } catch (err) {
      setError(err.message);
    } finally {
      setTogglingIds((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
    }
  }

  async function handleDelete(id) {
    setDeletingIds((prev) => new Set(prev).add(id));
    try {
      await deletePolicy(id);
      await load();
    } catch (err) {
      setError(err.message);
    } finally {
      setDeletingIds((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
    }
  }

  return (
    <div className="px-6 py-6">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <span className="text-sm text-[var(--prism-text-secondary)]">
          {loading ? '' : `${filteredPolicies.length} ${filteredPolicies.length === 1 ? 'policy' : 'policies'}`}
        </span>
        <div className="flex items-center gap-2">
          <select
            className="h-8 rounded-sm border border-[var(--prism-border-default)] bg-[var(--prism-bg-elevated)] px-3 text-sm text-[var(--prism-text-primary)] outline-none transition-colors focus:border-[var(--prism-accent)]/60 focus:ring-1 focus:ring-[var(--prism-accent)]/30"
            value={selectedStatus}
            onChange={(e) => setSelectedStatus(e.target.value)}
            aria-label="Filter policies by status"
          >
            <option value="all">All</option>
            <option value="active">Active</option>
            <option value="disabled">Disabled</option>
          </select>
          <button
            className="inline-flex h-8 items-center justify-center rounded border border-[var(--prism-accent)]/40 bg-[var(--prism-accent)] px-3 text-sm font-semibold text-white transition-colors hover:bg-[#b75a3b] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[var(--prism-accent)]/40 active:bg-[#a65135]"
            onClick={() => setShowForm(true)}
          >
            Add Policy
          </button>
        </div>
      </div>

      {showForm && (
        <PolicyForm
          policy={selectedPolicy}
          onSuccess={() => { setShowForm(false); setSelectedPolicy(null); load(); }}
          onCancel={() => { setShowForm(false); setSelectedPolicy(null); }}
        />
      )}

      {viewPolicy && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-[rgba(39,36,30,0.42)] px-4"
          onClick={() => setViewPolicy(null)}
        >
          <div
            className="max-h-[80vh] w-full max-w-[560px] overflow-y-auto rounded border border-[var(--prism-border-default)] bg-[var(--prism-bg-surface)] p-6 shadow"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="mb-5 text-lg font-semibold text-[var(--prism-text-primary)]">Policy Details</div>
            <div className="grid grid-cols-[160px_1fr] gap-x-4 gap-y-2 text-sm">

              <span className="pt-px text-xs font-medium uppercase tracking-wider text-[var(--prism-text-secondary)]">ID</span>
              <span className="break-words font-mono text-sm text-[var(--prism-text-primary)]">{viewPolicy.id}</span>

              <span className="pt-px text-xs font-medium uppercase tracking-wider text-[var(--prism-text-secondary)]">Name</span>
              <span className="break-words text-[var(--prism-text-primary)]">{viewPolicy.name}</span>

              <span className="pt-px text-xs font-medium uppercase tracking-wider text-[var(--prism-text-secondary)]">Tenant ID</span>
              <span className="break-words font-mono text-sm text-[var(--prism-text-primary)]">{viewPolicy.tenant_id ?? '—'}</span>

              <span className="pt-px text-xs font-medium uppercase tracking-wider text-[var(--prism-text-secondary)]">Status</span>
              <span className="break-words text-[var(--prism-text-primary)]">{viewPolicy.status}</span>

              <span className="pt-px text-xs font-medium uppercase tracking-wider text-[var(--prism-text-secondary)]">Policy Type</span>
              <span className="break-words text-[var(--prism-text-primary)]">{viewPolicy.policy_type ?? viewPolicy.type ?? '—'}</span>

              <span className="pt-px text-xs font-medium uppercase tracking-wider text-[var(--prism-text-secondary)]">Priority</span>
              <span className="break-words text-[var(--prism-text-primary)]">{viewPolicy.priority ?? '—'}</span>

              <div className="col-span-2 my-1 border-t border-[var(--prism-border-default)]" />

              <span className="pt-px text-xs font-medium uppercase tracking-wider text-[var(--prism-text-secondary)]">Match: Operation</span>
              <span className="break-words text-[var(--prism-text-primary)]">{viewPolicy.match?.op ?? viewPolicy.match?.operation ?? '—'}</span>

              <span className="pt-px text-xs font-medium uppercase tracking-wider text-[var(--prism-text-secondary)]">Match: Target/Tool</span>
              <span className="break-words text-[var(--prism-text-primary)]">{viewPolicy.match?.t ?? viewPolicy.match?.target_tool ?? viewPolicy.match?.tool ?? viewPolicy.match?.target ?? '—'}</span>

              {viewPolicy.match?.parameters && (
                <>
                  <span className="pt-px text-xs font-medium uppercase tracking-wider text-[var(--prism-text-secondary)]">Match: Parameters</span>
                  <span className="break-words font-mono text-sm text-[var(--prism-text-primary)]">{JSON.stringify(viewPolicy.match.parameters)}</span>
                </>
              )}

              {viewPolicy.match?.risk_context && (
                <>
                  <span className="pt-px text-xs font-medium uppercase tracking-wider text-[var(--prism-text-secondary)]">Match: Risk Context</span>
                  <span className="break-words font-mono text-sm text-[var(--prism-text-primary)]">{JSON.stringify(viewPolicy.match.risk_context)}</span>
                </>
              )}

              <div className="col-span-2 my-1 border-t border-[var(--prism-border-default)]" />

              <span className="pt-px text-xs font-medium uppercase tracking-wider text-[var(--prism-text-secondary)]">Threshold: Action</span>
              <span className="break-words font-mono text-sm text-[var(--prism-text-primary)]">{viewPolicy.thresholds?.action ?? '—'}</span>

              <span className="pt-px text-xs font-medium uppercase tracking-wider text-[var(--prism-text-secondary)]">Threshold: Resource</span>
              <span className="break-words font-mono text-sm text-[var(--prism-text-primary)]">{viewPolicy.thresholds?.resource ?? '—'}</span>

              <span className="pt-px text-xs font-medium uppercase tracking-wider text-[var(--prism-text-secondary)]">Threshold: Data</span>
              <span className="break-words font-mono text-sm text-[var(--prism-text-primary)]">{viewPolicy.thresholds?.data ?? '—'}</span>

              <span className="pt-px text-xs font-medium uppercase tracking-wider text-[var(--prism-text-secondary)]">Threshold: Risk</span>
              <span className="break-words font-mono text-sm text-[var(--prism-text-primary)]">{viewPolicy.thresholds?.risk ?? '—'}</span>

              {viewPolicy.weights && (
                <>
                  <div className="col-span-2 my-1 border-t border-[var(--prism-border-default)]" />
                  <span className="pt-px text-xs font-medium uppercase tracking-wider text-[var(--prism-text-secondary)]">Weight: Action</span>
                  <span className="break-words font-mono text-sm text-[var(--prism-text-primary)]">{viewPolicy.weights.action ?? '—'}</span>

                  <span className="pt-px text-xs font-medium uppercase tracking-wider text-[var(--prism-text-secondary)]">Weight: Resource</span>
                  <span className="break-words font-mono text-sm text-[var(--prism-text-primary)]">{viewPolicy.weights.resource ?? '—'}</span>

                  <span className="pt-px text-xs font-medium uppercase tracking-wider text-[var(--prism-text-secondary)]">Weight: Data</span>
                  <span className="break-words font-mono text-sm text-[var(--prism-text-primary)]">{viewPolicy.weights.data ?? '—'}</span>

                  <span className="pt-px text-xs font-medium uppercase tracking-wider text-[var(--prism-text-secondary)]">Weight: Risk</span>
                  <span className="break-words font-mono text-sm text-[var(--prism-text-primary)]">{viewPolicy.weights.risk ?? '—'}</span>
                </>
              )}

              {viewPolicy.drift_threshold != null && (
                <>
                   <div className="col-span-2 my-1 border-t border-[var(--prism-border-default)]" />
                   <span className="pt-px text-xs font-medium uppercase tracking-wider text-[var(--prism-text-secondary)]">Drift Threshold</span>
                   <span className="break-words font-mono text-sm text-[var(--prism-text-primary)]">{viewPolicy.drift_threshold}</span>
                </>
              )}

              {viewPolicy.notes && (
                <>
                   <div className="col-span-2 my-1 border-t border-[var(--prism-border-default)]" />
                   <span className="pt-px text-xs font-medium uppercase tracking-wider text-[var(--prism-text-secondary)]">Notes</span>
                   <span className="break-words text-[var(--prism-text-primary)]">{viewPolicy.notes}</span>
                </>
              )}

              <div className="col-span-2 my-1 border-t border-[var(--prism-border-default)]" />

              <span className="pt-px text-xs font-medium uppercase tracking-wider text-[var(--prism-text-secondary)]">Created At</span>
              <span className="break-words text-[var(--prism-text-primary)]">{formatDate(viewPolicy.created_at)}</span>

              <span className="pt-px text-xs font-medium uppercase tracking-wider text-[var(--prism-text-secondary)]">Updated At</span>
              <span className="break-words text-[var(--prism-text-primary)]">{viewPolicy.updated_at ? formatDate(viewPolicy.updated_at) : '—'}</span>

            </div>
            <button
              className="mt-6 inline-flex h-8 items-center justify-center rounded border border-[var(--prism-border-default)] px-3 text-sm text-[var(--prism-text-primary)] transition-colors hover:bg-[var(--prism-accent-subtle)] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[var(--prism-accent)]/40 active:bg-[rgba(201,100,66,0.2)]"
              onClick={() => setViewPolicy(null)}
            >
              Close
            </button>
          </div>
        </div>
      )}

      {loading && <p className="text-sm text-[var(--prism-text-secondary)]">Loading...</p>}
      {error && <p className="text-sm text-red-400">{error}</p>}

      {!loading && !error && filteredPolicies.length === 0 && (
        <div className="rounded border border-[var(--prism-border-default)] bg-[var(--prism-bg-surface)] px-4">
          <PrismEmptyState
            icon={hasFilters ? ListFilter : ShieldPlus}
            title={emptyTitle}
            description={emptyDescription}
            actionLabel={emptyActionLabel}
            onAction={emptyAction}
          />
        </div>
      )}

      {!loading && !error && filteredPolicies.length > 0 && (
        <div className="rounded border border-[var(--prism-border-default)] bg-[var(--prism-bg-surface)]">
          <Table className="w-full text-sm">
            <TableHeader className="bg-[var(--prism-bg-base)]">
              <TableRow className="border-b border-[var(--prism-border-default)] hover:bg-transparent">
                <TableHead className="whitespace-nowrap px-3 py-2.5 text-xs font-medium uppercase tracking-wider text-[var(--prism-text-secondary)]">Name</TableHead>
                <TableHead className="whitespace-nowrap px-3 py-2.5 text-xs font-medium uppercase tracking-wider text-[var(--prism-text-secondary)]">Status</TableHead>
                <TableHead className="whitespace-nowrap px-3 py-2.5 text-xs font-medium uppercase tracking-wider text-[var(--prism-text-secondary)]">Type</TableHead>
                <TableHead className="whitespace-nowrap px-3 py-2.5 text-xs font-medium uppercase tracking-wider text-[var(--prism-text-secondary)]">Created</TableHead>
                <TableHead className="whitespace-nowrap px-3 py-2.5 text-xs font-medium uppercase tracking-wider text-[var(--prism-text-secondary)]">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
            {filteredPolicies.map((policy) => {
              const isDeleting = deletingIds.has(policy.id);
              const isToggling = togglingIds.has(policy.id);
              const isActive = policy.status === 'active';

              return (
                <TableRow
                  key={policy.id}
                  className="border-b border-[var(--prism-border-subtle)] transition-colors hover:bg-[rgba(201,100,66,0.08)]"
                >
                  <TableCell className="px-3 py-2.5 text-[var(--prism-text-primary)]">{policy.name}</TableCell>
                  <TableCell className="px-3 py-2.5 text-[var(--prism-text-primary)]">
                    <div className="flex items-center gap-2">
                      <Switch
                        checked={isActive}
                        disabled={isToggling}
                        onCheckedChange={() => handleToggle(policy.id)}
                        aria-label={isActive ? `Disable ${policy.name}` : `Enable ${policy.name}`}
                        className={isToggling ? 'opacity-50' : ''}
                      />
                      <Badge
                        variant="outline"
                        className={`rounded-full border px-2 py-0.5 text-xs font-medium uppercase tracking-wide ${getStatusBadgeClass(policy.status)}`}
                      >
                        {policy.status}
                      </Badge>
                    </div>
                  </TableCell>
                  <TableCell className="px-3 py-2.5 text-[var(--prism-text-primary)]">
                    <Badge
                      variant="outline"
                      className="rounded-full border border-[var(--prism-border-default)] bg-[var(--prism-bg-base)] px-2 py-0.5 text-xs font-medium text-[var(--prism-text-secondary)]"
                    >
                      {policy.policy_type ?? policy.type ?? '—'}
                    </Badge>
                  </TableCell>
                  <TableCell className="px-3 py-2.5 text-[var(--prism-text-primary)]">{formatDate(policy.created_at)}</TableCell>
                  <TableCell className="whitespace-nowrap px-3 py-2.5 text-[var(--prism-text-primary)]">
                    <button
                      className="mr-1.5 inline-flex h-7 items-center justify-center rounded border border-[var(--prism-border-default)] px-2.5 text-xs text-[var(--prism-text-primary)] transition-colors hover:bg-[var(--prism-accent-subtle)] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[var(--prism-accent)]/40 active:bg-[rgba(201,100,66,0.2)]"
                      onClick={() => setViewPolicy(policy)}
                    >
                      View
                    </button>
                    <button
                      className="mr-1.5 inline-flex h-7 items-center justify-center rounded border border-[var(--prism-accent)]/35 bg-[var(--prism-accent-subtle)] px-2.5 text-xs text-[var(--prism-accent)] transition-colors hover:bg-[rgba(201,100,66,0.2)] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[var(--prism-accent)]/40 active:bg-[rgba(201,100,66,0.24)] disabled:cursor-not-allowed disabled:opacity-40"
                      disabled={isDeleting}
                      onClick={() => { setSelectedPolicy(policy); setShowForm(true); }}
                    >
                      Edit
                    </button>
                    <button
                      className="inline-flex h-7 items-center justify-center rounded border border-red-500/35 bg-red-50 px-2.5 text-xs text-red-700 transition-colors hover:bg-red-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[var(--prism-accent)]/40 active:bg-red-200 disabled:cursor-not-allowed disabled:opacity-40"
                      disabled={isDeleting}
                      onClick={() => handleDelete(policy.id)}
                    >
                      {isDeleting ? 'Deleting...' : 'Delete'}
                    </button>
                  </TableCell>
                </TableRow>
              );
            })}
            </TableBody>
          </Table>
        </div>
      )}
    </div>
  );
}
