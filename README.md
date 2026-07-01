# Wiki.js를 claude.ai에 MCP 커넥터로 연결하기 (v2 — 전체 기능판)

개인 홈서버(Ubuntu)의 Wiki.js를 claude.ai 커스텀 커넥터로 연결해서, 검색·읽기뿐 아니라 페이지 생성·수정·이동·삭제까지 가능하게 만드는 전체 가이드입니다.

라우터 포트포워딩이나 도메인 없이도 가능하도록 **Tailscale Funnel**로 외부 HTTPS 접근을 엽니다 (가정용 회선이 CGNAT거나 ISP가 인바운드를 막아둔 경우에도 동작합니다).

## 아키텍처

```
claude.ai
  → Tailscale Funnel (https://<기기명>.<tailnet>.ts.net)
  → 로컬 nginx :8100  (GET 요청 차단용 필터)
  → wikijs-mcp Python 서버 :8099  (실제 MCP 도구 구현)
  → Wiki.js GraphQL API
```

## 사전 준비물

- Wiki.js 설치 및 API 토큰 발급 (Administration → API Access → New API Key)
- Ubuntu 18.04+ 홈서버, Python 3.10+
- Tailscale 계정 (무료 플랜으로 충분)
- claude.ai Pro/Max/Team/Enterprise 계정 (커스텀 커넥터 기능)

---

## 1. wikijs-mcp(Python) 설치

