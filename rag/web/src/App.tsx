import { useEffect, useState } from "react";
import { WorkbenchProvider, useWorkbench } from "./state/workbench";
import { CommandPalette, RunLog, Sidebar, ToastStack, Topbar } from "./components/Shell";
import { Notice } from "./components/ui";
import { FlowTrack } from "./components/FlowTrack";
import {
  CasePanel,
  MethodPanel,
  QuestionPanel,
  ScopePanel,
  TermsPanel,
} from "./panels/PreparePanels";
import { RequestPanel, RunPanel } from "./panels/ExecutePanels";
import { ResultsPanel } from "./panels/ResultsPanel";
import { AuditPanel, GuidelinesPanel } from "./panels/GuidelinesPanel";

function CurrentPanel() {
  const { step } = useWorkbench();
  switch (step) {
    case "case":
      return <CasePanel />;
    case "question":
      return <QuestionPanel />;
    case "terms":
      return <TermsPanel />;
    case "scope":
      return <ScopePanel />;
    case "method":
      return <MethodPanel />;
    case "request":
      return <RequestPanel />;
    case "run":
      return <RunPanel />;
    case "results":
      return <ResultsPanel />;
    case "guidelines":
      return <GuidelinesPanel />;
    case "audit":
      return <AuditPanel />;
    default:
      return <CasePanel />;
  }
}

function WorkbenchApp() {
  const { backendError, setCmdOpen, cmdOpen, step } = useWorkbench();
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [runlogOpen, setRunlogOpen] = useState(false);

  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setCmdOpen(!cmdOpen);
      }
      if (event.key === "Escape" && cmdOpen) setCmdOpen(false);
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [cmdOpen, setCmdOpen]);

  return (
    <div className="shell">
      <Topbar
        onToggleRunlog={() => setRunlogOpen((prev) => !prev)}
        onToggleSidebar={() => setSidebarOpen((prev) => !prev)}
      />
      <Sidebar open={sidebarOpen} onNavigate={() => setSidebarOpen(false)} />
      <main className="main">
        <div className="main-inner">
          {backendError ? (
            <div style={{ marginBottom: 14 }}>
              <Notice tone="error" icon="alert">
                {backendError}
              </Notice>
            </div>
          ) : null}
          <FlowTrack />
          <div className="sheet-view" key={step}>
            <CurrentPanel />
          </div>
          <div className="sheet-foot">
            <span>ChinaLLM · 新生儿证据检索</span>
            <span>合成演示 · 只读检索 · 非诊疗结论</span>
          </div>
        </div>
      </main>
      <RunLog open={runlogOpen} onToggle={() => setRunlogOpen((prev) => !prev)} />
      <ToastStack />
      <CommandPalette />
    </div>
  );
}

export default function App() {
  return (
    <WorkbenchProvider>
      <WorkbenchApp />
    </WorkbenchProvider>
  );
}
