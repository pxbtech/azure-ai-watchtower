import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api";
import { Card, TableWrap, Th, Td, Badge } from "../components/Card";
import { Confirm } from "../components/Confirm";
import DeploymentDrawer from "./DeploymentDrawer";

function burnTone(pct: number | null): "good" | "warn" | "bad" | "slate" {
  if (pct == null) return "slate";
  if (pct >= 95) return "bad";
  if (pct >= 80) return "warn";
  return "good";
}

export default function Projects() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["projects"], queryFn: api.projects.list });
  const [open, setOpen] = useState<string | null>(null);
  const [confirmProject, setConfirmProject] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const deleteProjectMut = useMutation({
    mutationFn: (name: string) => api.projects.delete(name),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["projects"] });
      setConfirmProject(null);
      setDeleteError(null);
    },
    onError: (e: Error) => setDeleteError(e.message),
  });

  return (
    <>
      <div className="wt-page-head">
        <div>
          <h2>Projects</h2>
          <p>Governed AI projects with their deployed endpoints.</p>
        </div>
      </div>

      {q.isPending && <div className="wt-card"><div className="wt-loading">Loading projects...</div></div>}
      {q.isError && <div className="wt-error">{(q.error as Error).message}</div>}

      {q.data && q.data.projects.length === 0 && (
        <Card>
          <div style={{ textAlign: "center", padding: "40px 20px" }}>
            <i className="ni ni-single-copy-04" style={{ fontSize: 48, color: "#c5cdd8", display: "block", marginBottom: 16 }}></i>
            <p style={{ color: "#67748e", marginBottom: 20 }}>No projects yet. Teams onboard AI endpoints through the standard intake process.</p>
            <Link to="/intake" className="wt-btn wt-btn-primary">
              <i className="ni ni-fat-add"></i> Onboard first project
            </Link>
          </div>
        </Card>
      )}

      {q.data && q.data.projects.map((p: any) => (
        <Card
          key={p.project_name}
          title={p.project_name}
          subtitle={`${p.deployment_count} deployment${p.deployment_count === 1 ? "" : "s"} · ${p.active_count} active · $${p.mtd_cost_usd.toFixed(2)} MTD · $${p.monthly_budget_usd.toLocaleString()} budget`}
          action={
            p.project_name !== "unassigned" && (
              <button
                className="wt-btn wt-btn-danger wt-btn-sm"
                disabled={p.deployment_count > 0}
                title={p.deployment_count > 0 ? "Delete all deployments in this project first" : "Delete project"}
                onClick={() => setConfirmProject(p.project_name)}
              >
                Delete project
              </button>
            )
          }
        >
          <TableWrap>
            <colgroup>
              <col style={{ width: "22%" }} />
              <col style={{ width: "18%" }} />
              <col style={{ width: "14%" }} />
              <col style={{ width: "16%" }} />
              <col style={{ width: "14%" }} />
              <col style={{ width: "8%" }} />
              <col style={{ width: "8%" }} />
            </colgroup>
            <thead>
              <tr>
                <Th>Endpoint</Th>
                <Th>App</Th>
                <Th>Team</Th>
                <Th>Consumed</Th>
                <Th>Budget</Th>
                <Th>State</Th>
                <Th> </Th>
              </tr>
            </thead>
            <tbody>
              {p.deployments.map((d: any) => (
                <tr key={d.deployment_name} style={{ cursor: "pointer" }} onClick={() => setOpen(d.deployment_name)}>
                  <Td style={{ fontWeight: 600 }}>{d.deployment_name}</Td>
                  <Td>{d.app_name || "-"}</Td>
                  <Td>{d.app_team || "-"}</Td>
                  <Td>
                    {d.mtd_cost_usd !== null && d.mtd_cost_usd !== undefined
                      ? <div>
                          <div style={{ fontWeight: 600 }}>${d.mtd_cost_usd.toFixed(2)}</div>
                          <div className="wt-small wt-muted">
                            {d.mtd_total_tokens?.toLocaleString?.() ?? 0} tok
                            {d.burn_pct != null && <> · <span className={`wt-badge wt-badge-${burnTone(d.burn_pct)}`}>{d.burn_pct}%</span></>}
                          </div>
                        </div>
                      : d.has_pricing === false
                        ? <Badge tone="slate">no pricing</Badge>
                        : <Badge tone="slate">no traffic</Badge>}
                  </Td>
                  <Td>
                    {d.monthly_budget_usd
                      ? <div>
                          <div style={{ fontWeight: 600 }}>${d.monthly_budget_usd.toLocaleString()}</div>
                          <div className="wt-small wt-muted">stop at {d.threshold_pct}%</div>
                        </div>
                      : <Badge tone="warn">not set</Badge>}
                  </Td>
                  <Td>{d.state === "suspended" ? <Badge tone="bad">suspended</Badge> : <Badge tone="good">active</Badge>}</Td>
                  <Td>
                    <button className="wt-btn wt-btn-secondary wt-btn-sm" onClick={(e) => { e.stopPropagation(); setOpen(d.deployment_name); }}>
                      Details
                    </button>
                  </Td>
                </tr>
              ))}
            </tbody>
          </TableWrap>
        </Card>
      ))}

      {open && <DeploymentDrawer name={open} onClose={() => setOpen(null)} />}

      <Confirm
        open={confirmProject !== null}
        title={`Delete project ${confirmProject}?`}
        confirmTone="danger"
        confirmLabel="Delete project"
        requireTypedConfirmation={confirmProject || ""}
        busy={deleteProjectMut.isPending}
        onCancel={() => { setConfirmProject(null); setDeleteError(null); }}
        onConfirm={() => confirmProject && deleteProjectMut.mutate(confirmProject)}
        message={
          <>
            <p style={{ margin: "0 0 10px" }}>Deletes the Foundry project resource. The project must have zero deployments (checked server-side).</p>
            {deleteError && <div className="wt-error" style={{ marginTop: 8 }}>{deleteError}</div>}
          </>
        }
      />
    </>
  );
}
