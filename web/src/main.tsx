import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { setApiBase } from "./api";
import { inDesktop, invoke, onDesktopEvent, routeExternalLinks, type Service } from "./desktop";
import { applyTheme } from "./theme";
import "./styles.css";

applyTheme();
routeExternalLinks();

/** In the desktop window the UI is built into Bapu.exe: it talks to the Python service the
 *  shell starts (on a free port), and sets that service's runtime up when it is missing. */
function Root() {
  const [svc, setSvc] = useState<Service | null>(null);
  useEffect(() => {
    if (!inDesktop()) return;
    invoke<Service>("service_status").then(setSvc).catch(() => {});
    return onDesktopEvent<Service>("service", setSvc);
  }, []);
  if (!inDesktop()) return <App />; // a browser on `bapu serve`: same origin
  if (!svc) return null;
  if (svc.state === "ready") setApiBase(`http://127.0.0.1:${svc.port}`);
  return <App service={svc} />;
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Root />
  </StrictMode>,
);
