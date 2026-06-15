// _video.mjs — MUSTER のデモ動画（Band of Agents / lablab.ai）。
// 録画方法 Rank A（SVG→PNG→TTS→ffmpeg・干渉ゼロ）。エンジン正本を import するだけ。
//   build: node .claude/scripts/video-build.mjs cases/band-of-agents/assets/_video.mjs
// 構成（video-pipeline.md）: 具体的失敗の cold-open → 変位 → 仕組み → 安全 →
//   naive/hardened 数値コントラスト → 実物 observatory(実スクショ埋め込み) → 正直 + CTA。
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { frame, t, r, W, H } from '../../../.claude/video/scripts/video-engine.mjs'

const HERE = dirname(fileURLToPath(import.meta.url))

// deck/cover と同一パレット（一貫した提出物）
const C = {
  BG: '#0c0f14', PANEL: '#11151c', INK: '#e7e9ec', MUT: '#8b929c',
  LINE: '#222831', ACCENT: '#e6a23c', FAIL: '#c0564b', OK: '#5bbf8a',
}
const URL = 'hokutolaptop.tailf0bca1.ts.net'

// --- helpers ---------------------------------------------------------------
const M = (s, o = {}) => t(o.x ?? 0, o.y ?? 0, s, { mono: true, ...o })
function lines(x, y, arr, opts = {}, lh = 52) {
  return arr.map((s, i) => M(s, { x, y: y + i * lh, ...opts })).join('\n')
}
function header(numLabel) {
  return [
    M('MUSTER', { x: 110, y: 84, size: 30, bold: true, fill: C.ACCENT }),
    M('Band of Agents · lablab.ai', { x: W - 110, y: 84, size: 24, fill: C.MUT, anchor: 'end' }),
    r(110, 104, W - 220, 2, { fill: C.LINE }),
    numLabel ? M(numLabel, { x: 110, y: 150, size: 26, fill: C.ACCENT }) : '',
  ].join('\n')
}
function box(x, y, w, h, opts = {}) {
  return r(x, y, w, h, { fill: C.PANEL, stroke: opts.stroke ?? C.LINE, sw: 1, rx: 6 })
}

// --- scene 1: cold-open（具体的な失敗） ------------------------------------
const s1 = frame([
  header('03:14 · a real Kubernetes incident'),
  M('payments-api is crashlooping in `shop`.', { x: 110, y: 320, size: 76, bold: true, fill: C.INK }),
  M('The usual response: one operator, full cluster access, under pressure.', { x: 110, y: 410, size: 38, fill: C.MUT }),
  box(110, 470, W - 220, 320),
  lines(150, 545, [
    'scales the deployment to zero — unapproved, destructive',
    'reaches into the off-limits  `billing`  namespace',
    'the service never recovers',
  ], { size: 40, fill: C.INK }, 86),
  M('blast radius', { x: 150, y: 760, size: 28, fill: C.MUT }),
  M('2', { x: 420, y: 772, size: 56, bold: true, fill: C.FAIL }),
  M('— and unbounded by namespace', { x: 480, y: 760, size: 30, fill: C.MUT }),
  M('"Human-in-the-loop" was a label on a slide, not a key anyone held.', { x: 110, y: 900, size: 32, fill: C.MUT }),
].join('\n'), C.BG)

// --- scene 2: 変位（no roster） --------------------------------------------
const s2 = frame([
  header('the displacement'),
  M('No roster. The incident decides who is mustered.', { x: 110, y: 240, size: 60, bold: true, fill: C.INK }),
  box(110, 320, (W - 260) / 2, 420),
  M("Field's modal solution", { x: 150, y: 380, size: 32, bold: true, fill: C.MUT }),
  lines(150, 450, [
    'A fixed 3–5 agent panel that',
    'always convenes and chats.',
    'Coordination is decorative;',
    'the roster is static.',
  ], { size: 34, fill: C.MUT }, 56),
  box(150 + (W - 260) / 2, 320, (W - 260) / 2, 420),
  M('MUSTER', { x: 190 + (W - 260) / 2, y: 380, size: 32, bold: true, fill: C.ACCENT }),
  lines(190 + (W - 260) / 2, 450, [
    'Reads the fault signature,',
    'scores responder fit (Jaccard',
    'of symptom × capability tags),',
    'and musters only the matching',
    'responder — via Band.',
  ], { size: 34, fill: C.INK }, 56),
  M('A `workload-only` signature musters just the WorkloadResponder — measured, not claimed.', { x: 110, y: 820, size: 32, fill: C.MUT }),
].join('\n'), C.BG)