검색/읽기/생성/수정/이동/삭제/히스토리까지 모두 지원하는 [jaalbin24/wikijs-mcp](https://github.com/jaalbin24/wikijs-mcp)를 사용합니다. 이 패키지는 공식 Anthropic MCP Python SDK(FastMCP)로 만들어져 있어, 코드를 건드리지 않고 작은 진입점 스크립트만 추가하면 streamable-http 모드로 띄울 수 있습니다.

```bash
sudo apt update
sudo apt install -y python3.12-venv git

sudo mkdir -p /opt/wikijs-mcp-http
sudo git clone --depth 1 https://github.com/jaalbin24/wikijs-mcp.git /opt/wikijs-mcp-http/src
sudo python3 -m venv /opt/wikijs-mcp-http/venv
sudo /opt/wikijs-mcp-http/venv/bin/pip install mcp httpx pydantic truststore
```

## 2. HTTP 진입점 스크립트 작성

```bash
sudo tee /opt/wikijs-mcp-http/run_http.py > /dev/null <<'EOF'
import os, sys
sys.path.insert(0, "/opt/wikijs-mcp-http/src")
from wikijs_mcp.server import WikiJSMCPServer

def main():
    server = WikiJSMCPServer()
    server.app.settings.host = os.environ.get("MCP_HTTP_HOST", "0.0.0.0")
    server.app.settings.port = int(os.environ.get("MCP_HTTP_PORT", "8099"))

    # FastMCP는 host가 기본값(127.0.0.1)일 때 DNS 리바인딩 방지를 자동으로 켜고
    # allowed_hosts를 127.0.0.1/localhost로만 제한한다. 외부(Funnel/nginx)를
    # 거치는 구성이라 이 기본 보호를 끈다 (앞단 nginx GET 차단 + Funnel로 이미
    # 한 겹 보호되고 있음).
    server.app.settings.transport_security.enable_dns_rebinding_protection = False

    print(f"Starting wikijs-mcp (streamable-http) on {server.app.settings.host}:{server.app.settings.port}")
    server.app.run(transport="streamable-http")

if __name__ == "__main__":
    main()
EOF
```

> **중요**: 이 한 줄(`enable_dns_rebinding_protection = False`)을 빼먹으면, 외부 도메인/Tailscale 호스트명으로 들어오는 모든 요청이 `421 Invalid Host header`로 거부됩니다. MCP 공식 SDK의 기본 보안 기능이라 흔히 놓치기 쉬운 부분입니다.

## 3. 환경변수 파일

```bash
sudo nano /etc/wikijs-mcp.env
```

```
WIKIJS_URL=http://<Wiki.js 내부 IP>:3000
WIKIJS_API_KEY=<발급받은 토큰>
MCP_HTTP_HOST=0.0.0.0
MCP_HTTP_PORT=8099
```

```bash
sudo chmod 600 /etc/wikijs-mcp.env
```

## 4. systemd 서비스 등록

```bash
sudo tee /etc/systemd/system/wikijs-mcp.service > /dev/null <<'EOF'
[Unit]
Description=WikiJS MCP Server (Python, streamable-http)
After=network.target

[Service]
EnvironmentFile=/etc/wikijs-mcp.env
ExecStart=/opt/wikijs-mcp-http/venv/bin/python3 /opt/wikijs-mcp-http/run_http.py
Restart=on-failure
RestartSec=3
User=root

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now wikijs-mcp
sudo systemctl status wikijs-mcp
```

> `User=root`로 구동합니다. 일반 계정으로 돌리고 싶다면 `useradd -r -m -d /var/lib/wikijs-mcp -s /usr/sbin/nologin wikijsmcp`로 전용 계정(반드시 홈 디렉터리 있는 `-m` 옵션 포함)을 만들고 `User=wikijsmcp`로 바꾸면 됩니다. 홈 디렉터리 없는 계정(`nobody` 등)을 쓰면 pip/캐시 관련 `EACCES` 에러로 죽습니다.

로그에 아래가 보이면 정상입니다.

```
Starting wikijs-mcp (streamable-http) on 0.0.0.0:8099
Uvicorn running on http://0.0.0.0:8099
```

## 5. 로컬 동작 확인

```bash
curl -s -X POST http://localhost:8099/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}'
```

`serverInfo`와 함께 200 OK가 오면 정상입니다.

## 6. GET 요청 차단용 nginx 필터 (필수)

이 패키지(MCP 공식 SDK 공통 사항)는 세션 없는 GET 요청에 `400 Bad Request`를 반환하는데, claude.ai는 커넥터 등록 시 GET으로 스트림을 열어보면서 이 응답을 받으면 "유효한 MCP 서버가 아니다"로 판단해 연결을 거부합니다. 그래서 GET만 `405`로 바꿔주는 가벼운 nginx를 앞에 둡니다.

```bash
sudo apt install -y nginx
sudo rm -f /etc/nginx/sites-enabled/default

sudo tee /etc/nginx/sites-available/wikijs-mcp-filter > /dev/null <<'EOF'
server {
    listen 127.0.0.1:8100 default_server;
    server_name _;

    location = /mcp {
        if ($request_method = GET) {
            add_header Content-Type text/plain always;
            return 405 'Method Not Allowed';
        }

        proxy_pass http://127.0.0.1:8099;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_buffering off;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }

    location / {
        return 404;
    }
}
EOF

sudo ln -sf /etc/nginx/sites-available/wikijs-mcp-filter /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx
```

확인:
```bash
curl -i -X GET http://127.0.0.1:8100/mcp -H "Accept: text/event-stream"   # → 405
curl -i -X POST http://127.0.0.1:8100/mcp \
  -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}'   # → 200
```

> `location / { return 404; }`는 외부 노출 시 인터넷을 상시 스캔하는 취약점 봇들(`/wp-config.php`, `/.env`, `/.git/HEAD` 등)이 nginx 기본 문서 루트를 건드리지 못하게 막아줍니다. 빼먹지 마세요.

## 7. Tailscale Funnel로 외부 노출

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```
출력되는 링크로 로그인/인증.

```bash
sudo tailscale funnel --bg 8100
tailscale funnel status
```

**처음 설정 시 흔한 문제**: `tailscale status`에 아래 경고가 뜨면 Funnel의 TLS 핸드셰이크가 불안정해져 `SSL routines::tlsv1 unrecognized name` 에러가 날 수 있습니다.
```
# Health check:
#     - Tailscale can't reach the configured DNS servers.
```
해결:
```bash
sudo tailscale set --accept-dns=false
sudo systemctl restart tailscaled
sleep 5
tailscale status   # 경고 사라졌는지 확인
sudo tailscale funnel --bg 8100
```

발급된 URL 확인 (Tailscale 관리자 콘솔 → DNS → HTTPS Certificates가 켜져 있어야 인증서 자동 발급됨):
```bash
tailscale funnel status
# https://<기기명>.<tailnet명>.ts.net
```

## 8. 외부 경로 최종 확인

```bash
curl -i -X GET https://<발급받은 URL>/mcp -H "Accept: text/event-stream"   # → 405
curl -i -X POST https://<발급받은 URL>/mcp \
  -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}'   # → 200 + serverInfo
