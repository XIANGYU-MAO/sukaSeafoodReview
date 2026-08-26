import type { KeyboardEvent } from "react";

interface PillChoiceGroupProps<T extends string> {
  label: string;
  options: readonly T[];
  value: T | null;
  onChange: (value: T) => void;
  disabled?: boolean;
  getOptionLabel?: (value: T) => string;
}

export function PillChoiceGroup<T extends string>({
  label,
  options,
  value,
  onChange,
  disabled = false,
  getOptionLabel = (option) => option,
}: PillChoiceGroupProps<T>) {
  const selectedIndex = value === null ? 0 : Math.max(0, options.indexOf(value));

  function selectAt(index: number, target: HTMLButtonElement) {
    if (disabled || options.length === 0) return;
    const nextIndex = (index + options.length) % options.length;
    onChange(options[nextIndex]);
    const group = target.closest('[role="radiogroup"]');
    const pills = group?.querySelectorAll<HTMLButtonElement>('[role="radio"]');
    pills?.[nextIndex]?.focus();
  }

  function handleKeyDown(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    if (disabled) return;
    let nextIndex: number | null = null;
    switch (event.key) {
      case "ArrowLeft":
      case "ArrowUp":
        nextIndex = index - 1;
        break;
      case "ArrowRight":
      case "ArrowDown":
        nextIndex = index + 1;
        break;
      case "Home":
        nextIndex = 0;
        break;
      case "End":
        nextIndex = options.length - 1;
        break;
      case " ":
        nextIndex = index;
        break;
      default:
        return;
    }
    event.preventDefault();
    selectAt(nextIndex, event.currentTarget);
  }

  return (
    <div className="pill-group" role="radiogroup" aria-label={label} aria-disabled={disabled}>
      {options.map((option, index) => {
        const selected = value === option;
        return (
          <button
            className={`pill-choice${selected ? " pill-choice--selected" : ""}`}
            disabled={disabled}
            key={option}
            type="button"
            role="radio"
            aria-checked={selected}
            tabIndex={disabled ? -1 : index === selectedIndex ? 0 : -1}
            onClick={() => onChange(option)}
            onKeyDown={(event) => handleKeyDown(event, index)}
          >
            <span>{getOptionLabel(option)}</span>
            {selected ? (
              <span className="pill-choice__check" aria-hidden="true">
                ✓
              </span>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}
