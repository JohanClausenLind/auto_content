# Infra & Frontend Research — Retrieved 2026-08-27

Phase-zero research. Every fact below was confirmed against a page fetched on
2026-08-27 unless marked **UNVERIFIED**. Release dates come from GitHub/PyPI/npm
API JSON where noted; Docker Hub "N days ago" values are relative to 2026-08-27.

---

## 1. Temporal

### Python SDK (`temporalio`)
- Latest version on PyPI: **1.32.0**. License: **MIT**.
  Source: https://pypi.org/project/temporalio/ (2026-08-27)
- `requires_python`: `>=3.10`; classifiers list Python 3.10, 3.11, 3.12, 3.13, 3.14.
  Source: https://pypi.org/pypi/temporalio/json (2026-08-27)
- README badge "Python 3.10+", license MIT.
  Source: https://github.com/temporalio/sdk-python (2026-08-27)
- Upload date of 1.32.0: **UNVERIFIED** (not surfaced by the fetch).

### Temporal CLI dev server (`temporal server start-dev`)
- Purpose: "Run a development Temporal Server on your local system."
- Flags (quoted from the CLI reference):
  - `--db-filename`, `-f`: "Path to file for persistent Temporal state store. By default, Workflow Executions are lost when the server process dies."
  - `--port`, `-p`: "Port for the front-end gRPC Service." Default **7233**.
  - `--ui-port`: "Port for the Web UI. Defaults to '--port' value + 1000." (→ 8233 by default)
  - `--namespace`, `-n`: "Namespaces to be created at launch. The 'default' Namespace is always created automatically."
  - `--ip`: "IP address bound to the front-end Service." `--ui-ip` defaults to same as `--ip`.
  - `--headless`: "Disable the Web UI."
  - Also: `--log-level` (default "warn"), `--sqlite-pragma`, `--dynamic-config-value KEY=VALUE`, `--search-attribute KEY=VALUE`, `--ui-asset-path`, `--ui-public-path` (default `/`).
  Source: https://docs.temporal.io/cli/server (2026-08-27)
- Latest CLI release: **v1.8.2**, published 2026-07-31T15:05:06Z, prerelease=false. CLI repo license: MIT.
  Sources: https://api.github.com/repos/temporalio/cli/releases/latest ; https://github.com/temporalio/cli (2026-08-27)

### Official Docker image for the CLI/dev server
- Image: **`temporalio/temporal`** — "Temporal command-line interface and development server"; contains "the Temporal Server, SQLite persistence, and the Temporal Web UI." linux/amd64 + linux/arm64. Tags observed: `latest`, `release`, `1.8.2` (27 days ago), `1.8.1`, `1.8.0`, `1.7.x`. ~43 MB compressed.
  Sources: https://hub.docker.com/r/temporalio/temporal ; https://hub.docker.com/r/temporalio/temporal/tags (2026-08-27)
- Documented run command (README + Docker Hub):
  `docker run --rm -p 7233:7233 -p 8233:8233 temporalio/temporal:latest server start-dev --ip 0.0.0.0`
  "for dev server to be accessible from host system, it needs to listen on external IP and the ports need to be forwarded." "UI is now accessible from host at http://localhost:8233/"
  Source: https://github.com/temporalio/cli (2026-08-27)
- `temporalio/admin-tools` exists (`latest`, ~165 MB, updated ~2 months ago) but is positioned as an admin toolkit, not the dev-server image. Contents not enumerated on the Hub page.
  Source: https://hub.docker.com/r/temporalio/admin-tools (2026-08-27)

### Licenses
- Temporal Web UI (`temporalio/ui`): **MIT** — "The MIT License / Copyright (c) 2022 Temporal Technologies Inc."
  Source: https://github.com/temporalio/ui/blob/main/LICENSE (2026-08-27)
- Temporal Server (`temporalio/temporal`): **MIT** — "Copyright (c) 2025 Temporal Technologies Inc. ... Copyright (c) 2020 Uber Technologies, Inc."
  Source: https://github.com/temporalio/temporal/blob/main/LICENSE (2026-08-27)

