/** App i18n scaffolding: a typed catalog, a context-backed `useT()`, and a locale preference
 * persisted per account (`/v1/prefs/locale`). English is always the fallback. */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { api } from "../api/client";
import { useSession } from "../api/queries";
import { CATALOGS, en, type LocaleId, type MessageKey } from "./messages";

interface LocaleContextValue {
  locale: LocaleId;
  setLocale: (locale: LocaleId) => void;
}

const LocaleContext = createContext<LocaleContextValue>({ locale: "en", setLocale: () => undefined });

function isLocaleId(v: unknown): v is LocaleId {
  return typeof v === "string" && v in CATALOGS;
}

const LOCALE_PREF_KEY = ["prefs", "locale"] as const;

export function LocaleProvider({ children }: { children: ReactNode }) {
  const { data: session } = useSession();
  const client = useQueryClient();
  const [localOnly, setLocalOnly] = useState<LocaleId>("en");
  const pref = useQuery({
    queryKey: LOCALE_PREF_KEY,
    queryFn: () => api.prefs.get("locale"),
    enabled: Boolean(session),
    staleTime: Infinity,
  });
  const save = useMutation({
    mutationFn: (locale: LocaleId) => api.prefs.put("locale", locale),
    onSuccess: () => void client.invalidateQueries({ queryKey: LOCALE_PREF_KEY }),
  });
  const locale: LocaleId = session && isLocaleId(pref.data?.value) ? pref.data.value : localOnly;
  const saveLocale = save.mutate;
  const setLocale = useCallback(
    (next: LocaleId) => {
      setLocalOnly(next);
      if (session) {
        client.setQueryData(LOCALE_PREF_KEY, { value: next });
        saveLocale(next);
      }
    },
    [session, client, saveLocale],
  );
  const value = useMemo(() => ({ locale, setLocale }), [locale, setLocale]);
  return <LocaleContext.Provider value={value}>{children}</LocaleContext.Provider>;
}

export function useLocale(): LocaleContextValue {
  return useContext(LocaleContext);
}

/** Translate a message key in the active locale, with English as the guaranteed fallback. */
export function useT(): (key: MessageKey) => string {
  const { locale } = useLocale();
  return useCallback((key: MessageKey) => CATALOGS[locale][key] ?? en[key], [locale]);
}