```

## 9. claude.ai에 커넥터 등록

1. **설정(Customize) → Connectors → + → Add custom connector**
2. Name: `WikiJS`
3. URL: `https://<발급받은 URL>/mcp`
4. Advanced settings(OAuth)는 비워두고 **Add**
5. 대화창 좌하단 **+ → Connectors**에서 토글 ON

---

## 사용 가능한 도구 (12개)

| 도구 | 기능 |
|---|---|
| `wiki_search` | 전체 검색 |
| `wiki_get_page` | 페이지 조회 (경로 또는 ID) |
| `wiki_list_pages` | 페이지 목록 (태그/정렬 필터) |
| `wiki_get_tree` | 폴더/페이지 트리 구조 |
| `wiki_create_page` | 새 페이지 생성 |
| `wiki_update_page` | 페이지 수정 (전체 교체 또는 부분 find-replace) |
| `wiki_delete_page` | 페이지 삭제 |
| `wiki_move_page` | 페이지 경로 이동 |
| `wiki_list_tags` | 전체 태그 목록 |
| `wiki_get_site_info` | 사이트 메타정보 |
| `wiki_get_history` | 페이지 수정 이력 |
| `wiki_get_version` | 특정 과거 버전 조회 |

---

## 트러블슈팅 체크리스트

| 증상 | 원인 | 해결 |
|---|---|---|
| `EACCES: permission denied, mkdir '/nonexistent'` | systemd User가 홈 디렉터리 없는 계정 | `User=root` 또는 홈 디렉터리 있는 전용 계정 사용 |
| `The virtual environment was not created successfully` | `python3-venv` 패키지 미설치 | `sudo apt install python3.12-venv` |
| `ModuleNotFoundError: No module named 'mcp'` | venv가 실제로 안 만들어졌는데 pip install이 조용히 실패 | venv 디렉터리 삭제 후 위 패키지 설치하고 재생성 |
| 로컬은 되는데 외부에서 매번 404 | 다른 Docker 컨테이너가 같은 포트 선점 | `docker ps`로 포트 충돌 확인, 빈 포트로 변경 |
| `ss -tlnp`에 포트가 `127.0.0.1`로만 보임 | 호스트 바인딩이 0.0.0.0이 아님 | env/스크립트의 host 설정 확인 |
| claude.ai: "유효한 MCP 서버를 가리키지 않습니다" | GET 요청에 400/405가 아닌 다른 응답 | 6단계 nginx GET 차단 필터 적용 |
| `SSL routines::tlsv1 unrecognized name` | tailscaled DNS 헬스체크 실패로 Funnel 불안정 | `tailscale set --accept-dns=false` + tailscaled 재시작 |
| **`421 Misdirected Request` / `Invalid Host header`** | MCP SDK의 DNS 리바인딩 방지가 외부 도메인을 허용 목록에 안 넣어 거부 | `run_http.py`에 `transport_security.enable_dns_rebinding_protection = False` 추가 (2단계 참고) |
| 시스템 재부팅 후 갑자기 연결 끊김 | 포트 설정값이 의도치 않게 바뀌었거나 nginx 기본 사이트가 재생성됨 | `/etc/wikijs-mcp.env`와 `sites-enabled` 재확인 |

## 보안 참고사항

- URL을 아는 사람은 누구나 접근 가능한 구조입니다 (OAuth 미지원). URL을 공개된 곳에 올리지 마세요.
- `wiki_delete_page`, `wiki_move_page`는 되돌리기 까다로운 작업입니다. 중요한 페이지는 별도 백업을 권장합니다.
- Wiki.js API 토큰은 채팅, 깃허브, 공개 문서 등에 평문으로 절대 남기지 마세요. 노출됐다면 즉시 Administration → API Access에서 폐기 후 재발급하세요.
- 외부 노출 즉시 인터넷 스캔 봇이 도달할 수 있습니다 (`/backup.zip`, `/wp-config.php`, `/.env`, `/.git/HEAD` 등 탐색 시도). 6단계의 `location / { return 404; }`가 이를 차단합니다.
