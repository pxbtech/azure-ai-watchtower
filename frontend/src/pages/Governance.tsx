import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { Card, TableWrap, Th, Td, Badge, Grade } from "../components/Card";
import DeploymentDrawer from "./DeploymentDrawer";

export default function Governance() {
  const q = useQuery({ queryKey: ["comp-summary"], queryFn: api.compliance.summary });
  const [open, setOpen] = useState<string | null>(null);

  const stats = useMemo(() => {
    const deps: any[] = q.data?.deployments ?? [];
    const total = deps.length;
    const byGrade: Record<string, number> = { A: 0, B: 0, C: 0, D: 0, F: 0 };
    let critical = 0;
    let sumPct = 0;
    for (const d of deps) {
      if (byGrade[d.grade] !== undefined) byGrade[d.grade]++;
      if (d.critical_fail) critical++;
      sumPct += (d.pass_pct ?? 0);
    }
    return { total, byGrade, critical, avgPct: total ? Math.round(sumPct / total) : 0 };
  }, [q.data]);

  return (
    <>
      <div className="wt-page-head">
        <div>
          <h2>Governance</h2>
          <p>OWASP LLM Top 10 compliance grade per governed model. Critical control failures cap the grade at F regardless of other passes.</p>
        </div>
      </div>

      <div className="wt-metric-row">
        <div className="wt-metric">
          <div className="wt-metric-label">Models governed</div>
          <div className="wt-metric-value">{stats.total}</div>
        </div>
        <div className="wt-metric">
          <div className="wt-metric-label">Avg pass rate</div>
          <div className="wt-metric-value">{stats.avgPct}%</div>
          <div className="wt-metric-sub">across all controls</div>
        </div>
        <div className="wt-metric">
          <div className="wt-metric-label">Grade A / B</div>
          <div className="wt-metric-value" style={{ color: "#087a52" }}>{stats.byGrade.A + stats.byGrade.B}</div>
        </div>
        <div className="wt-metric">
          <div className="wt-metric-label">Critical failures</div>
          <div className="wt-metric-value" style={{ color: stats.critical > 0 ? "#a31c1c" : "#344767" }}>{stats.critical}</div>
          <div className="wt-metric-sub">any critical caps grade at F</div>
        </div>
      </div>

      <Card title="Per-model compliance" subtitle="Click a row to see which controls passed or failed with evidence.">
        {q.isPending && <div className="wt-loading">Computing compliance scores...</div>}
        {q.isError && <div className="wt-error">{(q.error as Error).message}</div>}
        {q.data && q.data.deployments.length === 0 && <div className="wt-empty">No governed deployments yet.</div>}
        {q.data && q.data.deployments.length > 0 && (
          <TableWrap>
            <thead>
              <tr>
                <Th>Deployment</Th>
                <Th>App</Th>
                <Th>Team</Th>
                <Th>Grade</Th>
                <Th>Pass rate</Th>
                <Th>Notes</Th>
                <Th> </Th>
              </tr>
            </thead>
            <tbody>
              {q.data.deployments.map((d: any) => (
                <tr key={d.deployment_name} style={{ cursor: "pointer" }} onClick={() => setOpen(d.deployment_name)}>
                  <Td style={{ fontWeight: 600 }}>{d.deployment_name}</Td>
                  <Td>{d.app_name || "-"}</Td>
                  <Td>{d.app_team || "-"}</Td>
                  <Td><Grade grade={d.grade} /></Td>
                  <Td>{d.pass_pct}%</Td>
                  <Td>{d.critical_fail ? <Badge tone="bad">critical failure</Badge> : <Badge tone="good">no critical fails</Badge>}</Td>
                  <Td>
                    <button className="wt-btn wt-btn-secondary wt-btn-sm" onClick={(e) => { e.stopPropagation(); setOpen(d.deployment_name); }}>
                      Details
                    </button>
                  </Td>
                </tr>
              ))}
            </tbody>
          </TableWrap>
        )}
      </Card>

      <Card title="OWASP LLM Top 10 (2025) - controls checked" subtitle="What each model is scored against.">
        <TableWrap>
          <thead><tr><Th>ID</Th><Th>Name</Th><Th>Severity</Th><Th>Watchtower check</Th></tr></thead>
          <tbody>
            <tr><Td>LLM01</Td><Td>Prompt Injection</Td><Td>critical</Td><Td>RAI policy applied with Jailbreak + Indirect Attack filters set to Blocking</Td></tr>
            <tr><Td>LLM02</Td><Td>Sensitive Information Disclosure</Td><Td>critical</Td><Td>KV secret present + Foundry has disableLocalAuth=true (no key bypass)</Td></tr>
            <tr><Td>LLM03</Td><Td>Supply Chain</Td><Td>high</Td><Td>Model version pinned (not "latest") and not past deprecation date</Td></tr>
            <tr><Td>LLM05</Td><Td>Improper Output Handling</Td><Td>high</Td><Td>RAI policy in Blocking mode with Protected Material filter enabled</Td></tr>
            <tr><Td>LLM10</Td><Td>Unbounded Consumption</Td><Td>critical</Td><Td>TPM limit set + monthly budget set + gateway token-limit policy in place</Td></tr>
          </tbody>
        </TableWrap>
      </Card>

      {open && <DeploymentDrawer name={open} onClose={() => setOpen(null)} />}
    </>
  );
}