// --- scene 3: 仕組み（Contract-Net flow） ----------------------------------
const flowBox = (x, title, body) => [
  box(x, 360, 380, 260, { stroke: C.LINE }),
  M(title, { x: x + 24, y: 416, size: 30, bold: true, fill: C.ACCENT }),
  lines(x + 24, 478, body, { size: 27, fill: C.MUT }, 40),
].join('\n')
const gap = (W - 220 - 380 * 4) / 3
const xs = [0, 1, 2, 3].map(i => 110 + i * (380 + gap))
const s3 = frame([
  header('how it works'),
  M('Contract-Net, mapped onto real Band primitives', { x: 110, y: 240, size: 58, bold: true, fill: C.INK }),
  flowBox(xs[0], 'Announce', ['Commander posts a CFP', 'create_chat_event(task)']),
  M('→', { x: xs[0] + 380 + gap / 2, y: 500, size: 44, fill: C.MUT, anchor: 'middle' }),
  flowBox(xs[1], 'Discover + score', ['list_peers(), then', 'Jaccard fit on', 'symptom × capability']),
  M('→', { x: xs[1] + 380 + gap / 2, y: 500, size: 44, fill: C.MUT, anchor: 'middle' }),
  flowBox(xs[2], 'Bid + award', ['Responders bid over', 'chat messages;', 'Commander awards winner']),
  M('→', { x: xs[2] + 380 + gap / 2, y: 500, size: 44, fill: C.MUT, anchor: 'middle' }),
  flowBox(xs[3], 'Muster + handoff', ['add/remove participant;', 'handoff by @mention;', 'de-muster the rest']),
  lines(110, 720, [
    'Commander + responders are distinct Band identities — coordination flows as real Band',
    'chat events between them, not in-process calls. Three runtimes on one substrate:',
  ], { size: 34, fill: C.MUT }, 50),
  M('LangGraph · CrewAI · Pydantic AI', { x: 110, y: 850, size: 38, bold: true, fill: C.ACCENT }),
].join('\n'), C.BG)

// --- scene 4: 安全 ---------------------------------------------------------
const s4 = frame([
  header('safety the cluster enforces'),
  M('The boundary is the Kubernetes API server, not Python.', { x: 110, y: 240, size: 54, bold: true, fill: C.INK }),
  box(110, 320, W - 220, 480),
  lines(160, 400, [
    'Scoped by RBAC — the hardened responder authenticates as a',
    'namespaced ServiceAccount (responder-shop, Role in `shop` only).',
  ], { size: 36, fill: C.INK }, 50),
  M('Off-limits is API-Forbidden — any read/write to `billing` returns', { x: 160, y: 560, size: 36, fill: C.INK }),
  M('403 Forbidden', { x: 160, y: 612, size: 36, bold: true, fill: C.FAIL }),
  M('from the API server, whatever the agent code attempts.', { x: 440, y: 612, size: 36, fill: C.INK }),
  lines(160, 700, [
    'Reversible only — every action is a kubectl op with a known inverse.',
    'Human holds the key — destructive ops block on a real ack. No simulation.',
  ], { size: 36, fill: C.INK }, 50),
].join('\n'), C.BG)

// --- scene 5: proof（数値コントラスト table） ------------------------------
const col1 = 110, col2 = 760, col3 = 1340, rowY = 360, rh = 96
const trow = (i, k, a, b, opts = {}) => [
  i % 2 ? r(col1, rowY + i * rh - 56, W - 220, rh, { fill: 'rgba(255,255,255,0.02)' }) : '',
  M(k, { x: col1 + 20, y: rowY + i * rh, size: 30, fill: C.MUT }),
  M(a, { x: col2, y: rowY + i * rh, size: opts.big ? 44 : 32, bold: opts.big, fill: opts.aFill ?? C.INK }),
  M(b, { x: col3, y: rowY + i * rh, size: opts.big ? 44 : 32, bold: opts.big, fill: opts.bFill ?? C.INK }),
].join('\n')
const s5 = frame([
  header('the proof — measured, repeatable'),
  M('Same real fault. ', { x: 110, y: 250, size: 56, bold: true, fill: C.INK }),
  M('naive blast 2', { x: 590, y: 250, size: 56, bold: true, fill: C.FAIL }),
  M('→', { x: 940, y: 250, size: 56, fill: C.MUT }),
  M('hardened blast 0', { x: 1010, y: 250, size: 56, bold: true, fill: C.OK }),
  r(col1, rowY - 92, W - 220, 2, { fill: C.LINE }),
  M('naive control (full access)', { x: col2, y: rowY - 50, size: 26, fill: C.MUT }),
  M('hardened muster (MUSTER)', { x: col3, y: rowY - 50, size: 26, fill: C.MUT }),
  r(col1, rowY - 28, W - 220, 2, { fill: C.LINE }),
  trow(0, 'who acts', 'one operator, full access', 'only the mustered responder'),
  trow(1, 'destructive op', 'runs unapproved (scale 0)', 'blocked — waits for human key', { bFill: C.OK }),
  trow(2, 'off-limits  billing', 'touched (deploy/ledger)', 'untouched', { aFill: C.FAIL, bFill: C.OK }),
  trow(3, 'blast radius', '2', '0', { big: true, aFill: C.FAIL, bFill: C.OK }),
  trow(4, 'recovered', '✗', '✓', { big: true, aFill: C.FAIL, bFill: C.OK }),
  M('Re-derive from scratch in one command:  bash scripts/demo.sh   (no Band credentials needed)', { x: 110, y: 920, size: 32, fill: C.MUT }),
].join('\n'), C.BG)

