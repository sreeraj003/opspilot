import { Fragment } from "react";

/**
 * Read-only SQL display with light syntax highlighting. This tokenizer is for colouring text only: it never
 * decides anything about the query (the backend parses and validates SQL with SQLGlot).
 */

const KEYWORDS = new Set(
  (
    "select from where group by order having limit offset fetch next rows only top join inner left right full outer " +
    "cross on and or not in is null as with distinct case when then else end between like ilike asc desc union all " +
    "interval cast exists any nulls first last over partition"
  ).split(" "),
);

type Kind = "keyword" | "string" | "number" | "identifier" | "plain";

const TOKEN = /('(?:[^']|'')*')|("(?:[^"]|"")*"|`[^`]*`|\[[^\]]*\])|(\b\d+(?:\.\d+)?\b)|([A-Za-z_][A-Za-z0-9_]*)|(\s+|.)/g;

const CLASSES: Record<Kind, string> = {
  keyword: "font-semibold text-info-text",
  string: "text-success-text",
  number: "text-warning-text",
  identifier: "text-fg",
  plain: "text-fg-secondary",
};

function tokens(sql: string): { text: string; kind: Kind }[] {
  const result: { text: string; kind: Kind }[] = [];
  for (const match of sql.matchAll(TOKEN)) {
    const [text, str, quoted, num, word] = match;
    let kind: Kind = "plain";
    if (str) kind = "string";
    else if (quoted) kind = "identifier";
    else if (num) kind = "number";
    else if (word) kind = KEYWORDS.has(word.toLowerCase()) ? "keyword" : "identifier";
    result.push({ text, kind });
  }
  return result;
}

export function SqlCode({ sql, label = "Generated SQL" }: { sql: string; label?: string }) {
  return (
    <pre
      aria-label={label}
      className="max-h-96 overflow-auto rounded-md border border-line bg-surface-muted p-4 font-mono text-sm leading-relaxed whitespace-pre"
    >
      <code>
        {tokens(sql).map((token, index) =>
          token.kind === "plain" ? (
            <Fragment key={index}>{token.text}</Fragment>
          ) : (
            <span key={index} className={CLASSES[token.kind]}>
              {token.text}
            </span>
          ),
        )}
      </code>
    </pre>
  );
}
