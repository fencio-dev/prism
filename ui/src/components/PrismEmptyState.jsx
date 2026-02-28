import { cn } from '../lib/utils';

export default function PrismEmptyState({
  icon: Icon,
  title,
  description,
  actionLabel,
  onAction,
  actionDisabled = false,
  fullHeight = false,
  className,
}) {
  return (
    <div
      className={cn(
        'flex w-full items-center justify-center',
        fullHeight ? 'h-full min-h-[280px]' : 'min-h-[220px] py-8',
        className,
      )}
    >
      <div className="mx-auto flex w-full max-w-md flex-col items-center text-center">
        {Icon ? (
          <Icon
            aria-hidden="true"
            focusable="false"
            className="h-10 w-10 text-[var(--prism-text-muted)]"
            strokeWidth={1.75}
          />
        ) : null}
        <h3 className={cn('text-2xl font-semibold text-[var(--prism-text-primary)]', Icon && 'mt-4')}>
          {title}
        </h3>
        <p className="mt-3 text-sm text-[var(--prism-text-secondary)]">{description}</p>
        {actionLabel && onAction && (
          <button
            type="button"
            onClick={onAction}
            disabled={actionDisabled}
            className="mt-5 inline-flex h-8 items-center justify-center rounded bg-[var(--prism-accent)] px-3 text-sm font-semibold text-white transition-colors hover:bg-[#b75a3b] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[var(--prism-accent)]/40 active:bg-[#a65135] disabled:cursor-not-allowed disabled:opacity-40"
          >
            {actionLabel}
          </button>
        )}
      </div>
    </div>
  );
}
