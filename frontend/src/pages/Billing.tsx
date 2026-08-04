import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { Card, TableWrap, Th, Td, Badge } from "../components/Card";

export default function Billing() {
  const [days, setDays] = useState(30);
  const q = useQuery({ queryKey: ["billing-endpoints", days], queryFn: () => api.billing.byEndpoint(days), retry: false });
  const ftotal = useQuery({ queryKey: ["foundry-total"], queryFn: () => api.billing.foundryTotal(), retry: false });

  return (
    <>
      <div className="wt-page-head">
        <div>
          <h2>Billing</h2>
          <p>Per-endpoint cost from real App Insights token metrics. No aggregate, no fake attribution.</p>
        </div>
        <div style={{ display: "flex", gap: 10 }}>
          <select className="wt-input" style={{ width: "auto" }} value={days} onChange={(e) => setDays(Number(e.target.value))}>
            <option value={7}>Last 7 days</option>
            <option value={30}>Last 30 days</option>
            <option value={90}>Last 90 days</option>
          </select>
          <a href={api.billing.byEndpointCsvUrl(days)} download className="wt-btn wt-btn-primary">
            <i className="ni ni-cloud-download-95"></i> Download CSV
          </a>
        </div>
      </div>

      {q.isPending && <div className="wt-card"><div className="wt-loading">Loading endpoint metrics…</div></div>}
      {q.isError && <div className="wt-error">{(q.error as Error).message}</div>}

      {q.data && !q.data.workspace_configured && (
        <Card>
          <div style={{ color: "#915c00" }}>
            <i className="ni ni-notification-70" style={{ marginRight: 6 }}></i>
            Log Analytics workspace is not configured on APIM diagnostics. Per-endpoint token metrics can't be queried until this is fixed - see Diagnostics.
          </div>
        </Card>
      )}

      {q.data && q.data.endpoints.length === 0 && (
        <Card><div className="wt-empty">No governed endpoints yet. Onboard one via <a style={{ color: "#5e72e4" }} href="/intake">New project</a>.</div></Card>
      )}

      {q.data && q.data.endpoints.length > 0 && (
        <Card title="Cost per endpoint" subtitle={q.data.pricing_note}>
          <TableWrap>
            <thead>
              <tr>
                <Th>Endpoint</Th>
                <Th>Project</Th>
                <Th>App / team</Th>
                <Th>Env</Th>
                <Th>Model</Th>
                <Th>Prompt tokens</Th>
                <Th>Completion tokens</Th>
                <Th>Est. cost (USD)</Th>
                <Th>Budget</Th>
                <Th> </Th>
              </tr>
            </thead>
            <tbody>
              {q.data.endpoints.map((e: any) => (
                <tr key={e.deployment_name}>
                  <Td>
                    <div style={{ fontWeight: 600 }}>{e.deployment_name}</div>
                    {e.cost_center && <div className="wt-muted wt-small">{e.cost_center}</div>}
                  </Td>
                  <Td>{e.project_name || "-"}</Td>
                  <Td>
                    <div>{e.app_name}</div>
                    <div className="wt-muted wt-small">{e.app_team || "-"}</div>
                  </Td>
                  <Td><Badge tone="blue">{e.environment || "-"}</Badge></Td>
                  <Td className="wt-small">{e.model}</Td>
                  <Td>{e.has_traffic ? e.prompt_tokens.toLocaleString() : <span className="wt-muted">-</span>}</Td>
                  <Td>{e.has_traffic ? e.completion_tokens.toLocaleString() : <span className="wt-muted">-</span>}</Td>
                  <Td>
                    {e.estimated_cost_usd !== null
                      ? <span style={{ fontWeight: 600 }}>${e.estimated_cost_usd.toLocaleString(undefined, { minimumFractionDigits: 2 })}</span>
                      : !e.has_traffic
                        ? <Badge tone="slate">no traffic</Badge>
                        : <Badge tone="warn">no pricing</Badge>}
                  </Td>
                  <Td className="wt-small">{e.monthly_budget_usd ? `$${e.monthly_budget_usd.toLocaleString()}` : "-"}</Td>
                  <Td>
                    <a className="wt-btn wt-btn-secondary wt-btn-sm" href={api.billing.endpointPdfUrl(e.deployment_name, days)} download>
                      <i className="ni ni-cloud-download-95"></i> PDF
                    </a>
                  </Td>
                </tr>
              ))}
            </tbody>
          </TableWrap>
        </Card>
      )}

      {ftotal.data && (
        <Card title="Foundry account total (reconciliation)" subtitle="Sum of Cost Management line items for the whole Foundry account. Not attributed per endpoint.">
          <TableWrap>
            <tbody>
              <tr><Td className="wt-muted">Period</Td><Td>{ftotal.data.year}-{String(ftotal.data.month).padStart(2, "0")}</Td></tr>
              <tr><Td className="wt-muted">Foundry account</Td><Td>{ftotal.data.foundry_account}</Td></tr>
              <tr><Td className="wt-muted">Total pre-tax USD</Td><Td style={{ fontWeight: 600 }}>${ftotal.data.total_usd.toLocaleString()}</Td></tr>
              <tr><Td className="wt-muted">Line items</Td><Td>{ftotal.data.line_count}</Td></tr>
            </tbody>
          </TableWrap>
        </Card>
      )}
    </>
  );
}
