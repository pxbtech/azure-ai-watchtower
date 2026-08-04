import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { Card, TableWrap, Th, Td, Badge, Grade } from "../components/Card";
import { Confirm } from "../components/Confirm";

interface Props { name: string; onClose: () => void; }

export default function DeploymentDrawer({ name, onClose }: Props) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["details", name], queryFn: () => api.deployments.details(name) });
  const drift = useQuery({ queryKey: ["drift", name], queryFn: () => api.deployments.configDrift(name, 168), retry: false });
  const [edit, setEdit] = useState(false);
  const [form, setForm] = useState<any>({});

  const inv = () => {
    qc.invalidateQueries({ queryKey: ["details", name] });
    qc.invalidateQueries({ queryKey: ["projects"] });
  };

  const updateMut = useMutation({
    mutationFn: () => {
      // Only send fields that actually changed vs the current record.
      const orig: any = q.data?.record ?? {};
      const diff: any = {};
      for (const k of Object.keys(form)) {
        const v = (form as any)[k];
        if (v === undefined) continue;
        // Empty string strips would blank things out; skip.
        if (typeof v === "string" && v.trim() === "") continue;
        if (v === orig[k]) continue;
        diff[k] = v;
      }
      if (Object.keys(diff).length === 0) {
        return Promise.resolve({ _noop: true });
      }
      return api.deployments.update(name, diff);
    },
    onSuccess: (r: any) => {
      if (r?._policy_error) {
        // DB was saved but APIM policy rewrite failed - keep drawer open, show error
        alert("Saved to Watchtower DB but APIM policy update failed:\n\n" + r._policy_error);
      }
      inv();
      setEdit(false);
      setForm({});
    },
    onError: (e: Error) => {
      alert("Save failed:\n\n" + e.message);
    },
  });
  const suspendMut   = useMutation({ mutationFn: () => api.deployments.suspend(name), onSuccess: inv });
  const unsuspendMut = useMutation({ mutationFn: () => api.deployments.unsuspend(name), onSuccess: inv });
  const [checkResult, setCheckResult] = useState<any>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const deleteMut = useMutation({
    mutationFn: () => api.deployments.delete(name),
    onSuccess: (r: any) => {
      qc.invalidateQueries({ queryKey: ["projects"] });
      qc.invalidateQueries({ queryKey: ["records"] });
      setConfirmDelete(false);
      const failed = (r?.steps || []).filter((s: any) => !s.ok);
      if (failed.length > 0) {
        setCheckResult({ ok: false, error: `Deleted with ${failed.length} step failure(s): ` + failed.map((s: any) => `${s.step}: ${s.error || 'failed'}`).join("; ") });
      } else {
        onClose();
      }
    },
    onError: (e: Error) => setCheckResult({ ok: false, error: "Delete failed: " + e.message }),
  });
  const checkMut     = useMutation({
    mutationFn: () => api.deployments.budgetCheck(name),
    onSuccess: (r: any) => { inv(); setCheckResult({ ok: true, ...r }); },
    onError: (e: Error) => setCheckResult({ ok: false, error: e.message }),
  });

  const d = q.data;

  return (
    <>
      <div className="wt-drawer-overlay" onClick={onClose}></div>
      <div className="wt-drawer">
        <div className="wt-drawer-head">
          <h3>{name}</h3>
          <button className="wt-drawer-close" onClick={onClose} aria-label="Close">×</button>
        </div>

        {q.isPending && <div className="wt-card"><div className="wt-loading">Loading details...</div></div>}
        {q.isError && <div className="wt-error">{(q.error as Error).message}</div>}

        {checkResult && (
          <div className="wt-card" style={{ borderLeft: `4px solid ${checkResult.ok ? (checkResult.action?.suspended ? "#a31c1c" : (checkResult.status?.burn_pct >= 80 ? "#915c00" : "#087a52")) : "#a31c1c"}`, marginBottom: 20 }}>
            <div className="wt-card-head">
              <div className="wt-card-head-inner">
                <h3>Budget check result</h3>
                <p>Ran a live enforcement pass on this endpoint.</p>
              </div>
              <button className="wt-btn wt-btn-secondary wt-btn-sm" onClick={() => setCheckResult(null)}>Dismiss</button>
            </div>
            <div className="wt-card-body">
              {!checkResult.ok && <div className="wt-error">{checkResult.error}</div>}
              {checkResult.ok && (
                <>
                  {checkResult.status?.skip_reason && (
                    <div style={{ padding: 10, background: "#fdf2d9", color: "#915c00", borderRadius: 8, marginBottom: 12, fontSize: 13 }}>
                      Enforcement skipped: {checkResult.status.skip_reason}
                    </div>
                  )}
                  <div className="wt-metric-row" style={{ marginBottom: 0 }}>
                    <div className="wt-metric" style={{ padding: "14px 18px" }}>
                      <div className="wt-metric-label">MTD spend</div>
                      <div className="wt-metric-value" style={{ fontSize: 22 }}>${checkResult.status?.mtd_cost_usd?.toFixed(4) ?? "0.00"}</div>
                    </div>
                    <div className="wt-metric" style={{ padding: "14px 18px" }}>
                      <div className="wt-metric-label">Budget</div>
                      <div className="wt-metric-value" style={{ fontSize: 22 }}>${checkResult.status?.monthly_budget_usd?.toLocaleString() ?? "-"}</div>
                    </div>
                    <div className="wt-metric" style={{ padding: "14px 18px" }}>
                      <div className="wt-metric-label">Burn</div>
                      <div className="wt-metric-value" style={{ fontSize: 22, color: checkResult.status?.over_threshold ? "#a31c1c" : checkResult.status?.burn_pct >= 80 ? "#915c00" : "#087a52" }}>
                        {checkResult.status?.burn_pct != null ? `${checkResult.status.burn_pct}%` : "-"}
                      </div>
                      <div className="wt-metric-sub">threshold {checkResult.status?.threshold_pct}%</div>
                    </div>
                    <div className="wt-metric" style={{ padding: "14px 18px" }}>
                      <div className="wt-metric-label">Action</div>
                      <div className="wt-metric-value" style={{ fontSize: 16 }}>
                        {checkResult.action?.suspended
                          ? <Badge tone="bad">auto-suspended</Badge>
                          : checkResult.status?.over_threshold
                            ? <Badge tone="warn">over threshold</Badge>
                            : <Badge tone="good">no action</Badge>}
                      </div>
                    </div>
                  </div>
                  {checkResult.action?.reason && (
                    <div className="wt-small wt-muted" style={{ marginTop: 12 }}>{checkResult.action.reason}</div>
                  )}
                </>
              )}
            </div>
          </div>
        )}

        {d && (
          <>
            <Confirm
              open={confirmDelete}
              title={`Delete ${name}?`}
              confirmTone="danger"
              confirmLabel="Delete permanently"
              requireTypedConfirmation={name}
              busy={deleteMut.isPending}
              onCancel={() => setConfirmDelete(false)}
              onConfirm={() => deleteMut.mutate()}
              message={
                <>
                  <p style={{ margin: "0 0 10px" }}>This will permanently delete the following in this order:</p>
                  <ol style={{ margin: "0 0 12px 20px", padding: 0 }}>
                    <li>APIM subscription <span className="wt-mono">{d.record.apim_subscription_id || "-"}</span></li>
                    <li>APIM API <span className="wt-mono">{d.record.apim_api_id || "-"}</span></li>
                    <li>Key Vault secret <span className="wt-mono">{d.record.kv_secret_name || "-"}</span> (soft-delete + purge)</li>
                    <li>Foundry deployment <span className="wt-mono">{d.record.deployment_name}</span></li>
                    <li>Watchtower record (audit + suspension history for this deployment)</li>
                  </ol>
                  <p style={{ margin: 0, color: "#a31c1c" }}>This cannot be undone.</p>
                </>
              }
            />
            <Card title="Overview" action={
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                <button className="wt-btn wt-btn-secondary wt-btn-sm" onClick={() => checkMut.mutate()} disabled={checkMut.isPending}>
                  {checkMut.isPending ? "Checking..." : "Check budget"}
                </button>
                {d.record.state === "suspended"
                  ? <button className="wt-btn wt-btn-primary wt-btn-sm" onClick={() => unsuspendMut.mutate()}>Unsuspend</button>
                  : <button className="wt-btn wt-btn-danger wt-btn-sm" onClick={() => suspendMut.mutate()}>Suspend</button>}
                <button className="wt-btn wt-btn-danger wt-btn-sm" onClick={() => setConfirmDelete(true)}>Delete</button>
              </div>
            }>
              <TableWrap>
                <tbody>
                  <tr><Td className="wt-muted">Project</Td><Td>{d.record.project_name || "-"}</Td></tr>
                  <tr><Td className="wt-muted">Model</Td><Td>{d.record.model_name}@{d.record.model_version}</Td></tr>
                  <tr><Td className="wt-muted">SKU / capacity</Td><Td>{d.record.sku_name} · {d.record.capacity}K</Td></tr>
                  <tr><Td className="wt-muted">Use case</Td><Td style={{ whiteSpace: "normal" }}>{d.record.use_case_description || "-"}</Td></tr>
                  <tr><Td className="wt-muted">App</Td><Td>{d.record.app_name || "-"}</Td></tr>
                  <tr><Td className="wt-muted">Owner</Td><Td>{d.record.app_owner || "-"}</Td></tr>
                  <tr><Td className="wt-muted">Team</Td><Td>{d.record.app_team || "-"}</Td></tr>
                  <tr><Td className="wt-muted">Business unit</Td><Td>{d.record.business_unit || "-"}</Td></tr>
                  <tr><Td className="wt-muted">Cost center</Td><Td>{d.record.cost_center || "-"}</Td></tr>
                  <tr><Td className="wt-muted">Environment</Td><Td><Badge tone="blue">{d.record.environment || "-"}</Badge></Td></tr>
                  <tr><Td className="wt-muted">RAI policy</Td><Td>{d.record.rai_policy_name}</Td></tr>
                  <tr><Td className="wt-muted">State</Td><Td>{d.record.state === "suspended" ? <Badge tone="bad">suspended</Badge> : <Badge tone="good">active</Badge>}</Td></tr>
                  <tr><Td className="wt-muted">APIM subscription</Td><Td className="wt-mono">{d.apim_subscription_state || "not published"}</Td></tr>
                  <tr><Td className="wt-muted">Key Vault</Td><Td>{d.record.kv_secret_uri ? (() => { try { return new URL(d.record.kv_secret_uri).hostname.split(".")[0]; } catch { return d.record.kv_secret_uri; } })() : "-"}</Td></tr>
                  <tr><Td className="wt-muted">Secret name</Td><Td className="wt-mono">{d.record.kv_secret_name || "-"}</Td></tr>
                </tbody>
              </TableWrap>
            </Card>

            <Card title="Ownership & governance" action={
              edit
                ? <div style={{ display: "flex", gap: 8 }}>
                    <button className="wt-btn wt-btn-secondary wt-btn-sm" onClick={() => { setEdit(false); setForm({}); }}>Cancel</button>
                    <button className="wt-btn wt-btn-primary wt-btn-sm" disabled={updateMut.isPending} onClick={() => updateMut.mutate()}>
                      {updateMut.isPending ? "Saving..." : "Save"}
                    </button>
                  </div>
                : <button className="wt-btn wt-btn-secondary wt-btn-sm" onClick={() => { setEdit(true); setForm({
                    tpm_limit: d.record.tpm_limit,
                    throttling_rpm: d.record.throttling_rpm,
                    monthly_budget_usd: d.record.monthly_budget_usd,
                    threshold_pct: d.record.threshold_pct,
                    app_owner: d.record.app_owner ?? "",
                    app_team: d.record.app_team ?? "",
                    business_unit: d.record.business_unit ?? "",
                    environment: d.record.environment ?? "",
                    cost_center: d.record.cost_center ?? "",
                  }); }}>Edit</button>
            }>
              {!edit && (
                <TableWrap>
                  <tbody>
                    <tr><Td className="wt-muted">App owner</Td><Td>{d.record.app_owner || "-"}</Td></tr>
                    <tr><Td className="wt-muted">App team</Td><Td>{d.record.app_team || "-"}</Td></tr>
                    <tr><Td className="wt-muted">Business unit</Td><Td>{d.record.business_unit || "-"}</Td></tr>
                    <tr><Td className="wt-muted">Environment</Td><Td>{d.record.environment || "-"}</Td></tr>
                    <tr><Td className="wt-muted">Cost center</Td><Td>{d.record.cost_center || "-"}</Td></tr>
                    <tr><Td className="wt-muted">Rate limit (tokens/min)</Td><Td>{(d.record.tpm_limit ?? 0).toLocaleString()}</Td></tr>
                    <tr><Td className="wt-muted">Throttling (req/min)</Td><Td>{d.record.throttling_rpm ?? "-"}</Td></tr>
                    <tr><Td className="wt-muted">Monthly budget</Td><Td>{d.record.monthly_budget_usd ? `$${d.record.monthly_budget_usd.toLocaleString()} USD` : <Badge tone="warn">not set</Badge>}</Td></tr>
                    <tr><Td className="wt-muted">Auto-suspend at</Td><Td>{d.record.threshold_pct}% of budget</Td></tr>
                  </tbody>
                </TableWrap>
              )}
              {edit && (
                <>
                  <div className="wt-form-row">
                    <div>
                      <label className="wt-label">App owner</label>
                      <input className="wt-input" value={form.app_owner ?? ""} onChange={(e) => setForm({ ...form, app_owner: e.target.value })} />
                    </div>
                    <div>
                      <label className="wt-label">App team</label>
                      <input className="wt-input" value={form.app_team ?? ""} onChange={(e) => setForm({ ...form, app_team: e.target.value })} />
                    </div>
                    <div>
                      <label className="wt-label">Business unit</label>
                      <input className="wt-input" value={form.business_unit ?? ""} onChange={(e) => setForm({ ...form, business_unit: e.target.value })} />
                    </div>
                  </div>
                  <div className="wt-form-row">
                    <div>
                      <label className="wt-label">Environment</label>
                      <select className="wt-input" value={form.environment ?? ""} onChange={(e) => setForm({ ...form, environment: e.target.value })}>
                        <option value="">-</option><option>dev</option><option>test</option><option>staging</option><option>prod</option>
                      </select>
                    </div>
                    <div>
                      <label className="wt-label">Cost center</label>
                      <input className="wt-input" value={form.cost_center ?? ""} onChange={(e) => setForm({ ...form, cost_center: e.target.value })} />
                    </div>
                  </div>
                  <div className="wt-form-row">
                    <div>
                      <label className="wt-label">Rate limit (tokens/min)</label>
                      <input type="number" className="wt-input" value={form.tpm_limit ?? ""} onChange={(e) => setForm({ ...form, tpm_limit: e.target.value === "" ? undefined : Number(e.target.value) })} />
                    </div>
                    <div>
                      <label className="wt-label">Throttling (req/min)</label>
                      <input type="number" className="wt-input" value={form.throttling_rpm ?? ""} onChange={(e) => setForm({ ...form, throttling_rpm: e.target.value === "" ? undefined : Number(e.target.value) })} />
                    </div>
                    <div>
                      <label className="wt-label">Monthly budget (USD)</label>
                      <input type="number" step="0.01" className="wt-input" value={form.monthly_budget_usd ?? ""} onChange={(e) => setForm({ ...form, monthly_budget_usd: e.target.value === "" ? undefined : Number(e.target.value) })} />
                    </div>
                    <div>
                      <label className="wt-label">Auto-suspend %</label>
                      <input type="number" className="wt-input" value={form.threshold_pct ?? ""} onChange={(e) => setForm({ ...form, threshold_pct: e.target.value === "" ? undefined : Number(e.target.value) })} />
                    </div>
                  </div>
                  <div className="wt-small wt-muted" style={{ marginTop: 8 }}>
                    Note: changing TPM / RPM / metadata re-pushes the APIM policy in place.
                  </div>
                </>
              )}
            </Card>

            <Card title="APIM policies applied">
              {d.policies_applied.length === 0
                ? <div className="wt-empty">Not published to APIM.</div>
                : <TableWrap>
                    <tbody>
                      {d.policies_applied.map((p: any) => (
                        <tr key={p.name}>
                          <Td>{p.name}</Td>
                          <Td>{p.enabled ? <Badge tone="good">enabled</Badge> : <Badge tone="slate">not present</Badge>}</Td>
                        </tr>
                      ))}
                    </tbody>
                  </TableWrap>}
            </Card>

            <Card
              title="OWASP LLM Top 10 compliance"
              subtitle={`${d.compliance.pass_pct}% controls passing${d.compliance.critical_fail ? " · critical failure caps grade at F" : ""}`}
              action={<Grade grade={d.compliance.grade} />}
            >
              <TableWrap>
                <thead><tr><Th>Control</Th><Th>Severity</Th><Th>Status</Th><Th>Evidence</Th></tr></thead>
                <tbody>
                  {d.compliance.checks.map((c: any) => (
                    <tr key={c.id}>
                      <Td><strong>{c.id}</strong> {c.name}</Td>
                      <Td className="wt-small">{c.severity}</Td>
                      <Td>{c.pass ? <Badge tone="good">pass</Badge> : <Badge tone={c.severity === "critical" ? "bad" : "warn"}>fail</Badge>}</Td>
                      <Td className="wt-small">{c.evidence}</Td>
                    </tr>
                  ))}
                </tbody>
              </TableWrap>
            </Card>

            <Card title="Budget enforcement" subtitle="Background worker checks every 5 min. If cost crosses threshold, endpoint is auto-suspended.">
              {d.cost?.enforcement_skip_reason
                ? <div className="wt-muted wt-small">Enforcement skipped: {d.cost.enforcement_skip_reason}</div>
                : <TableWrap>
                    <tbody>
                      <tr><Td className="wt-muted">Month-to-date cost</Td><Td>{d.cost?.mtd_usd !== null && d.cost?.mtd_usd !== undefined ? `$${d.cost.mtd_usd.toFixed(4)} USD` : "no traffic"}</Td></tr>
                      <tr><Td className="wt-muted">Monthly budget</Td><Td>${d.cost?.monthly_budget_usd?.toLocaleString() ?? "-"}</Td></tr>
                      <tr><Td className="wt-muted">Burn</Td><Td>{d.cost?.burn_pct !== null && d.cost?.burn_pct !== undefined ? `${d.cost.burn_pct}%` : "-"}</Td></tr>
                      <tr><Td className="wt-muted">Auto-suspend at</Td><Td>${d.cost?.threshold_cost_usd?.toFixed(2) ?? "-"} ({d.record.threshold_pct}% of budget)</Td></tr>
                      <tr><Td className="wt-muted">Over threshold?</Td><Td>{d.cost?.over_threshold ? <Badge tone="bad">YES</Badge> : <Badge tone="good">no</Badge>}</Td></tr>
                      <tr><Td className="wt-muted">Prompt tokens (MTD)</Td><Td>{(d.cost?.mtd_prompt_tokens ?? 0).toLocaleString()}</Td></tr>
                      <tr><Td className="wt-muted">Completion tokens (MTD)</Td><Td>{(d.cost?.mtd_completion_tokens ?? 0).toLocaleString()}</Td></tr>
                    </tbody>
                  </TableWrap>}
            </Card>

            <Card title="Configuration drift (last 7 days)" subtitle="Real Azure Activity Log events touching this specific deployment or the parent Foundry account. Scope shows if the change was on this endpoint or the whole account.">
              {drift.isPending && <div className="wt-loading">Loading...</div>}
              {drift.isError && <div className="wt-error">{(drift.error as Error).message}</div>}
              {drift.data && drift.data.rows?.length === 0 && <div className="wt-empty">No configuration changes affecting this deployment in the last 7 days.</div>}
              {drift.data && drift.data.rows?.length > 0 && (
                <TableWrap>
                  <thead><tr><Th>When</Th><Th>Scope</Th><Th>Operation</Th><Th>Caller</Th><Th>Status</Th></tr></thead>
                  <tbody>
                    {drift.data.rows.map((r: any, i: number) => (
                      <tr key={i}>
                        <Td className="wt-small">{r.timestamp}</Td>
                        <Td>{r.scope === "deployment" ? <Badge tone="blue">this endpoint</Badge> : <Badge tone="slate">account-wide</Badge>}</Td>
                        <Td className="wt-mono wt-small">{r.operation}</Td>
                        <Td className="wt-small">{r.caller}</Td>
                        <Td>{r.status === "Succeeded" ? <Badge tone="good">{r.status}</Badge> : r.status === "Failed" ? <Badge tone="bad">{r.status}</Badge> : <Badge tone="slate">{r.status || "-"}</Badge>}</Td>
                      </tr>
                    ))}
                  </tbody>
                </TableWrap>
              )}
            </Card>

            <Card title="Security signals (last 24h)">
              {d.security.workspace_missing
                ? <div className="wt-empty">Log Analytics not configured on APIM diagnostics. Fix under Diagnostics to see signals.</div>
                : <TableWrap>
                    <tbody>
                      <tr><Td className="wt-muted">Blocked content events</Td><Td>{d.security.blocked_count > 0 ? <Badge tone="warn">{d.security.blocked_count}</Badge> : <Badge tone="good">0</Badge>}</Td></tr>
                      <tr><Td className="wt-muted">Jailbreak attempts</Td><Td>{d.security.jailbreak_count > 0 ? <Badge tone="bad">{d.security.jailbreak_count}</Badge> : <Badge tone="good">0</Badge>}</Td></tr>
                    </tbody>
                  </TableWrap>}
            </Card>
          </>
        )}
      </div>
    </>
  );
}
