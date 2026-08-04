import { useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api";
import { Card, TableWrap, Th, Td, Badge } from "../components/Card";

interface IntakeForm {
  project_name: string; create_project: boolean;
  deployment_name: string;
  model_name: string; model_version: string; model_format: string;
  sku_name: string; capacity: number;
  rai_policy_name: string;
  use_case_description: string;
  app_name: string; app_owner: string; app_team: string; business_unit: string;
  environment: string; cost_center: string;
  tpm_limit: number; throttling_rpm: number; monthly_budget_usd: number; threshold_pct: number;
}

const DEFAULT: IntakeForm = {
  project_name: "", create_project: true,
  deployment_name: "",
  model_name: "", model_version: "", model_format: "OpenAI",
  sku_name: "", capacity: 1,
  rai_policy_name: "watchtower-balanced",
  use_case_description: "",
  app_name: "", app_owner: "", app_team: "", business_unit: "",
  environment: "prod", cost_center: "",
  tpm_limit: 10000, throttling_rpm: 60, monthly_budget_usd: 500, threshold_pct: 95,
};

interface ModelEntry {
  name: string; version: string; format: string;
  lifecycle_status?: string; deprecation_inference?: string | null;
  skus: { name: string; capacity_default?: number; capacity_min?: number; capacity_max?: number }[];
}

function sanitizeDeploymentName(input: string): string {
  // Trim, lowercase (optional), strip anything not letter/digit/hyphen, cap 64
  return input.replace(/[^a-zA-Z0-9-]/g, "").slice(0, 64);
}

function validate(form: IntakeForm): Record<string, string> {
  const errors: Record<string, string> = {};

  if (!form.deployment_name) errors.deployment_name = "Required";
  else if (form.deployment_name.length < 2) errors.deployment_name = "At least 2 characters";
  else if (form.deployment_name.length > 64) errors.deployment_name = "Max 64 characters";
  else if (!/^[a-zA-Z0-9-]+$/.test(form.deployment_name)) errors.deployment_name = "Only letters, digits, hyphens";

  if (!form.model_name) errors.model_name = "Pick a model";
  if (!form.model_version) errors.model_version = "Pick a version";
  if (!form.sku_name) errors.sku_name = "Pick a SKU";
  if (form.capacity < 1) errors.capacity = "At least 1";

  if (!form.use_case_description.trim()) errors.use_case_description = "Required";
  else if (form.use_case_description.trim().length < 10) errors.use_case_description = "At least 10 characters";

  if (!form.app_name.trim()) errors.app_name = "Required";
  if (!form.app_owner.trim()) errors.app_owner = "Required";
  else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(form.app_owner) && !form.app_owner.includes(" ")) {
    // Loose: allow email OR name-with-spaces; only flag as warning-ish if it looks like broken email
  }
  if (!form.app_team.trim()) errors.app_team = "Required";
  if (!form.business_unit.trim()) errors.business_unit = "Required";
  if (!form.environment) errors.environment = "Required";

  if (form.tpm_limit < 100) errors.tpm_limit = "At least 100";
  if (form.throttling_rpm < 1) errors.throttling_rpm = "At least 1";
  if (form.monthly_budget_usd < 0) errors.monthly_budget_usd = "Cannot be negative";
  if (form.threshold_pct < 50 || form.threshold_pct > 100) errors.threshold_pct = "Between 50 and 100";

  return errors;
}

function FieldError({ msg }: { msg?: string }) {
  if (!msg) return null;
  return <div style={{ color: "#a31c1c", fontSize: 12, marginTop: 4, fontWeight: 500 }}>{msg}</div>;
}

