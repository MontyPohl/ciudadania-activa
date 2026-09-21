import { render, screen, within } from "@testing-library/react";

import { ReportTimeline } from "@/features/reports/panel/ReportTimeline";
import type { ReportHistoryEntry } from "@/types/api";

const HISTORY: ReportHistoryEntry[] = [
  {
    previous_status: "",
    new_status: "CREADO",
    cycle: 0,
    reason: "",
    created_at: "2026-08-28T10:00:00Z",
  },
  {
    previous_status: "CREADO",
    new_status: "CLASIFICADO",
    cycle: 0,
    reason: "Clasificación inicial",
    created_at: "2026-08-28T11:00:00Z",
  },
  {
    previous_status: "CLASIFICADO",
    new_status: "ASIGNADO",
    cycle: 1,
    reason: "",
    created_at: "2026-08-28T12:00:00Z",
  },
];

describe("ReportTimeline", () => {
  it("presenta una lista semántica en el orden recibido", () => {
    render(<ReportTimeline entries={HISTORY} />);
    const list = screen.getByRole("list", { name: "Historial de estados" });
    const items = within(list).getAllByRole("listitem");
    expect(items).toHaveLength(3);
    expect(items[0]).toHaveTextContent("Reporte creado");
    expect(items[1]).toHaveTextContent("Creado → Clasificado");
    expect(items[1]).toHaveTextContent("Clasificación inicial");
    expect(items[2]).toHaveTextContent("Clasificado → Asignado");
    expect(items[2]).not.toHaveTextContent("Clasificación inicial");
    expect(list).not.toHaveTextContent("changed_by");
  });

  it("maneja un historial vacío con un estado neutro", () => {
    render(<ReportTimeline entries={[]} />);
    expect(screen.getByText("Todavía no hay movimientos registrados.")).toBeVisible();
  });
});
