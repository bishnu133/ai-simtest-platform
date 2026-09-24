"use client";

import { Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import type { Column, Row } from "@/lib/engine/gates";

type EditableTableProps = {
  columns: Column[];
  rows: Row[];
  isEditing: boolean;
  onChange: (rows: Row[]) => void;
  /** "remove" mode shows an include checkbox instead of inline editing */
  mode: "edit" | "remove";
  excluded?: Set<string>;
  onToggleExcluded?: (id: string) => void;
  onAddRow?: () => void;
};

const SEVERITY_TONES: Record<string, string> = {
  critical: "bg-fail/10 text-fail",
  high: "bg-warn/10 text-warn",
  medium: "bg-accent text-accent-foreground",
  low: "bg-muted text-muted-foreground",
};

function CellValue({ column, value }: { column: Column; value: string }) {
  if (column.options && value) {
    return (
      <span
        className={`inline-block rounded-full px-2 py-0.5 text-xs font-semibold capitalize ${
          SEVERITY_TONES[value.toLowerCase()] ?? "bg-muted text-muted-foreground"
        }`}
      >
        {value}
      </span>
    );
  }
  return <span className="break-words block py-1 whitespace-pre-line">{value || <span className="text-muted-foreground">—</span>}</span>;
}

export function EditableTable({
  columns,
  rows,
  isEditing,
  onChange,
  mode,
  excluded,
  onToggleExcluded,
  onAddRow,
}: EditableTableProps) {
  const update = (id: string, key: string, value: string) =>
    onChange(rows.map((row) => (row.id === id ? { ...row, [key]: value } : row)));
  const remove = (id: string) => onChange(rows.filter((row) => row.id !== id));

  const showRemoveToggle = mode === "remove" && isEditing;
  const showDelete = mode === "edit" && isEditing && !!onAddRow;

  return (
    <div className="rounded-md border bg-card shadow-sm overflow-x-auto">
      <Table className="min-w-[720px]">
        <TableHeader className="bg-muted/50">
          <TableRow>
            {showRemoveToggle && <TableHead className="w-12">Include</TableHead>}
            {columns.map((col) => (
              <TableHead key={col.key} className="font-semibold text-foreground whitespace-normal" style={{ width: col.width }}>
                {col.header}
              </TableHead>
            ))}
            {showDelete && <TableHead className="w-12"><span className="sr-only">Delete</span></TableHead>}
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.length === 0 ? (
            <TableRow>
              <TableCell colSpan={columns.length + 1} className="text-center py-8 text-muted-foreground">
                No items proposed
              </TableCell>
            </TableRow>
          ) : (
            rows.map((row) => {
              const isExcluded = excluded?.has(row.id) ?? false;
              return (
                <TableRow key={row.id} className={isExcluded ? "opacity-40" : undefined}>
                  {showRemoveToggle && (
                    <TableCell className="align-top pt-4">
                      <Checkbox
                        checked={!isExcluded}
                        onCheckedChange={() => onToggleExcluded?.(row.id)}
                        aria-label={`Include ${row[columns[1]?.key] ?? row.id}`}
                      />
                    </TableCell>
                  )}
                  {columns.map((col) => (
                    <TableCell key={col.key} className="align-top whitespace-normal">
                      {isEditing && mode === "edit" && col.editable ? (
                        col.options ? (
                          <select
                            className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm capitalize focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50"
                            value={row[col.key]}
                            onChange={(e) => update(row.id, col.key, e.target.value)}
                            aria-label={col.header}
                          >
                            {col.options.map((opt) => (
                              <option key={opt} value={opt}>
                                {opt}
                              </option>
                            ))}
                          </select>
                        ) : col.multiline ? (
                          <Textarea
                            value={row[col.key]}
                            onChange={(e) => update(row.id, col.key, e.target.value)}
                            className="min-h-[4.5rem] w-full text-sm"
                            aria-label={col.header}
                          />
                        ) : (
                          <Input
                            value={row[col.key]}
                            onChange={(e) => update(row.id, col.key, e.target.value)}
                            className="h-9 w-full text-sm"
                            aria-label={col.header}
                          />
                        )
                      ) : (
                        <CellValue column={col} value={row[col.key]} />
                      )}
                    </TableCell>
                  ))}
                  {showDelete && (
                    <TableCell className="align-top">
                      <Button type="button" variant="ghost" size="icon-sm" onClick={() => remove(row.id)} aria-label="Delete row">
                        <Trash2 className="text-muted-foreground" />
                      </Button>
                    </TableCell>
                  )}
                </TableRow>
              );
            })
          )}
        </TableBody>
      </Table>
      {showDelete && (
        <div className="border-t p-2">
          <Button type="button" variant="ghost" size="sm" onClick={onAddRow}>
            <Plus /> Add row
          </Button>
        </div>
      )}
    </div>
  );
}
