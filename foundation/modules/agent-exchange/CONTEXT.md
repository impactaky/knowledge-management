# Agent Exchange

一つのRequestと一つのResponseを交換し、明示したThreadだけを継続するモジュール。[Knowledge Management](../../CONTEXT.md)の語彙の一部をここに記述し、Task / OrderとRequest / Threadを同じモデル内の異なる概念として区別する。外部Launcherのresource名とはcontractで対応づける。

Related decisions: [ADR 0001](../../docs/adr/0001-shared-delivery-and-agent-exchange.md)。

## Language

**Agent Exchange**:
Request、Response、Thread、Continuation Requestとそのidentity、参加role、exchange boundaryを担当する、Knowledge Managementの内部モジュール。
_Avoid_: 独立したBounded Context、一般chat、RPC、queue、session名

**Request**:
Requesting AgentがResponding Agentへ渡す一つの依頼。初回は自己完結し、Continuation Requestだけが明示Threadの会話文脈を前提にでき、どのRequestも一つの最終Responseで完結する。
_Avoid_: Task、Order、Message

**Thread**:
同じResponding Agentの会話と作業resourceへ、複数のRequest / Responseを一つずつ順序づける継続単位。推測ではなくThread IDで指定し、同時に一つのactive Requestだけを持つ。
_Avoid_: queue、chat、session、repositoryとの同一視

**Continuation Request**:
Thread IDで既存Threadへ渡す次のRequest。前Requestの完了後だけactiveになり、既存会話文脈を前提とする差分依頼にできる。
_Avoid_: Resume、Follow-up Message、並行Request

**Response**:
一つのRequestに対応するResponding Agentの最終応答。成功結果だけでなく完了できなかった理由も含むが、progressやstreamは含めない。
_Avoid_: Progress、Stream、Event、Review Boundary

**Request ID**:
一つのRequestとResponseを対応づける一意な識別子。内容、Agent、Thread、Taskのidentityは表さない。
_Avoid_: Thread ID、Task ID、Agent ID、Session ID

**Thread ID**:
一つのThreadを明示的に指定するRequest IDとは別の識別子。repository、worktree、Agentから継続先を推測する代わりに使う。
_Avoid_: Request ID、Task ID、Agent ID、Session ID

**依頼元Agent (Requesting Agent)**:
Requestを作り、Responseを受け取り、Threadでは次のContinuation Requestと最終的な終了判断を担うAgent。process上の親子関係は前提としない。
_Avoid_: 親Agent、Caller、Delivery Coordinator

**応答Agent (Responding Agent)**:
一つのRequestを受け取り一つのResponseを返すAgent。通常Requestではfreshに隔離され、明示Thread内だけ同じ会話を継続する。
_Avoid_: 子Agent、Worker、Task owner

**Launcher**:
初回Requestには新しいResponding Agentを割り当て、Continuation RequestにはThread resourceを検証して既存Agentへ渡す外部role。失敗時に継続をfresh Requestへ黙って置換しない。
_Avoid_: Agent Exchangeそのもの、Broker、Scheduler、Delivery Coordination

**交換ディレクトリ (Exchange Directory)**:
Request、Response、Thread metadataを同じmachine上の参加者が受け渡す共有境界。一般的なmailboxや異なるmachine間のqueueではない。
_Avoid_: Spool Root、Mailbox、Queue、Knowledge Package

**交換境界 (Exchange Boundary)**:
同一host・同一OS user・local filesystemだけを対象とし、一Requestにつき最大一Response、Threadは直列、file状態を正本とし、壊れた状態やresource消失をfail-closedで扱う制約。自動fallback、queue、retry、自動closeは設けない。
_Avoid_: network越しの連携、progress channel、自動再配送
