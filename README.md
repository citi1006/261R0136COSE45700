# MVP1 Franchise Quality Monitor

FastAPI 기반 로컬 웹 서버로 CCTV/수동 업로드 영상을 분석하고, ROI 단위 POP 판정과 리포트 조회를 제공하는 MVP1 프로젝트다.

## 요구 사항

- Python 3.12 이상
- PowerShell
- OpenAI API 사용을 위한 `OPENAI_API_KEY`

## 초기 실행

```powershell
pip install -r requirements.txt
Copy-Item .env.example .env
```

`.env`에 아래 값을 채운다.

```env
OPENAI_API_KEY=your_real_key
OPENAI_MODEL=gpt-4.1-mini
```

테스트 데이터가 필요하면 아래 명령을 먼저 실행한다.

```powershell
python scripts\generate_test_data.py
```

## 협업 가이드

### 1) 저장소를 바로 clone 해서 작업하는 경우

쓰기 권한이 있는 팀원은 아래 흐름으로 작업한다.

```powershell
git clone https://github.com/mebam1/20261R0136COSE45700.git
cd 20261R0136COSE45700
git checkout -b feature/your-task
```

작업 후에는 아래 순서로 올린다.

```powershell
git add .
git commit -m "Describe your change"
git push origin feature/your-task
```

그 다음 GitHub에서 `main` 대상으로 PR을 만든다.

### 2) fork 떠서 작업하는 경우

쓰기 권한이 없거나 개인 브랜치로 분리하고 싶으면 GitHub에서 먼저 저장소를 fork 한 뒤 아래 흐름으로 작업한다.

```powershell
git clone https://github.com/<your-id>/20261R0136COSE45700.git
cd 20261R0136COSE45700
git remote add upstream https://github.com/mebam1/20261R0136COSE45700.git
git checkout -b feature/your-task
```

작업 후에는 fork 쪽으로 push 한다.

```powershell
git add .
git commit -m "Describe your change"
git push origin feature/your-task
```

그 다음 GitHub에서 `upstream/main` 대상으로 PR을 만든다.

### 3) 원본 저장소 최신 내용 동기화

fork 저장소 사용 시 주기적으로 `upstream`을 받아서 맞춘다.

```powershell
git fetch upstream
git checkout main
git merge upstream/main
git push origin main
```

### 4) 주의 사항

- `.env`는 절대 커밋하지 않는다.
- `data/uploads/`, `data/analysis_crops/`, `data/mvp1.sqlite3` 같은 로컬 산출물은 커밋하지 않는다.
- 기능 작업은 `main`에 직접 하지 말고 브랜치에서 진행한다.

## 서버 실행

### foreground 실행

현재 터미널을 점유한 상태로 서버 로그를 보면서 실행한다.

```powershell
$existing = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty OwningProcess
if ($existing) { Stop-Process -Id $existing -Force }
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

### background 실행

백그라운드 프로세스로 띄우고 터미널을 바로 돌려받는다.

```powershell
$existing = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty OwningProcess
if ($existing) { Stop-Process -Id $existing -Force }
Start-Process python -ArgumentList '-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8000' -WindowStyle Hidden
```

브라우저 접속 주소는 아래와 같다.

```text
http://127.0.0.1:8000
```

실행 중인 서버를 수동으로 내릴 때는 아래 명령을 쓴다.

```powershell
$existing = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty OwningProcess
if ($existing) { Stop-Process -Id $existing -Force }
```

### Docker 컨테이너 실행
```powershell
docker run --rm -p 8000:8000 --env-file .env -v "${PWD}\data:/app/data" mvp1-backend:latest
```


## 테스트

```powershell
python -m unittest tests.test_pipeline -v
python -m compileall app tests
```
