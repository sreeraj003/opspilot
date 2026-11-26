import type { OutlineTable, RelationshipType, SchemaImportRequest, ValidationIssue } from "@/types/schemaRegistry";

/**
 * Client-side checks that mirror the backend's rules so mistakes are caught
 * before a request is sent. The backend validates everything again and its
 * answer is authoritative (``app/schema_registry``).
 */

export const IDENTIFIER_PATTERN = /^[A-Za-z_][A-Za-z0-9_]{0,62}$/;
export const IDENTIFIER_MESSAGE = "Use letters, digits and underscores, starting with a letter or underscore (at most 63).";

/** Largest import file the interface reads; the server's own limit (SCHEMA_IMPORT_MAX_BYTES) is checked again. */
export const MAX_IMPORT_FILE_BYTES = 5 * 1024 * 1024;

export const DATABASE_TYPE_LABELS: Record<string, string> = {
  postgresql: "PostgreSQL",
  mysql: "MySQL",
  mariadb: "MariaDB",
  sqlserver: "SQL Server",
  oracle: "Oracle",
  sqlite: "SQLite",
  other: "Other",
};

export const RELATIONSHIP_LABELS: Record<RelationshipType, string> = {
  many_to_one: "Many to one",
  one_to_many: "One to many",
  one_to_one: "One to one",
  many_to_many: "Many to many",
};

// Base type names accepted by the backend (app/schema_registry/data_types.py), by category.
const TYPES: Record<string, string[]> = {
  number: [
    "smallint",
    "integer",
    "int",
    "bigint",
    "tinyint",
    "mediumint",
    "int2",
    "int4",
    "int8",
    "serial",
    "smallserial",
    "bigserial",
    "serial4",
    "serial8",
    "decimal",
    "numeric",
    "dec",
    "number",
    "real",
    "float",
    "float4",
    "float8",
    "double",
    "double precision",
    "money",
    "smallmoney",
    "binary_float",
    "binary_double",
  ],
  string: [
    "char",
    "character",
    "varchar",
    "character varying",
    "nchar",
    "nvarchar",
    "national char",
    "national character",
    "national character varying",
    "varchar2",
    "nvarchar2",
    "text",
    "tinytext",
    "mediumtext",
    "longtext",
    "ntext",
    "string",
    "clob",
    "nclob",
    "citext",
    "uuid",
    "uniqueidentifier",
    "enum",
    "set",
    "name",
  ],
  boolean: ["boolean", "bool", "bit"],
  date: ["date"],
  datetime: [
    "timestamp",
    "timestamptz",
    "timestamp with time zone",
    "timestamp without time zone",
    "timestamp with local time zone",
    "datetime",
    "datetime2",
    "smalldatetime",
    "datetimeoffset",
  ],
  other: [
    "time",
    "timetz",
    "time with time zone",
    "time without time zone",
    "interval",
    "year",
    "json",
    "jsonb",
    "xml",
    "bytea",
    "blob",
    "tinyblob",
    "mediumblob",
    "longblob",
    "binary",
    "varbinary",
    "raw",
    "long raw",
    "long",
    "image",
    "rowversion",
    "sql_variant",
    "hierarchyid",
    "inet",
    "cidr",
    "macaddr",
    "macaddr8",
    "geometry",
    "geography",
    "point",
    "line",
    "polygon",
    "hstore",
    "tsvector",
    "tsquery",
    "oid",
    "bit varying",
    "varbit",
    "rowid",
    "urowid",
    "bfile",
    "interval year to month",
    "interval day to second",
  ],
};
const CATEGORY_BY_TYPE = new Map(Object.entries(TYPES).flatMap(([category, names]) => names.map((name) => [name, category] as const)));

/** Common types offered as suggestions in the column form. */
export const COMMON_DATA_TYPES = [
  "integer",
  "bigint",
  "numeric(12,2)",
  "decimal(10,2)",
  "varchar(255)",
  "text",
  "boolean",
  "date",
  "timestamp",
  "timestamp with time zone",
  "uuid",
  "jsonb",
];

const PARAMETERS = /\(\s*(?:\d{1,5}|max)\s*(?:,\s*-?\d{1,5}\s*)?\)/g;

/** The data type's category, or null when the backend would reject it. */
export function dataTypeCategory(raw: string): string | null {
  const value = raw.trim().toLowerCase().split(/\s+/).join(" ");
  if (!value || value.length > 64 || !/^[a-z0-9_ (),[\]-]+$/.test(value)) return null;
  const parameterLists = value.match(PARAMETERS) ?? [];
  if (parameterLists.length > 1) return null;
  let rest = value.replace(PARAMETERS, " ");
  if (/[()]/.test(rest)) return null;
  rest = rest.split(/\s+/).join(" ").trim();
  const isArray = rest.endsWith("[]");
  while (rest.endsWith("[]")) rest = rest.slice(0, -2).trimEnd();
  if (/[[\]]/.test(rest)) return null;
  if (rest.endsWith(" unsigned")) rest = rest.slice(0, -" unsigned".length);
  const category = CATEGORY_BY_TYPE.get(rest.trim());
  if (!category) return null;
  return isArray ? "other" : category;
}

export function dataTypeError(raw: string): string | null {
  return dataTypeCategory(raw) === null ? "Use a supported data type, such as integer, varchar(255) or numeric(12,2)." : null;
}

// --- relationship form ------------------------------------------------------------------------------------

export interface RelationshipDraft {
  sourceTableId: string;
  sourceColumnId: string;
  targetTableId: string;
  targetColumnId: string;
  relationshipType: RelationshipType | "";
}

