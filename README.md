# wikijs-claude-mcp

개인 홈서버(Ubuntu)의 Wiki.js를 claude.ai 커스텀 커넥터로 연결하는 설정 모음입니다.

[jaalbin24/wikijs-mcp](https://github.com/jaalbin24/wikijs-mcp)를 기반으로 다음을 추가/변경했습니다.
- `run_http.py`: stdio 대신 **streamable-http** 모드로 띄우는 진입점 스크립트
- `wikijs_mcp/client.py`, `wikijs_mcp/server.py`: 페이지 생성/이동 시 locale 기본값 `en` → **`ko`** 변경
- `configs/`: systemd 서비스, nginx 필터, env 예시 파일 모음

## 아키텍처

```
claude.ai
  → Tailscale Funnel (https://<기기명>.<tailnet>.ts.net)
  → 로컬 nginx :8100  (GET 요청 차단용 필터)
  → wikijs-mcp Python 서버 :8099
  → Wiki.js GraphQL API
```

## 사전 준비물

- Wiki.js 설치 및 API 토큰 발급 (Administration → API Access → New API Key)
- Ubuntu 18.04+ 홈서버, Python 3.10+
- Tailscale 계정 (무료 플랜으로 충분)
- claude.ai Pro/Max/Team/Enterprise 계정

---

## 설치

### 1. 저장소 클론 및 의존성 설치

```bash
sudo apt update
sudo apt install -y python3.12-venv git

sudo mkdir -p /opt/wikijs-mcp-http
sudo git clone https://github.com/kwon2288/wikijs-claude-mcp.git /opt/wikijs-mcp-http
sudo python3 -m venv /opt/wikijs-mcp-http/venv
sudo /opt/wikijs-mcp-http/venv/bin/pip install mcp httpx pydantic truststore
```

### 2. 환경변수 파일

```bash
sudo cp /opt/wikijs-mcp-http/configs/wikijs-mcp.env.example /etc/wikijs-mcp.env
sudo nano /etc/wikijs-mcp.env   # WIKIJS_URL, WIKIJS_API_KEY 입력
sudo chmod 600 /etc/wikijs-mcp.env
```

### 3. systemd 서비스 등록

```bash
sudo cp /opt/wikijs-mcp-http/configs/wikijs-mcp.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now wikijs-mcp
sudo systemctl status wikijs-mcp
```

로그에 아래가 보이면 정상입니다.
```
Starting wikijs-mcp (streamable-http) on 0.0.0.0:8099
Uvicorn running on http://0.0.0.0:8099
```

### 4. GET 요청 차단용 nginx 필터

claude.ai는 커넥터 등록 시 GET으로 스트림을 열어보는데, MCP SDK가 세션 없는 GET에 `400`을 반환해서 등록이 거부됩니다. nginx로 GET만 `405`로 변환합니다.

```bash
sudo apt install -y nginx
sudo rm -f /etc/nginx/sites-enabled/default
sudo cp /opt/wikijs-mcp-http/configs/nginx-wikijs-mcp-filter.conf \
     /etc/nginx/sites-available/wikijs-mcp-filter
sudo ln -sf /etc/nginx/sites-available/wikijs-mcp-filter \
     /etc/nginx/sites-enabled/
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

### 5. Tailscale Funnel로 외부 노출

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
sudo tailscale funnel --bg 8100
tailscale funnel status
```

> **Tailscale이 443 포트를 점유해서 다른 서비스와 충돌하는 경우**
> `/etc/default/tailscaled`에 `FLAGS="--port=41641"` 추가 후 `sudo systemctl restart tailscaled`

**DNS 헬스체크 경고가 뜨는 경우**:
```bash
sudo tailscale set --accept-dns=false
sudo systemctl restart tailscaled
sudo tailscale funnel --bg 8100
```

Tailscale 관리자 콘솔 → DNS → **HTTPS Certificates**가 켜져 있어야 인증서가 자동 발급됩니다.

### 6. 외부 경로 최종 확인

```bash
curl -i -X GET https://<발급받은 URL>/mcp -H "Accept: text/event-stream"   # → 405
curl -i -X POST https://<발급받은 URL>/mcp \
  -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}'   # → 200 + serverInfo
```

### 7. claude.ai에 커넥터 등록

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

## 트러블슈팅

| 증상 | 원인 | 해결 |
|---|---|---|
| `EACCES: permission denied, mkdir '/nonexistent'` | systemd User가 홈 디렉터리 없는 계정 | `User=root` 또는 홈 디렉터리 있는 전용 계정 사용 |
| `The virtual environment was not created successfully` | `python3-venv` 패키지 미설치 | `sudo apt install python3.12-venv` |
| `ModuleNotFoundError: No module named 'mcp'` | venv가 실제로 안 만들어짐 | venv 삭제 후 재생성 |
| 로컬은 되는데 외부에서 404 | Docker 컨테이너가 같은 포트 선점 | `docker ps`로 포트 충돌 확인 |
| claude.ai: "유효한 MCP 서버를 가리키지 않습니다" | GET 요청에 400 반환 | 4단계 nginx GET 차단 필터 적용 |
| `SSL routines::tlsv1 unrecognized name` | tailscaled DNS 헬스체크 실패 | `tailscale set --accept-dns=false` + 재시작 |
| `421 Misdirected Request` / `Invalid Host header` | MCP SDK DNS 리바인딩 방지가 외부 호스트 거부 | `run_http.py`의 `enable_dns_rebinding_protection = False` 확인 |
| Tailscale이 443 포트 점유해서 NPM 등 충돌 | Funnel이 `0.0.0.0:443` 바인딩 | `/etc/default/tailscaled`에 `FLAGS="--port=41641"` 추가 |
| API 키 변경 후 연결 안 됨 | env 파일 수정 후 서비스 재시작 필요 | `sudo systemctl restart wikijs-mcp` |

## 보안 참고사항

- URL을 아는 사람은 누구나 접근 가능합니다 (OAuth 미지원). URL을 공개된 곳에 올리지 마세요.
- `wiki_delete_page`, `wiki_move_page`는 되돌리기 까다로운 작업입니다. 중요한 페이지는 별도 백업을 권장합니다.
- Wiki.js API 토큰이 노출됐다면 즉시 Administration → API Access에서 폐기 후 재발급하세요.

## License

소스 수정 부분(client.py, server.py)의 원본은 [jaalbin24/wikijs-mcp](https://github.com/jaalbin24/wikijs-mcp)에 귀속됩니다.
