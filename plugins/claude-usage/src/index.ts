import { readFile } from "node:fs/promises"
import { homedir } from "node:os"
import { join } from "node:path"
import { Plugin } from "@opencode/plugin"
import { ClaudeUsage, type Accounts, type Limits, type UsageWindow } from "./rpc.js"

const STORAGE_KEY = "limits"
const ROUTER = "http://localhost:20128"
const PASSWORD_FILE = join(homedir(), ".config/opencode/9router.password")

function read(headers: Headers, name: "5h" | "7d"): UsageWindow | undefined {
  const utilization = Number(headers.get(`anthropic-ratelimit-unified-${name}-utilization`))
  const reset = Number(headers.get(`anthropic-ratelimit-unified-${name}-reset`))
  if (!Number.isFinite(utilization) || !Number.isFinite(reset) || !headers.has(`anthropic-ratelimit-unified-${name}-reset`)) return
  return {
    utilization,
    reset: reset * 1000,
    status: headers.get(`anthropic-ratelimit-unified-${name}-status`) ?? "allowed",
  }
}

let cookie: string | undefined

async function login() {
  const password = (await readFile(PASSWORD_FILE, "utf8")).trim()
  const response = await fetch(`${ROUTER}/api/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password }),
  })
  const token = response.headers.get("set-cookie")?.match(/auth_token=[^;]+/)?.[0]
  if (!token) throw new Error(`9router login failed (${response.status})`)
  cookie = token
}

// Each log line: "date | model | PROVIDER | account | input | output | status", newest first.
async function accounts(): Promise<Accounts> {
  if (!cookie) await login()
  let response = await fetch(`${ROUTER}/api/usage/request-logs`, { headers: { cookie: cookie! } })
  if (response.status === 401) {
    await login()
    response = await fetch(`${ROUTER}/api/usage/request-logs`, { headers: { cookie: cookie! } })
  }
  const lines = (await response.json()) as string[]
  const result: Accounts = {}
  for (const line of lines) {
    const [, , provider, account] = line.split(" | ")
    if (provider === "CLAUDE") result.claude ??= account
    if (provider === "CODEX") result.openai ??= account
    if (result.claude && result.openai) break
  }
  return result
}

export default Plugin.define({
  id: "claude-usage",
  async setup(ctx) {
    let limits: Limits = ((await ctx.storage.get(STORAGE_KEY)) as Limits | undefined) ?? { updated: 0 }
    let checked = 0

    await ctx.rpc.register(ClaudeUsage, {
      limits: async () => {
        if (Date.now() - checked > 5_000) {
          checked = Date.now()
          limits = { ...limits, accounts: await accounts().catch(() => limits.accounts) }
        }
        return limits
      },
    })

    await ctx.session.hook("http.response", (event) => {
      const session = read(event.response.headers, "5h")
      const weekly = read(event.response.headers, "7d")
      if (!session && !weekly) return
      limits = { ...limits, updated: Date.now(), session: session ?? limits.session, weekly: weekly ?? limits.weekly }
      void ctx.storage.set(STORAGE_KEY, limits as never)
    })
  },
})
