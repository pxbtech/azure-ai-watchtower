import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { Card, TableWrap, Th, Td, Badge } from "../components/Card";

export default function Monitoring() {
  const [hours, setHours] = useState(24);

  const summary = useQuery({ queryKey: ["mon-summary", hours], queryFn: () => api.monitoring.summary(hours), retry: false });
  const requests = useQuery({ queryKey: ["mon-requests", hours], queryFn: () => api.monitoring.requests(hours, 100), retry: false });
  const errors = useQuery({ queryKey: ["mon-errors", hours], queryFn: () => api.monitoring.errors(hours, 100), retry: false });
  const rate = useQuery({ queryKey: ["mon-rl", hours], queryFn: () => api.monitoring.rateLimits(hours, 100), retry: false });
  const drift = useQuery({ queryKey: ["mon-drift", 168], queryFn: () => api.security.configDrift(168), retry: false });
  const foundry = useQuery({ queryKey: ["mon-foundry", hours], queryFn: () => api.monitoring.foundry(hours, 100), retry: false });

  const statusTone = (code: any) => {
    const n = Number(code);
    if (n >= 500) return "bad";
    if (n === 429) return "warn";
    if (n >= 400) return "warn";
    if (n >= 200 && n < 300) return "good";
    return "slate";
  };

  return (
    <>
      <div className="wt-page-head" style={{ display: "flex", justifyContent: "space-between", alignItems: "end" }}>
        <div>
          <h2>Monitoring</h2>
          <p>Live events from Log Analytics for APIM gateway + Foundry. Real requests, real errors, real rate-limit hits.</p>
        </div>
        <select className="wt-input" style={{ width: "auto" }} value={hours} onChange={(e) => setHours(Number(e.target.value))}>
          <option value={1}>Last hour</option>
          <option value={6}>Last 6 hours</option>
          <option value={24}>Last 24 hours</option>
          <option value={168}>Last 7 days</option>
        </select>
      </div>

      <div className="wt-metric-row">
        <div className="wt-metric">
          <div className="wt-metric-label">Total requests</div>
          <div className="wt-metric-value">{summary.data?.total?.toLocaleString?.() ?? "-"}</div>
        </div>
        <div className="wt-metric">
          <div className="wt-metric-label">Errors (4xx/5xx)</div>
          <div className="wt-metric-value" style={{ color: (summary.data?.errors ?? 0) > 0 ? "#915c00" : "#344767" }}>
            {summary.data?.errors?.toLocaleString?.() ?? "-"}
          </div>
        </div>
        <div className="wt-metric">
          <div className="wt-metric-label">Rate limit hits (429)</div>
          <div className="wt-metric-value" style={{ color: (summary.data?.rate_limits ?? 0) > 0 ? "#915c00" : "#344767" }}>
            {summary.data?.rate_limits?.toLocaleString?.() ?? "-"}
          </div>
        </div>
        <div className="wt-metric">
          <div className="wt-metric-label">Server errors (5xx)</div>
          <div className="wt-metric-value" style={{ color: (summary.data?.server_errors ?? 0) > 0 ? "#a31c1c" : "#344767" }}>
            {summary.data?.server_errors?.toLocaleString?.() ?? "-"}
          </div>
        </div>
        <div className="wt-metric">
          <div className="wt-metric-label">P95 latency</div>
          <div className="wt-metric-value">{summary.data?.p95_latency_ms ? `${Math.round(summary.data.p95_latency_ms)}ms` : "-"}</div>
          <div className="wt-metric-sub">avg {summary.data?.avg_latency_ms ? `${Math.round(summary.data.avg_latency_ms)}ms` : "-"}</div>
        </div>
        <div className="wt-metric">
          <div className="wt-metric-label">Unique callers</div>
          <div className="wt-metric-value">{summary.data?.unique_callers?.toLocaleString?.() ?? "-"}</div>
          <div className="wt-metric-sub">across {summary.data?.unique_apis ?? 0} APIs</div>
        </div>
      </div>

      {summary.data && !summary.data.workspace_configured && (
        <Card>
          <div style={{ color: "#915c00" }}>
            Log Analytics workspace not configured on APIM diagnostics. All queries return empty until fixed. See Diagnostics.
          </div>
        </Card>
      )}

      <Card title="Recent errors (4xx / 5xx)" subtitle="Failed gateway requests with backend response code.">
        {errors.isPending && <div className="wt-loading">Loading...</div>}
        {errors.data && errors.data.rows?.length === 0 && <div className="wt-empty">No errors in the last {hours}h.</div>}
        {errors.data && errors.data.rows?.length > 0 && (
          <TableWrap>
            <thead><tr><Th>When</Th><Th>API</Th><Th>Method</Th><Th>Response</Th><Th>Backend</Th><Th>Caller</Th><Th>Subscription</Th></tr></thead>
            <tbody>
              {errors.data.rows.map((r: any, i: number) => (
                <tr key={i}>
                  <Td className="wt-small">{r.TimeGenerated}</Td>
                  <Td className="wt-mono wt-small">{r.ApiId}</Td>
                  <Td className="wt-small">{r.Method}</Td>
                  <Td><Badge tone={statusTone(r.ResponseCode)}>{r.ResponseCode}</Badge></Td>
                  <Td className="wt-small">{r.BackendResponseCode ?? "-"}</Td>
                  <Td className="wt-small">{r.CallerIpAddress ?? "-"}</Td>
                  <Td className="wt-mono wt-small">{r.ApimSubscriptionId ?? "-"}</Td>
                </tr>
              ))}
            </tbody>
          </TableWrap>
        )}
      </Card>

      <Card title="Rate limit hits (429)" subtitle="Requests blocked by azure-openai-token-limit or rate-limit-by-key.">
        {rate.isPending && <div className="wt-loading">Loading...</div>}
        {rate.data && rate.data.rows?.length === 0 && <div className="wt-empty">No rate-limit hits in the last {hours}h.</div>}
        {rate.data && rate.data.rows?.length > 0 && (
          <TableWrap>
            <thead><tr><Th>When</Th><Th>API</Th><Th>Method</Th><Th>Caller</Th><Th>Subscription</Th></tr></thead>
            <tbody>
              {rate.data.rows.map((r: any, i: number) => (
                <tr key={i}>
                  <Td className="wt-small">{r.TimeGenerated}</Td>
                  <Td className="wt-mono wt-small">{r.ApiId}</Td>
                  <Td className="wt-small">{r.Method}</Td>
                  <Td className="wt-small">{r.CallerIpAddress ?? "-"}</Td>
                  <Td className="wt-mono wt-small">{r.ApimSubscriptionId ?? "-"}</Td>
                </tr>
              ))}
            </tbody>
          </TableWrap>
        )}
      </Card>

      <Card title="Recent gateway requests" subtitle={`Last ${hours}h. Every request through APIM (limit 100).`}>
        {requests.isPending && <div className="wt-loading">Loading...</div>}
        {requests.data && requests.data.rows?.length === 0 && <div className="wt-empty">No requests in the last {hours}h.</div>}
        {requests.data && requests.data.rows?.length > 0 && (
          <TableWrap>
            <thead><tr><Th>When</Th><Th>API</Th><Th>Method</Th><Th>URL</Th><Th>Status</Th><Th>Latency</Th><Th>Caller</Th></tr></thead>
            <tbody>
              {requests.data.rows.map((r: any, i: number) => (
                <tr key={i}>
                  <Td className="wt-small">{r.TimeGenerated}</Td>
                  <Td className="wt-mono wt-small">{r.ApiId}</Td>
                  <Td className="wt-small">{r.Method}</Td>
                  <Td className="wt-mono wt-small" style={{ maxWidth: 300, overflow: "hidden", textOverflow: "ellipsis" }}>{r.Url}</Td>
                  <Td><Badge tone={statusTone(r.ResponseCode)}>{r.ResponseCode}</Badge></Td>
                  <Td className="wt-small">{r.TotalTime ? `${Math.round(r.TotalTime)}ms` : "-"}</Td>
                  <Td className="wt-small">{r.CallerIpAddress ?? "-"}</Td>
                </tr>
              ))}
            </tbody>
          </TableWrap>
        )}
      </Card>

      <Card title="Foundry diagnostic events" subtitle="Requires diagnostic settings enabled on the Foundry account itself (separate from APIM diagnostics).">
        {foundry.isPending && <div className="wt-loading">Loading...</div>}
        {foundry.data && foundry.data.rows?.length === 0 && <div className="wt-empty">No Foundry diagnostic events. Foundry-side diagnostic settings may not be enabled.</div>}
        {foundry.data && foundry.data.rows?.length > 0 && (
          <TableWrap>
            <thead><tr><Th>When</Th><Th>Operation</Th><Th>Category</Th><Th>Result</Th><Th>Duration</Th><Th>Caller</Th></tr></thead>
            <tbody>
              {foundry.data.rows.map((r: any, i: number) => (
                <tr key={i}>
                  <Td className="wt-small">{r.TimeGenerated}</Td>
                  <Td className="wt-mono wt-small">{r.OperationName}</Td>
                  <Td className="wt-small">{r.Category}</Td>
                  <Td>{r.ResultType === "Success" ? <Badge tone="good">{r.ResultType}</Badge> : <Badge tone="bad">{r.ResultType}</Badge>}</Td>
                  <Td className="wt-small">{r.DurationMs ? `${Math.round(r.DurationMs)}ms` : "-"}</Td>
                  <Td className="wt-small">{r.CallerIpAddress ?? "-"}</Td>
                </tr>
              ))}
            </tbody>
          </TableWrap>
        )}
      </Card>

      <Card title="Foundry configuration drift (last 7 days)" subtitle="Real Azure Activity Log events for the Foundry account (SKU changes, capacity, RAI, network, local-auth toggles).">
        {drift.isPending && <div className="wt-loading">Loading...</div>}
        {drift.data && drift.data.rows?.length === 0 && <div className="wt-empty">No Foundry configuration changes in the last 7 days.</div>}
        {drift.data && drift.data.rows?.length > 0 && (
          <TableWrap>
            <thead><tr><Th>When</Th><Th>Operation</Th><Th>Resource</Th><Th>Caller</Th><Th>Status</Th></tr></thead>
            <tbody>
              {drift.data.rows.slice(0, 30).map((r: any, i: number) => (
                <tr key={i}>
                  <Td className="wt-small">{r.timestamp}</Td>
                  <Td className="wt-mono wt-small">{r.operation}</Td>
                  <Td className="wt-small">{r.resource}</Td>
                  <Td className="wt-small">{r.caller}</Td>
                  <Td>{r.status === "Succeeded" ? <Badge tone="good">{r.status}</Badge> : <Badge tone="bad">{r.status}</Badge>}</Td>
                </tr>
              ))}
            </tbody>
          </TableWrap>
        )}
      </Card>
    </>
  );
}
