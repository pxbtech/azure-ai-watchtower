import { ReactNode, useState } from "react";

interface Props {
  open: boolean;
  title: string;
  message: ReactNode;
  confirmLabel?: string;
  confirmTone?: "danger" | "primary";
  requireTypedConfirmation?: string; // if set, user must type this string exactly to enable Confirm
  busy?: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}

export function Confirm({ open, title, message, confirmLabel = "Confirm", confirmTone = "primary", requireTypedConfirmation, busy, onCancel, onConfirm }: Props) {
  const [typed, setTyped] = useState("");
  if (!open) return null;
  const canConfirm = !requireTypedConfirmation || typed === requireTypedConfirmation;

  return (
    <>
      <div style={{ position: "fixed", inset: 0, background: "rgba(52,71,103,0.45)", zIndex: 1000 }} onClick={onCancel}></div>
      <div style={{
        position: "fixed", top: "50%", left: "50%", transform: "translate(-50%,-50%)",
        background: "#fff", borderRadius: 12, padding: "24px 26px", width: "min(520px, 92vw)",
        boxShadow: "0 30px 60px rgba(0,0,0,0.25)", zIndex: 1001,
      }}>
        <h3 style={{ margin: "0 0 12px", color: "#344767", fontSize: 17, fontWeight: 700 }}>{title}</h3>
        <div style={{ color: "#67748e", fontSize: 14, lineHeight: 1.55, marginBottom: 16 }}>{message}</div>
        {requireTypedConfirmation && (
          <div style={{ marginBottom: 16 }}>
            <label style={{ display: "block", fontSize: 12, fontWeight: 600, color: "#67748e", textTransform: "uppercase", letterSpacing: 0.4, marginBottom: 6 }}>
              Type <span className="wt-mono" style={{ background: "#f6f9fc", padding: "1px 6px", borderRadius: 4 }}>{requireTypedConfirmation}</span> to confirm
            </label>
            <input autoFocus className="wt-input" value={typed} onChange={(e) => setTyped(e.target.value)} placeholder={requireTypedConfirmation} />
          </div>
        )}
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 10 }}>
          <button className="wt-btn wt-btn-secondary" onClick={() => { setTyped(""); onCancel(); }} disabled={busy}>Cancel</button>
          <button
            className={confirmTone === "danger" ? "wt-btn wt-btn-danger" : "wt-btn wt-btn-primary"}
            onClick={() => { onConfirm(); setTyped(""); }}
            disabled={!canConfirm || busy}
          >
            {busy ? "Working..." : confirmLabel}
          </button>
        </div>
      </div>
    </>
  );
}
