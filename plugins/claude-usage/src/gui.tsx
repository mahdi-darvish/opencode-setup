import { createMemo, createSignal, onCleanup, Show } from "solid-js"
import { lastAssistantWithUsage } from "@opencode/client/solid"
import { createLatest, MenuItem, Panel, Plugin, Style, type MountedSession, type PanelTab } from "@opencode/plugin/gui"
import { ClaudeUsage, type Limits, type UsageWindow } from "./rpc"
import css from "./panel.css?inline"
import { setupWorkers } from "./workers-gui"

const number = new Intl.NumberFormat("en-US")
const compact = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 })

function tone(percent: number) {
  if (percent >= 90) return "danger"
  if (percent >= 70) return "warning"
  return "ok"
}

function resetsIn(at: number | undefined, now: number) {
  if (!at) return "Not started"
  const minutes = Math.max(0, Math.round((at - now) / 60_000))
  if (minutes < 60) return `Resets in ${minutes} min`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `Resets in ${hours} hr ${minutes % 60} min`
  return `Resets ${new Date(at).toLocaleString("en-US", { weekday: "short", hour: "numeric", minute: "2-digit" })}`
}

function Bar(props: { percent: number }) {
  return (
    <div class="cu-bar">
      <div class={`cu-fill cu-${tone(props.percent)}`} style={{ width: `${Math.min(100, Math.max(0, props.percent))}%` }} />
    </div>
  )
}

function UsageRow(props: { title: string; window?: UsageWindow; now: number }) {
  const percent = () => Math.round((props.window?.utilization ?? 0) * 100)
  return (
    <div class="cu-row">
      <div class="cu-row-head">
        <span class="cu-label">{props.title}</span>
        <Show when={props.window} fallback={<span class="cu-muted">No data yet</span>}>
          <span class="cu-value">{percent()}% used</span>
        </Show>
      </div>
      <Bar percent={percent()} />
      <Show when={props.window}>
        {(window) => (
          <div class="cu-sub">
            <Show when={window().status !== "allowed"} fallback={resetsIn(window().reset, props.now)}>
              <span class="cu-danger-text">Limit reached · {resetsIn(window().reset, props.now)}</span>
            </Show>
          </div>
        )}
      </Show>
    </div>
  )
}

function UsageView(props: { session: MountedSession }) {
  const [now, setNow] = createSignal(Date.now())
  const timer = setInterval(() => setNow(Date.now()), 30_000)
  onCleanup(() => clearInterval(timer))

  const data = () => props.session.server.data
  const info = () => data().session.get(props.session.id)
  const messages = () => data().session.message.list(props.session.id)
  const step = createMemo(() => lastAssistantWithUsage(messages(), info()?.revert?.messageID))

  const model = createMemo(() => {
    const ref = step()?.model ?? info()?.model
    if (!ref) return undefined
    const list = data().location.model.list(props.session.location) ?? []
    return list.find((item) => item.providerID === ref.providerID && item.id === ref.id)
  })

  const used = () => {
    const tokens = step()?.tokens
    if (!tokens) return 0
    return tokens.input + tokens.output + tokens.reasoning + tokens.cache.read + tokens.cache.write
  }
  const limit = () => model()?.limit.context ?? 0
  const percent = () => (limit() ? (used() / limit()) * 100 : 0)
  const isClaude = () => (step()?.model.id ?? info()?.model?.id ?? "").includes("claude")

  const counts = createMemo(() => {
    let user = 0
    let assistant = 0
    for (const message of messages()) {
      if (message.type === "user") user++
      if (message.type === "assistant") assistant++
    }
    return { user, assistant }
  })

  const total = () => info()?.tokens

  const limits = createLatest(
    () => `${props.session.server.id}:${Math.floor(now() / 30_000)}:${step()?.id ?? ""}`,
    async () => (await props.session.server.client.rpc(ClaudeUsage).limits({})) as Limits,
  )
  const plan = () => (isClaude() ? limits.latest?.claude : limits.latest?.openai)
  const account = () => plan()?.account

  return (
    <div class="cu-root">
      <section class="cu-section">
        <h3 class="cu-heading">Context window</h3>
        <div class="cu-big">
          <span class="cu-big-value">{Math.round(percent())}%</span>
          <span class="cu-muted">used</span>
        </div>
        <Bar percent={percent()} />
        <div class="cu-sub">
          {compact.format(used())} of {compact.format(limit())} tokens · {compact.format(Math.max(0, limit() - used()))} left
        </div>
        <Show when={percent() >= 80}>
          <div class="cu-hint">Getting full. Run /compact to summarize and free up space.</div>
        </Show>
      </section>

      <section class="cu-section">
        <div class="cu-row-head">
          <h3 class="cu-heading">Plan usage limits</h3>
          <Show when={account()}>
            {(name) => (
              <span class="cu-account" title="9router account handling your requests">
                <span class="cu-dot" />
                {name()}
              </span>
            )}
          </Show>
        </div>
        <UsageRow title="Current session" window={plan()?.session} now={now()} />
        <UsageRow title="Weekly limit" window={plan()?.weekly} now={now()} />
        <Show
          when={!limits.latest?.error}
          fallback={<div class="cu-foot cu-danger-text">Couldn't reach 9router: {limits.latest?.error}</div>}
        >
          <Show when={limits.latest?.updated}>
            {(updated) => {
              const minutes = () => Math.max(0, Math.round((now() - updated()) / 60_000))
              return <div class="cu-foot">Updated {minutes() < 1 ? "just now" : `${minutes()} min ago`}</div>
            }}
          </Show>
        </Show>
      </section>

      <section class="cu-section">
        <h3 class="cu-heading">This conversation</h3>
        <dl class="cu-list">
          <dt>Model</dt>
          <dd>{model()?.name ?? step()?.model.id ?? "—"}</dd>
          <dt>Account</dt>
          <dd>{account() ?? "—"}</dd>
          <dt>Messages</dt>
          <dd>
            {counts().user} from you · {counts().assistant} replies
          </dd>
          <dt>Tokens sent</dt>
          <dd>{number.format(total()?.input ?? 0)}</dd>
          <dt>Tokens received</dt>
          <dd>{number.format((total()?.output ?? 0) + (total()?.reasoning ?? 0))}</dd>
          <Show when={(total()?.cache.read ?? 0) + (total()?.cache.write ?? 0) > 0}>
            <dt>Cached</dt>
            <dd>{number.format(total()?.cache.read ?? 0)}</dd>
          </Show>
        </dl>
      </section>
    </div>
  )
}

export default Plugin.define({
  setup(ctx) {
    ctx.add(Style, css)
    setupWorkers(ctx)

    const tab: PanelTab = { id: "main", title: "Usage", closable: "compact", fallback: true }

    ctx.add(Panel, {
      id: "main",
      region: "side",
      list: (input) => (input.open.includes("main") ? [tab] : []),
      render: (props) => (
        <Show when={props.session} keyed>
          {(session) => <UsageView session={session} />}
        </Show>
      ),
    })

    ctx.add(MenuItem, (): MenuItem => {
      const session = ctx.sessions.current()
      return {
        menu: "session.panel",
        id: "open",
        title: "Usage",
        icon: "status",
        description: "Context and plan limits",
        opened: !!session && ctx.layout.state(`${ctx.id}:main`, session) !== "closed",
        run: () => {
          if (session) ctx.layout.open(`${ctx.id}:main`, session)
        },
      }
    })
  },
})
