"""文件级加密：PBKDF2 派生密钥 + SHAKE-256 计数器流。

磁盘格式（64 字节头部 + 密文）：

    magic     8B   b"WFSENC01"
    salt     16B   每个文件随机
    verifier 32B   HMAC-SHA256(key, b"wfs-verify")，用于校验密码
    size      8B   明文长度，大端

密钥：PBKDF2-HMAC-SHA256(password, salt, 150000) → 32B
密钥流：第 i 个 64KB 块的密钥流 = SHAKE-256(key || i.to_bytes(8, "big"))，取 64KB

因为密钥流按块独立派生，任意偏移都能单独解密，所以加密文件同样支持 Range
断点续传与视频拖动。密文右下角不存明文任何信息，密码不对拿到的只是乱码。
"""

import hashlib
import hmac
import os
import secrets

MAGIC = b"WFSENC01"
SALT_SIZE = 16
VERIFIER_SIZE = 32
HEADER_SIZE = len(MAGIC) + SALT_SIZE + VERIFIER_SIZE + 8
KEY_SIZE = 32
BLOCK = 64 * 1024
PBKDF2_ROUNDS = 150_000


def _derive_key(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PBKDF2_ROUNDS, dklen=KEY_SIZE
    )


def _verifier(key: bytes) -> bytes:
    return hmac.new(key, b"wfs-verify", hashlib.sha256).digest()


def _keystream(key: bytes, index: int) -> bytes:
    seed = key + b"blk" + index.to_bytes(8, "big")
    return hashlib.shake_256(seed).digest(BLOCK)


def _xor(data: bytes, stream: bytes) -> bytes:
    """整块异或。转成大整数交给 C 层算，比逐字节循环快一个数量级。"""
    n = len(data)
    if n == 0:
        return b""
    return (
        int.from_bytes(data, "big") ^ int.from_bytes(stream[:n], "big")
    ).to_bytes(n, "big")


def new_salt() -> bytes:
    return secrets.token_bytes(SALT_SIZE)


def encrypt_bytes(data: bytes, password: str) -> bytes:
    """内存内加密，返回完整文件内容（头 + 密文）。"""
    salt = new_salt()
    key = _derive_key(password, salt)
    out = bytearray()
    out += MAGIC
    out += salt
    out += _verifier(key)
    out += len(data).to_bytes(8, "big")
    for offset in range(0, len(data), BLOCK):
        out += _xor(data[offset:offset + BLOCK], _keystream(key, offset // BLOCK))
    return bytes(out)


def is_encrypted(real: str) -> bool:
    """看文件头前 8 字节是不是本项目的加密魔数。"""
    try:
        with open(real, "rb") as f:
            return f.read(len(MAGIC)) == MAGIC
    except OSError:
        return False


def read_header(real: str):
    """读出 (salt, verifier, plain_size)；不是加密文件返回 None。"""
    try:
        with open(real, "rb") as f:
            head = f.read(HEADER_SIZE)
    except OSError:
        return None
    if len(head) < HEADER_SIZE or not head.startswith(MAGIC):
        return None
    pos = len(MAGIC)
    salt = head[pos:pos + SALT_SIZE]
    pos += SALT_SIZE
    verifier = head[pos:pos + VERIFIER_SIZE]
    pos += VERIFIER_SIZE
    return salt, verifier, int.from_bytes(head[pos:pos + 8], "big")


def unlock(real: str, password: str):
    """校验密码，正确返回派生密钥，错误返回 None。"""
    header = read_header(real)
    if header is None:
        return None
    salt, verifier, _ = header
    key = _derive_key(password, salt)
    if hmac.compare_digest(_verifier(key), verifier):
        return key
    return None


def plain_size(real: str) -> int:
    header = read_header(real)
    return header[2] if header else 0


def iter_decrypted(real: str, key: bytes, start: int, end: int):
    """按明文区间 [start, end] 逐块产出解密后的字节，内存恒定 64KB。"""
    pos = start
    with open(real, "rb") as f:
        while pos <= end:
            index, offset = divmod(pos, BLOCK)
            take = min(BLOCK - offset, end - pos + 1)
            f.seek(HEADER_SIZE + index * BLOCK + offset)
            chunk = f.read(take)
            if not chunk:
                return
            yield _xor(chunk, _keystream(key, index)[offset:offset + len(chunk)])
            pos += len(chunk)
