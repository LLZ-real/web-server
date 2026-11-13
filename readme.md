# Web File Server

A pure-Python HTTP/1.1 file server. The core service has no third-party dependencies; the only external component is an optional Cloudflare tunnel used to expose the server publicly. The browser is the client: directory browsing, drag-and-drop upload, streaming download with resume, password-protected encrypted storage, role-based access control, in-page video playback and a live request monitor.

---

## 1. What the application does

| Capability | Description |
|---|---|
| Directory management | Browse folders with breadcrumb-style path navigation, create folders, upload into the current folder, recursively delete folders |
| Download | Streaming `seek + read(64 KB)`, single-range `206 Partial Content`, `416` for unsatisfiable ranges, `ETag` + `304` caching |
| Upload | Hand-written `multipart/form-data` parser, drag-and-drop, multi-file, live progress bar |
| Encrypted files | Optional password at upload time; PBKDF2-HMAC-SHA256 key derivation + SHAKE-256 counter stream; ciphertext on disk, plaintext never stored |
| Encrypted access | Password exchanged for a file-bound token via `POST /unlock`; valid for 10 minutes, sent as `?token=` (see §4 for session requirements and log exposure) |
| Authentication | Three shared access codes → `guest` / `builder` / `admin`, server-side enforcement on every route |
| Video playback | In-page `<video>` player fed from the current listing; encrypted videos are decrypted per 64 KB block, so seeking works |
| Request monitor | Last 20 requests with method, path, status, byte count and Range/cache notes; tokens masked as `token=***`. The buffer is global, not per session, so builder and admin see requests from all connected clients |

### Role model

| Role | Allowed operations |
|---|---|
| Guest (访客) | Browse and download |
| Builder (建设者) | Guest + upload files, create folders, view the request monitor |
| Admin (管理员) | Builder + delete files and folders (including non-empty folders) |

Permissions are enforced in `src/server.py` *before* any handler runs. Hiding buttons in the UI is only cosmetic; direct `curl` calls are rejected with `401` / `403` as well.

Two behaviours matter when reasoning about the encrypted-file protection:

- **Uploads never overwrite.** If a file with the same name already exists, the server stores the new upload as `name (1).ext` (or the next free suffix), so a builder cannot replace another user's encrypted file by uploading over it.
- **Recursive folder deletion is an admin-only operation.** When an admin deletes a folder, `shutil.rmtree` removes everything inside it; encrypted files in that subtree are **not** individually re-checked for an unlock token. The per-file token protects against deletion by ordinary logged-in users, not against an administrator who is intentionally deleting a whole folder. This boundary is stated explicitly in §4 and §8.

---

## 2. Screenshots

### 2.1 Login and role-based access


Unauthenticated visitors see only this page. Each access code maps to one role; the page states what each role can do. A successful login sets an `HttpOnly; SameSite=Lax` session cookie (`wfs_session`), valid for 12 hours of activity.

### 2.2 Main interface



The header shows the current role (`管理员`), file count and total size, plus a logout button. The upload card (drop zone, file picker, optional encryption password) is only rendered for builder and admin sessions. The table lists folders first, then files by modification time, with type badges (`DIR`, `MP4`, …) and per-row actions.

### 2.3 Encrypted file unlock


An encrypted file is marked with a lock icon. Every download, playback and delete operation asks for the password again — the browser never caches an unlock token. The dialog shows which file is being unlocked (`实验.zip` in this screenshot).

### 2.4 Video playback and request monitor


Videos are streamed directly from the listing. Dragging the progress bar issues `Range` requests; the monitor below shows the protocol behaviour live — `304 cache-hit` for the same video requested twice, and a `404` for `/favicon.ico`. This panel is available to builder and admin roles.

---

## 3. How to deploy

### Requirements

- Python **3.8+** (tested on Python 3.13, Windows 11)
- No third-party packages, no `pip install`, no database
- A writable `src/storage/` directory (created automatically on first start)

### Start the server

```bash
cd homework-group2-network-project
python src/server.py
```

On the first run the server creates `src/auth.json` with three random access codes and prints them:

