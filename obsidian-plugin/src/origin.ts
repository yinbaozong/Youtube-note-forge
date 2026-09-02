export function isAllowedExtensionOrigin(origin: string): boolean {
  try {
    const parsed = new URL(origin.trim());
    return parsed.protocol === "chrome-extension:"
      && /^[a-z0-9-]+$/i.test(parsed.hostname)
      && !parsed.username
      && !parsed.password
      && !parsed.port
      && (parsed.pathname === "" || parsed.pathname === "/")
      && !parsed.search
      && !parsed.hash;
  } catch {
    return false;
  }
}
