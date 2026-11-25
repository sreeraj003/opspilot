import { describe, expect, it } from "vitest";

import {
  buildPolicy,
  describeNode,
  draftFromPolicy,
  emptyDraft,
  newCondition,
  newGroup,
  summarizePolicy,
  validateDraft,
  type PolicyDraft,
} from "@/features/rbac/policyBuilder";

const DATA = "database_data";

function salesDraft(changes: Partial<PolicyDraft> = {}): PolicyDraft {
  return { ...emptyDraft(), tables: ["sales"], allColumns: { sales: true }, columns: { sales: [] }, ...changes };
}

describe("policy builder model", () => {
  it("builds an allow policy with columns, operations and nested filters", () => {
    const region = { ...newCondition("region"), value: "Karnataka" };
    const group = newGroup("or");
    group.children = [
      { ...newCondition("amount"), valueType: "number" as const, operator: "greater_than" as const, value: "1000" },
      { ...newCondition("channel"), operator: "in" as const, value: "online, retail" },
    ];
    const draft = salesDraft({ allColumns: { sales: false }, columns: { sales: ["amount", "region"] }, filters: [region, group], operations: { read: false, aggregate: true } });

    expect(buildPolicy(DATA, draft)).toEqual({
      version: 1,
      effect: "allow",
      allowed_tables: ["sales"],
      allowed_columns: ["amount", "region"],
      allowed_operations: ["aggregate"],
      row_filters: [
        { column: "region", operator: "equals", value: "Karnataka" },
        {
          logic: "or",
          conditions: [
            { column: "amount", operator: "greater_than", value: 1000 },
            { column: "channel", operator: "in", value: ["online", "retail"] },
          ],
        },
      ],
      allow_export: false,
      allow_sql_view: false,
    });
  });

  it("qualifies columns when several tables are allowed and round-trips through the editor", () => {
    const draft = salesDraft({ tables: ["sales", "targets"], allColumns: { sales: true, targets: false }, columns: { targets: ["goal"] } });
    const policy = buildPolicy(DATA, draft);
    expect(policy.allowed_columns).toEqual(["sales.*", "targets.goal"]);

    const back = draftFromPolicy(policy);
    expect(back.tables).toEqual(["sales", "targets"]);
    expect(back.allColumns).toEqual({ sales: true, targets: false });
    expect(back.columns.targets).toEqual(["goal"]);
  });

  it("builds deny policies and flag-only policies for other resources", () => {
    const deny = { ...emptyDraft(), effect: "deny" as const, deniedColumns: ["sales.customer_email"] };
    expect(buildPolicy(DATA, deny)).toMatchObject({ effect: "deny", deny_all: false, denied_columns: ["sales.customer_email"] });
    expect(buildPolicy("executive_reports", { ...emptyDraft(), allowExport: true })).toEqual({
      version: 1,
      effect: "allow",
      allow_export: true,
      allow_sql_view: false,
    });
  });

  it("reports what must be fixed before review", () => {
    expect(validateDraft(DATA, emptyDraft()).map((i) => i.message)).toContain("Add at least one table.");

    const noColumns = salesDraft({ allColumns: { sales: false } });
    expect(validateDraft(DATA, noColumns)).toContainEqual({ path: "Columns of sales", message: "Choose columns or allow all columns." });

    const noOps = salesDraft({ operations: { read: false, aggregate: false } });
    expect(validateDraft(DATA, noOps).map((i) => i.message)).toContain("Allow reading rows, aggregates, or both.");

    const badNumber = salesDraft({ filters: [{ ...newCondition("amount"), valueType: "number", value: "lots" }] });
    expect(validateDraft(DATA, badNumber)).toContainEqual({ path: "Row filter 1", message: "Enter a number." });

    const reversed = salesDraft({ filters: [{ ...newCondition("amount"), valueType: "number", operator: "between", value: "10", value2: "5" }] });
    expect(validateDraft(DATA, reversed).map((i) => i.message)).toContain("The lower bound must not be greater than the upper bound.");

    const emptyGroup = salesDraft({ filters: [{ ...newGroup(), children: [] }] });
    expect(validateDraft(DATA, emptyGroup).map((i) => i.message)).toContain("A group needs at least one condition.");

    const unqualified = salesDraft({ tables: ["sales", "targets"], allColumns: { sales: true, targets: true }, filters: [{ ...newCondition("region"), value: "x" }] });
    expect(validateDraft(DATA, unqualified).map((i) => i.message)).toContain("Write the column as table.column.");

    expect(validateDraft(DATA, { ...emptyDraft(), effect: "deny" }).map((i) => i.message)).toContain("Choose at least one thing to deny.");
    expect(validateDraft(DATA, salesDraft())).toEqual([]);
  });

  it("limits nesting depth", () => {
    let node = newGroup();
    for (let level = 0; level < 4; level += 1) {
      const outer = newGroup();
      outer.children = [node];
      node = outer;
    }
    expect(validateDraft(DATA, salesDraft({ filters: [node] })).map((i) => i.message)).toContain("Groups can be nested at most 4 levels deep.");
  });

  it("describes policies in plain language", () => {
    expect(describeNode({ logic: "or", conditions: [{ column: "region", operator: "equals", value: "Karnataka" }, { column: "region", operator: "equals", value: "Kerala" }] })).toBe(
      "(region equals Karnataka OR region equals Kerala)",
    );
    const lines = summarizePolicy(DATA, buildPolicy(DATA, salesDraft({ filters: [{ ...newCondition("region"), value: "Karnataka" }] })));
    expect(lines).toContain("Allows the tables sales.");
    expect(lines).toContain("Only rows where region equals Karnataka.");
    expect(lines).toContain("Exporting results is not allowed.");
    expect(summarizePolicy(DATA, { effect: "deny", deny_all: true })[0]).toMatch(/entirely/);
  });
});
