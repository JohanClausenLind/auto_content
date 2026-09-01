/** Message catalogs. `en` is the source of truth; other locales must cover the same keys
 * (the Locale type enforces it) and fall back to English at runtime if a key is missing. */

export const en = {
  "common.loading": "Loading…",
  "common.tryAgain": "Try again",
  "inbox.title": "Inbox",
  "inbox.lead": "Messages from fans. Nothing is sent from here — you decide what each one needs.",
  "inbox.check": "Check for new messages",
  "inbox.checking": "Checking…",
  "inbox.empty.title": "Your inbox is clear",
  "inbox.empty.body": "New fan messages appear here after a check, each one classified so you can see what needs a human first.",
  "inbox.urgent": "This one needs you personally — it is never handled automatically.",
  "inbox.markAnswered": "Mark answered",
  "inbox.skip": "Skip",
  "inbox.cancelSkip": "Cancel skip",
  "inbox.skipReasonLabel": "Why skip this one?",
  "inbox.skipWithReason": "Skip with reason",
  "settings.language.label": "Language",
  "settings.language.description": "Applies to the app itself. Content is written in whatever language a campaign asks for.",
} as const;

export type MessageKey = keyof typeof en;
export type Catalog = Record<MessageKey, string>;

const sv: Catalog = {
  "common.loading": "Läser in…",
  "common.tryAgain": "Försök igen",
  "inbox.title": "Inkorg",
  "inbox.lead": "Meddelanden från fans. Inget skickas härifrån — du avgör vad varje meddelande behöver.",
  "inbox.check": "Sök efter nya meddelanden",
  "inbox.checking": "Söker…",
  "inbox.empty.title": "Din inkorg är tom",
  "inbox.empty.body": "Nya meddelanden från fans dyker upp här efter en sökning, klassificerade så att du ser vad som behöver en människa först.",
  "inbox.urgent": "Det här behöver dig personligen — det hanteras aldrig automatiskt.",
  "inbox.markAnswered": "Markera som besvarat",
  "inbox.skip": "Hoppa över",
  "inbox.cancelSkip": "Avbryt",
  "inbox.skipReasonLabel": "Varför hoppa över det här?",
  "inbox.skipWithReason": "Hoppa över med anledning",
  "settings.language.label": "Språk",
  "settings.language.description": "Gäller själva appen. Innehåll skrivs på det språk en kampanj ber om.",
};

export const CATALOGS = { en, sv } as const;
export type LocaleId = keyof typeof CATALOGS;
export const LOCALE_NAMES: Record<LocaleId, string> = { en: "English", sv: "Svenska" };
