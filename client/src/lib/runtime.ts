const ABSOLUTE_URL_RE = /^https?:\/\//i;

function trimTrailingSlash(value: string): string {
  return value.replace(/\/+$/, "");
}

function normalizePath(path: string): string {
  if (!path) {
    return "/";
  }
  return path.startsWith("/") ? path : `/${path}`;
}

const apiBaseRaw = String(import.meta.env.VITE_API_URL ?? "").trim();
const socketBaseRaw = String(import.meta.env.VITE_SOCKET_URL ?? "").trim();
const assetBaseRaw = String(import.meta.env.VITE_ASSET_BASE_URL ?? "").trim();

export const API_BASE_URL = trimTrailingSlash(apiBaseRaw);
export const SOCKET_URL = trimTrailingSlash(socketBaseRaw) || API_BASE_URL || "/";
export const ASSET_BASE_URL = trimTrailingSlash(assetBaseRaw) || API_BASE_URL || trimTrailingSlash(socketBaseRaw);

export function apiUrl(path: string): string {
  const cleanPath = normalizePath(path);
  if (!API_BASE_URL) {
    return cleanPath;
  }
  return `${API_BASE_URL}${cleanPath}`;
}

export function assetUrl(path: string): string {
  const cleanPath = (path || "").trim();
  if (!cleanPath) {
    return cleanPath;
  }
  if (ABSOLUTE_URL_RE.test(cleanPath)) {
    return cleanPath;
  }

  const normalized = normalizePath(cleanPath);
  if (!ASSET_BASE_URL) {
    return normalized;
  }
  return `${ASSET_BASE_URL}${normalized}`;
}
