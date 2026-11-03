import localFont from "next/font/local";

/*
 * Self-hosted from the fontsource packages. Geologica carries wght plus the SHRP axis. The
 * latin-ext face sits first in the stack behind a unicode-range, so it is only downloaded when a
 * vendor title needs one of its characters; basic latin falls through to the preloaded face.
 */

export const geologica = localFont({
  src: "../../node_modules/@fontsource-variable/geologica/files/geologica-latin-shrp-normal.woff2",
  variable: "--font-geologica",
  weight: "100 900",
  display: "swap",
  adjustFontFallback: "Arial",
});

export const geologicaExt = localFont({
  src: "../../node_modules/@fontsource-variable/geologica/files/geologica-latin-ext-shrp-normal.woff2",
  variable: "--font-geologica-ext",
  weight: "100 900",
  display: "swap",
  preload: false,
  adjustFontFallback: false,
  declarations: [
    {
      prop: "unicode-range",
      value:
        "U+0100-02BA,U+02BD-02C5,U+02C7-02CC,U+02CE-02D7,U+02DD-02FF,U+0304,U+0308,U+0329,U+1D00-1DBF,U+1E00-1E9F,U+1EF2-1EFF,U+2020,U+20A0-20AB,U+20AD-20C0,U+2113,U+2C60-2C7F,U+A720-A7FF",
    },
  ],
});

export const martian = localFont({
  src: "../../node_modules/@fontsource-variable/martian-mono/files/martian-mono-latin-wght-normal.woff2",
  variable: "--font-martian",
  weight: "100 800",
  display: "swap",
  adjustFontFallback: false,
});