### Schedules from the Python SDK
- "use the create_schedule() asynchronous method on the Client. Then pass the Schedule ID and the Schedule object". `action=ScheduleActionStartWorkflow(...)`, `spec=ScheduleSpec(intervals=[ScheduleIntervalSpec(every=timedelta(...))])`; optional `cron_expressions`, `calendars`, `skip`, `start_at`, `jitter`; `state=ScheduleState(note=...)`.
  ```python
  await client.create_schedule(
      "workflow-schedule-id",
      Schedule(
          action=ScheduleActionStartWorkflow(
              YourSchedulesWorkflow.run,
              "my schedule arg",
              id="schedules-workflow-id",
              task_queue="schedules-task-queue",
          ),
          spec=ScheduleSpec(intervals=[ScheduleIntervalSpec(every=timedelta(minutes=2))]),
          state=ScheduleState(note="Here's a note on my Schedule."),
      ),
  )
  ```
  Source: https://docs.temporal.io/develop/python/schedules (2026-08-27)

### Replay testing (`Replayer`)
- "Replay succeeds only if the Workflow Definition is compatible with the provided history from a deterministic point of view."
- Constructor (keyword-only): `Replayer(*, workflows: Sequence[type], workflow_task_executor=None, workflow_runner=SandboxedWorkflowRunner(), unsandboxed_workflow_runner=UnsandboxedWorkflowRunner(), namespace='ReplayNamespace', data_converter=DataConverter.default, interceptors=[], plugins=[], build_id=None, identity=None, workflow_failure_exception_types=[], debug_mode=False, runtime=None, disable_safe_workflow_eviction=False, header_codec_behavior=HeaderCodecBehavior.NO_CODEC)`
- `async replay_workflow(history: WorkflowHistory, *, raise_on_replay_failure=True) -> WorkflowReplayResult`
- `async replay_workflows(histories: AsyncIterator[WorkflowHistory], *, raise_on_replay_failure=True) -> WorkflowReplayResults`
  Source: https://python.temporal.io/temporalio.worker.Replayer.html (2026-08-27)
- Doc examples: `histories = client.list_workflows("TaskQueue=foo and StartTime > '...'").map_histories(); await Replayer(workflows=[...]).replay_workflows(histories)` and `await replayer.replay_workflow(WorkflowHistory.from_json(history_json_str))`.
  Source: https://docs.temporal.io/develop/python/testing-suite (2026-08-27)

### `WorkflowEnvironment.start_time_skipping()`
- "start a test server process and skip time automatically. In time-skipping mode, Timers, which include sleeps and conditional timeouts, are fast-forwarded except when Activities are running." Manual: `await env.sleep(3)`.
  Source: https://docs.temporal.io/develop/python/testing-suite (2026-08-27)
- "lazily downloads a test-server binary for the current OS/arch into the temp directory if it is not already there." Params: `test_server_existing_path`, `test_server_download_version`, `test_server_extra_args`, `test_server_download_ttl`. "Time skipping ... is global to the environment, not to the workflow under test" — reuse for independent workflows "but not concurrently."
- `start_local()` uses the Temporal CLI dev server (SQLite); "supports_time_skipping will always return False".
  Source: https://python.temporal.io/temporalio.testing.WorkflowEnvironment.html (2026-08-27)
- Caveat: "The time-skipping test environment does not work on ARM. The SDK will try to download the x64 binary on macOS for use with the Intel emulator, but for Linux or Windows ARM there is no proper time-skipping test server at this time."
  Source: https://github.com/temporalio/sdk-python (2026-08-27)

---

## 2. Remotion

### Version
- npm `remotion` latest: **4.0.518**; `license: "SEE LICENSE IN LICENSE.md"`; no `engines` field (also absent on `@remotion/renderer` and `@remotion/cli` 4.0.518).
  Sources: https://registry.npmjs.org/remotion/latest ; https://registry.npmjs.org/@remotion/renderer/latest ; https://registry.npmjs.org/@remotion/cli/latest (2026-08-27)
