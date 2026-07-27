# hermes-peers-bridge

[English](README.md) | **한국어**

**[Hermes Agent](https://hermes-agent.nousresearch.com)를 위한 네트워크 간 피어 메시징 — 서로 다른 머신의 AI 에이전트들이 대화할 수 있게 합니다.**

여러 대의 Hermes 인스턴스(서로 다른 머신·네트워크)를 중앙 [claude-peers](https://github.com/louislva/claude-peers-mcp) 브로커 허브로 연결하는 Hermes Agent 플러그인입니다. 각 에이전트는 이름을 가진 피어로 등록되고, 별명으로 서로에게 메시지를 보낼 수 있습니다 — 클라우드 VM의 게이트웨이 에이전트가 노트북의 에이전트에게 로컬 작업을 요청하고, 그 답을 일반 대화 턴으로 받아볼 수 있습니다.

```
┌─────────┐      ┌──────────────────┐      ┌─────────┐
│ Agent A │◄────►│   Remote Hub     │◄────►│ Agent B │
│ (노트북) │      │ (브로커, HTTPS)   │      │  (VM)   │
└─────────┘      └──────────────────┘      └─────────┘
                        ▲
                        │
                  ┌─────────┐
                  │ Agent C │
                  │ (서버)   │
                  └─────────┘
```

## 누구를 위한 플러그인인가요?

- **여러 머신에서 Hermes Agent를 운영**하는 경우 (예: 노트북 + 홈서버 + 클라우드 VM) — 에이전트끼리 작업 위임, 질문, 상태 보고 등 협업이 필요할 때.
- 각 머신을 외부에 노출하지 않고 **네트워크를 넘어 에이전트 간 메시징**을 하고 싶을 때 — 허브 한 대만 공개 엔드포인트가 있으면 됩니다.
- 같은 머신 안에서 로컬 `claude-peers` 브로커를 쓰고 있고, 이를 여러 호스트로 확장하고 싶을 때.

요구 사항: 플러그인을 지원하는 Hermes Agent, Python 3.10+, 그리고 브로커를 돌릴 호스트 1대 (Bun/Node + 공개 HTTPS 엔드포인트 — 리버스 프록시나 Cloudflare Tunnel 뒤에 두면 됩니다).

## 주요 기능

- **이름 있는 피어** — 불투명한 ID 대신 사람이 읽을 수 있는 별명(`Laptop`, `HomeServer`, `CloudVM`)으로 식별하고, 별명 또는 ID로 전송.
- **게이트웨이/CLI 자동 구분** — 게이트웨이 프로세스는 원격 허브에, 대화형 CLI/TUI 세션은 로컬 브로커에 등록. 서브에이전트는 자동 제외.
- **실제 메시지 전달** — 수신한 피어 메시지를 게이트웨이 홈채널 세션에 일반 에이전트 턴으로 주입하므로, 받는 쪽 에이전트가 실제로 읽고 답장합니다 (최신·구버전 Hermes 게이트웨이 빌드 모두 지원).
- **중복 방지** — 메시지 ID 기반 중복 제거와 허브 방식에 맞춘 전달 처리로 같은 메시지가 두 번 처리되지 않습니다.
- **자가 복구 등록** — 하트비트 기반 생존 확인, 브로커 재시작·일시 장애 후 자동 재등록.

## 설치 방법

### 1. 허브 브로커 설치 (머신 1대)

업스트림 `claude-peers` 브로커는 모든 피어가 브로커 호스트에 있다고 가정합니다(PID를 로컬에서 확인). 네트워크 간 허브로 쓰려면 이 저장소의 패치판 `broker.remote-hub.ts`를 사용하세요 — PID 대신 하트비트 나이로 생존을 판단합니다:

```bash
git clone https://github.com/louislva/claude-peers-mcp
cd claude-peers-mcp
# 기본 브로커를 원격 허브 버전으로 교체
curl -sL https://raw.githubusercontent.com/insightflo/hermes-peers-bridge/main/broker.remote-hub.ts \
  -o broker.ts
```

환경 변수를 설정하고 서비스로 실행합니다 (systemd 등):

```bash
CLAUDE_PEERS_PORT=7899
CLAUDE_PEERS_DB=/var/lib/claude-peers/peers.db
CLAUDE_PEERS_HUB_TOKEN=<길고-무작위인-토큰-생성>
# 선택 사항; 이 시간(ms)보다 오래된 피어는 정리됩니다. 기본 90000.
CLAUDE_PEERS_STALE_TIMEOUT_MS=90000
```

7899 포트를 HTTPS로 노출한 뒤(리버스 프록시 / Cloudflare Tunnel) 확인:

```bash
curl -H "Authorization: Bearer $CLAUDE_PEERS_HUB_TOKEN" \
  https://your-broker.example.com/health
# → {"status":"ok","peers":0}
```

> 허브 토큰은 비밀로 유지하세요. `chmod 600` 권한의 env 파일에 저장하고, 절대 커밋하지 마세요.

### 2. 플러그인 설치 (모든 Hermes 머신)

플러그인 파일을 Hermes 플러그인 디렉토리에 복사합니다:

```bash
mkdir -p ~/.hermes/plugins/claude_peers_bridge
cd ~/.hermes/plugins/claude_peers_bridge
for f in __init__.py bridge.py schemas.py plugin.yaml; do
  curl -sLO https://raw.githubusercontent.com/insightflo/hermes-peers-bridge/main/$f
done
python3 -m py_compile bridge.py && echo OK
```

`~/.hermes/config.yaml`에서 활성화:

```yaml
plugins:
  enabled:
    - claude-peers-bridge
```

`~/.hermes/.env` 설정:

```bash
# 원격 허브 (게이트웨이 프로세스가 사용)
CLAUDE_PEERS_BROKER_URL=https://your-broker.example.com
CLAUDE_PEERS_BROKER_AUTH=Bearer <허브-토큰>
# 이 머신 피어의 고유한, 사람이 읽을 수 있는 이름
CLAUDE_PEERS_NAME=Laptop
```

### 3. 메시지 수신 배선 (게이트웨이 머신)

받는 쪽 에이전트가 피어 메시지를 실제로 **읽고 답장**할 수 있는지는 두 가지 설정에 달려 있습니다:

**a) 홈채널** — 수신한 피어 메시지는 게이트웨이의 홈채널 세션에 주입됩니다. 게이트웨이가 사용하는 플랫폼의 홈채널을 `~/.hermes/.env`에 설정하세요:

```bash
TELEGRAM_HOME_CHANNEL=<chat-id>     # 게이트웨이가 Telegram을 쓰는 경우
# 또는
SLACK_HOME_CHANNEL=<dm-channel-id>  # 게이트웨이가 Slack을 쓰는 경우
```

**b) 툴셋** — 받는 쪽 에이전트가 답장하려면 `claude-peers` 도구가 필요합니다. `~/.hermes/config.yaml`에서 해당 플랫폼의 툴셋 목록에 `claude-peers`를 추가하세요:

