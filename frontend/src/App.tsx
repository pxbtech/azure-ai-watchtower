import { NavLink, Route, Routes } from "react-router-dom";
import Projects from "./pages/Projects";
import Intake from "./pages/Intake";
import Security from "./pages/Security";
import Governance from "./pages/Governance";
import Billing from "./pages/Billing";
import Monitoring from "./pages/Monitoring";
import Diagnostics from "./pages/Diagnostics";

const NAV = [
  { to: "/",            label: "Projects",    icon: "ni-single-copy-04" },
  { to: "/intake",      label: "New project", icon: "ni-fat-add" },
  { to: "/governance",  label: "Governance",  icon: "ni-badge" },
  { to: "/security",    label: "Security",    icon: "ni-lock-circle-open" },
  { to: "/billing",     label: "Billing",     icon: "ni-money-coins" },
  { to: "/monitoring",  label: "Monitoring",  icon: "ni-chart-bar-32" },
];

export default function App() {
  return (
    <>
      <aside className="wt-sidebar">
        <div className="wt-brand">
          <div className="wt-brand-icon" aria-label="AI Watchtower">AW</div>
          <div>
            <h1>AI Watchtower</h1>
            <div className="wt-brand-sub">Foundry governance</div>
          </div>
        </div>
        <nav className="wt-nav">
          {NAV.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              end={n.to === "/"}
              className={({ isActive }) => (isActive ? "active" : "")}
            >
              <div className="wt-nav-icon-box">
                <i className={`ni ${n.icon}`} style={{ color: "#5e72e4" }}></i>
              </div>
              {n.label}
            </NavLink>
          ))}
        </nav>
      </aside>

      <main className="wt-main">
        <Routes>
          <Route path="/" element={<Projects />} />
          <Route path="/intake" element={<Intake />} />
          <Route path="/governance" element={<Governance />} />
          <Route path="/security" element={<Security />} />
          <Route path="/billing" element={<Billing />} />
          <Route path="/monitoring" element={<Monitoring />} />
          <Route path="/diagnostics" element={<Diagnostics />} />
        </Routes>
      </main>
    </>
  );
}
