import path from "node:path";

const FRONTMATTER = /^---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)/;

export function sanitizeNoteFilename(input: string): string {
  const withoutExtension = input.replace(/\.md$/i, "");
  const safe = withoutExtension
    .replace(/[<>:"/\\|?*\u0000-\u001f]/g, "")
    .replace(/\s+/g, " ")
    .replace(/[. ]+$/g, "")
    .trim();
  if (!/[\u3400-\u9fff].+ - [A-Za-z]/.test(safe)) {
    throw new Error("文件名必须使用“中文标题 - English Title”格式。");
  }
  if (!safe) throw new Error("文件名不能为空。");
  return `${safe.slice(0, 180)}.md`;
}

export function resolveInsideVault(vaultPath: string, relativePath: string): string {
  const winPath = path.win32;
  const vault = winPath.resolve(vaultPath);
  if (winPath.isAbsolute(relativePath)) {
    throw new Error("目标路径必须位于当前 Vault 内。");
  }
  const resolved = winPath.resolve(vault, relativePath);
  const relative = winPath.relative(vault, resolved);
  if (!relative || relative.startsWith("..") || winPath.isAbsolute(relative)) {
    if (!relative) return resolved;
    throw new Error("目标路径不能越过当前 Vault。");
  }
  return resolved;
}

export function splitFrontmatter(source: string): { frontmatter: string; body: string } {
  const match = source.match(FRONTMATTER);
  if (!match) return { frontmatter: "", body: source };
  return { frontmatter: match[1], body: source.slice(match[0].length) };
}

function yamlString(value: string): string {
  return JSON.stringify(value);
}

export function frontmatterValue(source: string, key: string): string {
  const { frontmatter } = splitFrontmatter(source);
  const match = frontmatter.match(new RegExp(`^${escapeRegExp(key)}:\\s*(.*)$`, "m"));
  if (!match) return "";
  const raw = match[1].trim();
  if (raw.startsWith('"') && raw.endsWith('"')) {
    try {
      return JSON.parse(raw) as string;
    } catch {
      return raw.slice(1, -1);
    }
  }
  return raw.replace(/^['"]|['"]$/g, "");
}

export function updateFrontmatter(source: string, updates: Record<string, string>): string {
  const parsed = splitFrontmatter(source);
  const lines = parsed.frontmatter ? parsed.frontmatter.split(/\r?\n/) : [];
  for (const [key, value] of Object.entries(updates)) {
    const next = `${key}: ${yamlString(value)}`;
    const index = lines.findIndex((line) => new RegExp(`^${escapeRegExp(key)}\\s*:`).test(line));
    if (index >= 0) lines[index] = next;
    else lines.push(next);
  }
  return `---\n${lines.join("\n")}\n---\n${parsed.body.replace(/^\r?\n/, "")}`;
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