```yaml
platform_toolsets:
  slack:            # 또는 telegram, discord, ...
    - claude-peers
    # ...기존 항목들...
```

(b)가 없으면 에이전트가 메시지를 받아도 조용히 답장에 실패합니다 — 그 세션에 전송 도구가 없기 때문입니다.

### 4. 재시작 및 검증

```bash
hermes gateway restart          # 또는: systemctl --user restart hermes-gateway
```

아무 에이전트에서나 피어 목록을 확인하고 왕복 테스트를 보냅니다:

```text
claude_peers_list_peers()
claude_peers_send_message(to_id="CloudVM", message="ping — ACK 부탁해")
```

원격 에이전트가 홈채널 세션에서 메시지를 받고, 다음 폴링 주기(수 초) 안에 답장해야 정상입니다.

## 사용법

플러그인이 제공하는 에이전트 도구:

| 도구 | 용도 |
|------|------|
| `claude_peers_list_peers` | 살아있는 피어 목록 (별명, ID, 요약, 마지막 접속) |
| `claude_peers_send_message` | 별명 또는 ID로 피어에게 메시지 전송 |
| `claude_peers_check_messages` | 읽지 않은 메시지 수동 폴링 |
| `claude_peers_set_alias` | 이 피어의 별명 변경 |
| `claude_peers_set_summary` | 이 피어의 "지금 하는 일" 요약 갱신 |
| `claude_peers_bridge_status` | 등록 상태, 브로커 URL, 폴링 상태 확인 |

