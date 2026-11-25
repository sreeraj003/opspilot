/**
 * Form state <-> permission policy JSON for the permission builder.
 *
 * Client-side checks here only guide the administrator: the server validates
 * every policy again (structure, operators, value types, nesting, contradictions)
 * and is the only authority. See docs/PERMISSION_POLICY.md.
 */

import { isGroup, type Condition, type FilterNode, type Operator, type ValueType } from "@/types/rbac";

export const DATA_RESOURCE_TYPE = "database_data";
export const MAX_DEPTH = 4;

export const OPERATOR_LABELS: Record<Operator, string> = {
  equals: "equals",
  not_equals: "does not equal",
  in: "is one of",
  not_in: "is not one of",
  greater_than: "is greater than",
  greater_than_or_equal: "is at least",
  less_than: "is less than",
  less_than_or_equal: "is at most",
  between: "is between",
  is_null: "is empty",
  is_not_null: "is not empty",
};

/** Operators the server accepts for each value type. */
export const OPERATORS_BY_TYPE: Record<ValueType, Operator[]> = {
  string: ["equals", "not_equals", "in", "not_in", "is_null", "is_not_null"],
  boolean: ["equals", "not_equals", "is_null", "is_not_null"],
  number: ["equals", "not_equals", "in", "not_in", "greater_than", "greater_than_or_equal", "less_than", "less_than_or_equal", "between", "is_null", "is_not_null"],
  date: ["equals", "not_equals", "in", "not_in", "greater_than", "greater_than_or_equal", "less_than", "less_than_or_equal", "between", "is_null", "is_not_null"],
  datetime: ["equals", "not_equals", "in", "not_in", "greater_than", "greater_than_or_equal", "less_than", "less_than_or_equal", "between", "is_null", "is_not_null"],
};

export const VALUE_TYPE_LABELS: Record<ValueType, string> = {
  string: "Text",
  number: "Number",
  boolean: "Yes / no",
  date: "Date",
  datetime: "Date and time",
};

export interface ConditionDraft {
  kind: "condition";
  id: string;
  column: string;
  operator: Operator;
  valueType: ValueType;
  /** Single value, comma-separated list (in / not_in), or the low bound (between). */
  value: string;
  /** High bound for between. */
  value2: string;
}

export interface GroupDraft {
  kind: "group";
  id: string;
  logic: "and" | "or";
  children: NodeDraft[];
}

export type NodeDraft = ConditionDraft | GroupDraft;

export interface PolicyDraft {
  effect: "allow" | "deny";
  // allow (data)
  tables: string[];
  columns: Record<string, string[]>;
  allColumns: Record<string, boolean>;
  operations: { read: boolean; aggregate: boolean };
  filters: NodeDraft[];
  allowExport: boolean;
  allowSqlView: boolean;
  // deny
  denyAll: boolean;
  deniedTables: string[];
  deniedColumns: string[];
  excludedRows: NodeDraft[];
  denyExport: boolean;
  denySqlView: boolean;
}

export interface DraftIssue {
  path: string;
  message: string;
}

let counter = 0;
export function newId(): string {
  counter += 1;
  return `n${counter}`;
}

export function emptyDraft(): PolicyDraft {
  return {
    effect: "allow",
    tables: [],
    columns: {},
    allColumns: {},
    operations: { read: true, aggregate: true },
    filters: [],
    allowExport: false,
    allowSqlView: false,
    denyAll: false,
    deniedTables: [],
    deniedColumns: [],
    excludedRows: [],
    denyExport: false,
    denySqlView: false,
  };
}

export function newCondition(column = ""): ConditionDraft {
  return { kind: "condition", id: newId(), column, operator: "equals", valueType: "string", value: "", value2: "" };
}

export function newGroup(logic: "and" | "or" = "or"): GroupDraft {
  return { kind: "group", id: newId(), logic, children: [newCondition()] };
}

const IDENTIFIER = /^[A-Za-z_][A-Za-z0-9_]{0,62}$/;
const TABLE = /^[A-Za-z_][A-Za-z0-9_]{0,62}(\.[A-Za-z_][A-Za-z0-9_]{0,62})?$/;
const QUALIFIED_COLUMN = /^([A-Za-z_][A-Za-z0-9_]{0,62}\.){1,2}[A-Za-z_][A-Za-z0-9_]{0,62}$/;

export function isValidTable(value: string): boolean {
  return TABLE.test(value);
}

