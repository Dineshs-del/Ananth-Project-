import React, { useEffect, useState } from 'react';

interface FloatProps {
  children: React.ReactNode;
  duration?: number;
  distance?: number;
  delay?: number;
  className?: string;
}

export const Float: React.FC<FloatProps> = ({
  children,
  duration = 4.5,
  distance = 7,
  delay = 0,
  className = '',
}) => {
  const [isReducedMotion, setIsReducedMotion] = useState(false);

  useEffect(() => {
    const mediaQuery = window.matchMedia('(prefers-reduced-motion: reduce)');
    setIsReducedMotion(mediaQuery.matches);
    const handler = (e: MediaQueryListEvent) => setIsReducedMotion(e.matches);
    mediaQuery.addEventListener('change', handler);
    return () => mediaQuery.removeEventListener('change', handler);
  }, []);

  if (isReducedMotion) {
    return <div className={className}>{children}</div>;
  }

  return (
    <div
      style={{
        animation: `floatBob ${duration}s ease-in-out ${delay}s infinite alternate`,
      }}
      className={className}
    >
      <style>{`
        @keyframes floatBob {
          0% { transform: translateY(0px); }
          100% { transform: translateY(-${distance}px); }
        }
      `}</style>
      {children}
    </div>
  );
};
