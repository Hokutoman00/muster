#!/usr/bin/env node
// P1 Band 疎通スパイク (L8: 新APIは最小実装で実機確認してから着手)
// Contract-Net の Band primitive 写像を「実アカウント・実REST」で1周通す:
//   list_peers(discovery) → create_chat(room) → add_participant(award路の前提)
//   → create_event(CFP announce, type=task) → message @mention(handoff)
//   → responder reads messages/next(bid受領) → responder posts bid(@mention back)
//   → commander reads messages(bid確認)
// 全 request/response を evidence JSON に保存する。
import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));

// --- creds ---
const envText = readFileSync("C:/Users/hokut/.credentials/band.env", "utf8");
const env = Object.fromEntries(
  envText.split(/\r?\n/).filter(l => l && !l.startsWith("#") && l.includes("="))
    .map(l => { const i = l.indexOf("="); return [l.slice(0, i).trim(), l.slice(i + 1).trim()]; })
);
const BASE = (env.THENVOI_REST_URL || "https://app.band.ai/").replace(/\/$/, "");
const COMMANDER = { key: env.BAND_COMMANDER_API_KEY, id: env.BAND_COMMANDER_AGENT_ID, handle: "hokutoman00/muster-commander" };
const WORKLOAD = { key: env.BAND_WORKLOAD_API_KEY, id: env.BAND_WORKLOAD_AGENT_ID, handle: "hokutoman00/workload-responder" };

const evidence = [];
async function call(label, who, method, path, body) {
  const url = BASE + path;
  const opts = { method, headers: { "X-API-Key": who.key, "Content-Type": "application/json" } };
  if (body) opts.body = JSON.stringify(body);
  const res = await fetch(url, opts);
  const text = await res.text();
  let json; try { json = JSON.parse(text); } catch { json = text; }
  const rec = { step: label, who: who.handle, method, path, status: res.status, request: body || null, response: json };
  evidence.push(rec);
  const ok = res.status >= 200 && res.status < 300;
  console.log(`${ok ? "OK " : "ERR"} [${res.status}] ${label} (${method} ${path})`);
  if (!ok) console.log("    ->", typeof json === "string" ? json.slice(0, 300) : JSON.stringify(json).slice(0, 300));
  return { ok, status: res.status, json };
}

(async () => {
  // 1. who am I (Commander)
  await call("commander.me", COMMANDER, "GET", "/api/v1/agent/me");

  // 2. discovery: list peers (registry access ON → 自owner配下が見えるはず)
  const peers = await call("commander.peers", COMMANDER, "GET", "/api/v1/agent/peers?page_size=50");
  const peerList = peers.json?.data || peers.json || [];
  console.log(`    peers seen: ${Array.isArray(peerList) ? peerList.length : "?"}`);

  // 3. create chat room (the muster room)
  const room = await call("commander.create_chat", COMMANDER, "POST", "/api/v1/agent/chats",
    { chat: { title: "MUSTER spike — incident-" + Date.now() } });
  const chatId = room.json?.data?.id || room.json?.id;
  if (!chatId) { console.log("FATAL: no chat id; aborting"); finish(); return; }
  console.log("    chat_id:", chatId);

  // 4. add Workload responder as participant (award路の前提: 候補をroomへ)
  await call("commander.add_participant", COMMANDER, "POST", `/api/v1/agent/chats/${chatId}/participants`,
    { participant: { participant_id: WORKLOAD.id, role: "member" } });

  // 5. announce CFP as a task event (Contract-Net: announce)
  await call("commander.announce_cfp", COMMANDER, "POST", `/api/v1/agent/chats/${chatId}/events`,
    { event: { message_type: "task", content: "CFP: pod CrashLoopBackOff after rollout in ns=payments. Seeking bids for reversible remediation.",
      metadata: { incident_id: "INC-spike", tags: ["workload", "rollout"], severity: "high", capability_required: ["kubectl.rollout.undo"] } } });

  // 6. @mention handoff to Workload responder (CFP delivery)
  await call("commander.mention_workload", COMMANDER, "POST", `/api/v1/agent/chats/${chatId}/messages`,
    { message: { content: `@${WORKLOAD.handle} can you bid on INC-spike (workload/rollout)? Reply with your fit score and proposed reversible action.`,
      mentions: [{ id: WORKLOAD.id, handle: WORKLOAD.handle, name: "workload-responder" }] } });

  // 7. responder reads its next message (bid受領経路)
  const next = await call("workload.messages_next", WORKLOAD, "GET", `/api/v1/agent/chats/${chatId}/messages/next`);
  const incomingMsgId = next.json?.data?.id || next.json?.id;
  // mark processing/processed if id present (Contract-Net ack lifecycle)
  if (incomingMsgId) {
    await call("workload.mark_processing", WORKLOAD, "POST", `/api/v1/agent/chats/${chatId}/messages/${incomingMsgId}/processing`);
    await call("workload.mark_processed", WORKLOAD, "POST", `/api/v1/agent/chats/${chatId}/messages/${incomingMsgId}/processed`);
  }

  // 8. responder posts a bid (Contract-Net: bid) @mention back to commander
  await call("workload.post_bid", WORKLOAD, "POST", `/api/v1/agent/chats/${chatId}/messages`,
    { message: { content: `@${COMMANDER.handle} BID INC-spike: fit=0.92. Proposed reversible action: kubectl rollout undo deploy/payments-api -n payments (revert to previous ReplicaSet). Fully reversible.`,
      mentions: [{ id: COMMANDER.id, handle: COMMANDER.handle, name: "muster-commander" }] } });

  // 9. commander reads messages (bid確認 = award判断の入力)
  await call("commander.read_messages", COMMANDER, "GET", `/api/v1/agent/chats/${chatId}/messages?limit=20`);

  finish();
})().catch(e => { console.error("SPIKE CRASH:", e); finish(); process.exit(1); });

function finish() {
  const out = join(__dirname, "p1-band-connectivity.evidence.json");
  writeFileSync(out, JSON.stringify({ ranAt: new Date().toISOString(), base: BASE, steps: evidence }, null, 2));
  const okCount = evidence.filter(e => e.status >= 200 && e.status < 300).length;
  console.log(`\n=== ${okCount}/${evidence.length} steps OK. evidence -> ${out}`);
}
