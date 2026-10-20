import { render, screen } from "@testing-library/react";
import { PublicPhoto } from "../PublicPhoto";
import { publicReport } from "@/test/fixtures";

it("carga la foto diferida desde el origen API y permite ampliarla sin datos personales", () => {
  vi.stubEnv("NEXT_PUBLIC_API_URL", "https://api.example.test");
  const report = publicReport({
    status: "ASIGNADO",
    photo_url: "/api/v1/public/reports/11111111-1111-4111-8111-111111111111/photo/",
  });
  render(<PublicPhoto report={report} enlarge />);
  const image = screen.getByRole("img", { name: "Reporte de Baches · Asignado" });
  expect(image).toHaveAttribute("loading", "lazy");
  expect(image).toHaveAttribute("src", `https://api.example.test${report.photo_url}`);
  expect(screen.getByRole("link")).toHaveAttribute("href", image.getAttribute("src"));
  vi.unstubAllEnvs();
});
it("no crea una imagen cuando no hay foto pública", () => {
  render(<PublicPhoto report={publicReport()} />);
  expect(screen.queryByRole("img")).not.toBeInTheDocument();
});
