import { useId, useState, type ReactNode } from "react";

export function HelpHint({ context, label, children }: { context: "字段" | "表头"; label: string; children: ReactNode }) {
  const tooltipId = useId();
  const [open, setOpen] = useState(false);

  return <span className="help-hint">
    <button
      type="button"
      className="help-hint__trigger"
      aria-label={`${context}说明：${label}`}
      aria-describedby={open ? tooltipId : undefined}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
      onFocus={() => setOpen(true)}
      onBlur={() => setOpen(false)}
    >?</button>
    {open ? <span id={tooltipId} role="tooltip" className="help-hint__tooltip">{children}</span> : null}
  </span>;
}
