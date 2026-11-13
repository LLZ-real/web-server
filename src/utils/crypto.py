"""文件级加密：PBKDF2 派生密钥 + SHAKE-256 计数器流。

磁盘格式 v2（带完整性校验，新写入一律使用）：

    magic      8B   b"WFSENC02"
    salt      16B   每个文件随机
    verifier  32B   HMAC-SHA256(key, b"wfs-verify")，用于校验密码
    rounds     4B   PBKDF2 迭代次数（大端，便于将来升级）
    size       8B   明文长度，大端
    header_mac 32B  HMAC-SHA256(mac_key, magic..size)，防止伪造头部
    ciphertext     每 64KB 块 = 密文 64KB + 块校验 16B

v1（旧版，仅保密性）仍可解锁读取，但不再用于新写入。

密钥：PBKDF2-HMAC-SHA256(password, salt, 600000) → 32B
  enc_key = HMAC(key, b"enc")，mac_key = HMAC(key, b"mac")
密钥流：第 i 块 = SHAKE-256(enc_key || b"blk" || uint64_be(i))，取 64KB。
每块独立派生，因此任意偏移都能单独解密，Range/视频拖动仍然可用。
"""

import hashlib
import hmac
import os
import secrets

MAGIC_V1 = b"WFSENC01"
MAGIC_V2 = b"WFSENC02"
SALT_SIZE = 16
VERIFIER_SIZE = 32
BLOCK = 64 * 1024
KEY_SIZE = 32
TAG_SIZE = 16
PBKDF2_ROUNDS = 600_000

HEADER_V1 = 8 + SALT_SIZE + VERIFIER_SIZE + 8
HEADER_V2 = 8 + SALT_SIZE + VERIFIER_SIZE + 4 + 8 + 32
STRIDE = BLOCK + TAG_SIZE


class IntegrityError(Exception):
    """密文或头部校验失败：文件被篡改或头部伪造。"""


def _derive_key(password: str, salt: bytes, rounds: int) -> bytes:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, rounds, dklen=KEY_SIZE
    )


def _subkeys(key: bytes):
    return (
        hmac.new(key, b"enc", hashlib.sha256).digest(),
        hmac.new(key, b"mac", hashlib.sha256).digest(),
    )


def _verifier(key: bytes) -> bytes:
    return hmac.new(key, b"wfs-verify", hashlib.sha256).digest()


def _header_mac(mac_key: bytes, head: bytes) -> bytes:
    return hmac.new(mac_key, head, hashlib.sha256).digest()


def _block_tag(mac_key: bytes, index: int, ciphertext: bytes) -> bytes:
    return hmac.new(
        mac_key, b"tag" + index.to_bytes(8, "big") + ciphertext, hashlib.sha256
    ).digest()[:TAG_SIZE]


def _keystream(enc_key: bytes, index: int) -> bytes:
    return hashlib.shake_256(
        enc_key + b"blk" + index.to_bytes(8, "big")
    ).digest(BLOCK)


def _xor(data: bytes, stream: bytes) -> bytes:
    n = len(data)
    if n == 0:
        return b""
    return (
        int.from_bytes(data, "big") ^ int.from_bytes(stream[:n], "big")
    ).to_bytes(n, "big")


def new_salt() -> bytes:
    return secrets.token_bytes(SALT_SIZE)


def encrypt_bytes(data: bytes, password: str, rounds: int = PBKDF2_ROUNDS) -> bytes:
    """内存内加密为 v2 格式，返回完整文件内容。"""
    salt = new_salt()
    key = _derive_key(password, salt, rounds)
    enc_key, mac_key = _subkeys(key)

    head = bytearray()
    head += MAGIC_V2
    head += salt
    head += _verifier(key)
    head += int(rounds).to_bytes(4, "big")
    head += len(data).to_bytes(8, "big")

    out = bytearray(head)
    out += _header_mac(mac_key, bytes(head))
    count = (len(data) + BLOCK - 1) // BLOCK
    for index in range(count):
        chunk = data[index * BLOCK:(index + 1) * BLOCK]
        ct = _xor(chunk, _keystream(enc_key, index))
        out += ct
        out += _block_tag(mac_key, index, ct)
    return bytes(out)


def _magic(real: str):
    try:
        with open(real, "rb") as f:
            return f.read(8)
    except OSError:
        return b""


