import { createRoot } from "react-dom/client";
import App from "./App";
import "./styles/fonts.css";
import "./styles/index.css";

const container = document.getElementById("root");
if (!container) throw new Error("缺少 #root 挂载点");

createRoot(container).render(<App />);
