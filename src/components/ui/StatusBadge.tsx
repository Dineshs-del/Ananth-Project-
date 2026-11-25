import React from 'react';

export type SemanticStatus =
  | 'PASS'
  | 'BORDERLINE'
  | 'FAIL'
  | 'BLOCKED'
  | 'APPROVED'
  | 'REJECTED'
  | 'EXECUTED'
  | 'EXPIRED'
  | 'CONSUMED'
  | 'PENDING'
  | 'MONITORING';

interface StatusBadgeProps {
  status: SemanticStatus | string;
  size?: 'sm' | 'md' | 'lg';
  label?: string;
  className?: string;
}

export const StatusBadge: React.FC<StatusBadgeProps> = ({
  status,
  size = 'md',
  label,
  className = '',
}) => {
  const norm = (status || '').toUpperCase();

  let styles = {
    bg: 'bg-slate-800/70',
    border: 'border-slate-700/60',
    text: 'text-slate-300',
    dot: 'bg-slate-400 shadow-[0_0_8px_#94a3b8]',
  };

  if (['PASS', 'APPROVED', 'EXECUTED'].includes(norm)) {
    styles = {
      bg: 'bg-emerald-950/60',
      border: 'border-emerald-500/40',
      text: 'text-emerald-300',
      dot: 'bg-emerald-400 shadow-[0_0_8px_#34d399]',
    };
  } else if (['BORDERLINE', 'ESCALATED_REVIEW', 'MONITORING'].includes(norm)) {
    styles = {
      bg: 'bg-amber-950/60',
      border: 'border-amber-500/40',
      text: 'text-amber-300',
      dot: 'bg-amber-400 shadow-[0_0_8px_#fbbf24]',
    };
  } else if (['FAIL', 'BLOCKED', 'REJECTED'].includes(norm)) {
    styles = {
      bg: 'bg-rose-950/60',
      border: 'border-rose-500/40',
      text: 'text-rose-300',
      dot: 'bg-rose-400 shadow-[0_0_8px_#f43f5e]',
    };
  } else if (['EXPIRED', 'CONSUMED'].includes(norm)) {
    styles = {
      bg: 'bg-slate-900/80',
      border: 'border-slate-700/50',
      text: 'text-slate-400',
      dot: 'bg-slate-500 shadow-none',
    };
  }

  const sizeClasses = {
    sm: 'px-2 py-0.5 text-[11px] gap-1.5',
    md: 'px-2.5 py-1 text-xs gap-2',
    lg: 'px-3 py-1.5 text-sm gap-2.5 font-semibold',
  }[size];

  return (
    <span
      data-testid="status-badge"
      className={`inline-flex items-center font-mono rounded-md border backdrop-blur-md font-medium tracking-wide uppercase ${styles.bg} ${styles.border} ${styles.text} ${sizeClasses} ${className}`}
    >
      <span className={`w-1.5 h-1.5 rounded-full ${styles.dot}`} />
      <span>{label || status.replace(/_/g, ' ')}</span>
    </span>
  );
};