def is_encrypted(real: str) -> bool:
    return _magic(real) in (MAGIC_V1, MAGIC_V2)


def file_size(real: str) -> int:
    try:
        return os.stat(real).st_size
    except OSError:
        return 0


def _capacity(real: str, header: int) -> int:
    """磁盘上实际能容纳的明文上限。v2 按 stride 扣除每块 tag。"""
    remaining = max(0, file_size(real) - header)
    if header == HEADER_V2:
        full, rem = divmod(remaining, STRIDE)
        return full * BLOCK + max(0, rem - TAG_SIZE)
    return remaining


def read_header(real: str):
    """读出 (salt, verifier, plain_size, rounds, version, header_mac)。

    v2 的 header_mac 由 unlock() 校验（需要 key）；此处先做结构与上界检查：
    size 不得超过磁盘实际容量，rounds 必须在合理范围，杜绝伪造头污染。
    """
    magic = _magic(real)
    if magic == MAGIC_V2:
        header, version = HEADER_V2, 2
    elif magic == MAGIC_V1:
        header, version = HEADER_V1, 1
    else:
        return None
    try:
        with open(real, "rb") as f:
            head = f.read(header)
    except OSError:
        return None
    if len(head) < header:
        return None

    pos = 8
    salt = head[pos:pos + SALT_SIZE]; pos += SALT_SIZE
    verifier = head[pos:pos + VERIFIER_SIZE]; pos += VERIFIER_SIZE
    if version == 2:
        rounds = int.from_bytes(head[pos:pos + 4], "big"); pos += 4
        size = int.from_bytes(head[pos:pos + 8], "big"); pos += 8
        mac = head[pos:pos + 32]
        if not (1 <= rounds <= 10_000_000):
            return None
    else:
        rounds = 150_000
        size = int.from_bytes(head[pos:pos + 8], "big")
        mac = b""
    if size > _capacity(real, header):
        return None
    return salt, verifier, size, rounds, version, mac


def unlock(real: str, password: str):
    """校验密码 + 头部完整性；成功返回 (enc_key, mac_key, version)，否则 None。"""
    h = read_header(real)
    if h is None:
        return None
    salt, verifier, size, rounds, version, mac = h
    key = _derive_key(password, salt, rounds)
    if not hmac.compare_digest(_verifier(key), verifier):
        return None
    if version == 2:
        enc_key, mac_key = _subkeys(key)
        try:
            with open(real, "rb") as f:
                head = f.read(HEADER_V2 - 32)
        except OSError:
            return None
        if not hmac.compare_digest(_header_mac(mac_key, head), mac):
            return None
        return enc_key, mac_key, 2
    return key, key, 1  # v1 无 MAC，密钥流直接用 key


def plain_size(real: str) -> int:
    h = read_header(real)
    return h[2] if h else 0


def iter_decrypted(real: str, keys, start: int, end: int):
    """按明文区间 [start, end] 逐块产出解密后的字节，内存恒定 64KB。

    v2 每块先校验 tag 再解密（篡改抛 IntegrityError）；读到文件末尾即停，
    不依赖头部声明的 size 循环，因此伪造头不会造成死循环或超大输出。
    """
    enc_key, mac_key, version = keys
    plain_len = plain_size(real) if version == 2 else 0
    pos = start
    with open(real, "rb") as f:
        while pos <= end:
            index, offset = divmod(pos, BLOCK)
            take = min(BLOCK - offset, end - pos + 1)
            if version == 2:
                # 该块密文实际长度 = min(BLOCK, 明文剩余字节数)
                ct_len = min(BLOCK, max(0, plain_len - index * BLOCK))
                if ct_len <= 0:
                    return
                base = HEADER_V2 + index * STRIDE
                f.seek(base + offset)
                chunk = f.read(min(take, ct_len - offset))
                if not chunk:
                    return
                f.seek(base)
                full_ct = f.read(ct_len)
                f.seek(base + ct_len)
                tag = f.read(TAG_SIZE)
                if len(full_ct) != ct_len or not hmac.compare_digest(
                    _block_tag(mac_key, index, full_ct), tag
                ):
                    raise IntegrityError("ciphertext block %d failed verification" % index)
            else:
                f.seek(HEADER_V1 + index * BLOCK + offset)
                chunk = f.read(take)
                if not chunk:
                    return
            yield _xor(chunk, _keystream(enc_key, index)[offset:offset + len(chunk)])
            pos += len(chunk)