export default function Intake() {
  const [form, setForm] = useState<IntakeForm>(DEFAULT);
  const [touched, setTouched] = useState<Record<string, boolean>>({});
  const [attempted, setAttempted] = useState(false);
  const [result, setResult] = useState<any>(null);

  const models = useQuery<ModelEntry[]>({
    queryKey: ["available-models"], queryFn: api.deployments.availableModels, staleTime: 5 * 60_000,
  });
  const mut = useMutation({ mutationFn: () => api.intake.submit(form), onSuccess: setResult });
  const set = <K extends keyof IntakeForm>(k: K, v: IntakeForm[K]) => setForm((f) => ({ ...f, [k]: v }));
  const touch = (k: string) => setTouched((t) => ({ ...t, [k]: true }));

  const errors = useMemo(() => validate(form), [form]);
  const errorCount = Object.keys(errors).length;
  const canSubmit = errorCount === 0;
  const shouldShowError = (k: string) => errors[k] && (touched[k] || attempted);

  const modelNames = useMemo(() => {
    if (!models.data) return [];
    const seen = new Set<string>();
    const out: { name: string; format: string; hasActive: boolean }[] = [];
    for (const m of models.data) {
      if (seen.has(m.name)) continue;
      seen.add(m.name);
      const active = models.data.some((x) => x.name === m.name && x.lifecycle_status !== "Deprecated");
      out.push({ name: m.name, format: m.format, hasActive: active });
    }
    return out.sort((a, b) => a.name.localeCompare(b.name));
  }, [models.data]);

  const versions = useMemo(() => {
    if (!models.data || !form.model_name) return [];
    return models.data
      .filter((m) => m.name === form.model_name)
      .map((m) => ({ version: m.version, lifecycle: m.lifecycle_status, deprecation: m.deprecation_inference }))
      .sort((a, b) => (b.version || "").localeCompare(a.version || ""));
  }, [models.data, form.model_name]);

  const skus = useMemo(() => {
    if (!models.data || !form.model_name || !form.model_version) return [];
    const entry = models.data.find((m) => m.name === form.model_name && m.version === form.model_version);
    return entry?.skus ?? [];
  }, [models.data, form.model_name, form.model_version]);

  function pickModel(name: string) {
    const entry = models.data?.find((m) => m.name === name);
    setForm((f) => ({ ...f, model_name: name, model_format: entry?.format || "OpenAI", model_version: "", sku_name: "", capacity: 1 }));
  }
  function pickVersion(version: string) { setForm((f) => ({ ...f, model_version: version, sku_name: "", capacity: 1 })); }
  function pickSku(sku_name: string) {
    const entry = skus.find((s) => s.name === sku_name);
    setForm((f) => ({ ...f, sku_name, capacity: entry?.capacity_default ?? 1 }));
  }

  function attemptSubmit() {
    setAttempted(true);
    if (!canSubmit) {
      // Mark all fields with errors as touched so errors show
      const t: Record<string, boolean> = {};
      Object.keys(errors).forEach((k) => (t[k] = true));
      setTouched((prev) => ({ ...prev, ...t }));
      return;
    }
    mut.mutate();
  }

  if (result) {
    return (
      <>
        <div className="wt-page-head">
          <div>
            <h2>Project onboarded</h2>
            <p>Model deployment created, APIM published, key stored, monitoring active.</p>
          </div>
        </div>

        <Card title="Endpoint">
          <TableWrap>
            <tbody>
              <tr><Td className="wt-muted">Callable URL</Td><Td className="wt-mono">{result.endpoint_url}</Td></tr>
              <tr><Td className="wt-muted">APIM API id</Td><Td className="wt-mono">{result.apim_api_id}</Td></tr>
              <tr><Td className="wt-muted">APIM subscription</Td><Td className="wt-mono">{result.apim_subscription_id}</Td></tr>
              <tr><Td className="wt-muted">Key Vault</Td><Td>{(() => { try { return new URL(result.apim_subscription_key_kv_uri).hostname.split(".")[0]; } catch { return "-"; } })()}</Td></tr>
              <tr><Td className="wt-muted">Secret name</Td><Td className="wt-mono">{(() => { try { const p = new URL(result.apim_subscription_key_kv_uri).pathname.split("/"); return p[2] || "-"; } catch { return "-"; } })()}</Td></tr>
              <tr><Td className="wt-muted">Project</Td><Td>{result.project_name || "(none)"}</Td></tr>
            </tbody>
          </TableWrap>
        </Card>

        <Card title="Stages">
          <TableWrap>
            <thead><tr><Th>Step</Th><Th>Status</Th><Th>Detail</Th></tr></thead>
            <tbody>
              {result.stages.map((s: any, i: number) => (
                <tr key={i}>
                  <Td className="wt-mono">{s.step}</Td>
                  <Td>{s.ok ? <Badge tone="good">ok</Badge> : <Badge tone="warn">warn</Badge>}</Td>
                  <Td className="wt-small" style={{ whiteSpace: "normal" }}>{s.detail}</Td>
                </tr>
              ))}
            </tbody>
          </TableWrap>
        </Card>

        <div style={{ display: "flex", gap: 10 }}>
          <Link to="/" className="wt-btn wt-btn-primary">Go to projects</Link>
          <button className="wt-btn wt-btn-secondary" onClick={() => { setResult(null); setForm(DEFAULT); setTouched({}); setAttempted(false); }}>Onboard another</button>
        </div>
      </>
    );
  }

  return (
    <>
      <div className="wt-page-head">
        <div>
          <h2>New project intake</h2>
          <p>Standard onboarding for a governed AI endpoint. All fields are captured as APIM metadata for audit and chargeback.</p>
        </div>
      </div>

      <Card title="Project">
        <div className="wt-form-row">
          <div>
            <label className="wt-label">Project name</label>
            <input className="wt-input" value={form.project_name}
              onChange={(e) => set("project_name", e.target.value.trim())}
              placeholder="my-project" />
          </div>
          <div>
            <label className="wt-label">Environment *</label>
            <select className="wt-input" value={form.environment} onChange={(e) => set("environment", e.target.value)} onBlur={() => touch("environment")}>
              <option>dev</option><option>test</option><option>staging</option><option>prod</option>
            </select>
            {shouldShowError("environment") && <FieldError msg={errors.environment} />}
          </div>
          <div style={{ display: "flex", alignItems: "flex-end", paddingBottom: 6 }}>
            <label className="wt-small" style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <input type="checkbox" checked={form.create_project} onChange={(e) => set("create_project", e.target.checked)} />
              Create Foundry project (uncheck if it already exists)
            </label>
          </div>
        </div>
      </Card>

      <Card title="Use case">
        <label className="wt-label">Description *</label>
        <textarea className="wt-input wt-textarea" value={form.use_case_description}
          onChange={(e) => set("use_case_description", e.target.value)} onBlur={() => touch("use_case_description")}
          placeholder="What is this AI endpoint for? Who uses it? Expected traffic? Any compliance concerns?" />
        {shouldShowError("use_case_description") && <FieldError msg={errors.use_case_description} />}
      </Card>

      <Card title="Ownership">
        <div className="wt-form-row">
          <div>
            <label className="wt-label">App name *</label>
            <input className="wt-input" value={form.app_name} onChange={(e) => set("app_name", e.target.value)} onBlur={() => touch("app_name")} placeholder="my-chat-assistant" />
            {shouldShowError("app_name") && <FieldError msg={errors.app_name} />}
          </div>
          <div>
            <label className="wt-label">App owner *</label>
            <input className="wt-input" value={form.app_owner} onChange={(e) => set("app_owner", e.target.value)} onBlur={() => touch("app_owner")} placeholder="jane.doe@example.com" />
            {shouldShowError("app_owner") && <FieldError msg={errors.app_owner} />}
          </div>
          <div>
            <label className="wt-label">App team *</label>
            <input className="wt-input" value={form.app_team} onChange={(e) => set("app_team", e.target.value)} onBlur={() => touch("app_team")} placeholder="Platform Engineering" />
            {shouldShowError("app_team") && <FieldError msg={errors.app_team} />}
          </div>
          <div>
            <label className="wt-label">Business unit *</label>
            <input className="wt-input" value={form.business_unit} onChange={(e) => set("business_unit", e.target.value)} onBlur={() => touch("business_unit")} placeholder="Business Unit" />
            {shouldShowError("business_unit") && <FieldError msg={errors.business_unit} />}
          </div>
          <div>
            <label className="wt-label">Cost center</label>
            <input className="wt-input" value={form.cost_center} onChange={(e) => set("cost_center", e.target.value)} placeholder="CC-1234" />
          </div>
        </div>
      </Card>

      <Card title="Governance">
        <div className="wt-form-row">
          <div>
            <label className="wt-label">Tokens / min (TPM)</label>
            <input type="number" className="wt-input" value={form.tpm_limit} onChange={(e) => set("tpm_limit", Number(e.target.value))} onBlur={() => touch("tpm_limit")} />
            {shouldShowError("tpm_limit") && <FieldError msg={errors.tpm_limit} />}
          </div>
          <div>
            <label className="wt-label">Requests / min (RPM)</label>
            <input type="number" className="wt-input" value={form.throttling_rpm} onChange={(e) => set("throttling_rpm", Number(e.target.value))} onBlur={() => touch("throttling_rpm")} />
            {shouldShowError("throttling_rpm") && <FieldError msg={errors.throttling_rpm} />}
          </div>
          <div>
            <label className="wt-label">Monthly budget (USD)</label>
            <input type="number" step="0.01" className="wt-input" value={form.monthly_budget_usd} onChange={(e) => set("monthly_budget_usd", Number(e.target.value))} onBlur={() => touch("monthly_budget_usd")} />
            {shouldShowError("monthly_budget_usd") && <FieldError msg={errors.monthly_budget_usd} />}
          </div>
          <div>
            <label className="wt-label">Auto-suspend %</label>
            <input type="number" className="wt-input" value={form.threshold_pct} onChange={(e) => set("threshold_pct", Number(e.target.value))} onBlur={() => touch("threshold_pct")} />
            {shouldShowError("threshold_pct") && <FieldError msg={errors.threshold_pct} />}
          </div>
        </div>
      </Card>

      <Card
        title="Model deployment"
        subtitle={models.isPending ? "Loading models from your Foundry..." : models.data?.length ? `${models.data.length} deployable model+version combos in ${form.environment} region` : "No models found. Check quota."}
      >
        {models.isError && <div className="wt-error">Failed to load model catalog: {(models.error as Error).message}</div>}

        <div className="wt-form-row">
          <div>
            <label className="wt-label">Deployment name *</label>
            <input className="wt-input" value={form.deployment_name}
              onChange={(e) => set("deployment_name", sanitizeDeploymentName(e.target.value))}
              onBlur={() => touch("deployment_name")}
              placeholder="e.g. my-chat-nano" />
            <div className="wt-small wt-muted" style={{ marginTop: 4 }}>
              Letters, digits, hyphens only. Auto-sanitized as you type. {form.deployment_name && <span style={{ fontFamily: "monospace" }}>&raquo; {form.deployment_name}</span>}
            </div>
            {shouldShowError("deployment_name") && <FieldError msg={errors.deployment_name} />}
          </div>

          <div>
            <label className="wt-label">Model *</label>
            <select className="wt-input" value={form.model_name} onChange={(e) => pickModel(e.target.value)} onBlur={() => touch("model_name")} disabled={models.isPending || !modelNames.length}>
              <option value="">Pick a model</option>
              {modelNames.map((m) => (
                <option key={m.name} value={m.name} disabled={!m.hasActive}>
                  {m.name} {m.hasActive ? "" : "(deprecated)"}
                </option>
              ))}
            </select>
            {shouldShowError("model_name") && <FieldError msg={errors.model_name} />}
          </div>

          <div>
            <label className="wt-label">Version *</label>
            <select className="wt-input" value={form.model_version} onChange={(e) => pickVersion(e.target.value)} onBlur={() => touch("model_version")} disabled={!form.model_name || !versions.length}>
              <option value="">Pick a version</option>
              {versions.map((v) => (
                <option key={v.version} value={v.version} disabled={v.lifecycle === "Deprecated"}>
                  {v.version} {v.lifecycle === "Deprecated" ? "(deprecated)" : ""}
                </option>
              ))}
            </select>
            {shouldShowError("model_version") && <FieldError msg={errors.model_version} />}
          </div>

          <div>
            <label className="wt-label">SKU *</label>
            <select className="wt-input" value={form.sku_name} onChange={(e) => pickSku(e.target.value)} onBlur={() => touch("sku_name")} disabled={!form.model_version || !skus.length}>
              <option value="">Pick a SKU</option>
              {skus.map((s) => (
                <option key={s.name} value={s.name}>{s.name}</option>
              ))}
            </select>
            {shouldShowError("sku_name") && <FieldError msg={errors.sku_name} />}
          </div>

          <div>
            <label className="wt-label">Capacity (K TPM)</label>
            <input type="number" min={1} className="wt-input" value={form.capacity}
              onChange={(e) => set("capacity", Number(e.target.value))}
              disabled={!form.sku_name} />
          </div>
        </div>
      </Card>

      {mut.isError && <div className="wt-error">{(mut.error as Error).message}</div>}

      {attempted && errorCount > 0 && (
        <div className="wt-error" style={{ marginBottom: 16 }}>
          <strong>{errorCount} field{errorCount === 1 ? "" : "s"} need attention:</strong>{" "}
          {Object.entries(errors).map(([k, v]) => (
            <span key={k} style={{ marginRight: 12 }}>{k.replace(/_/g, " ")}: {v}</span>
          ))}
        </div>
      )}

      <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
        <button className="wt-btn wt-btn-primary" disabled={mut.isPending} onClick={attemptSubmit}>
          {mut.isPending ? "Onboarding..." : "Onboard project"}
        </button>
        <button className="wt-btn wt-btn-secondary" onClick={() => { setForm(DEFAULT); setTouched({}); setAttempted(false); }} disabled={mut.isPending}>Reset</button>
        {!canSubmit && !attempted && (
          <span className="wt-small wt-muted">{errorCount} field{errorCount === 1 ? "" : "s"} still need input</span>
        )}
      </div>
    </>
  );
}
