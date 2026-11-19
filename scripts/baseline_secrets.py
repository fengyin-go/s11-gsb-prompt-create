"""Check objects reachable from the exact commit about to become public."""
from functools import lru_cache
from pathlib import Path
import re
import subprocess


SENSITIVE_NAME = re.compile(rb'(?:\.env(?:\.(?!example$|sample$|template$|dist$).+)?|id_(?:rsa|ecdsa|ed25519|dsa)|\.netrc|\.pypirc|credentials\.json|.*\.(?:pem|p12|pfx|jks|keystore))$', re.I)
CREDENTIAL = re.compile(rb'-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----|(?:gh[pousr]_|github_pat_|sk-)[A-Za-z0-9_-]{30,}|AKIA[0-9A-Z]{16}|xox[baprs]-[\w-]{20,}|(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)://[^\s:/@]{1,128}:[^\s@]{1,256}@|(?:_authToken|apikey|api_key|access_token|password)\s*["\']?\s*[:=]\s*["\']?[a-zA-Z0-9_/+=.-]{16,}', re.I)


@lru_cache(maxsize=32)
def check_public_commit(source: Path, commit: str) -> None:
    git = ['git', '--no-replace-objects', '-C', str(source)]
    if subprocess.check_output([*git, 'rev-parse', '--is-shallow-repository']).strip() == b'true':
        raise ValueError('公开发布基线前需要完整可达历史，请在完整克隆上复检')
    objects = subprocess.check_output([*git, 'rev-list', '--objects', '--no-object-names', commit]).splitlines()
    with subprocess.Popen([*git, 'cat-file', '--batch'], stdin=subprocess.PIPE, stdout=subprocess.PIPE) as reader:
        try:
            for oid in objects:
                reader.stdin.write(oid + b'\n')
                reader.stdin.flush()
                _, kind, size = reader.stdout.readline().split()
                remaining = int(size)
                if kind == b'tree':
                    tree = reader.stdout.read(remaining)
                    pos = 0
                    while pos < len(tree):
                        end = tree.index(b'\0', pos)
                        name = tree[pos:end].split(b' ', 1)[1]
                        if SENSITIVE_NAME.fullmatch(name):
                            raise ValueError(f'公开基线含敏感文件 {name.decode(errors="replace")}（tree {oid.decode()}）')
                        pos = end + 1 + len(oid) // 2
                else:
                    tail = b''
                    while remaining:
                        part = reader.stdout.read(min(remaining, 256 * 1024))
                        if not part:
                            raise ValueError('基线 Git 对象不完整')
                        if CREDENTIAL.search(tail + part):
                            raise ValueError(f'公开基线检测到疑似凭据（{kind.decode()} {oid.decode()}），请核对该对象')
                        tail = part[-1024:]
                        remaining -= len(part)
                if reader.stdout.read(1) != b'\n':
                    raise ValueError('基线 Git 对象读取失败')
        finally:
            reader.stdin.close()
            reader.stdout.close()
        if reader.wait():
            raise ValueError('基线 Git 对象扫描失败')
