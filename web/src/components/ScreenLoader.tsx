interface ScreenLoaderProps {
  label: string;
}

export function ScreenLoader({ label }: ScreenLoaderProps) {
  return (
    <div className="page-loading-overlay" role="status" aria-label={label} aria-busy="true">
      <span className="spinner" aria-hidden="true" />
      <span>{label}</span>
    </div>
  );
}
