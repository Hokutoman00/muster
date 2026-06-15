# BAND-API-CONTRACT — build 着手用の実 API 契約（確認済み）
確認日: 2026-06-14 / 一次情報: https://docs.band.ai/llms.txt（+ core-concepts）/ 取得: browser-do（WebFetch は 403）
OpenAPI: https://docs.band.ai/openapi.json / .yaml（build 時に DL して型生成可）
MCP for AI clients: https://docs.band.ai/_mcp/server（Claude Code 等から Band を操作可）

> L8: ここはドキュメント上の契約確認（確認済み）。実 SDK スパイク（実際に agent を接続して room を作る最小疎通）は build 初手で行う＝「最小実装で実機確認してから本実装」。

## モデル（core-concepts より・確認済み）
- **Agent**: 定義（name/description/model/tools）。room 参加時に **Execution**（room スコープの隔離ランタイム・全状態追跡）が生成される。
  - **Remote agent**: 自前環境で任意フレームワーク（LangGraph/CrewAI/Anthropic…）＋ SDK 接続。runtime/LLM/デプロイは自分管理。
  - **Platform agent**: Band 上で prompt/model/tools を設定し Band が実行。
- **Chat Room**: 協調レイヤ。agent と人間が混在。**@mention ルーティング**（mention された agent だけ受信・処理）。agent 同士が @mention で delegate/handoff/collaborate。
- **Contacts & Discovery**: 同一 owner 内は相互可視。global agent は全員可視。cross-boundary は contact request（双方向同意）。
- **Execution**: room 毎の runtime インスタンス・全状態追跡 ← **協調状態の可視化の源泉**。

## build で使う primitive（Agent API = autonomous collaboration）
| 用途 | エンドポイント | 役割 |
|------|--------------|------|
| 自分の素性 | `agent-api/identity` get-agent-me | agent profile |
| **discovery** | `agent-api/peers` list-agent-peers | 招集候補の専門 agent を探す |
| **room 生成** | `agent-api/chats` create-agent-chat | インシデント room を作る |
| room 取得/一覧 | get-agent-chat / list-agent-chats | 状態取得 |
| **動的リクルート（核）** | `agent-api/participants` **add-agent-chat-participant** | 専門 agent を room に**その場で追加** |
| 解任 | remove-agent-chat-participant | 不要 agent を外す（組織が伸縮） |
| 参加者一覧 | list-agent-chat-participants | 現メンバー |
| **協調メッセージ** | `agent-api/messages` create-agent-chat-message | @mention 付きで発話・handoff |
| 受信ループ | get-agent-next-message / mark-processing / mark-processed / mark-failed | pull 型処理ループ |
| **構造化イベント（可視化）** | `agent-api/events` **create-agent-chat-event** | type: **task / tool_call / tool_result / thought / error** を emit |
| context 再水和 | `agent-api/context` get-agent-chat-context | 再起動時 |
| keep-alive | `agent-api/activity` report | 作業中表示 |
| 記憶 | `agent-api/memories` 🔒 | 永続記憶（任意） |

## build で使う primitive（Subscriptions API = WebSocket・observatory の源泉）
- **Agent 側**（各 responder agent が購読）:
  - Chat Room Channel: `message-created`（@mention 受信）
  - Room Participants Channel: `participant-added/removed`, `room-deleted`
  - Agent Rooms Channel: `room-added/removed`（自分が room に入れられた）
  - Agent Contacts Channel: contact request 系
- **Human 側**（observatory フロントが購読）:
  - Chat Room Channel: `message-created/updated/deleted`, **`event-created`（tool_call/tool_result/thought/error/task）** ← timeline 描画
  - Room Participants Channel: `participant-added/removed`, `room-deleted` ← **招集の live 可視化**
  - User Rooms Channel / User Contacts Channel

## SDK / 接続経路
- **Python SDK**（推奨）: framework adapter 公式提供（LangGraphAdapter / CrewAIAdapter / AnthropicAdapter / PydanticAIAdapter / ClaudeSDKAdapter / GoogleADKAdapter / Codex / OpenCode / Parlant）。setup: integrations/sdks/tutorials/setup。lifecycle: create→start→run→shutdown。
- **Custom Integration**（SDK 無し）: Request API(REST) + Subscriptions API(WebSocket) を直接。
- env: integrations/sdks/tutorials/environment-variables に API キー等。
- 認証: Band アカウント要（promo BANDHACK26）。build 初手で ~/.credentials/ に band があるか確認 → 無ければ作成（browser-do）。

## 設計への含意（確認済み事実 → 設計判断）
1. **動的リクルートは add-participant + list-peers で成立** → 固定ロスター不要。Contract-Net（announce=task event / bid=message / award=add-participant）を Band primitive に写像できる。
2. **task/tool_call/tool_result/thought イベント**が公式 → 「協調状態を live で見せる」keystone を**作り込みでなく platform 機能の可視化**で satisfy できる（薄いラッパでなく Band が coordination substrate である証拠にもなる）。
3. **@mention で handoff** → 専門間の引き継ぎが「ワークフロー内で Band 経由」（失格条件=thin wrapper を回避）。
4. **cross-framework adapter** → responder を別フレームワークで作れば軸1×軸4 を上積み。
5. **human を participant に** → 破壊操作の鍵を人間が握る（台帳B・WarRoom 先端に並ぶ）を Band ネイティブで。
