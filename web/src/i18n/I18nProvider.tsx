import { createContext, type ReactNode, useContext, useMemo, useState } from "react";

import { messages, type Locale, type MessageKey } from "./catalog";

interface I18nContextValue {
  locale: Locale;
  setLocale: (locale: Locale) => void;
  toggleLocale: () => void;
  t: (key: MessageKey) => string;
}

const I18nContext = createContext<I18nContextValue | null>(null);

export function I18nProvider({
  children,
  initialLocale = "zh",
}: {
  children: ReactNode;
  initialLocale?: Locale;
}) {
  const [locale, setLocale] = useState<Locale>(initialLocale);
  const value = useMemo<I18nContextValue>(
    () => ({
      locale,
      setLocale,
      toggleLocale: () => setLocale((current) => (current === "zh" ? "en" : "zh")),
      t: (key) => messages[locale][key],
    }),
    [locale],
  );
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nContextValue {
  const context = useContext(I18nContext);
  if (!context) throw new Error("useI18n must be used inside I18nProvider");
  return context;
}
