import React from 'react';
import { Loader2 } from 'lucide-react';

interface PressableButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  children: React.ReactNode;
  variant?: 'primary' | 'secondary' | 'emerald' | 'amber' | 'rose' | 'ghost';
  size?: 'sm' | 'md' | 'lg' | 'chunky';
  loading?: boolean;
  glow?: boolean;
  icon?: React.ComponentType<{ className?: string }>;
}

export const PressableButton: React.FC<PressableButtonProps> = ({
  children,
  variant = 'primary',
  size = 'md',
  loading = false,
  glow = false,
  icon: Icon,
  disabled,
  className = '',
  ...props
}) => {
  const getVariantStyles = () => {
    switch (variant) {
      case 'primary':
        return 'bg-gradient-to-r from-cyan-500 to-blue-600 text-white shadow-[0_4px_16px_rgba(6,182,212,0.3)] hover:shadow-[0_6px_22px_rgba(6,182,212,0.45)] border-t border-cyan-300/40';
      case 'emerald':
        return 'bg-gradient-to-r from-emerald-600 to-teal-700 text-white shadow-[0_4px_16px_rgba(16,185,129,0.3)] hover:shadow-[0_6px_22px_rgba(16,185,129,0.45)] border-t border-emerald-300/40';
      case 'amber':
        return 'bg-gradient-to-r from-amber-600 to-orange-700 text-white shadow-[0_4px_16px_rgba(245,158,11,0.3)] hover:shadow-[0_6px_22px_rgba(245,158,11,0.45)] border-t border-amber-300/40';
      case 'rose':
        return 'bg-gradient-to-r from-rose-600 to-red-700 text-white shadow-[0_4px_16px_rgba(244,63,94,0.3)] hover:shadow-[0_6px_22px_rgba(244,63,94,0.45)] border-t border-rose-300/40';
      case 'secondary':
        return 'bg-slate-800/80 hover:bg-slate-700/80 text-slate-200 border border-white/10 hover:border-white/20 shadow-md';
      case 'ghost':
        return 'bg-transparent hover:bg-white/[0.06] text-slate-300 hover:text-white border border-transparent';
    }
  };

  const getSizeStyles = () => {
    switch (size) {
      case 'sm':
        return 'px-3 py-1.5 text-xs font-medium rounded-lg gap-1.5';
      case 'lg':
        return 'px-6 py-3 text-base font-semibold rounded-xl gap-2.5';
      case 'chunky':
        return 'h-14 px-4 text-xl font-bold font-mono rounded-xl tracking-wider';
      default:
        return 'px-4 py-2 text-sm font-medium rounded-lg gap-2';
    }
  };

  return (
    <button
      disabled={disabled || loading}
      className={`relative inline-flex items-center justify-center font-medium select-none cursor-pointer
        transition-all duration-150 transform-gpu
        hover:-translate-y-0.5 active:translate-y-0.5 active:scale-[0.98]
        disabled:opacity-40 disabled:cursor-not-allowed disabled:transform-none disabled:shadow-none
        focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-900
        ${getVariantStyles()} ${getSizeStyles()} ${glow ? 'ring-1 ring-cyan-400/40' : ''} ${className}`}
      {...props}
    >
      {loading ? (
        <>
          <Loader2 className="w-4 h-4 animate-spin shrink-0" />
          <span>{children}</span>
        </>
      ) : (
        <>
          {Icon && <Icon className="w-4 h-4 shrink-0" />}
          {children}
        </>
      )}
    </button>
  );
};
