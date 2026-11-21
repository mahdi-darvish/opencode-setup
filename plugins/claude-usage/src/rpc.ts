import { Rpc } from "@opencode/plugin/rpc"

const window = {
  type: "object",
  properties: {
    utilization: { type: "number" },
    reset: { type: "number" },
    status: { type: "string" },
  },
  required: ["utilization", "status"],
  additionalProperties: false,
} as const

const plan = {
  type: "object",
  properties: {
    account: { type: "string" },
    session: window,
    weekly: window,
  },
  required: ["account"],
  additionalProperties: false,
} as const

export const ClaudeUsage = Rpc.define({
  id: "claude-usage",
  methods: {
    workers: {
      input: { type: "object", properties: { directory: { type: "string" }, job: { type: "string" } }, required: ["directory"], additionalProperties: false },
      output: { type: "object", properties: { payload: { type: "string" } }, required: ["payload"], additionalProperties: false },
    },
    stopWorker: {
      input: { type: "object", properties: { id: { type: "string" } }, required: ["id"], additionalProperties: false },
      output: { type: "object", properties: { ok: { type: "boolean" } }, required: ["ok"], additionalProperties: false },
    },
    limits: {
      input: { type: "object", additionalProperties: false },
      output: {
        type: "object",
        properties: {
          updated: { type: "number" },
          claude: plan,
          openai: plan,
          error: { type: "string" },
        },
        required: ["updated"],
        additionalProperties: false,
      },
    },
  },
  events: {},
})

export interface UsageWindow {
  utilization: number
  reset?: number
  status: string
}

export interface Plan {
  account: string
  session?: UsageWindow
  weekly?: UsageWindow
}

export interface Limits {
  updated: number
  claude?: Plan
  openai?: Plan
  error?: string
}
