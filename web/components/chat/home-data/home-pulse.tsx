export const popClass = (pulse = 0) => (pulse > 0 ? 'animate-home-pop' : '');

export function Glow({ pulse = 0, className }: { pulse?: number; className: string }) {
  if (pulse === 0) return null;
  return (
    <span
      key={pulse}
      aria-hidden="true"
      className={`animate-home-glow pointer-events-none absolute inset-0 ${className}`}
    />
  );
}