export function isValidColumn(value: string): boolean {
  return IDENTIFIER.test(value);
}

// --- draft -> policy ------------------------------------------------------------------------------------

function parseScalar(raw: string, type: ValueType): unknown {
  const text = raw.trim();
  if (type === "number") return Number(text);
  if (type === "boolean") return text.toLowerCase() === "true" || text.toLowerCase() === "yes";
  return text;
}

function splitList(raw: string): string[] {
  return raw
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean);
}

export function conditionToPolicy(draft: ConditionDraft): Condition {
  const node: Condition = { column: draft.column.trim(), operator: draft.operator };
  if (draft.operator === "is_null" || draft.operator === "is_not_null") return node;
  if (draft.operator === "in" || draft.operator === "not_in") {
    node.value = splitList(draft.value).map((part) => parseScalar(part, draft.valueType));
  } else if (draft.operator === "between") {
    node.value = [parseScalar(draft.value, draft.valueType), parseScalar(draft.value2, draft.valueType)];
  } else {
    node.value = parseScalar(draft.value, draft.valueType);
  }
  if (draft.valueType !== "string" && draft.valueType !== "number" && draft.valueType !== "boolean") {
    node.value_type = draft.valueType;
  }
  return node;
}

export function nodeToPolicy(node: NodeDraft): FilterNode {
  if (node.kind === "condition") return conditionToPolicy(node);
  return { logic: node.logic, conditions: node.children.map(nodeToPolicy) };
}

export function buildPolicy(resourceType: string, draft: PolicyDraft): Record<string, unknown> {
  const isData = resourceType === DATA_RESOURCE_TYPE;
  if (draft.effect === "deny") {
    if (!isData) {
      return { version: 1, effect: "deny", deny_all: draft.denyAll, deny_export: draft.denyExport, deny_sql_view: draft.denySqlView };
    }
    return {
      version: 1,
      effect: "deny",
      deny_all: draft.denyAll,
      denied_tables: draft.deniedTables,
      denied_columns: draft.deniedColumns,
      excluded_rows: draft.excludedRows.map(nodeToPolicy),
      deny_export: draft.denyExport,
      deny_sql_view: draft.denySqlView,
    };
  }
  if (!isData) return { version: 1, effect: "allow", allow_export: draft.allowExport, allow_sql_view: draft.allowSqlView };
  const qualify = draft.tables.length > 1;
  const columns = draft.tables.flatMap((table) =>
    draft.allColumns[table] ? [`${table}.*`] : (draft.columns[table] ?? []).map((column) => (qualify ? `${table}.${column}` : column)),
  );
  return {
    version: 1,
    effect: "allow",
    allowed_tables: draft.tables,
    allowed_columns: columns,
    allowed_operations: (["aggregate", "read"] as const).filter((op) => draft.operations[op]),
    row_filters: draft.filters.map(nodeToPolicy),
    allow_export: draft.allowExport,
    allow_sql_view: draft.allowSqlView,
  };
}

// --- policy -> draft (editing an existing permission) -------------------------------------------------------

function scalarText(value: unknown): string {
  return value === undefined || value === null ? "" : String(value);
}

export function nodeFromPolicy(node: FilterNode): NodeDraft {
  if (isGroup(node)) {
    return { kind: "group", id: newId(), logic: node.logic === "or" ? "or" : "and", children: node.conditions.map(nodeFromPolicy) };
  }
  const valueType: ValueType =
    node.value_type ?? (typeof node.value === "number" ? "number" : typeof node.value === "boolean" ? "boolean" : "string");
  const draft = newCondition(node.column);
  draft.operator = node.operator;
  draft.valueType = valueType;
  if (Array.isArray(node.value)) {
    if (node.operator === "between") {
      draft.value = scalarText(node.value[0]);
      draft.value2 = scalarText(node.value[1]);
    } else {
      draft.value = node.value.map(scalarText).join(", ");
    }
  } else {
    draft.value = scalarText(node.value);
  }
  return draft;
}

