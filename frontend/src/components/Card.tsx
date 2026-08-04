import { ReactNode } from "react";

interface Props {
  title?: string;
  subtitle?: string;
  action?: ReactNode;
  children: ReactNode;
}

export function Card({ title, subtitle, action, children }: Props) {
  return (
    <div className="wt-card">
      {(title || action) && (
        <div className="wt-card-head">
          <div className="wt-card-head-inner">
            {title && <h3>{title}</h3>}
            {subtitle && <p>{subtitle}</p>}
          </div>
          {action}
        </div>
      )}
      <div className="wt-card-body">{children}</div>
    </div>
  );
}

export function TableWrap({ children }: { children: ReactNode }) {
  return (
    <div className="wt-table-container">
      <table className="wt-table">{children}</table>
    </div>
  );
}

export function Th({ children, right }: { children: ReactNode; right?: boolean }) {
  return <th style={right ? { textAlign: "right" } : undefined}>{children}</th>;
}

export function Td({ children, className = "", right, style }: { children: ReactNode; className?: string; right?: boolean; style?: React.CSSProperties }) {
  const s: React.CSSProperties = { ...(right ? { textAlign: "right" as const } : {}), ...(style || {}) };
  return <td className={className} style={Object.keys(s).length ? s : undefined}>{children}</td>;
}

export function Badge({ children, tone = "slate" }: { children: ReactNode; tone?: "good" | "warn" | "bad" | "slate" | "blue" }) {
  return <span className={`wt-badge wt-badge-${tone}`}>{children}</span>;
}

export function Grade({ grade }: { grade: string }) {
  return <span className={`wt-grade wt-grade-${grade}`}>{grade}</span>;
}
