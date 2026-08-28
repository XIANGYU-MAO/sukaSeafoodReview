import { useCallback, useId, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

export function HelpHint({ context, label, children }: { context: "字段" | "表头" | "操作"; label: string; children: ReactNode }) {
  const tooltipId = useId();
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState<{ top: number; left: number } | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const tooltipRef = useRef<HTMLSpanElement | null>(null);

  const updatePosition = useCallback(() => {
    const trigger = triggerRef.current;
    const tooltip = tooltipRef.current;
    if (!trigger || !tooltip) return;
    const triggerRect = trigger.getBoundingClientRect();
    const tooltipRect = tooltip.getBoundingClientRect();
    const gap = 8;
    const edge = 8;
    const below = triggerRect.bottom + gap;
    const above = triggerRect.top - tooltipRect.height - gap;
    const preferredTop = below + tooltipRect.height <= window.innerHeight || above < edge
      ? below
      : above;
    const top = Math.min(
      Math.max(edge, preferredTop),
      Math.max(edge, window.innerHeight - tooltipRect.height - edge),
    );
    const left = Math.min(
      Math.max(edge, triggerRect.left),
      Math.max(edge, window.innerWidth - tooltipRect.width - edge),
    );
    setPosition({ top, left });
  }, []);

  useLayoutEffect(() => {
    if (!open) {
      setPosition(null);
      return;
    }
    updatePosition();
    window.addEventListener("resize", updatePosition);
    window.addEventListener("scroll", updatePosition, true);
    return () => {
      window.removeEventListener("resize", updatePosition);
      window.removeEventListener("scroll", updatePosition, true);
    };
  }, [open, updatePosition]);

  return <span className="help-hint">
    <button
      ref={triggerRef}
      type="button"
      className="help-hint__trigger"
      aria-label={`${context}说明：${label}`}
      aria-describedby={open ? tooltipId : undefined}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
      onFocus={() => setOpen(true)}
      onBlur={() => setOpen(false)}
    >?</button>
    {open && typeof document !== "undefined" ? createPortal(
      <span
        ref={tooltipRef}
        id={tooltipId}
        role="tooltip"
        className="help-hint__tooltip"
        style={{
          position: "fixed",
          top: position?.top ?? 0,
          left: position?.left ?? 0,
          visibility: position ? "visible" : "hidden",
        }}
      >{children}</span>,
      document.body,
    ) : null}
  </span>;
}
