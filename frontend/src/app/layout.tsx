import type { Metadata, Viewport } from "next";
import { Archivo, Figtree } from "next/font/google";
import "maplibre-gl/dist/maplibre-gl.css";
import "./globals.css";

const archivo = Archivo({
  subsets: ["latin"],
  weight: ["600", "800"],
  variable: "--font-archivo",
});
const figtree = Figtree({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-figtree",
});

export const metadata: Metadata = {
  title: "SmartRouteNYC: accessible subway, bus and PATH trips",
  description:
    "Plan NYC subway, bus and PATH journeys that respect step-free and wheelchair needs, with every barrier and unconfirmed step shown.",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: "#fbf8f3",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className={`${archivo.variable} ${figtree.variable}`}>
      <body>
        <a className="skip-link" href="#results">
          Skip to results
        </a>
        {children}
      </body>
    </html>
  );
}
