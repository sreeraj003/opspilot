export function formatTime(value: string | Date, locale?: string): string {
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return "Unknown";
  return new Intl.DateTimeFormat(locale, { timeStyle: "medium" }).format(date);
}

export function humanize(value: string): string {
  const spaced = value.replace(/[_-]+/g, " ").trim();
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}
