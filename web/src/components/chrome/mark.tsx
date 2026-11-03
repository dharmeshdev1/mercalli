/** The Mercalli mark: an epicenter and two isoseismal rings. */
export function Mark({ size = 22, className }: { size?: number; className?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true" className={className} style={{ flex: "none" }}>
      <circle cx="12" cy="12" r="2.6" fill="currentColor" />
      <circle cx="12" cy="12" r="6.4" fill="none" stroke="currentColor" strokeWidth="1.5" />
      <path d="M12 1.75a10.25 10.25 0 1 1-8.88 5.13" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  );
}
