import { execSync } from "node:child_process";

export default function globalSetup() {
  execSync("uv run python -m app.demo.load --reset --all", { cwd: "../backend", stdio: "inherit" });
}