```
Access codes (src/auth.json):
  guest    <guest code>
  builder  <builder code>
  admin    <admin code>
```

Open **http://localhost:8000/** and log in with one of the codes. To change a code, edit `src/auth.json` and restart; `auth.json` is excluded from version control by `.gitignore`.

### Access from a phone or another computer on the same LAN

1. Find the host IPv4 address (`ipconfig` on Windows, `ifconfig`/`ip addr` on macOS/Linux).
2. Open `http://<host-ip>:8000/` on the other device.
3. If the connection is blocked, allow inbound TCP 8000 in the host firewall. On Windows (administrator PowerShell):

```powershell
New-NetFirewallRule -DisplayName "Web File Server 8000" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow -Profile Private
```

Campus/enterprise Wi-Fi often enables client isolation, so devices on the same SSID cannot reach each other. In that case use a phone hotspot, or expose the service through a tunnel.

### Optional: public access through a tunnel

The server itself still runs locally and does not need a public IP:

```bash
cloudflared tunnel --url http://localhost:8000
```

Cloudflare prints a temporary `https://…trycloudflare.com` address that works from any network and adds HTTPS. Quick tunnels have no uptime guarantee and change their hostname on every restart; a named tunnel with a custom domain is required for a stable address.

### Storage layout

```
homework-group2-network-project/
├── report.md                 # this file
└── src/
    ├── server.py             # entry point, routing, auth gate, logging
    ├── CONTRACT.md           # API contract (written before the code)
    ├── auth.json             # access codes (generated, gitignored)
    ├── handlers/             # login, listing, download, upload, delete, mkdir, unlock, debug
    ├── utils/                # auth, crypto, session, pathsafe, multipart, mime, httpjson, reqlog
    ├── public/               # index.html, login.html
    └── storage/              # user files
```

---

## 4. Design overview

### Layering

`server.py` owns the HTTP protocol and routing, performs the authentication/authorisation check, and delegates to a handler. Handlers depend only on a handler object, so they can be tested without a socket. Utilities are stateless helpers with side effects confined to explicit calls (crypto, path safety, multipart parsing, MIME, request log).

### Protocol details

- **HTTP/1.1 keep-alive** with an explicit `Content-Length` on responses that carry a body. Bodyless responses such as `304 Not Modified` are sent without `Content-Length`.
- **Range**: `bytes=0-99`, `bytes=1024-`, `bytes=-500` → `206` + `Content-Range`; an unsatisfiable range → `416` with `Content-Range: bytes */<length>`. For encrypted files `<length>` is the plaintext length, because ranges refer to decrypted bytes.
- **Conditional requests**: `ETag` is the quoted string `"<mtime_ns>-<size>"`; an `If-None-Match` hit → `304` with no body.
- **Resume caveat**: `If-Range` is **not** implemented. A client that resumes a download after the file has changed may therefore combine bytes from two versions. Static files are the intended use case.
- **Path safety**: every user-supplied path goes through `realpath + commonpath`; `..%2f`, `..%5c` and symlink tricks are rejected with `403`.
- **Streaming**: downloads read 64 KB at a time, so memory usage is independent of file size.

### Encryption format

```
magic "WFSENC01" | salt (16 B) | verifier (32 B) | plaintext length (8 B) | ciphertext
```

