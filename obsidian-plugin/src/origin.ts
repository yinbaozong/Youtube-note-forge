const CHROME_EXTENSION_ORIGIN = /^chrome-extension:\/\/[a-p]{32}$/;

export function isAllowedExtensionOrigin(origin: string): boolean {
  return CHROME_EXTENSION_ORIGIN.test(origin.trim());
}