export function draftFromPolicy(policy: Record<string, unknown>): PolicyDraft {
  const draft = emptyDraft();
  draft.effect = policy.effect === "deny" ? "deny" : "allow";
  const list = (key: string) => (Array.isArray(policy[key]) ? (policy[key] as unknown[]).map(String) : []);
  const nodes = (key: string) => (Array.isArray(policy[key]) ? (policy[key] as FilterNode[]).map(nodeFromPolicy) : []);
  draft.tables = list("allowed_tables");
  for (const table of draft.tables) {
    draft.columns[table] = [];
    draft.allColumns[table] = false;
  }
  for (const column of list("allowed_columns")) {
    const table = draft.tables.find((t) => column.startsWith(`${t}.`));
    if (!table) continue;
    const name = column.slice(table.length + 1);
    if (name === "*") draft.allColumns[table] = true;
    else draft.columns[table] = [...(draft.columns[table] ?? []), name];
  }
  if (Array.isArray(policy.allowed_operations)) {
    const ops = policy.allowed_operations as string[];
    draft.operations = { read: ops.includes("read"), aggregate: ops.includes("aggregate") };
  }
  draft.filters = nodes("row_filters");
  draft.allowExport = policy.allow_export === true;
  draft.allowSqlView = policy.allow_sql_view === true;
  draft.denyAll = policy.deny_all === true;
  draft.deniedTables = list("denied_tables");
  draft.deniedColumns = list("denied_columns");
  draft.excludedRows = nodes("excluded_rows");
  draft.denyExport = policy.deny_export === true;
  draft.denySqlView = policy.deny_sql_view === true;
  return draft;
}

// --- client-side guidance ---------------------------------------------------------------------------------

function checkNodes(nodes: NodeDraft[], path: string, depth: number, issues: DraftIssue[], requireQualified: boolean) {
  nodes.forEach((node, index) => {
    const here = `${path} ${index + 1}`;
    if (node.kind === "group") {
      if (depth > MAX_DEPTH) issues.push({ path: here, message: `Groups can be nested at most ${MAX_DEPTH} levels deep.` });
      if (node.children.length === 0) issues.push({ path: here, message: "A group needs at least one condition." });
      checkNodes(node.children, `${here} ›`, depth + 1, issues, requireQualified);
      return;
    }
    const column = node.column.trim();
    if (!column) {
      issues.push({ path: here, message: "Choose a column." });
    } else if (requireQualified ? !QUALIFIED_COLUMN.test(column) : !(IDENTIFIER.test(column) || QUALIFIED_COLUMN.test(column))) {
      issues.push({ path: here, message: requireQualified ? "Write the column as table.column." : `'${column}' is not a valid column name.` });
    }
    if (!OPERATORS_BY_TYPE[node.valueType].includes(node.operator)) {
      issues.push({ path: here, message: `'${OPERATOR_LABELS[node.operator]}' cannot be used with ${VALUE_TYPE_LABELS[node.valueType].toLowerCase()} values.` });
      return;
    }
    if (node.operator === "is_null" || node.operator === "is_not_null") return;
    const values = node.operator === "in" || node.operator === "not_in" ? splitList(node.value) : node.operator === "between" ? [node.value, node.value2] : [node.value];
    if (values.length === 0 || values.some((v) => !v.trim())) {
      issues.push({ path: here, message: node.operator === "between" ? "Enter both bounds." : "Enter a value." });
      return;
    }
    if (node.valueType === "number" && values.some((v) => !Number.isFinite(Number(v)))) {
      issues.push({ path: here, message: "Enter a number." });
    }
    if (node.valueType === "date" && values.some((v) => !/^\d{4}-\d{2}-\d{2}$/.test(v.trim()))) {
      issues.push({ path: here, message: "Enter dates as YYYY-MM-DD." });
    }
    if (node.valueType === "boolean" && values.some((v) => !["true", "false", "yes", "no"].includes(v.trim().toLowerCase()))) {
      issues.push({ path: here, message: "Enter true or false." });
    }
    if (node.operator === "between" && node.valueType === "number" && Number(node.value) > Number(node.value2)) {
      issues.push({ path: here, message: "The lower bound must not be greater than the upper bound." });
    }
  });
}

