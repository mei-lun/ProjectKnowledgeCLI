// project-kb:pi-extension:start
import { Type } from "typebox";
import { spawnSync } from "node:child_process";

const TOOL_NAMES = ["knowledge_status", "knowledge_context", "knowledge_search", "knowledge_get", "knowledge_impact"];
const command = process.platform === "win32" ? "project-kb.cmd" : "project-kb";

function callProjectKb(name: string, args: Record<string, unknown>) {
  const request = JSON.stringify({
    jsonrpc: "2.0",
    id: 1,
    method: "tools/call",
    params: { name, arguments: args },
  }) + "\n";
  const result = spawnSync(command, ["mcp", "--project", process.cwd()], {
    cwd: process.cwd(),
    input: request,
    encoding: "utf8",
    shell: true,
    windowsHide: true,
  });
  if (result.status !== 0) throw new Error(String(result.stderr || "project-kb failed").trim());
  const line = String(result.stdout || "").split(/\r?\n/).find(Boolean);
  if (!line) throw new Error("project-kb returned no MCP response");
  const response = JSON.parse(line);
  if (response.error) throw new Error(response.error.message || "project-kb MCP error");
  return response.result?.structuredContent ?? response.result?.content?.[0]?.text ?? response.result;
}

export default function projectKnowledgeExtension(pi: any) {
  const existing = new Set(pi.getAllTools().map((tool: any) => tool.name));
  const definitions: Record<string, any> = {
    knowledge_status: { parameters: Type.Object({}) },
    knowledge_context: { parameters: Type.Object({ task: Type.String(), maxTokens: Type.Optional(Type.Integer()) }) },
    knowledge_search: { parameters: Type.Object({ query: Type.String(), limit: Type.Optional(Type.Integer()) }) },
    knowledge_get: { parameters: Type.Object({ id: Type.String() }) },
    knowledge_impact: { parameters: Type.Object({ files: Type.Optional(Type.Array(Type.String())), symbols: Type.Optional(Type.Array(Type.String())) }) },
  };
  for (const name of TOOL_NAMES) {
    if (existing.has(name)) continue;
    pi.registerTool({
      name,
      label: name,
      description: "Project Knowledge source-traceable project context tool.",
      parameters: definitions[name].parameters,
      async execute(_toolCallId: string, params: Record<string, unknown>) {
        const value = callProjectKb(name, params);
        return { content: [{ type: "text", text: typeof value === "string" ? value : JSON.stringify(value, null, 2) }], details: value };
      },
    });
  }
}
// project-kb:pi-extension:end

