import builtins from "builtin-modules";
import esbuild from "esbuild";

const production = process.argv[2] === "production";
const context = await esbuild.context({
  banner: { js: "/* YouTube Note Reader v4.1.1 */" },
  bundle: true,
  entryPoints: ["src/main.ts"],
  external: ["obsidian", "electron", ...builtins, ...builtins.map((name) => `node:${name}`)],
  format: "cjs",
  logLevel: "info",
  outfile: "main.js",
  platform: "node",
  sourcemap: production ? false : "inline",
  target: "es2022",
  treeShaking: true,
});

if (production) {
  await context.rebuild();
  await context.dispose();
} else {
  await context.watch();
}
