import assert from "node:assert/strict";
import test from "node:test";

import {
  resolveInsideVault,
  sanitizeNoteFilename,
  updateFrontmatter,
} from "../src/note-utils";

test("creates a safe bilingual Markdown filename", () => {
  assert.equal(
    sanitizeNoteFilename("从零/理解 Git - Understand: Git?.md"),
    "从零理解 Git - Understand Git.md",
  );
  assert.throws(() => sanitizeNoteFilename("English only.md"), /中文标题 - English Title/);
});

test("resolves only paths inside the vault", () => {
  assert.equal(
    resolveInsideVault("C:\\Vault", "YouTube video/笔记.md"),
    "C:\\Vault\\YouTube video\\笔记.md",
  );
  assert.throws(() => resolveInsideVault("C:\\Vault", "../outside.md"), /Vault/);
  assert.throws(() => resolveInsideVault("C:\\Vault", "C:\\Other\\outside.md"), /Vault/);
});

test("updates required frontmatter without dropping source metadata", () => {
  const source = [
    "---",
    "title: Old title",
    "url: https://www.youtube.com/watch?v=abc",
    "channel: Example",
    "---",
    "",
    "## scaffold",
  ].join("\n");

  const updated = updateFrontmatter(source, {
    title: "从零理解 Git - Understand Git",
    skill_version: "4.0.3",
  });

  assert.match(updated, /^---\ntitle: "从零理解 Git - Understand Git"$/m);
  assert.match(updated, /^skill_version: "4.0.3"$/m);
  assert.match(updated, /^url: https:\/\/www\.youtube\.com\/watch\?v=abc$/m);
  assert.match(updated, /^channel: Example$/m);
  assert.match(updated, /## scaffold$/);
});