- Key: PBKDF2-HMAC-SHA256, 150 000 iterations, 32-byte output. The salt is the per-file random value; there is no separate nonce field.
- Keystream: for block *i*, `SHAKE-256(key || "blk" || uint64_be(i))`, taking 64 KB per block. Because each block is derived independently, any offset can be decrypted without reading the preceding blocks, which is what makes `Range` work on encrypted video. The salt is fed into PBKDF2 rather than into each block; the block index provides the per-block variation.
- PBKDF2 runs **once per unlock**, not once per request: the derived key is stored in the in-memory token entry and reused for every subsequent `Range` request, so seeking does not re-run 150 000 iterations.
- The ETag of an encrypted file is derived from the ciphertext's `mtime_ns` and size. The token check runs *before* the `If-None-Match` comparison, so a request without a valid token gets `401` rather than `304`; with a token, `304` works normally.
- The verifier is `HMAC-SHA256(key, "wfs-verify")` (32 B). It lets a wrong password be rejected before any data is returned, but it also gives an offline attacker a definitive password check. This is inherent to any password-based file format; the defence is the KDF cost, and 150 000 PBKDF2-SHA256 iterations is below the current OWASP recommendation (roughly 600 000) — see §8.
- **No integrity protection.** The format has no MAC and is not AEAD: a modified ciphertext is decrypted to modified plaintext without an error. This is a deliberate scope limit, not an authenticated-encryption design.
- **Content only.** Encryption protects the file body, not metadata: the original file name is visible in the listing and in the encrypted header, the plaintext length is stored unencrypted in the header, and the ciphertext's size and mtime are visible to anyone with filesystem access.
- The password itself is only used in memory and never enters a URL, log line or browser history. The derived unlock **token** is a different value and is sent in the query string (see below).

### Sessions and unlock tokens

- Login: `POST /login` (form-urlencoded) → random token in `Set-Cookie: wfs_session=…; HttpOnly; SameSite=Lax`, 12-hour sliding expiry, in-memory only. The cookie has no `Secure` attribute because the server normally runs over local HTTP; if it is published through an HTTPS tunnel, `Secure` should be added.
- Encrypted files use a *separate* token from `POST /unlock`. It is bound to the canonical file path and expires after 10 minutes. The endpoint still enforces authentication: `GET /files/…` requires any valid login session, `DELETE /files/…` requires an admin session. The token is therefore never a standalone bearer credential. It is not tied to the particular session that created it — another logged-in client holding it can use it — and `POST /logout` does not remove the token entry; however, after logout the request still fails the auth gate (`302` to `/login` for a page/download request) until the client logs in again. Restarting the server clears all tokens.
- The front end deliberately does not cache unlock tokens: every download, delete and playback asks for the password again. The 10-minute TTL is therefore a bound on a leaked token, not a UX optimisation.
- Unauthenticated page requests redirect (`302`) to `/login`; API requests return `401`. Insufficient role returns `403`.
- No CSRF token or `Origin`/`Referer` validation is implemented. `SameSite=Lax` blocks cookies on cross-site `POST` requests, which covers the main CSRF vector, but an explicit `Origin` check would be stronger for a public deployment.

### Security considerations

| Topic | Current behaviour | Consequence / mitigation |
|---|---|---|
| Unlock token transport | Query parameter `?token=` | Visible in browser history/DevTools and in proxy or tunnel access logs; only the application log masks it. A header such as `X-Unlock-Token` would avoid this. |
| Ciphertext integrity | No MAC / AEAD | Bit-flipping is undetected. An HMAC over the ciphertext (or AES-GCM) would be required for tamper detection. |
| Same-origin user content | Uploaded files are served with their guessed `Content-Type`, no `Content-Disposition`, no `X-Content-Type-Options: nosniff` | An uploaded `.html` or `.svg` opened in the browser runs script in the server's origin and can call authenticated APIs. Mitigation: force attachment for active types, add `nosniff` and a CSP, or serve user content from a separate origin. |
| Access-code / unlock brute force | No rate limiting | A public deployment should lock out after repeated failures. |
| Request monitor | Global ring buffer | Builder/admin see paths requested by other clients. |
| Admin recursive delete | Removes encrypted children without per-file token | By design: the admin role is trusted with deletion; encryption protects content, not availability against admins. |

---

## 5. Verification

All of the following were exercised end to end (`curl` and a real browser):

