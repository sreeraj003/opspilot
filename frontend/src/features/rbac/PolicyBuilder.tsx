import { FolderPlus, Plus, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/Button";
import { Checkbox, INPUT_CLASSES, SELECT_CLASSES, TagInput } from "@/features/rbac/components";
import {
  DATA_RESOURCE_TYPE,
  MAX_DEPTH,
  OPERATOR_LABELS,
  OPERATORS_BY_TYPE,
  VALUE_TYPE_LABELS,
  isValidColumn,
  isValidTable,
  newCondition,
  newGroup,
  type ConditionDraft,
  type NodeDraft,
  type PolicyDraft,
} from "@/features/rbac/policyBuilder";
import { VALUE_TYPES, type ValueType } from "@/types/rbac";

// --- row filters ------------------------------------------------------------------------------------

function ConditionEditor({ node, onChange, onRemove, label }: { node: ConditionDraft; onChange: (n: ConditionDraft) => void; onRemove: () => void; label: string }) {
  const noValue = node.operator === "is_null" || node.operator === "is_not_null";
  const operators = OPERATORS_BY_TYPE[node.valueType];
  const inputType = node.valueType === "date" ? "date" : node.valueType === "number" ? "text" : "text";
  return (
    <div role="group" aria-label={label} className="flex flex-wrap items-center gap-2 rounded-lg border border-stone-200 bg-stone-50/60 p-2">
      <input
        aria-label={`${label} column`}
        placeholder="column"
        value={node.column}
        onChange={(event) => onChange({ ...node, column: event.target.value })}
        className={`${INPUT_CLASSES} w-40 font-mono`}
      />
      <select
        aria-label={`${label} value type`}
        value={node.valueType}
        onChange={(event) => {
          const valueType = event.target.value as ValueType;
          const operator = OPERATORS_BY_TYPE[valueType].includes(node.operator) ? node.operator : "equals";
          onChange({ ...node, valueType, operator });
        }}
        className={SELECT_CLASSES}
      >
        {VALUE_TYPES.map((type) => (
          <option key={type} value={type}>
            {VALUE_TYPE_LABELS[type]}
          </option>
        ))}
      </select>
      <select
        aria-label={`${label} operator`}
        value={node.operator}
        onChange={(event) => onChange({ ...node, operator: event.target.value as ConditionDraft["operator"] })}
        className={SELECT_CLASSES}
      >
        {operators.map((operator) => (
          <option key={operator} value={operator}>
            {OPERATOR_LABELS[operator]}
          </option>
        ))}
      </select>
      {!noValue &&
        (node.valueType === "boolean" ? (
          <select aria-label={`${label} value`} value={node.value} onChange={(event) => onChange({ ...node, value: event.target.value })} className={SELECT_CLASSES}>
            <option value="">Choose…</option>
            <option value="true">true</option>
            <option value="false">false</option>
          </select>
        ) : (
          <input
            aria-label={`${label} value`}
            type={node.operator === "in" || node.operator === "not_in" ? "text" : inputType}
            placeholder={node.operator === "in" || node.operator === "not_in" ? "values, separated by commas" : node.operator === "between" ? "from" : "value"}
            value={node.value}
            onChange={(event) => onChange({ ...node, value: event.target.value })}
            className={`${INPUT_CLASSES} w-48`}
          />
        ))}
      {node.operator === "between" && (
        <input
          aria-label={`${label} upper bound`}
          type={inputType}
          placeholder="to"
          value={node.value2}
          onChange={(event) => onChange({ ...node, value2: event.target.value })}
          className={`${INPUT_CLASSES} w-40`}
        />
      )}
      <Button variant="ghost" onClick={onRemove} aria-label={`Remove ${label.toLowerCase()}`}>
        <Trash2 aria-hidden="true" className="h-4 w-4" />
      </Button>
    </div>
  );
}

function NodeList({
  nodes,
  onChange,
  depth,
  label,
  defaultColumn,
}: {
  nodes: NodeDraft[];
  onChange: (nodes: NodeDraft[]) => void;
  depth: number;
  label: string;
  defaultColumn: string;
}) {
  const update = (index: number, node: NodeDraft) => onChange(nodes.map((n, i) => (i === index ? node : n)));
  const remove = (index: number) => onChange(nodes.filter((_, i) => i !== index));
  return (
    <div className="space-y-2">
      {nodes.map((node, index) => {
        const name = `${label} ${index + 1}`;
        if (node.kind === "condition") {
          return <ConditionEditor key={node.id} node={node} label={name} onChange={(n) => update(index, n)} onRemove={() => remove(index)} />;
        }
        return (
          <div key={node.id} role="group" aria-label={`${name} (group)`} className="space-y-2 rounded-lg border border-dashed border-stone-300 p-3">
            <div className="flex items-center justify-between gap-2">
              <label className="flex items-center gap-2 text-sm text-stone-700">
                Match
                <select
                  aria-label={`${name} logic`}
                  value={node.logic}
                  onChange={(event) => update(index, { ...node, logic: event.target.value as "and" | "or" })}
                  className={SELECT_CLASSES}
                >
                  <option value="or">any (OR)</option>
                  <option value="and">all (AND)</option>
                </select>
                of these
              </label>
              <Button variant="ghost" onClick={() => remove(index)} aria-label={`Remove ${name.toLowerCase()} group`}>
                <Trash2 aria-hidden="true" className="h-4 w-4" />
              </Button>
            </div>
            <NodeList
              nodes={node.children}
              onChange={(children) => update(index, { ...node, children })}
              depth={depth + 1}
              label={`${name} ›`}
              defaultColumn={defaultColumn}
            />
          </div>
        );
      })}
      <div className="flex flex-wrap gap-2">
        <Button variant="secondary" onClick={() => onChange([...nodes, newCondition(defaultColumn)])}>
          <Plus aria-hidden="true" className="h-4 w-4" />
          {depth === 1 ? `Add ${label.toLowerCase()}` : "Add condition"}
        </Button>
        {depth < MAX_DEPTH && (
          <Button variant="secondary" onClick={() => onChange([...nodes, newGroup()])}>
            <FolderPlus aria-hidden="true" className="h-4 w-4" />
            Add group
          </Button>
        )}
      </div>
    </div>
  );
}

// --- the builder ------------------------------------------------------------------------------------

export function PolicyBuilder({ resourceType, draft, onChange }: { resourceType: string; draft: PolicyDraft; onChange: (draft: PolicyDraft) => void }) {
  const isData = resourceType === DATA_RESOURCE_TYPE;
  const set = (changes: Partial<PolicyDraft>) => onChange({ ...draft, ...changes });
  const tableError = (value: string) => (isValidTable(value) ? null : "Use a table name such as sales or finance.sales.");

  return (
    <div className="space-y-6">
      <fieldset>
        <legend className="mb-2 text-sm font-medium text-stone-700">Effect</legend>
        <div className="flex gap-4 text-sm">
          <label className="flex items-center gap-2">
            <input type="radio" name="effect" checked={draft.effect === "allow"} onChange={() => set({ effect: "allow" })} />
            Allow access
          </label>
          <label className="flex items-center gap-2">
            <input type="radio" name="effect" checked={draft.effect === "deny"} onChange={() => set({ effect: "deny" })} />
            Deny (restriction that always wins)
          </label>
        </div>
      </fieldset>

      {draft.effect === "allow" && isData && (
        <>
          <TagInput
            label="Allowed tables"
            values={draft.tables}
            placeholder="sales"
            validate={tableError}
            onChange={(tables) => set({ tables })}
            hint="Tables will be checked against registered schemas once schema registration is available."
          />
          {draft.tables.map((table) => (
            <div key={table} className="rounded-lg border border-stone-200 p-4">
              <Checkbox
                label={`All columns of ${table}`}
                checked={draft.allColumns[table] ?? false}
                onChange={(checked) => set({ allColumns: { ...draft.allColumns, [table]: checked } })}
              />
              {!draft.allColumns[table] && (
                <div className="mt-3">
                  <TagInput
                    label={`Allowed columns of ${table}`}
                    values={draft.columns[table] ?? []}
                    placeholder="amount"
                    validate={(value) => (isValidColumn(value) ? null : "Use a column name such as amount.")}
                    onChange={(columns) => set({ columns: { ...draft.columns, [table]: columns } })}
                  />
                </div>
              )}
            </div>
          ))}
          <fieldset className="space-y-2">
            <legend className="mb-1 text-sm font-medium text-stone-700">Allowed operations</legend>
            <Checkbox label="Read individual rows" checked={draft.operations.read} onChange={(read) => set({ operations: { ...draft.operations, read } })} />
            <Checkbox
              label="Aggregates only (totals, counts, averages)"
              checked={draft.operations.aggregate}
              onChange={(aggregate) => set({ operations: { ...draft.operations, aggregate } })}
            />
          </fieldset>
          <fieldset>
            <legend className="mb-1 text-sm font-medium text-stone-700">Row restrictions</legend>
            <p className="mb-3 text-xs text-stone-500">
              Only rows matching every restriction are visible. Use a group to allow any of several values.
              {draft.tables.length > 1 && " Write columns as table.column."}
            </p>
            <NodeList nodes={draft.filters} onChange={(filters) => set({ filters })} depth={1} label="Row filter" defaultColumn="" />
          </fieldset>
          <fieldset className="space-y-2">
            <legend className="mb-1 text-sm font-medium text-stone-700">Results</legend>
            <Checkbox label="Allow exporting results" checked={draft.allowExport} onChange={(allowExport) => set({ allowExport })} />
            <Checkbox label="Allow viewing the generated SQL" checked={draft.allowSqlView} onChange={(allowSqlView) => set({ allowSqlView })} />
          </fieldset>
        </>
      )}

      {draft.effect === "allow" && !isData && (
        <fieldset className="space-y-2">
          <legend className="mb-1 text-sm font-medium text-stone-700">Results</legend>
          <Checkbox label="Allow exporting" checked={draft.allowExport} onChange={(allowExport) => set({ allowExport })} />
          <Checkbox label="Allow viewing the generated SQL" checked={draft.allowSqlView} onChange={(allowSqlView) => set({ allowSqlView })} />
        </fieldset>
      )}

      {draft.effect === "deny" && (
        <>
          <Checkbox
            label="Deny this action entirely"
            checked={draft.denyAll}
            onChange={(denyAll) => set({ denyAll })}
            description="Overrides every permission that allows the same resource and action."
          />
          {isData && !draft.denyAll && (
            <>
              <TagInput label="Denied tables" values={draft.deniedTables} placeholder="payroll" validate={tableError} onChange={(deniedTables) => set({ deniedTables })} />
              <TagInput
                label="Denied columns"
                values={draft.deniedColumns}
                placeholder="sales.customer_email"
                validate={(value) => (/^[A-Za-z_][\w]*(\.[A-Za-z_][\w]*){1,2}$/.test(value) ? null : "Write table.column.")}
                onChange={(deniedColumns) => set({ deniedColumns })}
              />
              <fieldset>
                <legend className="mb-1 text-sm font-medium text-stone-700">Excluded rows</legend>
                <p className="mb-3 text-xs text-stone-500">Rows matching these conditions are always hidden. Write columns as table.column.</p>
                <NodeList nodes={draft.excludedRows} onChange={(excludedRows) => set({ excludedRows })} depth={1} label="Excluded row" defaultColumn="" />
              </fieldset>
            </>
          )}
          {!draft.denyAll && (
            <fieldset className="space-y-2">
              <legend className="mb-1 text-sm font-medium text-stone-700">Results</legend>
              <Checkbox label="Deny exporting" checked={draft.denyExport} onChange={(denyExport) => set({ denyExport })} />
              <Checkbox label="Deny viewing the generated SQL" checked={draft.denySqlView} onChange={(denySqlView) => set({ denySqlView })} />
            </fieldset>
          )}
        </>
      )}
    </div>
  );
}
