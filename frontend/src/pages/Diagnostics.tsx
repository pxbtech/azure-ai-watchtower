import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { Card, TableWrap, Th, Td, Badge } from "../components/Card";

export default function Diagnostics() {
  const diag = useQuery({ queryKey: ["diag"], queryFn: api.diagnostics.apim, retry: false });
  const svc = useQuery({ queryKey: ["apim-svc"], queryFn: api.diagnostics.apimService });

  return (
    <>
      <div className="wt-page-head">
        <div>
          <h2>Diagnostics gate</h2>
          <p>Without APIM body capture, content-filter details and jailbreak signals are invisible.</p>
        </div>
      </div>

      <Card title="APIM service">
        {svc.isPending && <div className="wt-loading">Loading…</div>}
        {svc.data && (
          <TableWrap>
            <tbody>
              <tr><Td className="wt-muted">Name</Td><Td>{svc.data.name}</Td></tr>
              <tr><Td className="wt-muted">SKU</Td><Td>{svc.data.sku}</Td></tr>
              <tr><Td className="wt-muted">Gateway URL</Td><Td className="wt-mono">{svc.data.gateway_url}</Td></tr>
              <tr><Td className="wt-muted">Managed identity</Td><Td>{svc.data.identity_principal_id
                ? <Badge tone="good">enabled ({svc.data.identity_type})</Badge>
                : <Badge tone="bad">missing</Badge>}</Td></tr>
            </tbody>
          </TableWrap>
        )}
      </Card>

      <Card
        title="Checks"
        subtitle={diag.data?.workspace_id ? `Workspace: ${diag.data.workspace_id}` : undefined}
        action={diag.data ? (diag.data.overall_pass ? <Badge tone="good">all pass</Badge> : <Badge tone="bad">failed</Badge>) : undefined}
      >
        {diag.isPending && <div className="wt-loading">Running diagnostics…</div>}
        {diag.isError && <div className="wt-error">{(diag.error as Error).message}</div>}
        {diag.data && (
          <TableWrap>
            <thead><tr><Th>Check</Th><Th>Status</Th><Th>Detail / remediation</Th></tr></thead>
            <tbody>
              {diag.data.checks?.map((c: any) => (
                <tr key={c.id}>
                  <Td><strong>{c.name}</strong></Td>
                  <Td>{c.pass ? <Badge tone="good">pass</Badge> : <Badge tone="bad">fail</Badge>}</Td>
                  <Td>
                    {c.detail && <div className="wt-small" style={{ marginBottom: 6 }}>{c.detail}</div>}
                    {!c.pass && c.remediation && (
                      <pre className="wt-mono" style={{ background: "#0f172a", color: "#a5b4fc", padding: "12px 14px", borderRadius: 8, overflowX: "auto", whiteSpace: "pre-wrap", margin: 0, fontSize: 12 }}>{c.remediation}</pre>
                    )}
                  </Td>
                </tr>
              ))}
            </tbody>
          </TableWrap>
        )}
      </Card>
    </>
  );
}
