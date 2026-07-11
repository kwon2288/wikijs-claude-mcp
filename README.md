# wikijs-claude-mcp

개인 홈서버(Proxmox LXC)의 Wiki.js를 claude.ai 커스텀 커넥터로 연결하는 설정 모음입니다.

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

> **별도 LXC 컨테이너 사용을 권장합니다.** Docker 호스트(NPM 등)와 같은 머신에서 Tailscale Funnel을 돌리면, Funnel과 다른 서비스(NPM 등)가 443 포트를 두고 경쟁하면서 재부팅할 때마다 둘 중 하나가 안 뜨는 문제가 생길 수 있습니다. 전용 LXC로 완전히 분리하면 네트워크 네임스페이스가 달라 이 문제가 구조적으로 발생하지 않습니다.

## 사전 준비물

- Proxmox 호스트 (SSH/콘솔 접근 가능)
- Wiki.js 설치 및 API 토큰 발급 (Administration → API Access → New API Key)
- Tailscale 계정 (무료 플랜으로 충분)
- claude.ai Pro/Max/Team/Enterprise 계정

---

## 0. Proxmox에 전용 LXC 생성

Proxmox 호스트에 SSH로 접속해서 진행합니다.

### 0-1. Ubuntu 템플릿 확인/다운로드

```bash
pveam update
pveam available | grep ubuntu
pveam download local ubuntu-24.04-standard_24.04-2_amd64.tar.zst   # 없을 경우
```

### 0-2. LXC 컨테이너 생성

```bash
pct list   # 기존 ID와 안 겹치는 번호 선택 (예: 201)

pct create 201 local:vztmpl/ubuntu-24.04-standard_24.04-2_amd64.tar.zst \
  --hostname wikijs-mcp \
  --cores 1 \
  --memory 512 \
  --swap 512 \
  --rootfs local-lvm:8 \
  --net0 name=eth0,bridge=vmbr0,ip=192.168.0.xx/24,gw=192.168.0.1 \
  --unprivileged 1 \
  --features nesting=1 \
  --onboot 1
```

- `local:vztmpl/파일명`은 `pveam list local` 결과의 정확한 경로를 그대로 사용하세요 (경로 생략 시 파싱 에러가 납니다).
- IP는 환경에 맞는 고정 IP 또는 `ip=dhcp`로 지정하세요.

### 0-3. Tailscale용 TUN 디바이스 권한 부여 (필수)

Proxmox LXC는 기본적으로 `/dev/net/tun`이 컨테이너 안에 없어서 Tailscale이 데몬 소켓을 못 엽니다. 컨테이너를 정지한 상태에서 설정을 추가합니다.

```bash
pct stop 201

cat >> /etc/pve/lxc/201.conf << 'EOF'
lxc.cgroup2.devices.allow: c 10:200 rwm
lxc.mount.entry: /dev/net/tun dev/net/tun none bind,create=file
EOF

pct start 201
```

### 0-4. 컨테이너 접속 및 root 비밀번호 설정 (선택)

```bash
pct exec 201 -- passwd   # SSH로 직접 접속하고 싶을 경우에만 필요
pct enter 201             # Proxmox 호스트 권한으로 바로 콘솔 진입
```

이후 모든 단계는 이 LXC 컨테이너 **안에서** 진행합니다.

---

## 1. 저장소 클론 및 의존성 설치

```bash
apt update
apt install -y python3-venv git nginx
# Ubuntu 버전에 따라 python3.12-venv / python3.13-venv 등 정확한 버전 패키지가 필요할 수 있습니다.
# `python3 --version` 확인 후 `apt-cache search python3.*-venv`로 정확한 패키지명을 찾으세요.

mkdir -p /opt/wikijs-mcp-http
git clone https://github.com/kwon2288/wikijs-claude-mcp.git /opt/wikijs-mcp-http
python3 -m venv /opt/wikijs-mcp-http/venv
/opt/wikijs-mcp-http/venv/bin/pip install mcp httpx pydantic truststore
```

## 2. 환경변수 파일

```bash
cp /opt/wikijs-mcp-http/configs/wikijs-mcp.env.example /etc/wikijs-mcp.env
nano /etc/wikijs-mcp.env   # WIKIJS_URL, WIKIJS_API_KEY 입력
chmod 600 /etc/wikijs-mcp.env
```

## 3. systemd 서비스 등록

```bash
cp /opt/wikijs-mcp-http/configs/wikijs-mcp.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now wikijs-mcp
systemctl status wikijs-mcp
```

로그에 아래가 보이면 정상입니다.
```
Starting wikijs-mcp (streamable-http) on 0.0.0.0:8099
Uvicorn running on http://0.0.0.0:8099
```

## 4. GET 요청 차단용 nginx 필터