| # | Test | Expected | Result |
|---|---|---|---|
| 1 | Open `/` without a session | `302` → `/login` | pass |
| 2 | Login with a wrong access code | `303` → `/login?err=1` | pass |
| 3 | Guest: upload / mkdir / delete / monitor | `403` for all | pass |
| 4 | Builder: upload, mkdir, monitor | `200`; delete still `403` | pass |
| 5 | Admin: upload, mkdir, delete | `200` | pass |
| 6 | Admin deletes a non-empty folder | `200`, children removed | pass |
| 7 | Delete `storage/` root | `403` | pass |
| 8 | Upload + download a 5 MB file | byte-identical | pass |
| 9 | Three Range forms / unsatisfiable range | `206` ×3 / `416` | pass |
| 10 | Same file with `If-None-Match` | `304`, 0 bytes | pass |
| 11 | Path traversal `..%2f`, `..%5c` | `403` | pass |
| 12 | Encrypted upload, download with correct password | hash matches source | pass |
| 13 | Wrong password / valid password | `401` / `200` + token | pass |
| 14 | Encrypted file without token | `401` | pass |
| 15 | Encrypted file Range request | `206`, plaintext segment matches | pass |
| 16 | Delete encrypted file without / with token | `401` / `200` | pass |
| 17 | Encrypted video seeking | progress bar works, `206` sequence | pass |
| 18 | Logout, then access `/` | `302` → `/login` | pass |
| 19 | Unlock token from encrypted file A used on encrypted file B | `401` | pass |
| 20 | Encrypted file with `If-None-Match` (with / without token) | `304` / `401` | pass |
| 21 | Encrypted file, unsatisfiable `Range` | `416` + `Content-Range: bytes */<plaintext length>` | pass |
| 22 | Logout, then reuse an unlock token | `302` → `/login` (auth gate still applies) | pass |
| 23 | Upload a second file with the same name | Stored as `name (1).ext`, original intact | pass |

The request monitor screenshot above is itself protocol evidence: the same video requested twice produces `304 cache-hit`, and a missing favicon produces `404`.

### Additional checks and known gaps

These cases are not part of the automated table above. They are listed with the behaviour implemented in the current code, so that the security claims can be reviewed precisely.

| Check | Current behaviour | Status |
|---|---|---|
| Token after 10 minutes | Rejected; expiry is checked on every use | Implemented, not timed in the suite |
| Unlock token after logout | The token entry remains until its TTL, but downloads and deletes still require a valid session; after logout the request is redirected to `/login` | Implemented; not a standalone bearer credential |
| Recursive delete of a folder containing an encrypted file | Succeeds without a per-file token (admin only) | Known gap by design |
| `If-Range` | Ignored; a `Range` request is still answered with `206` | Known gap |
| Uploaded `.html` / `.svg` | Served inline with guessed type; no `Content-Disposition` or `nosniff` | Known gap (stored XSS risk) |
| Session cookie under HTTPS | No `Secure` attribute | Known gap |
| Login / unlock brute force | No rate limiting | Known gap |
| Malformed or oversized multipart body | Basic `Content-Length` checks only; not fuzzed | Not load-tested |

---

## 6. Debugging experiences

These were the non-trivial problems we hit and how we resolved them.

1. **Password appeared to stop working after the first unlock.**
   The front end stored the unlock token in `dataset.token` and reused it, so the password was only checked once. Fix: removed the cache entirely; every download, delete and video play calls `POST /unlock` again. A `401` now always triggers a fresh password prompt. The 10-minute TTL remains as a bound on a leaked token, even though the UI re-prompts each time.

2. **Delete always reported “目标已不存在” (target no longer exists).**
   The delete button's `data-del` attribute contained the relative path without the `/files/` prefix, so `DELETE /中文目录` hit an unknown route and returned `404`. Fix: generate `/files/` + quoted path in `listing.py`; retested with Chinese file names and a non-empty Chinese folder, both `200`.

3. **`POST /mkdir` returned `400` from PowerShell.**
   PowerShell 5 writes a UTF-8 BOM with `Set-Content -Encoding utf8`, and the JSON parser rejected it; passing quoted JSON to `curl.exe` also loses inner quotes. Fix: write request bodies with `UTF8Encoding($false)` and send them via `--data-binary @file`.

4. **Encrypted files could be destroyed by anyone who could see the list.**
   Added the same password check to `DELETE`: obtain a token from `POST /unlock`, then delete with `?token=`. Without a token the response is `401`. This closes the single-file path; it does not apply to an admin's recursive folder delete, which is documented as a deliberate boundary. The token currently travels in the query string; sending it in a header such as `X-Unlock-Token` would keep it out of proxy logs. The application log masks it as `token=***`.

