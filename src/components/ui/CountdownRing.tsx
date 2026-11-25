import React from 'react';

interface CountdownRingProps {
  remainingSeconds: number;
  totalSeconds?: number;
  size?: number;
  strokeWidth?: number;
  className?: string;
}

export const CountdownRing: React.FC<CountdownRingProps> = ({
  remainingSeconds,
  totalSeconds = 120,
  size = 64,
  strokeWidth = 5,
  className = '',
}) => {
  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;
  const isExpired = remainingSeconds <= 0;

  const fraction = Math.max(0, Math.min(1, remainingSeconds / totalSeconds));
  const strokeDashoffset = circumference - fraction * circumference;

  let colorClass = '#10b981'; // emerald
  let glowColor = 'rgba(16, 185, 129, 0.4)';

  if (isExpired) {
    colorClass = '#64748b'; // slate
    glowColor = 'none';
  } else if (remainingSeconds <= 30) {
    colorClass = '#f43f5e'; // rose
    glowColor = 'rgba(244, 63, 94, 0.5)';
  } else if (remainingSeconds <= 60) {
    colorClass = '#f59e0b'; // amber
    glowColor = 'rgba(245, 158, 11, 0.4)';
  }

  const formatTime = (sec: number) => {
    if (sec <= 0) return '00:00';
    const m = Math.floor(sec / 60);
    const s = sec % 60;
    return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  };

  return (
    <div
      data-testid="countdown"
      className={`relative inline-flex items-center justify-center ${className}`}
      style={{ width: size, height: size }}
    >
      <svg width={size} height={size} className="transform -rotate-90">
        {/* Background track */}
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          stroke="rgba(255, 255, 255, 0.08)"
          strokeWidth={strokeWidth}
          fill="none"
        />
        {/* Animated fill ring */}
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          stroke={colorClass}
          strokeWidth={strokeWidth}
          strokeDasharray={circumference}
          strokeDashoffset={strokeDashoffset}
          strokeLinecap="round"
          fill="none"
          style={{
            transition: 'stroke-dashoffset 1s linear, stroke 0.3s ease',
            filter: glowColor !== 'none' ? `drop-shadow(0 0 6px ${glowColor})` : 'none',
          }}
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center font-mono tabular-nums leading-none">
        <span
          className="text-xs font-bold"
          style={{ color: colorClass }}
        >
          {isExpired ? '0s' : `${remainingSeconds}s`}
        </span>
      </div>
    </div>
  );
};
