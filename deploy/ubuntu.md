# Google Cloud Ubuntu 24.04 VM 배포

## 1. 로컬 봇 중지 및 파일 준비

PC의 실행 중인 봇을 Ctrl+C로 종료한 뒤 최종 데이터를 복사합니다.
두 머신에서 같은 봇을 동시에 실행하지 마세요.

코드는 Git에 올립니다. 이번에 추가된 `deploy` 폴더도 포함해야 합니다.
`.env`, `credentials.json`, `data`는 Git에 올리지 말고 별도로 VM에 전송하세요.
현재 `data/bot.json`이 원본이며 과거 DB와 backups도 data 폴더와 함께 보관하세요.

## 2. VM SSH 접속 및 코드 받기

Google Cloud Console → Compute Engine → VM 인스턴스 → SSH로 접속합니다.

```bash
sudo apt update
sudo apt install -y git python3 python3-venv unzip
git clone YOUR_REPOSITORY_URL ~/ponyangbot
cd ~/ponyangbot
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

이미 clone했다면 해당 폴더에서 `git pull --ff-only`를 사용합니다.
비공개 저장소는 VM에 읽기 전용 deploy key 등 Git 인증을 먼저 설정합니다.
Git 주소에 토큰을 넣지 마세요. PC의 Windows `.venv`는 복사하지 않습니다.

## 3. 설정과 데이터 옮기기

SSH 창의 파일 업로드 기능 또는 scp로 파일을 전송합니다.
브라우저에서 업로드한 파일은 보통 VM 사용자 홈 폴더에 놓입니다. 실제 업로드 위치를 확인하세요.
`data` 폴더는 data 폴더 자체를 포함한 zip으로 압축하면 편리합니다.
새로 clone한 폴더 기준 예시입니다. 기존 VM 데이터가 있다면 덮어쓰기 전에 확인하세요.

```bash
mv ~/.env ~/ponyangbot/.env
mv ~/credentials.json ~/ponyangbot/credentials.json
unzip -n ~/data.zip -d ~/ponyangbot
cd ~/ponyangbot
chmod 600 .env credentials.json data/bot.json
chmod 700 data
```

VM 프로젝트 폴더에 다음이 있어야 합니다.

```text
ponyangbot/
  bot.py (및 나머지 Python 파일)
  .env
  credentials.json
  data/bot.json
  deploy/install-service.sh
  .venv/bin/python
```

`.env`의 파일 경로는 다음처럼 사용합니다. 토큰·서버 ID·시트 ID는 기존 값을 유지합니다.

```dotenv
GOOGLE_APPLICATION_CREDENTIALS=credentials.json
DATABASE_PATH=data/bot.json
```

## 4. 수동 실행 확인

Developer Portal에서 Server Members Intent를 활성화하고, 봇 역할 위치와 채널 권한을 확인합니다.
시트 제목 F2 보호 범위에는 서비스 계정 편집 권한이 필요합니다.

```bash
cd ~/ponyangbot
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python bot.py
```

디스코드 연결 로그와 `/시트상태`를 확인한 뒤 Ctrl+C로 종료합니다.
시작 시 누락된 모집 게시·지난 모집 정리·시트 동기화가 자동 실행될 수 있습니다.

## 5. 24시간 자동 실행 등록

일반 SSH 계정으로 실행합니다. 스크립트 전체에 sudo를 붙이지 않습니다.

```bash
cd ~/ponyangbot
bash deploy/install-service.sh
sudo journalctl -u ponyangbot -n 100 --no-pager
```

이 서비스는 현재 계정과 프로젝트 경로를 사용합니다. 부팅 시 자동 시작하고 오류 종료 시 15초 후 재시작합니다.
SSH 창은 닫아도 됩니다. VM은 켜져 있어야 합니다. 같은 코드로 별도의 수동 실행을 추가하지 마세요.
봇 전용 HTTP/HTTPS 인바운드 포트 개방은 필요 없습니다. Discord·Google API로 나가는 HTTPS 연결과 DNS는 가능해야 합니다.

## 관리 및 코드 업데이트

```bash
sudo systemctl status ponyangbot --no-pager
sudo journalctl -u ponyangbot -f
sudo systemctl restart ponyangbot
sudo systemctl stop ponyangbot
```

로그 보기의 Ctrl+C는 로그 표시만 종료합니다. 서비스는 계속 실행됩니다.
업데이트는 아래 순서로 합니다.

```bash
sudo systemctl stop ponyangbot
cd ~/ponyangbot
git pull --ff-only
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v
sudo systemctl start ponyangbot
sudo journalctl -u ponyangbot -n 50 --no-pager
```

`data` 폴더는 정기적으로 백업합니다. 배포/업데이트 때 빈 data 폴더로 교체하지 마세요.

## 참고

- https://docs.cloud.google.com/compute/docs/connect/ssh-in-browser
- https://docs.cloud.google.com/compute/docs/instances/transfer-files
- https://www.freedesktop.org/software/systemd/man/259/systemd.service.html