별명은 1–64자 ASCII (영문·숫자·공백·`.`·`_`·`@`·`-`)이며, 허브 전체에서 대소문자 구분 없이 고유해야 합니다. 수신 메시지에는 `from_alias`와 `from_id`가 모두 담깁니다.

## 아키텍처 노트

| 프로세스 | 브로커 대상 |
|---------|-----------|
| 게이트웨이 에이전트 (`_HERMES_GATEWAY=1`) | 원격 허브 (`CLAUDE_PEERS_BROKER_URL`) |
| CLI / TUI 에이전트 (`HERMES_INTERACTIVE=1`) | 로컬 브로커 (`127.0.0.1:7899`) |
| 서브에이전트 (kanban / delegate) | 등록 제외 |

- **브로커를 import 시점이 아닌 루프마다 해석** — 플러그인 모듈은 게이트웨이 env가 설정되기 전에 import되므로, 매 주기 재해석해야 게이트웨이가 원격 허브를 자동으로 인식합니다.
- **게이트웨이 주입 + 폴백** — 수신 메시지는 게이트웨이 wake 메커니즘으로 전달되며, `gateway.wake`가 없는 구버전 Hermes에서는 합성 메시지 이벤트 주입으로 폴백합니다. 어느 쪽이든 메시지는 실제 에이전트 턴이 됩니다.
- **허브 방식에 맞춘 전달 처리** — 원격 허브는 폴링 시점에 메시지를 전달 완료로 표시하고 `/mark-message-delivered` 엔드포인트가 없습니다. 브리지는 이 404를 재큐잉 대신 "이미 전달됨"으로 처리해 무한 재주입을 방지합니다.
- **커스텀 User-Agent** — Cloudflare가 Python urllib 기본 UA를 차단하므로 모든 요청에 `User-Agent: hermes-peers-bridge/1.0`을 사용합니다.
- **하트비트 기반 생존 판단** — 허브는 호스트 로컬 PID 확인 대신 `last_seen` 나이를 사용합니다. 죽은 등록에는 `reregister: true`를 반환해 브리지가 스스로 복구합니다.

## 문제 해결

| 증상 | 원인 / 해결 |
|------|-----------|
| 전송은 `ok: true`인데 답장이 영원히 안 옴 | 받는 머신이 구버전 `bridge.py` (주입 폴백 없음) → 그쪽 플러그인 업데이트 후 게이트웨이 재시작 |
| 피어가 메시지를 받는데 답장을 못 함 | 받는 머신의 `platform_toolsets`에 `claude-peers` 누락 |
| 메시지가 아예 주입되지 않음 | 받는 게이트웨이에 `*_HOME_CHANNEL` env 변수 누락 |
| CLI/TUI에서 보내면 `Peer X not found` | CLI 세션은 **로컬** 브로커와 통신합니다; 원격 피어는 게이트웨이 세션에서만 보입니다. 게이트웨이 피어를 경유하거나 게이트웨이 세션에서 전송하세요 |
| 같은 메시지가 반복 주입됨 | 404 허용 전달 처리가 없는 구버전 `bridge.py` → 플러그인 업데이트 |
| Cloudflare 뒤의 브로커가 요청을 거부 | 커스텀 User-Agent 헤더를 보내는 플러그인 버전인지 확인 |

## 라이선스

MIT (플러그인 코드). 브로커는 [claude-peers-mcp](https://github.com/louislva/claude-peers-mcp)를 기반으로 합니다 — 해당 저장소의 라이선스를 참고하세요.
