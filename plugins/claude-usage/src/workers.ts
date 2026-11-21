import { readdir, readFile, writeFile, mkdir } from "node:fs/promises"
import { homedir } from "node:os"
import { join, resolve } from "node:path"

const root = join(homedir(), ".local/share/claude-workers")

export interface WorkerEvent {
  id: string
  kind: string
  title: string
  text: string
  time: number
  status?: string
  output?: string
}
export interface WorkerJob {
  id: string
  worker: string
  account?: string
  directory: string
  status: string
  activity?: string
  task?: string
  result?: string
  session_id?: string
  model?: string
  cli_version?: string
  started: number
  updated?: number
  finished?: number
  context_tokens?: number
  context_budget?: number
  resumed?: boolean
  compactions?: number
  tokens?: { input: number; output: number; cache_read: number; cache_write: number }
  limits?: Record<string, { utilization?: number; resetsAt?: number; status?: string }>
  limits_updated?: number
  events?: WorkerEvent[]
  owner_pid?: number
  historical?: boolean
}
export interface WorkersSnapshot {
  updated: number
  jobs: WorkerJob[]
  accounts: Record<string, Pick<WorkerJob, "account" | "limits" | "limits_updated">>
  error?: string
}

export async function workerSnapshot(directory: string, selected?: string): Promise<WorkersSnapshot> {
  let names: string[] = []
  try { names = await readdir(join(root, "jobs")) } catch (error: any) {
    if (error.code !== "ENOENT") throw error
  }
  const loaded = await Promise.all(names.filter(n => /^[a-f0-9]{8}\.json$/.test(n)).map(async name => {
    try {
      const job: WorkerJob = JSON.parse(await readFile(join(root, "jobs", name), "utf8"))
      if (job.status === "running" && job.owner_pid) {
        try { process.kill(job.owner_pid, 0) } catch { job.status = "interrupted"; job.activity = "Bridge stopped; conversation saved" }
      }
      delete job.owner_pid
      return job
    } catch { return undefined }
  }))
  const all = loaded.filter((j): j is WorkerJob => !!j).sort((a,b) => b.started - a.started)
  const accounts: WorkersSnapshot["accounts"] = {}
  for (const role of ["developer", "verifier"]) {
    const latest = all.find(j => j.worker === role)
    const limits = all.filter(j => j.worker === role && j.limits).sort((a,b) => (b.limits_updated ?? 0) - (a.limits_updated ?? 0))[0]
    accounts[role] = { account: latest?.account, limits: limits?.limits, limits_updated: limits?.limits_updated }
  }
  const jobs = all.filter(j => resolve(j.directory) === resolve(directory)).slice(0,30)
  const shown = jobs.find(j => j.id === selected) ?? jobs.find(j => j.status === "running") ?? jobs[0]
  // History rows need only metadata. Keep polling payloads small and send one transcript.
  for (const job of jobs) {
    if (job !== shown) { delete job.events; delete job.result }
  }
  return { updated: Date.now(), jobs, accounts }
}

export async function stopWorker(id: string) {
  if (!/^[a-f0-9]{8}$/.test(id)) throw new Error("Invalid job ID")
  const job = JSON.parse(await readFile(join(root, "jobs", id + ".json"), "utf8"))
  if (job.status !== "running") return { ok: false }
  await mkdir(join(root, "jobs"), { recursive: true, mode: 0o700 })
  await writeFile(join(root, "jobs", id + ".cancel"), JSON.stringify({ requested: Date.now() / 1000 }), { mode: 0o600 })
  return { ok: true }
}
