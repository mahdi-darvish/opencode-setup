import { createEffect, createSignal, For, onCleanup, Show } from "solid-js"
import { createStore, reconcile } from "solid-js/store"
import { ComposerPill, MenuItem, Panel, type Plugin, type PanelTab } from "@opencode/plugin/gui"
import { ClaudeUsage } from "./rpc"
import type { WorkerJob, WorkersSnapshot } from "./workers"

const fmt = new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 })
const roles = ["developer", "verifier"]
const label = (s: string) => s === "developer" ? "Developer" : "Verifier"
const elapsed = (seconds: number) => seconds < 60 ? `${Math.floor(seconds)}s` : `${Math.floor(seconds / 60)}m ${Math.floor(seconds % 60)}s`

export const setupWorkers: Plugin.Setup = ctx => {
  const [snapshot, setSnapshot] = createStore<WorkersSnapshot>({ updated: 0, jobs: [], accounts: {} })
  const [directory, setDirectory] = createSignal("")
  const [error, setError] = createSignal("")
  const [now, setNow] = createSignal(Date.now())
  const [selected, setSelected] = createSignal("")
  let pending = false
  let sequence = 0
  const refresh = async () => {
    if (pending) return
    const session = ctx.sessions.current()
    if (!session) return
    pending = true
    const current = ++sequence
    const path = session.directory
    try {
      const response = await session.server.client.rpc(ClaudeUsage).workers({ directory: path, job: selected() }) as { payload: string }
      if (current === sequence && ctx.sessions.current()?.directory === path) {
        setSnapshot(reconcile(JSON.parse(response.payload), { key: "id" }))
        setDirectory(path)
        setError("")
      }
    } catch (e) { setError(e instanceof Error ? e.message : String(e)) }
    finally { pending = false }
  }
  const tick = setInterval(() => { setNow(Date.now()); void refresh() }, 1200)
  onCleanup(() => clearInterval(tick))
  createEffect(() => {
    const path = ctx.sessions.current()?.directory
    if (path !== directory()) { setSelected(""); void refresh() }
  })
  const jobs = () => directory() === ctx.sessions.current()?.directory ? snapshot.jobs : []
  const active = () => jobs().filter(j => j.status === "running")
  const selectedJob = () => jobs().find(j => j.id === selected()) ?? active()[0] ?? jobs()[0]
  const open = () => {
    const session = ctx.sessions.current()
    if (session) ctx.layout.open(`${ctx.id}:workers`, session)
  }
  const stop = async (job: WorkerJob) => {
    if (!window.confirm(`Stop ${label(job.worker)}? Its conversation and file changes will be kept.`)) return
    try { await ctx.client.rpc(ClaudeUsage).stopWorker({ id: job.id }); await refresh() }
    catch (e) { setError(String(e)) }
  }
  const percent = (value: number | undefined) => value == null ? "—" : `${Math.round(value * 100)}%`
  const ago = (seconds?: number) => !seconds ? "No data yet" : `${elapsed(Math.max(0, now() / 1000 - seconds))} ago`
  const resets = (seconds?: number) => !seconds ? "Reset not reported" : seconds * 1000 < now() ? "Reset time passed · refreshes on next task" : `Resets ${new Date(seconds * 1000).toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" })}`
  const meter = (used?: number, total = 100) => <div class="cw-meter"><span style={{ width: `${Math.min(100, Math.max(0, (used ?? 0) / total * 100))}%` }} /></div>
  const accounts = (role: string) => snapshot.accounts[role]

  const Accounts = () => <div class="cw-cards"><For each={roles}>{role => {
    const latest = () => jobs().find(j => j.worker === role)
    const limits = () => accounts(role)?.limits
    return <button class="cw-card" classList={{ "cw-selected": selectedJob()?.worker === role }} onClick={() => { const j = latest(); if (j) setSelected(j.id) }}>
      <div class="cw-between"><strong>{label(role)}</strong><span class={`cw-badge cw-${latest()?.status ?? "idle"}`}>{latest()?.status ?? "Idle"}</span></div>
      <div class="cw-secondary cw-wrap">{accounts(role)?.account ?? "Account appears after first task"}</div>
      <div class="cw-secondary">{latest()?.model ?? (role === "developer" ? "Opus default" : "Sonnet default")}</div>
      <div class="cw-between"><span>5-hour plan</span><b>{percent(limits()?.five_hour?.utilization)}</b></div>
      {meter(limits()?.five_hour?.utilization == null ? undefined : limits()!.five_hour.utilization! * 100)}
      <div class="cw-secondary">{resets(limits()?.five_hour?.resetsAt)}</div>
      <div class="cw-between"><span>Weekly plan</span><b>{percent(limits()?.seven_day?.utilization)}</b></div>
      {meter(limits()?.seven_day?.utilization == null ? undefined : limits()!.seven_day.utilization! * 100)}
      <div class="cw-secondary">{resets(limits()?.seven_day?.resetsAt)}</div>
      <div class="cw-secondary">Reported by Claude Code · {ago(accounts(role)?.limits_updated)}</div>
    </button>
  }}</For></div>

  const Metrics = (props: { job: WorkerJob }) => {
    const j = () => props.job
    const cached = () => j().tokens?.cache_read ?? 0
    const total = () => cached() + (j().tokens?.input ?? 0) + (j().tokens?.cache_write ?? 0)
    return <div class="cw-metrics">
      <div class="cw-between"><strong>Active context</strong><span>{j().context_tokens == null ? "Waiting for first response" : `${fmt.format(j().context_tokens!)} / ${fmt.format(j().context_budget ?? 256000)}`}</span></div>
      {meter(j().context_tokens, j().context_budget ?? 256000)}
      <div class="cw-secondary">Latest request input · 256k compaction budget, not the model’s full capacity.</div>
      <div class="cw-stat-grid">
        <span>{j().historical ? "History input" : "Task input"}<b>{fmt.format(total())}</b></span>
        <span>{j().historical ? "History output" : "Task output"}<b>{fmt.format(j().tokens?.output ?? 0)}</b></span>
        <span>Cache reused<b>{total() ? `${Math.round(cached() / total() * 100)}%` : "—"}</b></span>
        <span>Conversation<b>{j().resumed ? "Continued" : "New"}</b></span>
      </div>
      <details><summary>Session details</summary><div class="cw-secondary cw-wrap">Session: {j().session_id ?? "Starting"}<br />Claude Code: {j().cli_version ?? "—"}<br />Automatic compactions this task: {j().compactions ?? 0}</div></details>
    </div>
  }

  const Transcript = (props: { job: WorkerJob }) => {
    let scroller: HTMLDivElement | undefined
    const [follow, setFollow] = createSignal(true)
    createEffect(() => {
      const events = props.job.events
      const stamp = props.job.updated
      if (events && stamp && follow()) requestAnimationFrame(() => scroller?.scrollTo({ top: scroller.scrollHeight }))
    })
    return <>
      <div class="cw-between cw-transcript-heading"><strong>Live conversation & activity</strong><label><input type="checkbox" checked={follow()} onChange={e => setFollow(e.currentTarget.checked)} /> Follow</label></div>
      <div class="cw-transcript" ref={scroller}>
        <For each={props.job.events ?? []}>{event => <article class={`cw-event cw-event-${event.kind}`}>
          <div class="cw-between"><strong>{event.title}</strong><span class="cw-secondary">{event.status ?? new Date(event.time * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span></div>
          <Show when={event.kind === "tool"} fallback={<pre>{event.text}</pre>}>
            <details><summary>{event.text.slice(0, 160) || "Tool input"}</summary><pre>{event.text}</pre><Show when={event.output}><pre class="cw-output">{event.output}</pre></Show></details>
          </Show>
        </article>}</For>
        <Show when={props.job.status === "running"}><div class="cw-working"><span class="cw-pulse" />{props.job.activity ?? "Working"}</div></Show>
        <Show when={props.job.result}><article class="cw-event cw-report"><strong>Final report</strong><pre>{props.job.result}</pre></article></Show>
      </div>
    </>
  }

  const View = () => <div class="cw-root">
    <div class="cw-between"><div><h2>Claude workers</h2><div class="cw-secondary">Direct Claude Code · two accounts · live updates</div></div><button class="cw-button" onClick={() => void refresh()}>Refresh</button></div>
    <Show when={error()}><div class="cw-error">Updates unavailable: {error()} · Showing last known data.</div></Show>
    <Accounts />
    <Show when={jobs().length} fallback={<div class="cw-empty"><strong>No worker tasks in this project yet</strong><p>Choose the coordinator agent and ask for a task. The worker’s activity and conversation will appear here automatically.</p><p>Plan usage appears after each account’s first response; viewing this panel does not spend AI tokens.</p></div>}>
      <label class="cw-history">Task history<select value={selectedJob()?.id} onChange={e => setSelected(e.currentTarget.value)}><For each={jobs()}>{j => <option value={j.id}>{label(j.worker)} · {j.status} · {j.task?.slice(0, 65)}</option>}</For></select></label>
      <Show when={selectedJob()}>{job => <>
        <div class="cw-between"><span class="cw-wrap">{job().activity ?? job().status} · {elapsed((job().finished ?? now()/1000) - job().started)}</span><Show when={job().status === "running"}><button class="cw-button cw-stop" onClick={() => void stop(job())}>Stop</button></Show></div>
        <Show when={job().status === "running" && now()/1000 - (job().updated ?? job().started) > 20}><div class="cw-secondary">Waiting for Claude Code output · last activity {ago(job().updated)}. The request may still be processing.</div></Show>
        <Metrics job={job()} />
        <Transcript job={job()} />
      </>}</Show>
    </Show>
    <div class="cw-secondary">Latest 160 activity entries per task. Local history only; hidden reasoning is not collected. Long tool output is shortened. Plan limits refresh when the account responds, not while idle.</div>
  </div>

  const tab: PanelTab = { id: "workers", title: "Workers", closable: "compact", fallback: true }
  ctx.add(Panel, { id: "workers", region: "side", list: input => input.open.includes("workers") ? [tab] : [], render: () => <View /> })
  ctx.add(MenuItem, (): MenuItem => ({ menu: "session.panel", id: "workers", title: "Workers", icon: "status", description: "Live Claude activity, context and both accounts", opened: !!ctx.sessions.current() && ctx.layout.state(`${ctx.id}:workers`, ctx.sessions.current()!) !== "closed", run: open }))
  for (const role of roles) ctx.add(ComposerPill, {
    id: `worker-${role}`,
    show: input => {
      if (input.session.directory !== directory()) return undefined
      const current = jobs().find(j => j.worker === role && j.status === "running") ?? jobs().find(j => j.worker === role)
      const coordinator = input.session.server.data.session.get(input.session.id)?.agent === "coordinator"
      if (!current && !coordinator) return undefined
      const context = current?.context_tokens ? ` · ${fmt.format(current.context_tokens)} context` : ""
      return { label: current ? `${label(current.worker)} · ${current.status === "running" ? (current.activity ?? "Working").slice(0,60) : current.status}${context}` : `${label(role)} · ready`, icon: "status", tone: current?.status === "failed" ? "danger" : current?.status === "done" ? "success" : undefined, run: () => { if (current) setSelected(current.id); open() } }
    },
  })
  // Show the live view on task start; selecting Usage later will not be overridden.
  let openedJob = ""
  createEffect(() => {
    const job = active()[0]
    if (job && job.id !== openedJob) { openedJob = job.id; setSelected(job.id); open() }
  })
}
