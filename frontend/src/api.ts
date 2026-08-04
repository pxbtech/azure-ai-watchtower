const API = "";

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`${API}${path}`, {
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    ...init,
  });
  if (!r.ok) {
    const text = await r.text();
    throw new Error(`${r.status} ${r.statusText}: ${text}`);
  }
  return r.json();
}

export const api = {
  health: () => req<{ status: string; app: string; version: string }>("/api/health"),

  projects: {
    list: () => req<{ projects: any[] }>("/api/projects"),
    compliance: (name: string) => req<any>(`/api/projects/${name}/compliance`),
    delete: (name: string) => req<any>(`/api/projects/${name}`, { method: "DELETE" }),
  },

  intake: {
    submit: (body: any) => req<any>("/api/projects/intake", { method: "POST", body: JSON.stringify(body) }),
  },

  compliance: {
    summary: () => req<{ deployments: any[] }>("/api/deployments/compliance/summary"),
  },

  deployments: {
    availableModels: () => req<any[]>(`/api/deployments/models/available`),
    details: (name: string) => req<any>(`/api/deployments/${name}/details`),
    configDrift: (name: string, hours = 168) => req<any>(`/api/deployments/${name}/config-drift?hours=${hours}`),
    update: (name: string, body: any) =>
      req<any>(`/api/deployments/${name}`, { method: "PATCH", body: JSON.stringify(body) }),
    delete: (name: string) => req<any>(`/api/deployments/${name}`, { method: "DELETE" }),
    budgetCheck: (name: string) => req<any>(`/api/deployments/${name}/budget-check`, { method: "POST", body: "{}" }),
    budgetCheckAll: () => req<any>(`/api/deployments/budget-check-all`, { method: "POST", body: "{}" }),
    suspend: (name: string, reason?: string) =>
      req<any>(`/api/deployments/${name}/suspend`, {
        method: "POST",
        body: JSON.stringify({ reason: reason ?? null, actor: "watchtower-ui" }),
      }),
    unsuspend: (name: string, reason?: string) =>
      req<any>(`/api/deployments/${name}/unsuspend`, {
        method: "POST",
        body: JSON.stringify({ reason: reason ?? null, actor: "watchtower-ui" }),
      }),
  },

  diagnostics: {
    apim: () => req<any>("/api/diagnostics/apim"),
    apimService: () => req<any>("/api/diagnostics/apim/service"),
  },

  security: {
    summary: (hours = 24) => req<any>(`/api/security/summary?hours=${hours}`),
    blockedContent: (hours = 24) => req<any>(`/api/security/blocked-content?hours=${hours}`),
    jailbreak: (hours = 24) => req<any>(`/api/security/jailbreak?hours=${hours}`),
    configDrift: (hours = 168) => req<any>(`/api/security/config-drift?hours=${hours}`),
  },

  monitoring: {
    summary: (hours = 24) => req<any>(`/api/monitoring/summary?hours=${hours}`),
    requests: (hours = 1, limit = 200) => req<any>(`/api/monitoring/requests?hours=${hours}&limit=${limit}`),
    errors: (hours = 24, limit = 200) => req<any>(`/api/monitoring/errors?hours=${hours}&limit=${limit}`),
    rateLimits: (hours = 24, limit = 200) => req<any>(`/api/monitoring/rate-limits?hours=${hours}&limit=${limit}`),
    traffic: (hours = 24, binMinutes = 15) => req<any>(`/api/monitoring/traffic?hours=${hours}&bin_minutes=${binMinutes}`),
    foundry: (hours = 24, limit = 100) => req<any>(`/api/monitoring/foundry?hours=${hours}&limit=${limit}`),
  },

  billing: {
    byEndpoint: (days = 30) => req<any>(`/api/billing/by-endpoint?days=${days}`),
    byEndpointCsvUrl: (days = 30) => `/api/billing/by-endpoint.csv?days=${days}`,
    endpointPdfUrl: (name: string, days = 30) => `/api/billing/endpoint/${name}/pdf?days=${days}`,
    foundryTotal: (year?: number, month?: number) => {
      const q = year && month ? `?year=${year}&month=${month}` : "";
      return req<any>(`/api/billing/foundry-total${q}`);
    },
  },

  audit: {
    list: () => req<any[]>("/api/audit"),
    suspensions: () => req<any[]>("/api/audit/suspensions"),
  },
};