- GitHub release **v4.0.518** published 2026-08-26T18:26:09Z, prerelease=false.
  Source: https://api.github.com/repos/remotion-dev/remotion/releases/latest (2026-08-27)
- Remotion 5.0: "Remotion 5.0 is not yet released."  Source: https://www.remotion.dev/docs/5-0-migration (2026-08-27)

### License terms (source-available, not OSI)
- https://www.remotion.dev/license 307-redirects to the LICENSE.md in the repo.
- Free License eligibility (quoted): you may use Remotion for free if you are
  "an individual"; "a for-profit organization with up to 3 employees"; "a non-profit or not-for-profit organization"; or "evaluating whether Remotion is a good fit, and are not yet using it in a commercial way".
- Trigger: "You are required to obtain a Company License to use Remotion if you are not within the group of entities eligible for a Free License."
- LICENSE.md does not define how contractors are counted; it refers edge cases to the FAQ.
  Source: https://github.com/remotion-dev/remotion/blob/main/LICENSE.md (2026-08-27)
- FAQ (remotion.pro/faq → /docs/license/faq): free for organizations with "up to 3 people"; paid tiers "Remotion for Creators" "$25 per Seat per month" and "Remotion for Automators" "$0.01 per Render" with a $100 minimum monthly spend. No explicit contractor/part-time counting rule found — **UNVERIFIED** how headcount is computed beyond "employees"/"people".
  Source: https://www.remotion.dev/docs/license/faq (2026-08-27)

### Node.js support
- 4.0 migration guide: "The minimum Node version is now 16.0.0."  Source: https://www.remotion.dev/docs/4-0-migration (2026-08-27)
- 5.0 (unreleased) raises the floor (placeholder `<MinNodeVersion />` in the page; search snippet indicated Node 18 / Bun 1.1.3 — **UNVERIFIED** exact numbers). Source: https://www.remotion.dev/docs/5-0-migration (2026-08-27)
- Practical floor for 4.0.x: Node ≥16 documented; no `engines` enforcement in package.json. Use current Node LTS.

### Headless rendering (`@remotion/renderer`)
- "provides APIs for rendering video server-side"; "the configuration file has no effect when using these APIs".  Source: https://www.remotion.dev/docs/renderer (2026-08-27)
- Browser: since **v4.0.208** Remotion uses **Chrome Headless Shell** (Chrome ≥123 split headless mode out). Downloaded automatically on first render into `node_modules/.remotion/chrome-headless-shell/[platform]/chrome-headless-shell-[platform]` (with a VERSION file). Pre-download with `npx remotion browser ensure` or `ensureBrowser()`. Custom `browserExecutable` allowed but "rendering behavior may differ between Chrome versions and may be less deterministic than Chrome Headless Shell."
  Source: https://www.remotion.dev/docs/miscellaneous/chrome-headless-shell (2026-08-27)
- `ensureBrowser(options?)` — "Ensures a browser is locally installed so a Remotion render can be executed." Available from v4.0.137.  Source: https://www.remotion.dev/docs/renderer/ensure-browser (2026-08-27)
- The renderer ships a native compositor via optionalDependencies: `@remotion/compositor-{darwin-x64,darwin-arm64,linux-x64-gnu,linux-x64-musl,linux-arm64-gnu,linux-arm64-musl,win32-x64-msvc}` (all 4.0.518). So: Chrome Headless Shell is *downloaded*, not bundled in the npm package; the compositor binary *is* installed via npm.
  Source: https://registry.npmjs.org/@remotion/renderer/latest (2026-08-27)

### Fonts (deterministic loading)
- Google fonts: `import {loadFont} from "@remotion/google-fonts/TitanOne"; const {fontFamily} = loadFont('normal', {weights: ['400'], subsets: ['latin']});` returns `fontFamily` and `waitUntilDone`; "Automatically blocks the render until the font is ready".
  Source: https://www.remotion.dev/docs/google-fonts/load-font (2026-08-27)
