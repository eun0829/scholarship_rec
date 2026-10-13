# -*- coding: utf-8 -*-
"""[COMMON] 비밀 값(DB 비밀번호, AI 키) 파일 저장소.

  - set_secret.py 로 '처음 한 번' 입력 -> 권한이 제한된 파일에 저장
  - 이후 run_ext01.py 는 이 파일을 자동으로 읽는다 (cron 등 입력이 불가능한 환경)

보안 방침
  - 파일 형식은 KEY=VALUE 한 줄씩 (.env 와 달리 인라인 주석/따옴표를 해석하지 않으므로 비밀번호에 # 이나 = 가 있어도 안전)
  - POSIX: 파일 600 / 폴더 700 으로 만들고, 읽을 때 권한이 넓으면(그룹/기타 접근 가능) 거부한다
  - Windows: icacls 로 현재 사용자만 접근하도록 제한 시도 (실패하면 경고)
  - 값은 로그/화면에 출력하지 않는다 (목록 보기는 글자 수만 표시)
  - 암호화는 하지 않는다: 같은 서버에 복호화 키를 두면 효과가 작으므로 OS 파일 권한에 의존한다
"""
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

ALLOWED_KEYS = ("DB_PASSWORD", "EXT01_DB_PASSWORD", "AI_API_KEY", "DREAMSPON_ID", "DREAMSPON_PASSWORD")
ENV_VAR = "SCHOLARSHIP_SECRET_FILE"


class SecretFileError(Exception):
    pass


def default_path() -> Path:
    v = os.environ.get(ENV_VAR, "").strip()
    return Path(v).expanduser() if v else Path.home() / ".scholarship" / "secrets.env"


def _check_mode(path: Path) -> None:
    if os.name == "nt":
        return
    mode = path.stat().st_mode & 0o777
    if mode & 0o077:
        raise SecretFileError("비밀 파일 권한이 너무 넓습니다 ({:03o}). 다음 명령으로 제한하세요: chmod 600 {}".format(mode, path))


def _parse(text: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for line in text.split("\n"):
        line = line.rstrip("\r")
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v                       # 값은 가공하지 않음 (공백/#/따옴표 그대로)
    return out


def read(path: Optional[Path] = None, check: bool = True) -> Dict[str, str]:
    p = Path(path) if path else default_path()
    if not p.is_file():
        return {}
    if check:
        _check_mode(p)
    return _parse(p.read_text(encoding="utf-8"))


def _validate(key: str, value: str) -> None:
    if key not in ALLOWED_KEYS:
        raise ValueError("저장할 수 없는 키: {} (허용: {})".format(key, ", ".join(ALLOWED_KEYS)))
    if not value:
        raise ValueError("{}: 빈 값은 저장할 수 없습니다 (삭제는 --delete)".format(key))
    if any(c in value for c in ("\n", "\r", "\0")):
        raise ValueError("{}: 줄바꿈/널 문자는 사용할 수 없습니다".format(key))


def _restrict_windows(path: Path) -> bool:
    user = os.environ.get("USERNAME")
    if not user:
        return False
    try:
        r = subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", "{}:F".format(user)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
        return r.returncode == 0
    except Exception:
        return False


def _atomic_write(path: Path, data: Dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    body = "# scholarship secrets - do not share, do not commit\n" + "".join(
        "{}={}\n".format(k, v) for k, v in data.items())
    tmp = path.with_name("{}.tmp{}".format(path.name, os.getpid()))
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)   # 처음부터 600 으로 생성
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(body)
            f.flush()
            os.fsync(f.fileno())
        os.replace(str(tmp), str(path))
    except Exception:
        if tmp.exists():
            tmp.unlink()
        raise
    if os.name == "nt":
        if not _restrict_windows(path):
            log.warning("Windows 파일 권한을 자동으로 제한하지 못했습니다. 파일 속성에서 본인만 접근하도록 직접 설정하세요: %s", path)
    else:
        os.chmod(str(path), 0o600)


def write(path: Optional[Path], updates: Dict[str, str]) -> Path:
    p = Path(path) if path else default_path()
    for k, v in updates.items():
        _validate(k, v)
    current = read(p, check=False)               # 어차피 600 으로 다시 쓰므로 기존 권한은 검사하지 않음
    current.update(updates)
    _atomic_write(p, current)
    return p


def delete(path: Optional[Path], key: str) -> bool:
    p = Path(path) if path else default_path()
    current = read(p, check=False)
    if key not in current:
        return False
    del current[key]
    _atomic_write(p, current)
    return True


def describe(path: Optional[Path] = None) -> List[Tuple[str, int]]:
    """(키, 값의 글자 수). 값 자체는 돌려주지 않는다."""
    return [(k, len(v)) for k, v in read(path, check=False).items()]


def load_into_environ(path: Optional[Path] = None) -> List[str]:
    """비밀 파일의 값을 환경변수로 올린다. 이미 값이 있는 환경변수는 덮어쓰지 않는다. 반환: 새로 설정한 키"""
    loaded = []
    for k, v in read(path, check=True).items():
        if k in ALLOWED_KEYS and not os.environ.get(k):
            os.environ[k] = v
            loaded.append(k)
    return loaded
