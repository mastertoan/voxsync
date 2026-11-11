// Bundles the MV3 extension into dist/ (load dist/ as "unpacked" in chrome://extensions).
import * as esbuild from "esbuild";
import { cpSync, mkdirSync, rmSync } from "node:fs";

const watch = process.argv.includes("--watch");
rmSync("dist", { recursive: true, force: true });
mkdirSync("dist", { recursive: true });
for (const f of ["manifest.json", "rules", "src/popup/popup.html", "src/popup/popup.css",
  "src/options/options.html", "src/options/options.css", "src/content/chophim-cosmetic.css", "icons"]) {
  try {
    cpSync(f, `dist/${f.split("/").pop()}`, { recursive: true });
  } catch (e) {
    if (e.code !== "ENOENT") throw e;
  }
}

const ctx = await esbuild.context({
  entryPoints: {
    sw: "src/sw/index.ts",
    content: "src/content/index.ts",
    popup: "src/popup/popup.ts",
    options: "src/options/options.ts",
  },
  bundle: true,
  format: "iife",        // content scripts can't be ES modules; IIFE works for every entry
  target: "chrome120",   // Promise.withResolvers needs Chrome 119+
  outdir: "dist",
  sourcemap: "linked",
  logLevel: "info",
});
if (watch) await ctx.watch();
else {
  await ctx.rebuild();
  await ctx.dispose();
}
