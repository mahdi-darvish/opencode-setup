import { Rpc } from "@opencode/plugin/rpc"

const window = {
  type: "object",
  properties: {
    utilization: { type: "number" },
    reset: { type: "number" },
    status: { type: "string" },
  },
  required: ["utilization", "reset", "status"],
  additionalProperties: false,
} as const

export const ClaudeUsage = Rpc.define({
  id: "claude-usage",
  methods: {
    limits: {
      input: { type: "object", additionalProperties: false },
      output: {
        type: "object",
        properties: {
          updated: { type: "number" },
          session: window,
          weekly: window,
          accounts: {
            type: "object",
            properties: { claude: { type: "string" }, openai: { type: "string" } },
            additionalProperties: false,
          },
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
  reset: number
  status: string
}

export interface Limits {
  updated: number
  session?: UsageWindow
  weekly?: UsageWindow
  accounts?: Accounts
}

export interface Accounts {
  claude?: string
  openai?: string
}
