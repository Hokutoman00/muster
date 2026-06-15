# P1 — Band 疎通スパイク（実機確認）

実行: `node p1-band-connectivity.mjs`（creds は `~/.credentials/band.env`、コミット禁止）
結果: **11/11 steps OK**。証拠 = `p1-band-connectivity.evidence.json`（全 request/response）。
確信度: **確認済み**（このセッションで live REST に直接観測）。

## 何を証明したか — Contract-Net を Band primitive で1周

| Contract-Net 段 | Band 実呼び出し | 結果 |
|---|---|---|
| discovery | `GET /api/v1/agent/peers` | 200 / peers=4（data・network・workload responder + owner contact が可視）|
| room 開設 | `POST /api/v1/agent/chats` | 201 / chat_id 取得 |
| 候補招集 | `POST .../participants` | 201（responder を room へ）|
| announce(CFP) | `POST .../events` `message_type=task` | 201（metadata に tags/severity/capability_required）|
| handoff | `POST .../messages`（@mention）| 201 |
| bid 受領 | responder: `GET .../messages/next` | 200（CFP を受信）|
| ack ライフサイクル | responder: `.../{id}/processing` → `/processed` | 200 / 200 |
| bid | responder: `POST .../messages`（@mention back）| 201 |
| award 判断の入力 | commander: `GET .../messages` | 200（bid を読める）|

## 実機で判明した build 必須事実（推測でなく観測）

1. **auth = `X-API-Key: <agent_key>` の素 REST**。SDK 不要でフル操作可能（base `https://app.band.ai`）。各 Remote Agent は自分の key で別人格として動く。
2. **@mention は server 側で `@[[<agent_id>]]` に正規化される**。投稿時は handle 文字列 + `mentions:[{id,handle,name}]` を渡すが、保存後の content は id 参照トークンになる。→ observatory での表示・解決は id→handle マップで行う。
3. **discovery は registry_access ON で「同一 owner 配下の全 agent」が見える**（cross-owner はディレクトリ経由）。MUSTER は単一 owner 内なので peers で十分。
4. **messages/next が responder 側の作業キュー**。processing/processed で処理状態を遷移できる（= 観測可能な実行トレース。keystone「実物デモ」の素材）。
5. **events(message_type=task) が CFP の正本**。metadata が構造化データを運ぶ → fit scoring / award の機械可読入力に使える。

## Definition of Deep 残3項の充足

design.md §11 の「実機未確認」3項（Band の discovery / handoff / 実行トレースが本当に存在するか）を、上表の live 観測で充足。設計の前提（Contract-Net 写像）は仮説でなく実機で成立する。

## スパイクごとの注入シナリオ（数値が違って見える理由）

各スパイクは**独立した注入シナリオ**で別々のことを証明している。だから fit スコアや
namespace が spike 間で異なるのは想定通りで、不整合ではない:

| spike | 注入シナリオ | namespace | fit（例） | 何を証明 |
|---|---|---|---|---|
| p1 | 疎通確認用のサンプル CFP（pod CrashLoopBackOff） | `payments` | bid `fit=0.92` | Band REST 1周（announce→bid→read）が live で通る |
| p3/p4 | 本番の shop workload インシデント | `shop` | `workload=0.67 / data=0.00 / network=0.00` | 選択的 muster（fit>0 のみ招集）＋3ランタイム1ルール |

p1 の `fit=0.92`／`ns=payments` は**疎通スパイク専用の説明用シナリオ**であって、
コミットされた本番インシデント（p3/p4 の shop, `workload=0.67`）とは別物。
`verify_coordination.py` は各 spike をその spike 固有の不変条件でだけ検証する。

## 次（P2〜）

- responder の実体は thenvoi SDK アダプタ（LangGraph/CrewAI/Pydantic AI）で messages/next をポーリングし、kubectl 可逆アクションを実行 → tool_call/tool_result event を流す。
- commander は Anthropic アダプタで CFP→bid 集計→award（敗者を participants から remove）→@mention で実行委任。
- observatory は events/messages を購読（WS `wss://app.band.ai/api/v1/socket/websocket`）して公開 URL のタイムラインに描画。
