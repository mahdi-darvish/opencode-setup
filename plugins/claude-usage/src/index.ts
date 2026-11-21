import { readFile } from "node:fs/promises"
import { homedir } from "node:os"
import { join } from "node:path"
import { Plugin } from "@opencode/plugin"
import { ClaudeUsage, type Limits, type Plan, type UsageWindow } from "./rpc.js"
import { workerSnapshot, stopWorker } from "./workers.js"

const ROUTER = "http://localhost:20128"
const PASSWORD_FILE = join(homedir(), ".config/opencode/9router.password")
const CACHE_MS = 30_000

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

// 9router's dashboard API. Logs in again whenever the session has expired (401 or a redirect to the login page).
async function router<T>(path: string): Promise<T> {
  for (const attempt of [0, 1]) {
    if (!cookie || attempt === 1) await login()
    const response = await fetch(`${ROUTER}${path}`, { headers: { cookie: cookie! }, redirect: "manual" })
    if (response.ok) return (await response.json()) as T
    if (attempt === 1) throw new Error(`9router ${path} failed (${response.status})`)
  }
  throw new Error("unreachable")
}

interface Connection {
  id: string
  provider: string
  name?: string
  email?: string
}

interface Quota {
  used: number
  total: number
  resetAt: string | null
}

function toWindow(quota: Quota | undefined): UsageWindow | undefined {
  if (!quota || !quota.total) return
  const reset = quota.resetAt ? Date.parse(quota.resetAt) : undefined
  return {
    utilization: quota.used / quota.total,
    reset: Number.isFinite(reset) ? reset : undefined,
    status: quota.used >= quota.total ? "rejected" : "allowed",
  }
}

// Claude reports "session (5h)" / "weekly (7d)", Codex "session" / "weekly".
function pick(quotas: Record<string, Quota>, prefix: string) {
  const key = Object.keys(quotas).find((name) => name.toLowerCase().startsWith(prefix))
  return key ? quotas[key] : undefined
}

async function plan(connection: Connection | undefined): Promise<Plan | undefined> {
  if (!connection) return
  const usage = await router<{ quotas?: Record<string, Quota> }>(`/api/usage/${connection.id}`)
  const quotas = usage.quotas ?? {}
  return {
    account: connection.name || connection.email || connection.id.slice(0, 8),
    session: toWindow(pick(quotas, "session")),
    weekly: toWindow(pick(quotas, "weekly")),
  }
}

// The account that served the newest request per provider. Log lines: "date | model | PROVIDER | account | in | out | status".
async function load(): Promise<Limits> {
  const [logs, providers] = await Promise.all([
    router<string[]>("/api/usage/request-logs"),
    router<{ connections?: Connection[] } | Connection[]>("/api/providers"),
  ])
  const connections = Array.isArray(providers) ? providers : (providers.connections ?? [])
  const latest: Record<string, string> = {}
  for (const line of logs) {
    const [, , provider, account] = line.split(" | ")
    if (provider && account) latest[provider] ??= account
  }
  const find = (provider: string, label: string) => {
    const own = connections.filter((item) => item.provider === provider)
    return own.find((item) => (item.name || item.email) === latest[label]) ?? own[0]
  }
  const [claude, openai] = await Promise.all([plan(find("claude", "CLAUDE")), plan(find("codex", "CODEX"))])
  return { updated: Date.now(), claude, openai }
}

export default Plugin.define({
  id: "claude-usage",
  async setup(ctx) {
    let limits: Limits = { updated: 0 }
    let pending: Promise<Limits> | undefined

    await ctx.rpc.register(ClaudeUsage, {
      workers: async (input) => {
        const { directory, job } = input as { directory: string; job?: string }
        return { payload: JSON.stringify(await workerSnapshot(directory, job)) }
      },
      stopWorker: async (input) => stopWorker((input as { id: string }).id),
      limits: async () => {
        if (Date.now() - limits.updated < CACHE_MS) return limits
        pending ??= load()
          .then((next) => (limits = next))
          .catch((error: unknown) => ({ ...limits, error: error instanceof Error ? error.message : String(error) }))
          .finally(() => (pending = undefined))
        return pending
      },
    })
  },
})
