import type { Metadata } from "next";
import { Poppins } from "next/font/google";
import "./globals.css";

import { SessionProvider } from "@/features/auth/SessionProvider";
import { SessionStatus } from "@/features/auth/SessionStatus";

const poppins = Poppins({
  variable: "--font-poppins",
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
});

export const metadata: Metadata = {
  title: "Ciudadanía Activa",
  description: "Plataforma de reportes urbanos ciudadanos e incentivos por participación.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="es" className={`${poppins.variable} h-full antialiased`}>
      <body className="min-h-full flex flex-col">
        <SessionProvider>
          <SessionStatus />
          {children}
        </SessionProvider>
      </body>
    </html>
  );
}