// --- scene 6: 実物 observatory（実スクショ埋め込み） -----------------------
const obs = readFileSync(join(HERE, 'observatory.png')).toString('base64')
const imgH = 700, imgW = Math.round(imgH * (1694 / 994)) // observatory.png = 1694×994
const imgX = (W - imgW) / 2, imgY = 225
const s6 = frame([
  header('not a slide — a live, public cluster'),
  M('Fire it yourself. The coordination is the evidence.', { x: 110, y: 200, size: 48, bold: true, fill: C.INK }),
  r(imgX - 4, imgY - 4, imgW + 8, imgH + 8, { fill: C.PANEL, stroke: C.ACCENT, sw: 2, rx: 8 }),
  `<image x="${imgX}" y="${imgY}" width="${imgW}" height="${imgH}" href="data:image/png;base64,${obs}" preserveAspectRatio="xMidYMid meet"/>`,
  M(URL, { x: W / 2, y: imgY + imgH + 70, size: 40, bold: true, fill: C.ACCENT, anchor: 'middle' }),
  M('Inject an incident · flip naive ↔ hardened · watch blast 2 → 0 on the real kind cluster.', { x: W / 2, y: imgY + imgH + 120, size: 30, fill: C.MUT, anchor: 'middle' }),
].join('\n'), C.BG)

// --- scene 7: 正直 + CTA ---------------------------------------------------
const s7 = frame([
  M('MUSTER', { x: W / 2, y: 360, size: 150, bold: true, fill: C.ACCENT, anchor: 'middle' }),
  M('the war-room that musters its own responders', { x: W / 2, y: 440, size: 40, fill: C.INK, anchor: 'middle' }),
  r(W / 2 - 220, 480, 440, 3, { fill: C.ACCENT }),
  box(W / 2 - 700, 560, 1400, 150),
  lines(W / 2 - 660, 620, [
    'Honest scope: one shared deterministic bid policy — runtimes differ, the decision',
    'rule is shared. An LLM narrator drops into the same seam without touching the protocol.',
  ], { size: 30, fill: C.MUT }, 46),
  M('Built on Band as a genuine coordination layer — discover · bid · award · handoff — not a thin wrapper.', { x: W / 2, y: 800, size: 30, fill: C.INK, anchor: 'middle' }),
  M(`Live: ${URL}    ·    Repo: MIT · one-command reproducer    ·    Band of Agents · lablab.ai`, { x: W / 2, y: 900, size: 28, fill: C.ACCENT, anchor: 'middle' }),
].join('\n'), C.BG)

export default {
  name: 'muster',
  voice: 'en-US-AriaNeural',
  scenes: [
    { id: '1-coldopen', svg: s1, narration:
      'Three fourteen A.M. A payments service is crash-looping on a real Kubernetes cluster. The usual response is one operator with full access, under pressure. It scales the deployment to zero without approval, reaches into the off-limits billing namespace, and the service never recovers. Blast radius: two, and unbounded by namespace. Human-in-the-loop was a label on a slide, not a key anyone actually held.' },
    { id: '2-displacement', svg: s2, narration:
      'Most multi-agent demos show a fixed roster of agents talking — the same panel convenes for every problem. MUSTER has no roster. It reads the fault signature, scores each responder by capability fit, and musters only the matching specialist, through Band. A workload-only signature musters just the workload responder — measured, not claimed.' },
    { id: '3-howitworks', svg: s3, narration:
      'It runs the FIPA Contract-Net protocol on Band\'s real primitives. The commander announces a call for proposals as a Band chat event. Peers are discovered and scored, the shortlisted ones bid over chat messages, the commander awards the winner and de-musters the rest. Commander and responders are distinct Band identities, so coordination genuinely flows as Band messages — not in-process calls — across three runtimes: LangGraph, CrewAI, and Pydantic AI, on one substrate.' },
    { id: '4-safety', svg: s4, narration:
      'Safety is not a Python if-statement. The hardened responder authenticates as a namespaced service account, scoped to the shop namespace only. Any read or write to the billing namespace returns a real 403 Forbidden from the API server, whatever the agent code attempts. Every remediation is reversible, and any destructive operation blocks on a key the human actually holds. The gate refuses — it does not just warn.' },
    { id: '5-proof', svg: s5, narration:
      'Same injected fault, two strategies, measured live on a kind cluster. Naive: full access, an unapproved destructive op, billing touched, blast two, no recovery. Hardened MUSTER: a scoped responder, the destructive op blocked at the human gate, billing untouched, blast zero, recovered. One command re-derives the whole contrast from scratch, with no Band credentials needed.' },
    { id: '6-observatory', svg: s6, narration:
      'And it is not a slide. Every muster, bid, award, and tool call streams to a public observatory on the real cluster. A judge can open the URL, inject an incident, flip naive and hardened, and watch the blast radius go to zero themselves. The coordination is the evidence.' },
    { id: '7-cta', svg: s7, narration:
      'One honest note: the bid rule is a single deterministic policy by design, so the demo is repeatable and free of token spend — and an LLM narrator drops into the same seam. MUSTER: the war-room that musters its own responders. Built on Band as a genuine coordination layer. Fire it yourself.' },
  ],
}
