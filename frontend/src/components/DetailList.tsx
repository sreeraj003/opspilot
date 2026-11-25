interface DetailListProps {
  items: ReadonlyArray<{ label: string; value: string }>;
}

/** Compact label/value pairs used inside cards. */
export function DetailList({ items }: DetailListProps) {
  return (
    <dl className="space-y-2">
      {items.map((item) => (
        <div key={item.label} className="flex items-center justify-between gap-4">
          <dt className="text-fg-muted">{item.label}</dt>
          <dd className="truncate font-medium text-fg">{item.value}</dd>
        </div>
      ))}
    </dl>
  );
}
