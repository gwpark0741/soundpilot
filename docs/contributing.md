# 기여 가이드

SoundPilot 저장소(`gwpark0741/soundpilot`)에서 팀원이 코드와 문서를 수정하는 절차이다. `main` 브랜치는 ruleset으로 보호되므로 모든 변경은 pull request(PR)를 거친다.

## 1. 시작하기

1. GitHub 초대 메일에서 collaborator 초대를 수락한다. 수락하기 전에는 push할 수 없다.
2. 저장소를 clone한다.

   ```bash
   git clone https://github.com/gwpark0741/soundpilot.git
   cd soundpilot
   ```

3. 로컬에서 코드를 실행하거나 테스트하려면 환경을 만든다. 문서만 고친다면 이 단계는 필요하지 않다.

   ```bash
   cp .env.example .env              # 본인 key를 .env에만 적는다
   uv sync --extra ui
   (cd web && npm ci)                # Node 22 권장
   ```

   GPU가 없어도 테스트는 실행된다. 실제 Generate/Separate는 GPU와 tools server가 필요하므로 로컬 대신 RunPod에서 확인한다 (4절).

## 2. 변경하고 PR 올리기

1. `main`에서 최신 상태를 받고 branch를 만든다. 이름은 변경 종류에 맞춰 `feat/<주제>`, `fix/<주제>`, `docs/<주제>`, `ci/<주제>` 중 하나로 한다.

   ```bash
   git switch main && git pull
   git switch -c fix/timeline-scrub
   ```

2. 수정하고 commit한다. commit 메시지는 `fix(ui): ...`, `feat(separate): ...`, `docs: ...`처럼 종류를 앞에 붙인다.
3. push 전에 로컬에서 CI와 같은 검사를 실행한다. PR에서 실패하면 다시 push해야 하므로 먼저 확인한다.

   ```bash
   uv run ruff check src/ server/src/
   uv run ruff format --check src/ server/src/
   uv run ty check src/
   uv run --extra ui --with pytest --with pytest-asyncio python -m pytest -q tests
   (cd web && npm test && npm run build)
   ```

   `./precommit.sh`는 ruff를 `--fix`와 `format`으로 실행해 파일을 직접 고친 뒤 `ty`를 실행한다.

4. branch를 push하고 `main`을 대상으로 PR을 연다.

   ```bash
   git push -u origin fix/timeline-scrub
   gh pr create --base main      # 또는 GitHub 웹에서 "Compare & pull request"
   ```

   PR 설명에는 무엇을 왜 바꿨는지와 확인한 방법을 적는다.

테스트는 `.env`나 API key에 의존하면 안 된다. CI에는 key가 없으므로, key가 필요한 호출은 테스트에서 fake로 바꾼다.

## 3. CI 결과 읽기와 merge

PR 화면의 Checks 영역에서 `ci-lint` workflow의 결과를 확인한다. 네 가지가 모두 통과해야 merge할 수 있다.

| Check | 내용 |
|---|---|
| `lint-and-format-check` | `ruff check`, `ruff format --check`, `ty check` |
| `tests` | backend `pytest` (ffmpeg 포함) |
| `web` | `npm test`, `npm run build` (Node 22) |
| `docker-build` | UI image와 server image build 확인 (push하지 않음) |

실패한 check의 "Details"에서 로그를 열면 실패한 단계와 메시지가 보인다. 원인을 고쳐 같은 branch에 push하면 check가 다시 실행된다. 모두 통과하면 "Merge pull request"를 누른다. 승인(approval)은 필요하지 않다.

## 4. merge 이후: 자동 build와 RunPod 확인

코드나 Docker 관련 파일(`src/`, `server/`, `web/`, `scripts/runpod/`, `pyproject.toml`, `uv.lock`, `Dockerfile`, `Dockerfile.runpod`, `.dockerignore`, `.github/workflows/publish-image.yaml`)이 바뀐 merge는 Publish Image workflow를 실행한다. 문서만 바뀐 merge는 image를 만들지 않는다.

- 이 workflow는 UI와 server image를 저장소 코드로 build하고, 검증된 tools image `ghcr.io/gwpark0741/soundpilot-tools:demo-1117`과 `Dockerfile.runpod`로 합쳐 `ghcr.io/gwpark0741/soundpilot:latest`와 `:<commit sha>`를 push한다. 약 25분이 걸린다.
- 진행 상황은 저장소의 Actions 탭 > Publish Image에서 확인한다.
- 내 변경만 RunPod에서 확인하려면 `latest` 대신 `:<commit sha>`를 쓴다. `latest`는 다른 사람의 merge로 바뀔 수 있기 때문이다. sha는 `git pull` 후 `git rev-parse main`이 출력하는 40자리 값이다.
- Pod 설정은 [runpod.md](runpod.md)를 따르고 Image 칸에 `ghcr.io/gwpark0741/soundpilot:<commit sha>`를 넣는다. package가 private이면 RunPod의 Container Registry Auth에 ghcr 계정과 `read:packages` token을 등록해야 받을 수 있다.
- RunPod pod는 유료이므로 띄우기 전에 저장소 owner에게 알린다.
- 자동 build는 image가 만들어지는지만 확인하고 GPU에서 실행해 보지는 않는다. 첫 자동 build(`000a20c`)는 2026-11-24 sv3 GPU에서 end to end 검증을 통과했다. 시연에는 GPU로 검증한 tag(`demo-20261123` 또는 검증을 마친 `<commit sha>`)를 쓴다.

## 5. 규칙

- `main`에 직접 push하지 않는다. force push와 branch 삭제도 막혀 있다.
- PR은 위 네 check가 통과해야 merge된다. 승인은 필요하지 않지만 큰 변경은 merge 전에 팀에 알린다.
- `.env`, `.env.demo`, API key, token은 commit하지 않는다. `.gitignore`가 `.env`를 제외하지만 `git add .` 대신 파일을 지정해서 추가하고, `git status`로 확인한다. key가 push되었다면 해당 key를 즉시 폐기하고 새로 발급한다.
- 로그와 PR 설명에도 key를 붙이지 않는다.
- Audyssey tools image를 바꾸려면 새 image를 GPU에서 end to end로 검증한 뒤 `.github/workflows/publish-image.yaml`의 `TOOLS_IMAGE`를 바꾸는 PR을 올린다. 검증 없이 바꾸면 이후 모든 자동 build가 검증되지 않은 tools를 포함한다.
- Python package 이름(`v2a_inspect`)과 CLI 이름(`v2a`)은 그대로 둔다. 화면과 image 이름만 SoundPilot이다.

## 6. 관련 문서

- [README.md](../README.md): 개요, 실행 방법, image tag
- [usage.md](usage.md): UI 사용법과 track 편집
- [runpod.md](runpod.md): RunPod pod 설정과 가중치
- [demo-runbook.md](demo-runbook.md): sv3 Docker 시연 절차
