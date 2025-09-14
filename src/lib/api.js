import { ENV } from "./env";

// base = http://localhost:8000 ; añadimos /api aquí
const API_BASE = `${ENV.BACKEND_URL.replace(/\/$/, "")}/api`;

export async function api(path, { token, method = "GET", body, headers = {} } = {}) {
  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...headers,
    },
    ...(body ? { body: JSON.stringify(body) } : {}),
    credentials: "include",
  });
  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    try { const j = await res.json(); msg = j.error || JSON.stringify(j); } catch {}
    throw new Error(msg);
  }
  return res.json();
}