- Local fonts: `@remotion/fonts` `loadFont({ family, url: staticFile("Inter-Regular.woff2"), weight: "500" })` — files in `public/`; available from v4.0.164/165; "automatically blocks the render until the font is ready".
  Sources: https://www.remotion.dev/docs/fonts ; https://www.remotion.dev/docs/fonts-api/load-font (2026-08-27)
- Manual fallback: `FontFace` + `delayRender()`/`continueRender()`.  Source: https://www.remotion.dev/docs/fonts (2026-08-27)

### Invoking from Node
- `bundle()` from `@remotion/bundler`: `const serveUrl = await bundle({ entryPoint: path.join(process.cwd(), './src/index.ts'), webpackOverride: (config) => config });` — returns "a `string` specifying the output directory"; options `outDir`, `publicDir`.
  Source: https://www.remotion.dev/docs/bundle (2026-08-27)
- `renderMedia()`:
  ```ts
  import {renderMedia, selectComposition} from '@remotion/renderer';
  const composition = await selectComposition({serveUrl, id: 'my-video', inputProps});
  await renderMedia({composition, serveUrl, codec: 'h264', outputLocation, inputProps});
  ```
  `serveUrl`: "Either a local path pointing to a Remotion Webpack bundle generated by `bundle()` or a URL"; `inputProps` "Must be a JSON object".
  Source: https://www.remotion.dev/docs/renderer/render-media (2026-08-27)
- `renderFrames()`: "Renders a series of images using Puppeteer and computes information for mixing audio." Options: `composition, serveUrl, outputDir, imageFormat ('jpeg'), onStart, onFrameUpdate, inputProps`; its `assetsInfo` "can be passed to `stitchFramesToVideo()` to mix audio."
  Source: https://www.remotion.dev/docs/renderer/render-frames (2026-08-27)

### Deterministic rendering guidance
- "Code your video in a way that animations run purely off the value of `useCurrentFrame()`"; components are "a function that transforms a frame number into an image."
- "A component should not rely on randomness - Exception: `random()`"; "A component should not animate when the video is paused"; avoid Date.now(), timeouts, CSS transitions; use `delayRender()` for async data.
  Source: https://www.remotion.dev/docs/flickering (2026-08-27)
- `random(seed: number | string | null)` — "Since Remotion renders a video on multiple threads and opens the website multiple times, the value returned by a `Math.random()` call will not be the same across multiple threads"; "If the seed is the same, the output is always the same."
  Source: https://www.remotion.dev/docs/random (2026-08-27)

---

## 3. Revideo (MIT swap-in reference)
- npm `@revideo/core` latest: **0.11.0**, license **MIT**.  Source: https://registry.npmjs.org/@revideo/core/latest (2026-08-27)
- Repo now lives at **`midrender/revideo`** (github.com/redotvideo/revideo redirects); license MIT; `fork: false`; last push 2026-07-15T22:43:55Z; no GitHub Releases published ("There aren't any releases here"). README: "Revideo is a rendering engine for creating videos in code ... borrows concepts from Remotion and Rive, but is, in its core, zero dep"; "the engine behind Midrender".
  Sources: https://api.github.com/repos/redotvideo/revideo ; https://github.com/redotvideo/revideo ; https://github.com/redotvideo/revideo/releases/latest (2026-08-27)
- Historical Motion Canvas lineage: **UNVERIFIED** from current pages (API reports not a fork).

---

## 4. React Flow and ELK
- `@xyflow/react` latest: **12.11.5**, license **MIT**; peerDependencies `react >=17`, `react-dom >=17`.
  Source: https://registry.npmjs.org/@xyflow/react/latest (2026-08-27)
- `elkjs` latest: **0.12.0**, license **`EPL-2.0 OR GPL-3.0-or-later`** (dual; choose EPL-2.0 for permissive use).
  Source: https://registry.npmjs.org/elkjs/latest (2026-08-27)

