import assert from "node:assert/strict";
import test from "node:test";

import { isAllowedExtensionOrigin } from "../src/origin";

test("accepts unpacked Chrome extensions regardless of installation id", () => {
  assert.equal(isAllowedExtensionOrigin("chrome-extension://abcdefghijklmnopabcdefghijklmnop"), true);
  assert.equal(isAllowedExtensionOrigin("chrome-extension://ponmlkjihgfedcbaponmlkjihgfedcba"), true);
  assert.equal(isAllowedExtensionOrigin("chrome-extension://reader-development-id"), true);
  assert.equal(isAllowedExtensionOrigin("chrome-extension://reader-development-id/"), true);
});

test("rejects web pages and malformed extension origins", () => {
  assert.equal(isAllowedExtensionOrigin("https://youtube.com"), false);
  assert.equal(isAllowedExtensionOrigin("chrome-extension://"), false);
  assert.equal(isAllowedExtensionOrigin("chrome-extension://abcdefghijklmnopabcdefghijklmnop.evil"), false);
  assert.equal(isAllowedExtensionOrigin("chrome-extension://reader-id/not-an-origin"), false);
  assert.equal(isAllowedExtensionOrigin(""), false);
});
