import { describe, expect, it } from "vitest";

import {
  dataTypeCategory,
  dataTypeError,
  importRequest,
  issueLocation,
  issuesFromError,
  MAX_IMPORT_FILE_BYTES,
  parseImportText,
  validateRelationshipDraft,
  type RelationshipDraft,
} from "@/features/schemas/validation";
import {
  CUSTOMER_EMAIL,
  CUSTOMER_PK,
  CUSTOMERS_ID,
  ORDER_CUSTOMER,
  ORDER_PLACED,
  ORDERS_ID,
  OUTLINE_TABLES,
  RELATIONSHIP_ID,
} from "@/test/schemaFixtures";

const valid: RelationshipDraft = {
  sourceTableId: ORDERS_ID,
  sourceColumnId: ORDER_CUSTOMER,
  targetTableId: CUSTOMERS_ID,
  targetColumnId: CUSTOMER_PK,
  relationshipType: "many_to_one",
};

describe("validateRelationshipDraft", () => {
  it("accepts a well-formed relationship", () => {
    expect(validateRelationshipDraft(valid, OUTLINE_TABLES, [])).toEqual({});
  });

  it("requires every field", () => {
    const errors = validateRelationshipDraft(
      { sourceTableId: "", sourceColumnId: "", targetTableId: "", targetColumnId: "", relationshipType: "" },
      OUTLINE_TABLES,
      [],
    );
    expect(Object.keys(errors).sort()).toEqual(["relationshipType", "sourceTableId", "targetTableId"]);
    expect(validateRelationshipDraft({ ...valid, sourceColumnId: "" }, OUTLINE_TABLES, []).sourceColumnId).toMatch(/Choose a column/);
  });

  it("rejects a column referencing itself, inactive columns and incompatible types", () => {
    const self = validateRelationshipDraft({ ...valid, targetTableId: ORDERS_ID, targetColumnId: ORDER_CUSTOMER }, OUTLINE_TABLES, []);
    expect(self.targetColumnId).toBe("A column cannot reference itself.");
    expect(validateRelationshipDraft({ ...valid, sourceColumnId: ORDER_PLACED }, OUTLINE_TABLES, []).sourceColumnId).toBe(
      "This column is inactive.",
    );
    expect(validateRelationshipDraft({ ...valid, targetColumnId: CUSTOMER_EMAIL }, OUTLINE_TABLES, []).targetColumnId).toMatch(
      /Types do not match: integer and varchar\(255\)/,
    );
  });

  it("rejects duplicates in either direction but not the relationship being edited", () => {
    const pair = { id: RELATIONSHIP_ID, sourceColumnId: ORDER_CUSTOMER, targetColumnId: CUSTOMER_PK };
    expect(validateRelationshipDraft(valid, OUTLINE_TABLES, [pair]).targetColumnId).toBe("This relationship already exists.");
    const reversed = { ...pair, sourceColumnId: CUSTOMER_PK, targetColumnId: ORDER_CUSTOMER };
    expect(validateRelationshipDraft(valid, OUTLINE_TABLES, [reversed]).targetColumnId).toBe("This relationship already exists.");
    expect(validateRelationshipDraft(valid, OUTLINE_TABLES, [pair], RELATIONSHIP_ID)).toEqual({});
  });
});

describe("data types", () => {
  it("normalises common types into categories", () => {
    expect(dataTypeCategory("VARCHAR(255)")).toBe("string");
    expect(dataTypeCategory("numeric(12, 2)")).toBe("number");
    expect(dataTypeCategory("int unsigned")).toBe("number");
    expect(dataTypeCategory("integer[]")).toBe("other");
    expect(dataTypeError("integer")).toBeNull();
    expect(dataTypeError("DROP TABLE users")).toMatch(/supported data type/);
  });
});

describe("parseImportText", () => {
  it("counts tables, columns and relationships", () => {
    const text = JSON.stringify({
      tables: [
        { table_name: "a", columns: [{}, {}] },
        { table_name: "b", columns: [{}] },
      ],
      relationships: [{}],
    });
    expect(parseImportText(text, text.length)).toMatchObject({ ok: true, tables: 2, columns: 3, relationships: 1 });
  });

  it("rejects oversized files, invalid JSON and the wrong shape", () => {
    expect(parseImportText("{}", MAX_IMPORT_FILE_BYTES + 1)).toEqual({ ok: false, error: "The file is larger than 5 MB." });
    expect(parseImportText("{nope", 5)).toEqual({ ok: false, error: "The file is not valid JSON." });
    expect(parseImportText("[]", 2).ok).toBe(false);
    expect(parseImportText('{"tables": {}}', 14)).toEqual({ ok: false, error: '"tables" must be a list.' });
  });
});

describe("import helpers", () => {
  it("only confirms a replace when it is applied", () => {
    expect(importRequest({}, "replace", true).confirm_replace).toBe(false);
    expect(importRequest({}, "replace", false, 4)).toMatchObject({ confirm_replace: true, expected_schema_version: 4, dry_run: false });
    expect(importRequest({}, "merge", false).confirm_replace).toBe(false);
  });

  it("formats issue locations and reads issues from error details", () => {
    expect(
      issueLocation({ code: "x", message: "m", severity: "error", location: ["document", "tables", "1", "columns", "2", "data_type"] }),
    ).toBe("tables[1] › columns[2] › data_type");
    expect(issuesFromError([{ code: "invalid_data_type", message: "Bad", location: [], severity: "error" }, "junk"])).toHaveLength(1);
    expect(issuesFromError(undefined)).toEqual([]);
  });
});