claude.ai는 커넥터 등록 시 GET으로 스트림을 열어보는데, MCP SDK가 세션 없는 GET에 `400`을 반환해서 등록이 거부됩니다. nginx로 GET만 `405`로 변환합니다.

```bash
rm -f /etc/nginx/sites-enabled/default
cp /opt/wikijs-mcp-http/configs/nginx-wikijs-mcp-filter.conf \
   /etc/nginx/sites-available/wikijs-mcp-filter
ln -sf /etc/nginx/sites-available/wikijs-mcp-filter \
   /etc/nginx/sites-enabled/
nginx -t
systemctl restart nginx
```

확인:
```bash
curl -i -X GET http://127.0.0.1:8100/mcp -H "Accept: text/event-stream"   # → 405
curl -i -X POST http://127.0.0.1:8100/mcp \
  -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}'   # → 200
```

## 5. Tailscale Funnel로 외부 노출

```bash
curl -fsSL https://tailscale.com/install.sh | sh
tailscale up
tailscale funnel --bg 8100
tailscale funnel status
```

**DNS 헬스체크 경고가 뜨는 경우**:
```bash
tailscale set --accept-dns=false
systemctl restart tailscaled
tailscale funnel --bg 8100
```

Tailscale 관리자 콘솔 → DNS → **HTTPS Certificates**가 켜져 있어야 인증서가 자동 발급됩니다.

## 6. 외부 경로 최종 확인

```bash
curl -i -X GET https://<발급받은 URL>/mcp -H "Accept: text/event-stream"   # → 405
curl -i -X POST https://<발급받은 URL>/mcp \
  -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}'   # → 200 + serverInfo
```

## 7. claude.ai에 커넥터 등록

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
| `pct create`: `unable to parse directory volume name` | 템플릿 경로에 `vztmpl/` 누락 | `local:vztmpl/파일명.tar.zst` 형식으로 지정 |
| Tailscale: `dial unix /var/run/tailscale/tailscaled.sock: connect: no such file or directory` | LXC에 `/dev/net/tun` 미노출 | 0-3단계의 `lxc.mount.entry` 설정 추가 후 컨테이너 재시작 |
| `EACCES: permission denied, mkdir '/nonexistent'` | systemd User가 홈 디렉터리 없는 계정 | `User=root` 또는 홈 디렉터리 있는 전용 계정 사용 |
| `The virtual environment was not created successfully` | `python3-venv` 계열 패키지 미설치 | `python3 --version` 확인 후 정확한 버전의 venv 패키지 설치 |
| `ModuleNotFoundError: No module named 'wikijs_mcp.config'` | `config.py`가 저장소에 누락됨 | `curl`로 원본 저장소에서 `wikijs_mcp/config.py` 직접 다운로드 |
| 로컬은 되는데 외부에서 404 | 다른 프로세스/컨테이너가 같은 포트 선점 | `ss -tlnp`로 포트 충돌 확인, 빈 포트로 변경 |
| claude.ai: "유효한 MCP 서버를 가리키지 않습니다" | GET 요청에 400 반환 | 4단계 nginx GET 차단 필터 적용 |
| `SSL routines::tlsv1 unrecognized name` | tailscaled DNS 헬스체크 실패 | `tailscale set --accept-dns=false` + 재시작 |
| `421 Misdirected Request` / `Invalid Host header` | MCP SDK DNS 리바인딩 방지가 외부 호스트 거부 | `run_http.py`의 `enable_dns_rebinding_protection = False` 확인 |
| **재부팅할 때마다 NPM과 443 포트 충돌** | Docker(NPM)와 Tailscale Funnel이 같은 호스트에서 `0.0.0.0:443`을 두고 경쟁 | 근본 해결책: 이 가이드처럼 **별도 LXC로 완전 분리**. 같은 호스트를 꼭 써야 한다면 NPM의 포트 바인딩을 `0.0.0.0` 대신 서버 LAN IP로 고정 |
| API 키 변경 후 연결 안 됨 | env 파일 수정 후 서비스 재시작 필요 | `systemctl restart wikijs-mcp` |

## 보안 참고사항

- URL을 아는 사람은 누구나 접근 가능합니다 (OAuth 미지원). URL을 공개된 곳에 올리지 마세요.
- `wiki_delete_page`, `wiki_move_page`는 되돌리기 까다로운 작업입니다. 중요한 페이지는 별도 백업을 권장합니다.
- Wiki.js API 토큰이 노출됐다면 즉시 Administration → API Access에서 폐기 후 재발급하세요.

## License

소스 수정 부분(client.py, server.py, config.py)의 원본은 [jaalbin24/wikijs-mcp](https://github.com/jaalbin24/wikijs-mcp)에 귀속됩니다.
