"use client";
import { Card } from "@/components/ui/Card";
import { useId, useState, type ReactNode } from "react";
export function ChartFigure({
  title,
  headings,
  rows,
  children,
}: {
  title: string;
  headings: string[];
  rows: (string | number)[][];
  children: ReactNode;
}) {
  const [table, setTable] = useState(false);
  const id = useId();
  return (
    <Card as="figure" aria-labelledby={`${id}-caption`}>
      <figcaption id={`${id}-caption`} className="mb-4 text-lg font-semibold">
        {title}
      </figcaption>
      {children}
      <button
        type="button"
        onClick={() => setTable(!table)}
        aria-expanded={table}
        aria-controls={`${id}-table`}
        className="mt-3 min-h-11 rounded-lg px-3 text-sm font-semibold text-primary underline underline-offset-4 hover:bg-primary/5 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
      >
        {table ? "Ocultar tabla" : "Ver como tabla"}
        <span className="sr-only">: {title}</span>
      </button>
      <div id={`${id}-table`} className={table ? "mt-3 overflow-x-auto" : "sr-only"}>
        <table className="w-full text-left text-sm tabular-nums">
          <caption className="sr-only">{title}</caption>
          <thead>
            <tr>
              {headings.map((label) => (
                <th key={label} scope="col" className="p-2">
                  {label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={i} className="border-t border-border">
                {row.map((cell, j) =>
                  j === 0 ? (
                    <th key={j} scope="row" className="p-2 font-medium">
                      {cell}
                    </th>
                  ) : (
                    <td key={j} className="p-2">
                      {cell}
                    </td>
                  ),
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}
/** Máximo lineal redondeado a 1, 2 o 5 × 10ⁿ. */
export function niceMax(values: number[]): number {
  const max = Math.max(1, ...values);
  const magnitude = 10 ** Math.floor(Math.log10(max));
  const fraction = max / magnitude;
  return (fraction <= 1 ? 1 : fraction <= 2 ? 2 : fraction <= 5 ? 5 : 10) * magnitude;
}
