import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { Card, TableWrap, Th, Td, Badge } from "../components/Card";

export default function Security() {
  const [hours, setHours] = useState(24);
  const summary = useQuery({ queryKey: ["sec-summary", hours], queryFn: () => api.security.summary(hours), retry: false });
  const blocked = useQuery({ queryKey: ["blocked", hours], queryFn: () => api.security.blockedContent(hours), retry: false });
  const jail = useQuery({ queryKey: ["jailbreak", hours], queryFn: () => api.security.jailbreak(hours), retry: false });
  const drift = useQuery({ queryKey: ["drift-sec", 168], queryFn: () => api.security.configDrift(168), retry: false });

  return (
    <>
      <div className="wt-page-head">
        <div>
          <h2>Security</h2>
          <p>Blocked content, jailbreak / indirect-attack attempts, and configuration drift on your Foundry account.</p>
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
          <div className="wt-metric-label">Content blocks</div>
          <div className="wt-metric-value" style={{ color: (summary.data?.blocked_content_events || 0) > 0 ? "#915c00" : "#087a52" }}>
            {summary.data?.blocked_content_events ?? 0}
          </div>
          <div className="wt-metric-sub">RAI policy trips (hate, sexual, violence, self-harm, protected material)</div>
        </div>
        <div className="wt-metric">
          <div className="wt-metric-label">Jailbreak / indirect-attack attempts</div>
          <div className="wt-metric-value" style={{ color: (summary.data?.jailbreak_attempts || 0) > 0 ? "#a31c1c" : "#087a52" }}>
            {summary.data?.jailbreak_attempts ?? 0}
          </div>
          <div className="wt-metric-sub">detected by Prompt Shield</div>
        </div>
        <div className="wt-metric">
          <div className="wt-metric-label">Config change events</div>
          <div className="wt-metric-value">{summary.data?.config_drift_events ?? 0}</div>
          <div className="wt-metric-sub">on Foundry account (last 7 days)</div>
        </div>
      </div>

      {summary.data && !summary.data.workspace_configured && (
        <Card>
          <div style={{ color: "#915c00" }}>
            <i className="ni ni-notification-70" style={{ marginRight: 6 }}></i>
            Log Analytics workspace is not configured on APIM diagnostics. Content-filter and jailbreak signals will not populate until this is fixed. Config drift below still works.
          </div>
        </Card>
      )}

      <Card title="Jailbreak / indirect-attack attempts" subtitle="Requests where Prompt Shield flagged direct jailbreak or indirect prompt-injection attempts.">
        {jail.isPending && <div className="wt-loading">Querying Log Analytics...</div>}
        {jail.isError && <div className="wt-error">{(jail.error as Error).message}</div>}
        {jail.data && jail.data.rows?.length === 0 && <div className="wt-empty">No jailbreak attempts detected in this window.</div>}
        {jail.data && jail.data.rows?.length > 0 && (
          <TableWrap>
            <thead><tr><Th>Time</Th><Th>Subscription</Th><Th>Caller IP</Th><Th>Jailbreak</Th><Th>Indirect attack</Th><Th>URL</Th></tr></thead>
            <tbody>
              {jail.data.rows.map((r: any, i: number) => (
                <tr key={i}>
                  <Td className="wt-small">{r.TimeGenerated ?? r.timegenerated}</Td>
                  <Td className="wt-mono">{r.ApimSubscriptionId ?? "-"}</Td>
                  <Td className="wt-small">{r.CallerIpAddress ?? "-"}</Td>
                  <Td>{r.jailbreak_detected ? <Badge tone="bad">yes</Badge> : <Badge tone="slate">no</Badge>}</Td>
                  <Td>{r.indirect_attack_detected ? <Badge tone="bad">yes</Badge> : <Badge tone="slate">no</Badge>}</Td>
                  <Td className="wt-small wt-mono">{r.Url}</Td>
                </tr>
              ))}
            </tbody>
          </TableWrap>
        )}
      </Card>

      <Card title="Content blocks (RAI filters)" subtitle="Content-filter categories that tripped: hate, sexual, violence, self-harm, protected material.">
        {blocked.isPending && <div className="wt-loading">Querying Log Analytics...</div>}
        {blocked.isError && <div className="wt-error">{(blocked.error as Error).message}</div>}
        {blocked.data && blocked.data.rows?.length === 0 && <div className="wt-empty">No content blocks in this window.</div>}
        {blocked.data && blocked.data.rows?.length > 0 && (
          <TableWrap>
            <thead><tr><Th>Time</Th><Th>Subscription</Th><Th>Category</Th><Th>Severity</Th><Th>Blocks</Th></tr></thead>
            <tbody>
              {blocked.data.rows.map((r: any, i: number) => (
                <tr key={i}>
                  <Td className="wt-small">{r.TimeGenerated ?? r.timegenerated}</Td>
                  <Td className="wt-mono">{r.ApimSubscriptionId ?? "-"}</Td>
                  <Td><Badge tone="warn">{r.category}</Badge></Td>
                  <Td><Badge tone="bad">{r.severity}</Badge></Td>
                  <Td style={{ fontWeight: 600 }}>{r.blocks}</Td>
                </tr>
              ))}
            </tbody>
          </TableWrap>
        )}
      </Card>

      <Card title="Configuration drift on Foundry account" subtitle="Every write on the Foundry or its deployments in the last 7 days (SKU changes, capacity changes, RAI changes, model swaps, network changes, local-auth toggles). Sourced from Azure Activity Log so it works even without APIM diagnostics.">
        {drift.isPending && <div className="wt-loading">Querying Activity Log...</div>}
        {drift.isError && <div className="wt-error">{(drift.error as Error).message}</div>}
        {drift.data && drift.data.rows?.length === 0 && <div className="wt-empty">No Foundry configuration changes in the last 7 days.</div>}
        {drift.data && drift.data.rows?.length > 0 && (
          <TableWrap>
            <thead><tr><Th>When</Th><Th>Operation</Th><Th>Resource</Th><Th>Caller</Th><Th>Status</Th></tr></thead>
            <tbody>
              {drift.data.rows.map((r: any, i: number) => (
                <tr key={i}>
                  <Td className="wt-small">{r.timestamp}</Td>
                  <Td className="wt-mono wt-small">{r.operation}</Td>
                  <Td className="wt-small">{r.resource}</Td>
                  <Td className="wt-small">{r.caller}</Td>
                  <Td>{r.status === "Succeeded" ? <Badge tone="good">{r.status}</Badge> : r.status === "Failed" ? <Badge tone="bad">{r.status}</Badge> : <Badge tone="slate">{r.status || "-"}</Badge>}</Td>
                </tr>
              ))}
            </tbody>
          </TableWrap>
        )}
      </Card>
    </>
  );
}