---

## 5. SearXNG
- Official images (mirrored): **`docker.io/searxng/searxng`** and **`ghcr.io/searxng/searxng`**. Tag policy: `:latest` plus date+commit tags, e.g. `2026.8.22-9fea41204` (= `latest` as of 6 days ago); multi-arch amd64/arm64/arm-v7.
  Sources: https://docs.searxng.org/admin/installation-docker.html ; https://github.com/searxng/searxng/pkgs/container/searxng (2026-08-27)
- Documented run: `docker run --name searxng -d -p 8888:8080 -v "./config/:/etc/searxng/" -v "./data/:/var/cache/searxng/" docker.io/searxng/searxng:latest` (Compose recommended).
  Source: https://docs.searxng.org/admin/installation-docker.html (2026-08-27)
- JSON API: `GET|POST /search` with `q` (required), `categories`, `engines`, `language`, `pageno` (default 1), `time_range` (day|month|year), `format` (json|csv|rss — "Format needs to be activated in search: settings."), `safesearch` (0|1|2). Example: `curl 'https://searx.example.org/search?q=searxng&format=json'`.
  Source: https://docs.searxng.org/dev/search_api.html (2026-08-27)
- Enabling JSON: default `settings.yml` has
  ```yaml
  search:
    # remove format to deny access, use lower case.
    # formats: [html, csv, json, rss]
    formats:
      - html
  ```
  Add `- json` under `search.formats` (in the instance's `/etc/searxng/settings.yml`).
  Sources: https://raw.githubusercontent.com/searxng/searxng/master/searx/settings.yml ; https://docs.searxng.org/admin/settings/settings_search.html (2026-08-27)
- License: **AGPL-3.0** — "This project is licensed under the GNU Affero General Public License (AGPL-3.0)."
  Source: https://github.com/searxng/searxng (2026-08-27)
- Implication (analysis, not quoted): running SearXNG unmodified as a separate container and calling its HTTP API keeps our code a separate work; AGPL obligations (source offer to network users) attach to the SearXNG service itself, not to the caller. Modifying SearXNG or linking its Python code into our process would pull our code under AGPL. Keep it as a stock image + config volume.

---

## 6. Web Push
### VAPID — RFC 8292
- "Voluntary Application Server Identification (VAPID) for Web Push". `Authorization: vapid t=<JWT>, k=<base64url public key>`. JWT signed with **ES256** (ECDSA P-256); `k` is "an ECDSA public key in uncompressed form that is encoded using base64url encoding". Claims: `aud` = push service origin; `exp` — "An 'exp' claim MUST NOT be more than 24 hours from the time of the request."; `sub` optional `mailto:` or `https:` contact.
  Source: https://www.rfc-editor.org/rfc/rfc8292.html (2026-08-27)

### `pywebpush`
- Latest **2.4.0**, released 2026-08-06, license **MPL-2.0**, `requires_python >=3.10`. Default encoding `aes128gcm` (RFC 8188); `aesgcm` deprecated. Maintainer note: "currently maintained by a single person"; designated a PyPI "Critical Project".
  Sources: https://pypi.org/project/pywebpush/ ; https://pypi.org/pypi/pywebpush/json (2026-08-27)
- Usage: `webpush(subscription_info={"endpoint":..., "keys": {"p256dh":..., "auth":...}}, data="...", vapid_private_key="path/to/vapid_private.pem", vapid_claims={"sub": "mailto:..."})`.
  Source: https://pypi.org/project/pywebpush/ (2026-08-27)

### iOS / iPadOS Safari
- Introduced in **iOS and iPadOS 16.4**. Only "a web app that has been added to the Home Screen can request permission to receive push notifications." Requires "a manifest file (with its `display` member set to `standalone` or `fullscreen`)". Permission must be requested "in response to direct user interaction — such as tapping on a 'subscribe' button."
  Source: https://webkit.org/blog/13878/web-push-for-web-apps-on-ios-and-ipados/ (2026-08-27)

---

## 7. Tailscale
### `tailscale serve`
- Syntax: `tailscale serve [flags] <target>`; `--https=<port>` (default scheme), `--http=<port>`, `--tcp=<port>`, `--tls-terminated-tcp=<port>`, `--set-path=<path>`, `--bg` ("Determines whether the command should run as a background process."). "HTTPS traffic uses an automatically provisioned TLS certificate." Examples: `tailscale serve localhost:3000`, `tailscale serve --http=80 localhost:3000`, `tailscale serve https+insecure://localhost:8443`, `tailscale serve status [--json]`, `tailscale serve reset`, `... off` to remove.
  Source: https://tailscale.com/kb/1242/tailscale-serve (2026-08-27)
- Bare-port shorthand (from CLI help text in source): "Expose an HTTP server running at 127.0.0.1:3000 in the background: $ tailscale serve --bg 3000"; `--bg`: "Run the command as a background process (default false, when --service is set defaults to true)."
  Source: https://raw.githubusercontent.com/tailscale/tailscale/main/cmd/tailscale/cli/serve_v2.go (2026-08-27)

### `tailscale funnel`
- Requirements: "Tailscale v1.38.3 or later"; "MagicDNS enabled for your tailnet"; "HTTPS enabled and valid HTTPS certificates for your tailnet"; "A funnel node attribute in your tailnet policy file":
  ```json
  "nodeAttrs": [ { "target": ["autogroup:member"], "attr": ["funnel"] } ]
  ```
- Ports: "Funnel can only listen on ports 443, 8443, and 10000". "Traffic sent over a Funnel is subject to non-configurable bandwidth limits". TLS-only; names under `tailnet-name.ts.net`.
  Source: https://tailscale.com/kb/1223/funnel (2026-08-27)
- CLI: `tailscale funnel localhost:3000`, `tailscale funnel status --json`, `tailscale funnel reset`; `--bg` supported; same help text as serve gives `tailscale funnel --bg 3000`.
  Sources: https://tailscale.com/kb/1311/tailscale-funnel ; serve_v2.go above (2026-08-27)

### `tailscale cert`
- Enable in admin console DNS page: MagicDNS → "Under **HTTPS Certificates**, select **Enable HTTPS**" (names go on a public CT ledger). Then `tailscale cert hostname.tails-scales.ts.net` — "Generate Let's Encrypt certificate and key files on the host"; flags `--cert-file=<cert>`, `--key-file=<key>`, `--min-validity=<duration>`. Files written via `tailscale cert` are **your** responsibility to renew.
  Sources: https://tailscale.com/kb/1153/enabling-https ; https://tailscale.com/kb/1080/cli (2026-08-27)
- Caddy: Caddy ≥2.5 fetches certs for `*.ts.net` from tailscaled automatically (auto-renewed). Caddyfile: `machine-name.domain-alias.ts.net { root * /var/www; file_server }`. Non-root Caddy needs `TS_PERMIT_CERT_UID=caddy` in `/etc/default/tailscaled`.
  Source: https://tailscale.com/kb/1190/caddy-certificates (2026-08-27)

### `tailscale whois`
- `tailscale whois ip[:port]` — "Get the machine and user associated with a Tailscale IP." Flags `--json`, `--proto=<tcp|udp>`. Text output: machine name/ID/Addresses/AllowedIPs, user name/ID, capabilities.
  Source: https://tailscale.com/kb/1080/cli (2026-08-27)
- `--json` shape = `apitype.WhoIsResponse { Node *tailcfg.Node; UserProfile *tailcfg.UserProfile; CapMap tailcfg.PeerCapMap }` — "In successful whois responses, Node and UserProfile are never nil." UserProfile fields: `ID`, `LoginName`, `DisplayName`, `ProfilePicURL`, `Groups`. Node fields incl. `Name`, `ComputedName`, `Addresses`, `Tags`, `User`, `Hostinfo`, `StableID`.
  Sources: https://pkg.go.dev/tailscale.com/client/tailscale/apitype#WhoIsResponse ; https://pkg.go.dev/tailscale.com/tailcfg#UserProfile (2026-08-27)

### `tailscale status --json`
- Flags `--json`, `--peers` (default true), `--self` (default true). JSON = `ipnstate.Status { Self *PeerStatus; Peer map[key.NodePublic]*PeerStatus; User map[tailcfg.UserID]tailcfg.UserProfile; CurrentTailnet *TailnetStatus; MagicDNSSuffix (legacy); BackendState string }`.
- `PeerStatus` fields: `ID`, `PublicKey`, `HostName` ("HostInfo's Hostname"), `DNSName` ("Peer's FQDN, ends with dot, form: host.<MagicDNSSuffix>."), `OS`, `UserID`, `TailscaleIPs []netip.Addr`, `Online bool`, `LastSeen` (only if offline), `Active`, `ExitNode`, `Tags`.
  Sources: https://tailscale.com/kb/1080/cli ; https://pkg.go.dev/tailscale.com/ipn/ipnstate#PeerStatus (2026-08-27)

---

## 8. PostgreSQL, MinIO, S3-compatible alternatives
### PostgreSQL
- Docker official image `postgres`: `latest` = **18.6**; also `17.11`, `16.15`, `15.19`, `14.24`, `19beta3`. Variants: trixie, bookworm, alpine.
  Source: https://hub.docker.com/_/postgres (2026-08-27)
- Upstream: 18 is newest GA (first release 2025-09-25, EOL 2030-11-14); 17.11 EOL 2029-11-08; 14 EOL 2026-11-12.
  Source: https://www.postgresql.org/support/versioning/ (2026-08-27)

### MinIO
- `github.com/minio/minio`: **archived** (read-only) — "archived by the owner on Apr 25, 2026"; last push 2026-04-24. License **AGPL-3.0**. README: "THIS REPOSITORY IS NO LONGER MAINTAINED." "The MinIO community edition is now distributed as source code only. We will no longer provide pre-compiled binary releases for the community version." "Production environments using compiled-from-source MinIO binaries do so at their own risk." Commercial path: "MinIO AIStor" (proprietary "MinIO Software License").
  Sources: https://github.com/minio/minio ; https://api.github.com/repos/minio/minio ; https://docs.min.io/community/minio-object-store/index.html (2026-08-27)
- Last release: `RELEASE.2025-10-15T17-29-55Z` (2025-10-16) — "For container environments, please clone the source and build the latest container." Docker Hub `minio/minio` is marked Archived (last update ~12 months ago; page points to `quay.io/minio/minio`).
  Sources: https://github.com/minio/minio/releases/latest ; https://hub.docker.com/r/minio/minio (2026-08-27)

### Alternatives (facts only)
- **Garage** (Deuxfleurs): "An S3 object store so reliable you can run it outside datacenters." License **AGPLv3** ("Garage is entirely free software released under the terms of the AGPLv3"). Current docs reference **v2.3.0**, image `dxflrs/garage:v2.3.0`; single static binary; repo active (commit 2026-08-23).
  Sources: https://garagehq.deuxfleurs.fr/ ; https://garagehq.deuxfleurs.fr/documentation/quick-start/ ; https://git.deuxfleurs.fr/Deuxfleurs/garage (2026-08-27)
- **RustFS**: "high-performance, distributed object storage system built in Rust", S3-compatible, **Apache-2.0**, image `rustfs/rustfs`. Latest release `1.0.0-rc.3` (2026-08-21) — **prerelease**; several features marked "Under Testing".
  Sources: https://github.com/rustfs/rustfs ; https://api.github.com/repos/rustfs/rustfs/releases?per_page=3 (2026-08-27)
- **SeaweedFS**: distributed object/file store with "Amazon S3 compatible API", **Apache-2.0**, latest release `4.44` (2026-08-22, not prerelease), S3 endpoint default `:8333`.
  Sources: https://github.com/seaweedfs/seaweedfs ; https://api.github.com/repos/seaweedfs/seaweedfs/releases/latest (2026-08-27)

---

## Decisions this research supports
1. **Temporal**: use `temporalio/temporal:1.8.2` (pin tag, not `latest`) with `server start-dev --ip 0.0.0.0 --db-filename /data/temporal.db --namespace <ns>`; ports 7233 (gRPC) / 8233 (UI). All Temporal components (server, CLI, UI, Python SDK) are MIT. Python SDK 1.32.0 requires Python ≥3.10 — target 3.12/3.13. Replay tests via `Replayer.replay_workflow(WorkflowHistory.from_json(...))`; time-skipping tests need x86-64 (not Linux ARM).
2. **Remotion 4.0.518** is viable only under the Free License if the operating entity is an individual / ≤3-employee for-profit / non-profit; otherwise budget "Automators" ($0.01/render, $100/mo min) or "Creators" ($25/seat/mo). Design compositions purely from `useCurrentFrame()` + `random(seed)`, load fonts via `@remotion/fonts`/`@remotion/google-fonts`, and pre-run `ensureBrowser()` in the render image (Chrome Headless Shell is downloaded, not bundled).
3. **Revideo 0.11.0 (MIT)** is a documented fallback if licensing blocks Remotion; note no tagged GitHub releases and a repo move to `midrender/revideo`.
4. **React Flow 12.11.5 (MIT)** + **elkjs 0.12.0 (EPL-2.0 OR GPL-3.0-or-later → pick EPL-2.0)** are license-clean for the graph UI.
5. **SearXNG**: run stock `docker.io/searxng/searxng:<date-tag>` as a separate service, mount a `settings.yml` that adds `json` to `search.formats`; consume `/search?format=json`. AGPL stays contained to the service.
6. **Web Push**: `pywebpush 2.4.0` (MPL-2.0) + VAPID keys; PWA must ship a manifest with `display: standalone` and be added to Home Screen for iOS ≥16.4.
7. **Tailscale**: `tailscale serve --bg <port>` for tailnet-only HTTPS; Funnel only if the `funnel` nodeAttr is granted and port ∈ {443, 8443, 10000}; identity via `tailscale whois --json <ip>` → `UserProfile.LoginName`; peers via `tailscale status --json` → `Peer[*].{HostName,DNSName,TailscaleIPs,Online}`. Prefer Caddy's built-in Tailscale cert integration over `tailscale cert` files (auto-renewal).
8. **PostgreSQL 18** (`postgres:18` image) is the current GA major.
9. **Do not adopt MinIO**: repo archived 2026-04, no binaries/images, AGPL. If S3 semantics are needed, evaluate **Garage 2.3.0** (AGPL-3.0, stable, tiny) or **SeaweedFS 4.44** (Apache-2.0, stable); RustFS is still RC.

## Open questions / UNVERIFIED
- `temporalio` 1.32.0 exact upload date (PyPI JSON fetch did not surface `upload_time`).
- Remotion's exact *current* minimum Node version for 4.0.518: only the 4.0 migration statement (Node ≥16.0.0) was confirmed; the 5.0 page uses dynamic placeholders. Node 18 / Bun 1.1.3 figures for 5.0 are from a search snippet only.
- Remotion license: how contractors / part-time staff count toward the "up to 3 employees" threshold — not stated in LICENSE.md or the FAQ page fetched.
- Revideo's historical relationship to Motion Canvas (GitHub API reports `fork: false`; README claims independence).
- Docker Hub `temporalio/temporal` tag dates are relative ("27 days ago") — absolute dates not captured.
- Tailscale Funnel bandwidth-limit values are not published on the KB page fetched.
- `temporalio/admin-tools` exact contents (tctl / temporal / schema tools) not enumerated on Docker Hub.
- MinIO: whether `quay.io/minio/minio` still receives any images after the April 2026 archive — not checked.
