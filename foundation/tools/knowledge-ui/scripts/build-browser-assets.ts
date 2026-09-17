import "katex";
import "mermaid";

const outputDir = new URL("../static/browser-assets/", import.meta.url);
const katexModule = new URL(import.meta.resolve("katex"));
const mermaidModule = new URL(import.meta.resolve("mermaid"));
const waterCss = new URL(import.meta.resolve("water.css"));

async function copy(source: URL, relativeDestination: string): Promise<void> {
  const destination = new URL(relativeDestination, outputDir);
  await Deno.mkdir(new URL(".", destination), { recursive: true });
  await Deno.copyFile(source, destination);
}

await Deno.remove(outputDir, { recursive: true }).catch((error) => {
  if (!(error instanceof Deno.errors.NotFound)) throw error;
});
await Deno.mkdir(outputDir, { recursive: true });

await copy(waterCss, "water.min.css");
await copy(new URL("katex.min.css", katexModule), "katex.min.css");
await copy(new URL("katex.min.js", katexModule), "katex.min.js");
await copy(new URL("mermaid.min.js", mermaidModule), "mermaid.min.js");

const katexFonts = new URL("fonts/", katexModule);
for await (const entry of Deno.readDir(katexFonts)) {
  if (entry.isFile) await copy(new URL(entry.name, katexFonts), `fonts/${entry.name}`);
}

console.log(`Generated browser assets in ${outputDir.pathname}`);

// Keep package notices beside generated assets, including locked dependencies
// used by the prebuilt Mermaid bundle. Do not copy any developer cache metadata.
async function packageRoot(module: URL, name: string): Promise<URL> {
  let directory = new URL(".", module);
  for (let depth = 0; depth < 8; depth++) {
    try {
      const metadata = JSON.parse(await Deno.readTextFile(new URL("package.json", directory)));
      if (metadata.name === name) return directory;
    } catch (error) {
      if (!(error instanceof Deno.errors.NotFound)) throw error;
    }
    directory = new URL("../", directory);
  }
  throw new Error(`Package root not found: ${name}`);
}
const registry = new URL("../../", await packageRoot(mermaidModule, "mermaid"));
const lock = JSON.parse(await Deno.readTextFile(new URL("../deno.lock", import.meta.url)));
const manifest = [];
for (const specifier of Object.keys(lock.npm).sort()) {
  const [, name, version] = specifier.match(/^(@?[^@]+)@([^_]+)/)!;
  const root = new URL(`${name}/${version}/`, registry);
  const metadata = JSON.parse(await Deno.readTextFile(new URL("package.json", root)));
  const notices = [];
  for await (const entry of Deno.readDir(root)) {
    if (entry.isFile && /^(licen[cs]e|notice|copying)/i.test(entry.name)) {
      const target = `licenses/${name.replaceAll("/", "_")}@${version}/${entry.name}`;
      await copy(new URL(entry.name, root), target);
      notices.push(target);
    }
  }
  manifest.push({ name, version, license: metadata.license ?? "See package metadata", notices });
}
await Deno.writeTextFile(new URL("third-party.json", outputDir), JSON.stringify(manifest, null, 2) + "\n");
console.log(`Preserved notices for ${manifest.length} locked packages`);