5. **Phone could not reach the server over campus Wi-Fi.**
   The phone reported `ERR_ADDRESS_UNREACHABLE` while the PC itself could reach its LAN IP. The cause was client isolation on the campus AP, not the firewall. We verified the Windows firewall already had an allow rule for `python.exe` (Public / TCP / any port). Workaround: expose the service through a Cloudflare quick tunnel, which also gives HTTPS.

---

## 7. Collaboration workflow

- **Contract first.** `src/CONTRACT.md` defines every endpoint, status code and error body before implementation. Changes to behaviour are reflected there first, which kept the front end and back end consistent.
- **Module split.** Protocol/routing, authentication, listing, transfer (download/upload), encryption/tokens, and front-end UI were developed as separate modules with narrow interfaces (`handler` in, response out). This allowed parallel work and isolated testing.
- **Integration and regression.** After each feature we ran the same curl suite (auth matrix, Range, `304`, traversal, encrypted download/delete) plus a browser pass on desktop and phone. Several regressions — the unlock-token cache and the missing `/files/` prefix — were caught exactly this way.

---

## 8. Known limitations and next steps

| Limitation | Impact | Possible improvement |
|---|---|---|
| Shared access codes | Cannot identify individual users; changing a code affects the whole role | Per-user accounts, hashed passwords |
| In-memory sessions | Restart requires logging in again | Signed cookies or persistent session store |
| No HTTPS by default | Passwords/plaintext can be sniffed on a LAN | Wrap the socket with `ssl.SSLContext`, or use a tunnel |
| Cookie has no `Secure` flag | A session cookie could be sent over plain HTTP if the host is reached that way | Set `Secure` when serving over TLS |
| Unlock token in the query string | Visible to proxies, tunnel logs, DevTools and browser history; only application logs mask it | Send it in `Authorization` / `X-Unlock-Token` and accept it from a header |
| Unlock token is not session-bound | The token entry survives logout and can be used from any other logged-in session for up to 10 minutes (still subject to the auth gate; delete also needs admin) | Bind the token to the session ID and clear it on logout |
| No MAC / AEAD on ciphertext | Modified ciphertext decrypts without detection | HMAC over ciphertext, or AES-GCM |
| PBKDF2 150 000 iterations | Below the current OWASP recommendation for PBKDF2-SHA256 (≈600 000) | Raise the count and store it in the header for migration |
| Same-origin active uploads | `.html` / `.svg` run script in the server origin (stored XSS) | `Content-Disposition: attachment` for active types, `nosniff`, CSP, or a separate origin |
| No `If-Range` | Resuming after the file changed can mix two versions | Implement `If-Range` with the existing ETag |
| Admin recursive delete | Deletes encrypted children without a per-file token | Documented by design; add an optional password gate for encrypted subtrees |
| No login/unlock rate limiting | A public deployment could be brute-forced | Per-IP failure counter with lockout |
| Global request monitor | Builder/admin see other clients' paths | Per-session filtering or redaction |
| Multipart upload is buffered in memory | Very large uploads increase memory use | Stream to a temporary file |
| Single-range only | Some advanced players require `multipart/byteranges` | Implement RFC 7233 multi-range |
| No rename/move, no recycle bin | Management is limited; recursive delete is irreversible | `PATCH`/move endpoints, soft delete |
| Lost encryption password is unrecoverable | Keys are derived only from the password | By design; users must keep their passwords |

---

## 9. Conclusion

The project implements a complete file service on top of the standard library and demonstrates the HTTP protocol directly: persistent connections, range requests, conditional caching, multipart uploads, path-traversal defence, authenticated sessions, role-based authorisation and password-based at-rest encryption. The encryption is *confidentiality-only*: it has no integrity tag, and unlock tokens travel in the query string with application-level masking only. Those limits, together with the same-origin XSS risk of user-uploaded active content and the absence of rate limiting, are stated explicitly in §4 and §8 rather than presented as solved. The screenshots show the working UI, and the debugging log records the issues that shaped the final design — in particular, that security checks must live on the server and must be repeated on every sensitive operation.
