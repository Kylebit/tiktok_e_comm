# DuoPlus official API contract used by this skill

## Verified public facts

- Mainland base URL: `https://openapi.duoplus.cn`
- Non-mainland base URL: `https://openapi.duoplus.net`
- Method: `POST`
- Required headers: `Content-Type: application/json`, `Lang`, and `DuoPlus-API-Key`
- Published limit: QPS 1 for each endpoint
- API key source: DuoPlus Console `自动化 -> API`

## Read-only endpoints implemented

| Operation | Path | Request |
| --- | --- | --- |
| Device list | `/api/v1/cloudPhone/list` | `page`, `pagesize` |
| Device detail | `/api/v1/cloudPhone/info` | `image_id` |
| Device status | `/api/v1/cloudPhone/status` | `image_ids` |
| Platform apps | `/api/v1/app/list` | `page`, `pagesize` |
| Installed packages | `/api/v1/app/installedList` | `image_id` |

`preview` 只展示请求，没有读取设备。实际只读调用使用 `read`，消费显式 profile 所选进程凭据；无需安装授权文件：

```text
python R/scripts/orbit_tools.py --runtime-root R --project-root P --profile profile.json read duoplus devices --payload devices.json
python R/scripts/orbit_tools.py --runtime-root R --project-root P --profile profile.json read duoplus status --payload status.json
```

`devices.json` 为 `{"page":1,"pagesize":20}`，`status.json` 为 `{"image_ids":["device-a"]}`。同一命令支持 `info` / `installed-apps`（`{"image_id":"device-a"}`）和 `apps`（`{"page":1,"pagesize":100}`）。以上是合成参数示例，实际设备 ID 来自已有范围内的设备列表。`read` 返回脱敏提供方 response，确实联网；返回不是安装版本核验，也不会写安装账本或转为 install 请求。

## Portable app installation

Public contract checked 2026-09-05: [app list](https://help.duoplus.net/docs/List-of-Platform-App), [batch install](https://help.duoplus.net/docs/Batch-Install-App), [installed packages](https://help.duoplus.net/docs/List-of-Installed-App). This was documentation reading, not an account API probe. The catalog supplies app ID, package and version IDs. Installation accepts up to 20 device IDs; this wrapper requires an explicit version instead of the provider's changing default.

The installation response acknowledges a request. Installed-list readback supplies package names only. The wrapper reports `PACKAGE_PRESENT_VERSION_UNVERIFIED`; it does not claim a verified version. An unknown submission stays `UNKNOWN` even when the package appears, because that observation cannot identify the installation attempt.

```text
python R/scripts/orbit_tools.py --runtime-root R --project-root P --profile profile.json preview duoplus devices --payload devices.json
python R/scripts/orbit_tools.py --runtime-root R --project-root P --profile profile.json preview duoplus install-app --payload install.json
python R/scripts/orbit_tools.py --runtime-root R --project-root P --profile profile.json execute duoplus install-app --payload install.json --authorization authorization.json
python R/scripts/orbit_tools.py --runtime-root R --project-root P --profile profile.json execute duoplus reconcile-install --payload install.json --authorization authorization.json
```

`devices.json`: `{"page":1,"pagesize":20}`. `install.json`: `{"image_ids":["device-a"],"app_id":"app-a","app_version_id":"version-a","package":"com.example.app"}`. These are synthetic identifiers. Bind the existing instruction as `authorization_id`, `instruction_ref` (`audit://...`) and the complete `request.authorization_scope` returned by preview. The wrapper validates this binding, not who issued the instruction; no new approval is needed when the original authority covers it.

The ledger is `artifact_root/duoplus/profile_digest/installs.json`. Installation and its recovery on the same profile are serialized with the existing kernel-lock/atomic-write helpers. SUBMITTING is durable before installation; unresolved installs block another app request on any overlapping device. Damaged records remain on disk and block execution. A readback failure preserves the original acknowledged/unknown submission, so the next run reads rather than installs again.

Relevant list fields include `id`, `name`, `status`, `os`, `region`, `area`, `ip`, `http_status`, `adb_status`, `adb`, `adb_password`, `created_at`, and `expired_at`. The client redacts `adb_password` and other secret-like fields.

Status values documented by DuoPlus:

| Value | Meaning |
| --- | --- |
| 0 | Proxy not configured |
| 1 | Powered on |
| 2 | Powered off |
| 3 | Expired |
| 4 | Renewal overdue |
| 10 | Powering on |
| 11 | Configuring |
| 12 | Configuration failed |

## AI HTTP control gate

DuoPlus's 2026-07-31 update says its recommended OpenAPI plus HTTP Gateway Agent integration currently supports Android 15 Pro cloud phones. The device-list response added `ip`, `http_status`, and `region` for third-party Agents. Do not infer readiness from the marketing page alone: require `os` to identify Android 15 Pro and `http_status=1` in live readback.

The public API index does not currently expose a complete, stable HTTP Gateway action schema for screenshot, UI inspection, tap, swipe, typing, or application control. Do not invent those paths. Obtain the official DuoPlus AI Skill package/contract, review its source and version, then add typed functions and tests.

## Authoritative references

- API introduction: https://help.duoplus.cn/docs/introduction
- API index: https://help.duoplus.cn/docs/api-reference
- Device list: https://help.duoplus.cn/docs/cloud-phone-list
- Device detail: https://help.duoplus.cn/docs/huo-qu-yun-ji-xiang-qing
- Device status: https://help.duoplus.cn/docs/cloud-phone-status
- 2026-07-31 Agent update: https://help.duoplus.cn/docs/Update-Log-20260731
- DuoPlus AI Agent page: https://www.duoplus.cn/ai/ai-agent/