export interface ExistingPair {
  id: string;
  sourceColumnId: string;
  targetColumnId: string;
}

/** Problems with a relationship before it is sent, keyed by field. Empty when it may be submitted. */
export function validateRelationshipDraft(
  draft: RelationshipDraft,
  tables: OutlineTable[],
  existing: ExistingPair[],
  editingId?: string,
): Partial<Record<keyof RelationshipDraft, string>> {
  const errors: Partial<Record<keyof RelationshipDraft, string>> = {};
  const sourceTable = tables.find((t) => t.id === draft.sourceTableId);
  const targetTable = tables.find((t) => t.id === draft.targetTableId);
  const sourceColumn = sourceTable?.columns.find((c) => c.id === draft.sourceColumnId);
  const targetColumn = targetTable?.columns.find((c) => c.id === draft.targetColumnId);
  if (!sourceTable) errors.sourceTableId = "Choose the source table.";
  else if (!sourceTable.is_active) errors.sourceTableId = "This table is inactive.";
  if (!targetTable) errors.targetTableId = "Choose the target table.";
  else if (!targetTable.is_active) errors.targetTableId = "This table is inactive.";
  if (sourceTable && !sourceColumn) errors.sourceColumnId = "Choose a column of the source table.";
  else if (sourceColumn && !sourceColumn.is_active) errors.sourceColumnId = "This column is inactive.";
  if (targetTable && !targetColumn) errors.targetColumnId = "Choose a column of the target table.";
  else if (targetColumn && !targetColumn.is_active) errors.targetColumnId = "This column is inactive.";
  if (!draft.relationshipType) errors.relationshipType = "Choose the relationship type.";
  if (sourceColumn && targetColumn) {
    if (sourceColumn.id === targetColumn.id) {
      errors.targetColumnId = "A column cannot reference itself.";
    } else if (
      sourceColumn.data_type_category !== targetColumn.data_type_category &&
      sourceColumn.data_type_category !== "other" &&
      targetColumn.data_type_category !== "other"
    ) {
      errors.targetColumnId = `Types do not match: ${sourceColumn.data_type} and ${targetColumn.data_type}.`;
    } else if (
      existing.some(
        (pair) =>
          pair.id !== editingId &&
          ((pair.sourceColumnId === sourceColumn.id && pair.targetColumnId === targetColumn.id) ||
            (pair.sourceColumnId === targetColumn.id && pair.targetColumnId === sourceColumn.id)),
      )
    ) {
      errors.targetColumnId = "This relationship already exists.";
    }
  }
  return errors;
}

// --- import files --------------------------------------------------------------------------------------------

export type ParsedImport =
  | {
      ok: true;
      document: Record<string, unknown>;
      tables: number;
      columns: number;
      relationships: number;
    }
  | { ok: false; error: string };

/** Read an import file locally: size, JSON syntax and the document's basic shape. */
export function parseImportText(text: string, size: number): ParsedImport {
  if (size > MAX_IMPORT_FILE_BYTES) {
    return {
      ok: false,
      error: `The file is larger than ${Math.round(MAX_IMPORT_FILE_BYTES / 1024 / 1024)} MB.`,
    };
  }
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch {
    return { ok: false, error: "The file is not valid JSON." };
  }
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return {
      ok: false,
      error: 'The file must contain a JSON object with a "tables" list.',
    };
  }
  const document = value as Record<string, unknown>;
  const tables = document.tables;
  if (tables !== undefined && !Array.isArray(tables)) return { ok: false, error: '"tables" must be a list.' };
  const relationships = document.relationships;
  if (relationships !== undefined && !Array.isArray(relationships)) return { ok: false, error: '"relationships" must be a list.' };
  const tableList = (tables ?? []) as unknown[];
  const columns = tableList.reduce<number>((sum, table) => {
    const list = typeof table === "object" && table !== null ? (table as { columns?: unknown }).columns : undefined;
    return sum + (Array.isArray(list) ? list.length : 0);
  }, 0);
  return {
    ok: true,
    document,
    tables: tableList.length,
    columns,
    relationships: ((relationships ?? []) as unknown[]).length,
  };
}

export function importRequest(
  document: Record<string, unknown>,
  strategy: SchemaImportRequest["strategy"],
  dryRun: boolean,
  expectedVersion?: number,
): SchemaImportRequest {
  return {
    strategy,
    dry_run: dryRun,
    confirm_replace: strategy === "replace" && !dryRun,
    ...(expectedVersion ? { expected_schema_version: expectedVersion } : {}),
    document,
  };
}

/** "document.tables.1.columns.2.data_type" → "tables[1] › columns[2] › data_type". */
export function issueLocation(issue: ValidationIssue): string {
  const parts = issue.location[0] === "document" ? issue.location.slice(1) : issue.location;
  const out: string[] = [];
  for (const part of parts) {
    if (/^\d+$/.test(part) && out.length > 0) out[out.length - 1] = `${out[out.length - 1]}[${part}]`;
    else out.push(part);
  }
  return out.join(" › ");
}

/** Validation issues from an API error (422 SCHEMA_VALIDATION_FAILED details), when it has them. */
export function issuesFromError(details: unknown): ValidationIssue[] {
  if (!Array.isArray(details)) return [];
  return details.filter(
    (item): item is ValidationIssue =>
      typeof item === "object" &&
      item !== null &&
      typeof (item as ValidationIssue).message === "string" &&
      typeof (item as ValidationIssue).code === "string",
  );
}