export function validateDraft(resourceType: string, draft: PolicyDraft): DraftIssue[] {
  const issues: DraftIssue[] = [];
  const isData = resourceType === DATA_RESOURCE_TYPE;
  if (draft.effect === "deny") {
    const something = isData
      ? draft.denyAll || draft.deniedTables.length || draft.deniedColumns.length || draft.excludedRows.length || draft.denyExport || draft.denySqlView
      : draft.denyAll || draft.denyExport || draft.denySqlView;
    if (!something) issues.push({ path: "Restrictions", message: "Choose at least one thing to deny." });
    if (isData) {
      draft.deniedTables.forEach((t) => !isValidTable(t) && issues.push({ path: "Denied tables", message: `'${t}' is not a valid table name.` }));
      draft.deniedColumns.forEach(
        (c) => !QUALIFIED_COLUMN.test(c) && issues.push({ path: "Denied columns", message: `Write '${c}' as table.column.` }),
      );
      checkNodes(draft.excludedRows, "Excluded rows", 1, issues, true);
    }
    return issues;
  }
  if (!isData) return issues;
  if (draft.tables.length === 0) issues.push({ path: "Tables", message: "Add at least one table." });
  draft.tables.forEach((table) => {
    if (!isValidTable(table)) issues.push({ path: "Tables", message: `'${table}' is not a valid table name.` });
    if (!draft.allColumns[table] && (draft.columns[table] ?? []).length === 0) {
      issues.push({ path: `Columns of ${table}`, message: "Choose columns or allow all columns." });
    }
    (draft.columns[table] ?? []).forEach((column) => {
      if (!isValidColumn(column)) issues.push({ path: `Columns of ${table}`, message: `'${column}' is not a valid column name.` });
    });
  });
  if (!draft.operations.read && !draft.operations.aggregate) {
    issues.push({ path: "Operations", message: "Allow reading rows, aggregates, or both." });
  }
  checkNodes(draft.filters, "Row filter", 1, issues, draft.tables.length > 1);
  return issues;
}

/** Plain-language summary of a row-filter node, for the review step. */
export function describeNode(node: FilterNode): string {
  if (isGroup(node)) {
    if (node.logic === "not") return `NOT (${node.conditions.map(describeNode).join(" AND ")})`;
    const joined = node.conditions.map(describeNode).join(` ${node.logic.toUpperCase()} `);
    return node.conditions.length > 1 ? `(${joined})` : joined;
  }
  const label = OPERATOR_LABELS[node.operator];
  if (node.operator === "is_null" || node.operator === "is_not_null") return `${node.column} ${label}`;
  if (Array.isArray(node.value)) {
    return node.operator === "between"
      ? `${node.column} ${label} ${String(node.value[0])} and ${String(node.value[1])}`
      : `${node.column} ${label} ${node.value.map(String).join(", ")}`;
  }
  return `${node.column} ${label} ${String(node.value)}`;
}

/** Plain-language lines describing a (normalized) policy, for the review step. */
export function summarizePolicy(resourceType: string, policy: Record<string, unknown>): string[] {
  const list = (key: string) => (Array.isArray(policy[key]) ? (policy[key] as unknown[]).map(String) : []);
  const nodes = (key: string) => (Array.isArray(policy[key]) ? (policy[key] as FilterNode[]) : []);
  const lines: string[] = [];
  if (policy.effect === "deny") {
    if (policy.deny_all === true) return [`Denies this action on ${resourceType} entirely, overriding any permission that allows it.`];
    const tables = list("denied_tables");
    const columns = list("denied_columns");
    if (tables.length) lines.push(`Never allows the tables ${tables.join(", ")}.`);
    if (columns.length) lines.push(`Never allows the columns ${columns.join(", ")}.`);
    for (const node of nodes("excluded_rows")) lines.push(`Always hides rows where ${describeNode(node)}.`);
    if (policy.deny_export === true) lines.push("Exporting results is denied.");
    if (policy.deny_sql_view === true) lines.push("Viewing the generated SQL is denied.");
    return lines;
  }
  if (resourceType === DATA_RESOURCE_TYPE) {
    const tables = list("allowed_tables");
    lines.push(`Allows the tables ${tables.join(", ") || "(none)"}.`);
    const columns = list("allowed_columns");
    lines.push(columns.every((c) => c.endsWith(".*")) ? "All columns of those tables are visible." : `Visible columns: ${columns.join(", ")}.`);
    const operations = list("allowed_operations");
    lines.push(
      operations.length === 2 ? "Individual rows and aggregates are allowed." : operations[0] === "aggregate" ? "Only aggregates (totals, counts, averages) are allowed." : "Only individual rows are allowed.",
    );
    const filters = nodes("row_filters");
    if (filters.length === 0) lines.push("Every row is visible.");
    else lines.push(`Only rows where ${filters.map(describeNode).join(" AND ")}.`);
  } else {
    lines.push(`Allows this action on ${resourceType}.`);
  }
  lines.push(policy.allow_export === true ? "Exporting results is allowed." : "Exporting results is not allowed.");
  lines.push(policy.allow_sql_view === true ? "Viewing the generated SQL is allowed." : "Viewing the generated SQL is not allowed.");
  return lines;
}
