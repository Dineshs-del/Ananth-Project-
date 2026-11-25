import React from 'react';

interface GlassCardProps extends React.HTMLAttributes<HTMLDivElement> {
  children: React.ReactNode;
  variant?: 'default' | 'glow' | 'accent' | 'emerald' | 'amber' | 'rose';
  className?: string;
  glow?: boolean;
}

export const GlassCard: React.FC<GlassCardProps> = ({
  children,
  variant = 'default',
  className = '',
  glow = false,
  ...props
}) => {
  const getVariantStyles = () => {
    switch (variant) {
      case 'glow':
      case 'accent':
        return 'border-cyan-500/30 shadow-[0_0_30px_-5px_rgba(6,182,212,0.18)] bg-slate-900/70';
      case 'emerald':
        return 'border-emerald-500/30 shadow-[0_0_30px_-5px_rgba(16,185,129,0.18)] bg-emerald-950/20';
      case 'amber':
        return 'border-amber-500/30 shadow-[0_0_30px_-5px_rgba(245,158,11,0.18)] bg-amber-950/20';
      case 'rose':
        return 'border-rose-500/30 shadow-[0_0_30px_-5px_rgba(244,63,94,0.18)] bg-rose-950/20';
      default:
        return 'border-white/[0.08] shadow-[0_12px_40px_rgba(0,0,0,0.45)] bg-slate-900/60';
    }
  };

  return (
    <div
      className={`relative rounded-2xl backdrop-blur-xl border transition-all duration-300 ${getVariantStyles()} ${
        glow ? 'ring-1 ring-cyan-400/20' : ''
      } ${className}`}
      {...props}
    >
      {/* Light sheen effect on top edge */}
      <div className="absolute inset-x-0 top-0 h-[1px] bg-gradient-to-r from-transparent via-white/20 to-transparent rounded-t-2xl pointer-events-none" />
      {children}
    </div>
  );
};
