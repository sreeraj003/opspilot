/** Contracts mirrored from backend/app/schemas/schema_registry.py (Module 06). */

export type DatabaseType = "postgresql" | "mysql" | "mariadb" | "sqlserver" | "oracle" | "sqlite" | "other";
export type RelationshipType = "one_to_one" | "one_to_many" | "many_to_one" | "many_to_many";
export type ImportStrategy = "create_only" | "merge" | "replace";
export type SchemaStatusFilter = "all" | "active" | "inactive";
export type SortOrder = "asc" | "desc";

export interface GlossaryTerm {
  term: string;
  definition: string;
  synonyms: string[];
}

export interface SchemaSource {
  id: string;
  name: string;
  description: string | null;
  database_type: DatabaseType;
  dialect: string;
  schema_version: number;
  metadata_updated_at: string;
  is_active: boolean;
  business_glossary: GlossaryTerm[];
  created_by: string | null;
  created_at: string;
  updated_at: string;
  table_count: number;
  column_count: number;
  relationship_count: number;
}

export interface SchemaSourceInput {
  name: string;
  description: string | null;
  database_type: DatabaseType;
  dialect?: string | null;
  is_active?: boolean;
  business_glossary?: GlossaryTerm[];
}

export interface SchemaListParams {
  offset: number;
  limit: number;
  search?: string;
  status?: SchemaStatusFilter;
  database_type?: DatabaseType | "";
  sort?: "name" | "updated_at" | "created_at" | "schema_version";
  order?: SortOrder;
}

export interface SchemaColumn {
  id: string;
  table_id: string;
  column_name: string;
  data_type: string;
  data_type_category: string;
  description: string | null;
  business_meaning: string | null;
  is_primary_key: boolean;
  is_foreign_key: boolean;
  is_nullable: boolean;
  is_sensitive: boolean;
  is_active: boolean;
  ordinal_position: number;
  created_at: string;
  updated_at: string;
}

export interface SchemaColumnInput {
  column_name: string;
  data_type: string;
  description?: string | null;
  business_meaning?: string | null;
  is_primary_key?: boolean;
  is_foreign_key?: boolean;
  is_nullable?: boolean;
  is_sensitive?: boolean;
  is_active?: boolean;
}

export interface SchemaTable {
  id: string;
  schema_source_id: string;
  schema_name: string;
  table_name: string;
  qualified_name: string;
  description: string | null;
  business_purpose: string | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
  column_count: number;
  relationship_count: number;
}

export interface SchemaTableDetail extends SchemaTable {
  columns: SchemaColumn[];
}

export interface SchemaTableInput {
  schema_name: string;
  table_name: string;
  description?: string | null;
  business_purpose?: string | null;
  is_active?: boolean;
}

export interface TableListParams {
  offset: number;
  limit: number;
  search?: string;
  status?: SchemaStatusFilter;
  schema_name?: string;
}

export interface RelationshipEndpoint {
  table_id: string;
  column_id: string;
  schema_name: string;
  table_name: string;
  column_name: string;
  data_type: string;
  is_active: boolean;
}

export interface SchemaRelationship {
  id: string;
  schema_source_id: string;
  source: RelationshipEndpoint;
  target: RelationshipEndpoint;
  relationship_type: RelationshipType;
  description: string | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface SchemaRelationshipInput {
  source_table_id: string;
  source_column_id: string;
  target_table_id: string;
  target_column_id: string;
  relationship_type: RelationshipType;
  description?: string | null;
  is_active?: boolean;
}

export interface OutlineColumn {
  id: string;
  column_name: string;
  data_type: string;
  data_type_category: string;
  is_primary_key: boolean;
  is_foreign_key: boolean;
  is_nullable: boolean;
  is_sensitive: boolean;
  is_active: boolean;
}

export interface OutlineTable {
  id: string;
  schema_name: string;
  table_name: string;
  is_active: boolean;
  columns: OutlineColumn[];
}

export interface SchemaOutline {
  schema_id: string;
  schema_version: number;
  tables: OutlineTable[];
}

export interface ValidationIssue {
  code: string;
  message: string;
  location: string[];
  severity: "error" | "warning";
}

export interface SchemaSummary {
  schema_id: string;
  name: string;
  database_type: DatabaseType;
  dialect: string;
  is_active: boolean;
  schema_version: number;
  metadata_updated_at: string;
  counts: {
    tables: number;
    active_tables: number;
    columns: number;
    active_columns: number;
    primary_key_columns: number;
    foreign_key_columns: number;
    sensitive_columns: number;
    relationships: number;
    active_relationships: number;
    schema_namespaces: string[];
    glossary_terms: number;
  };
  issues: ValidationIssue[];
}

export interface SchemaChange {
  id: string;
  event_type: string;
  entity_type: string;
  entity_id: string | null;
  schema_version: number;
  version_incremented: boolean;
  summary: string;
  details: Record<string, unknown>;
  actor_user_id: string | null;
  actor_name: string | null;
  created_at: string;
}

export interface ImportCount {
  created: number;
  updated: number;
  unchanged: number;
  removed: number;
}

export interface ImportChange {
  action: "create" | "update" | "remove";
  entity_type: "table" | "column" | "relationship" | "glossary_term";
  name: string;
  fields: string[];
}

export interface SchemaImportResult {
  strategy: ImportStrategy;
  dry_run: boolean;
  applied: boolean;
  valid: boolean;
  schema_version_before: number;
  schema_version_after: number;
  version_incremented: boolean;
  counts: { tables: ImportCount; columns: ImportCount; relationships: ImportCount; glossary_terms: ImportCount };
  changes: ImportChange[];
  changes_truncated: boolean;
  errors: ValidationIssue[];
  warnings: ValidationIssue[];
}

export interface SchemaImportRequest {
  strategy: ImportStrategy;
  dry_run: boolean;
  confirm_replace?: boolean;
  expected_schema_version?: number;
  document: Record<string, unknown>;
}
