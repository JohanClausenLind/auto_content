# TTS, alignment/ASR, and tier-1 publishing platforms — phase-zero research

Retrieved 2026-08-27

All facts below were confirmed against a fetched page on 2026-08-27 unless marked **UNVERIFIED**.
Release dates come from the PyPI JSON API (`upload_time`) where given. Nothing was installed or run.

---

## A. Text-to-speech (local, permissively licensed)

### 1. Chatterbox (resemble-ai/chatterbox)

- Repo license: **MIT**. — https://github.com/resemble-ai/chatterbox
- PyPI package `chatterbox-tts`, latest **0.1.7**, uploaded **2026-03-26**; license MIT; `Requires-Python >=3.10`. — https://pypi.org/pypi/chatterbox-tts/json
- GitHub "Releases" page shows only tag **v0.1.2** (June 13, year not shown); PyPI is ahead of GitHub releases. — https://github.com/resemble-ai/chatterbox/releases
- README: "We developed and tested Chatterbox on Python 3.11". — https://github.com/resemble-ai/chatterbox
- `pyproject.toml` pins: `torch==2.6.0` and `torchaudio==2.6.0` for Python <3.14 (`>=2.9.0` for Python >=3.14); `transformers==5.2.0`; `diffusers==0.29.0`; `librosa==0.11.0`; `numpy>=1.24,<2` on Python <3.13; also `resemble-perth` (watermarker) pulled from GitHub master. Tight pins => isolate in its own venv. — https://raw.githubusercontent.com/resemble-ai/chatterbox/master/pyproject.toml
- Word timestamps: **no mention anywhere** in README, PyPI page or model cards. Treat as "does not output timestamps". — https://github.com/resemble-ai/chatterbox ; https://huggingface.co/ResembleAI/chatterbox-turbo
- Variants (README "Latest Release: Chatterbox Multilingual V3"):
  - Chatterbox-Turbo — 350M params, English-only, paralinguistic tags (`[laugh]`, `[cough]`), "less compute and VRAM than our previous models", 1-step mel decoder. Weights license **MIT**. — https://huggingface.co/ResembleAI/chatterbox-turbo
  - Chatterbox-Nano — 110M, English-only, "3x faster than realtime on 8 CPU cores". — https://github.com/resemble-ai/chatterbox
  - Chatterbox-Multilingual V3 — 500M, 23+ languages; original Chatterbox — 500M English. Weights license **mit** on HF. — https://huggingface.co/ResembleAI/chatterbox
- All outputs carry Perth watermark ("surviving MP3 compression, audio editing…"). — https://pypi.org/project/chatterbox-tts/
- VRAM figures: **UNVERIFIED** — no numeric VRAM requirement in README or model cards; examples use `device="cuda"`.

### 2. Kokoro-82M (hexgrad) + `kokoro` PyPI

- Model weights license: **apache-2.0**; 82M params; StyleTTS 2 + ISTFTNet architecture, decoder-only. v1.0 published 2025-01-27 (8 languages, 54 voices); v0.19 published 2024-12-25. Training ≈ 1000 A100-80GB hours (~$1000). — https://huggingface.co/hexgrad/Kokoro-82M ; https://huggingface.co/hexgrad/Kokoro-82M/blob/main/README.md
- Inference code (`hexgrad/kokoro`) license: **Apache-2.0**. — https://github.com/hexgrad/kokoro
- PyPI `kokoro` latest **0.9.4**, uploaded **2025-04-05**; `Requires-Python >=3.10,<3.13` (PyPI metadata) — note GitHub `pyproject.toml` on main says `<3.14`, so the published wheel is the constraint that matters. Deps: `huggingface-hub`, `loguru`, `misaki[en]>=0.9.4`, `numpy`, `torch`, `transformers` (torch unpinned). — https://pypi.org/pypi/kokoro/json ; https://raw.githubusercontent.com/hexgrad/kokoro/main/pyproject.toml
- G2P: "Under the hood, `kokoro` uses `misaki`, a G2P library". `misaki` 0.9.4 (2025-03-18), **Apache-2.0**, `Requires-Python >=3.8,<3.13`; the `misaki[en]` extra pulls `espeakng-loader` and `phonemizer-fork`. — https://github.com/hexgrad/kokoro ; https://pypi.org/pypi/misaki/json
- espeak-ng: README says "Install espeak, used for English OOD fallback and some non-English languages" (`apt-get install espeak-ng`; Windows: run installer). **Neither the README nor the HF model card discusses espeak-ng's GPL license at all.** — https://github.com/hexgrad/kokoro/blob/main/README.md ; https://huggingface.co/hexgrad/Kokoro-82M/blob/main/README.md
  - espeak-ng itself: "released under the GPL version 3 or later license" (GitHub: "GPL-3.0 and 3 other licenses"). — https://github.com/espeak-ng/espeak-ng
  - `espeakng-loader` 0.2.4 (2025-01-17) bundles espeak-ng shared libraries for Linux/Windows/macOS wheels; PyPI license field is null. — https://pypi.org/pypi/espeakng-loader/json
  - Implication: Kokoro (Apache) dynamically loads a GPL library via `phonemizer-fork`/`espeakng-loader`. For a service we run ourselves (no distribution) this is not a compliance problem; if we ship binaries, keep espeak-ng as an external system dep.
- **Token-level timestamps: YES (English only).** `KPipeline.__call__` yields `Result(graphemes, phonemes, tokens: Optional[List[en.MToken]], output, text_index)`. For `lang_code in 'ab'` (American/British English) it calls `KPipeline.join_timestamps(tks, output.pred_dur)`, which sets `t.start_ts` and `t.end_ts` (seconds; `MAGIC_DIVISOR = 80`, i.e. 24000 Hz / 300 hop). Non-English branches yield without timestamps. — https://github.com/hexgrad/kokoro/blob/main/kokoro/pipeline.py
- CPU vs GPU speed: **UNVERIFIED** — official README/model card only say "significantly faster and more cost-efficient" than larger models; no CPU/GPU RTF numbers. (Mac: `PYTORCH_ENABLE_MPS_FALLBACK=1`.) — https://github.com/hexgrad/kokoro

### 3. Piper TTS

- `rhasspy/piper`: "This repository was archived by the owner on Oct 6, 2025. It is now read-only." License MIT. README: "Development has moved: https://github.com/OHF-Voice/piper1-gpl". — https://github.com/rhasspy/piper
- Successor `OHF-Voice/piper1-gpl`: license **GPL-3.0** ("embeds espeak-ng for phonemization"). — https://github.com/OHF-Voice/piper1-gpl
- PyPI `piper-tts` latest **1.7.0**, uploaded **2026-08-15**, license `GPL-3.0-or-later`, `Requires-Python >=3.9`, homepage github.com/OHF-voice/piper1-gpl. — https://pypi.org/pypi/piper-tts/json
- Conclusion: current Piper is GPL — not "permissively licensed"; only usable as an isolated subprocess/service if at all.

### 4. Premium APIs with timing output

- **ElevenLabs**: `POST /v1/text-to-speech/{voice_id}/with-timestamps` — "precise character-level timing". Response has `alignment` and `normalized_alignment`, each with `characters[]`, `character_start_times_seconds[]`, `character_end_times_seconds[]`. Character-level (word times derivable by grouping). — https://elevenlabs.io/docs/api-reference/text-to-speech/convert-with-timestamps
- **OpenAI TTS** (`POST /audio/speech`; models `tts-1`, `tts-1-hd`, `gpt-4o-mini-tts`, `gpt-4o-mini-tts-2025-12-15`): "Returns the audio file content, or a stream of audio events." `response_format`: mp3/opus/aac/flac/wav/pcm; `stream_format`: sse|audio. **No timestamp/alignment output.** Input max 4096 chars. — https://developers.openai.com/api/reference/resources/audio/subresources/speech/methods/create ; https://developers.openai.com/api/docs/guides/text-to-speech (platform.openai.com returned 403 to the fetcher; the developers.openai.com mirror is official)
- **Azure Speech**: `WordBoundary` event "raised at the beginning of each new spoken word, punctuation, and sentence"; fields `BoundaryType` (Word/Punctuation/Sentence), `AudioOffset` (ticks, 100 ns; docs convert via `(audio_offset + 5000) / 10000` ms), `Duration`, `Text`, `TextOffset`, `WordLength`. Sentence-level boundaries need `SpeechServiceResponse_RequestSentenceBoundary = "true"`. — https://learn.microsoft.com/en-us/azure/ai-services/speech-service/how-to-speech-synthesis
- Summary: ElevenLabs = character timing; Azure = word timing (SDK events); OpenAI = none.

---

## B. Alignment / ASR

### 5. Montreal Forced Aligner (MFA)

- License **MIT**. — https://github.com/MontrealCorpusTools/Montreal-Forced-Aligner ; https://raw.githubusercontent.com/MontrealCorpusTools/Montreal-Forced-Aligner/main/setup.cfg
- Latest release **3.4.2** (GitHub releases: 3.4.2 → 3.4.1 → 3.4.0; "Bug fixes (#971)"). PyPI `montreal-forced-aligner` 3.4.2 upload date reported as 2024-08-08 (year from PyPI JSON; GitHub page omits year). `python_requires >=3.8`. — https://github.com/MontrealCorpusTools/Montreal-Forced-Aligner/releases ; https://pypi.org/pypi/montreal-forced-aligner/json
- Install: "distributed as a package that can be installed into a Conda environment": `conda install -c conda-forge montreal-forced-aligner`. Pip is possible only after conda provides Kaldi etc. (`conda install -c conda-forge python=3.11 kaldi librosa praatio …` then `pip install montreal-forced-aligner`). Kaldi binaries are **not on PyPI** → effectively conda-only. Optional GPU via SpeechBrain (`pytorch-cuda=11.7` example, dated). — https://montreal-forced-aligner.readthedocs.io/en/latest/installation.html ; https://pypi.org/project/Montreal-Forced-Aligner/
- Runtime deps (setup.cfg): click, huggingface-hub, kneed, librosa, matplotlib, numpy, praatio>=6, pyyaml, requests, rich, rich-click, scikit-learn, seaborn, sqlalchemy>=2, tqdm (+ Kaldi/pynini from conda). — https://raw.githubusercontent.com/MontrealCorpusTools/Montreal-Forced-Aligner/main/setup.cfg
- Output formats (`mfa align … --output_format`): `long_textgrid` (default), `short_textgrid`, `json`, `csv`; `mfa align_hf CORPUS MODEL_ID OUT` pulls dictionary+model from HF. — https://montreal-forced-aligner.readthedocs.io/en/latest/user_guide/workflows/alignment.html
- Pretrained English MFA acoustic model v3.0.0: **CC BY 4.0**, ~3,771 h (Common Voice, LibriSpeech, CORAAL, regional corpora). — https://mfa-models.readthedocs.io/en/latest/acoustic/English/English%20MFA%20acoustic%20model%20v3_0_0.html
- English (US) MFA dictionary v3.0.0: **CC BY 4.0**, 80,723 entries, MFA phone set. — https://mfa-models.readthedocs.io/en/latest/dictionary/English/English%20(US)%20MFA%20dictionary%20v3_0_0.html

### 6. WhisperX (m-bain/whisperX)

- License **BSD-2-Clause** (GitHub + PyPI). — https://github.com/m-bain/whisperX ; https://pypi.org/pypi/whisperx/json
- PyPI `whisperx` latest **3.8.6**, uploaded **2026-05-25**; `Requires-Python >=3.10,<3.14` (3.8.2 was yanked: "incompatible with faster-whisper<1.2.0"). Main branch pyproject is 3.8.7rc1. — https://pypi.org/pypi/whisperx/json ; https://pypi.org/project/whisperx/
- Deps (pyproject): `ctranslate2>=4.5.0`, `faster-whisper>=1.2.0`, `pyannote-audio>=4.0.0`, `torch~=2.8.0`, `torchaudio~=2.8.0`, `torchvision~=0.23.0`, `transformers>=4.48.0`, `nltk>=3.9.1`, `numpy>=2.1.0`, `pandas>=2.2.3`, `torchcodec`, `triton>=3.3.0` (Linux x86_64). Torch ~=2.8 conflicts with Chatterbox's torch==2.6 → separate venvs. — https://raw.githubusercontent.com/m-bain/whisperX/main/pyproject.toml
- HF token: only "To enable Speaker Diarization, include your Hugging Face access token … after the `--hf_token` argument". Transcription + alignment need no token. pyannote diarization model "licensed under CC-BY-4.0 by pyannoteAI". — https://github.com/m-bain/whisperX
- Alignment models: `DEFAULT_ALIGN_MODELS_TORCH["en"] = "WAV2VEC2_ASR_BASE_960H"` (torchaudio pipeline); other languages via `DEFAULT_ALIGN_MODELS_HF` (e.g. jonatasgrosman/* checkpoints). — https://raw.githubusercontent.com/m-bain/whisperX/main/whisperx/alignment.py
  - `WAV2VEC2_ASR_BASE_960H`: "Originally published by the authors of wav2vec 2.0 under MIT License and redistributed with the same license." — https://docs.pytorch.org/audio/stable/generated/torchaudio.pipelines.WAV2VEC2_ASR_BASE_960H.html
  - `jonatasgrosman/wav2vec2-large-xlsr-53-english` (fallback/HF family): **apache-2.0**, base `facebook/wav2vec2-large-xlsr-53`. — https://huggingface.co/jonatasgrosman/wav2vec2-large-xlsr-53-english

### 7. faster-whisper / openai-whisper / CTranslate2

- `faster-whisper` latest **1.2.1**, uploaded **2025-10-31**; license **MIT**; `Requires-Python >=3.9`; dep `ctranslate2>=4.0,<5`. (1.2.0 added distil-large-v3.5; 1.2.1 Silero-VAD v6.) — https://pypi.org/pypi/faster-whisper/json ; https://github.com/SYSTRAN/faster-whisper/releases
- `WhisperModel.transcribe(..., word_timestamps=True)` yields per-word timing. README: "Python 3.9 or greater". — https://github.com/SYSTRAN/faster-whisper
- GPU: "The latest versions of `ctranslate2` only support CUDA 12 and cuDNN 9." Downgrade paths: CUDA 11 → `ctranslate2==3.24.0`; CUDA 12 + cuDNN 8 → `ctranslate2==4.4.0`. — https://github.com/SYSTRAN/faster-whisper
- CTranslate2 latest **4.8.1** (changelog dated 2026-07-03), **MIT**, `Requires-Python >=3.9`. Changelog: v4.0.0 "breaking change while updating to cuda 12"; v4.5.0 "now supports CUDNN 9 and is no longer compatible with CUDNN 8". The install docs page still says "cuDNN 8 for CUDA 12.x" — stale vs changelog/README; trust cuDNN 9. Docker `ghcr.io/opennmt/ctranslate2:latest-ubuntu22.04-cuda12.8` available. — https://pypi.org/pypi/ctranslate2/json ; https://raw.githubusercontent.com/OpenNMT/CTranslate2/master/CHANGELOG.md ; https://opennmt.net/CTranslate2/installation.html
- `openai-whisper` latest **20250625**, uploaded **2025-06-26**, **MIT** ("Whisper's code and model weights are released under the MIT License"), `Requires-Python >=3.8`; README: compatible with Python 3.8–3.11 and recent PyTorch. — https://pypi.org/pypi/openai-whisper/json ; https://github.com/openai/whisper

---

## C. Tier-1 publishing APIs (free, no app review)

### 8. Bluesky / AT Protocol

Note: `docs.bsky.app/docs/api/*` now 301-redirects to https://endpoints.bsky.app/ and guides to https://bsky.network/docs/…; those pages render client-side and came back empty, so lexicon JSON and the docs source repo were used.

- **Auth (legacy sessions)**: `com.atproto.server.createSession` — input `identifier`, `password`, `authFactorToken`, `allowTakendown`; output `accessJwt`, `refreshJwt`, `handle`, `did`, `didDoc`, `email`, `emailConfirmed`, `emailAuthFactor`, `active`, `status`. — https://raw.githubusercontent.com/bluesky-social/atproto/main/lexicons/com/atproto/server/createSession.json
  - Official blog recommends App Passwords for scripts; accessJwt "expires after a few minutes" (use refreshJwt). — https://atproto.com/blog/create-post
- **OAuth status**: "OAuth is the primary mechanism in atproto for clients to make authorized requests to PDS instances." Confidential clients publish public keys in `jwks`/`jwks_uri` and "may be trusted with longer session and token lifetimes"; `client_id` must be an https URL serving client-metadata JSON; "mandates use of DPoP for all client types". Spec still points to "legacy HTTP client sessions/tokens" as an alternative (not deprecated). — https://atproto.com/specs/oauth ; https://raw.githubusercontent.com/bluesky-social/bsky-docs/main/docs/advanced-guides/oauth-client.md
- **Posting**: `com.atproto.repo.createRecord` — input `repo`, `collection`, `rkey` (maxLength 512), `validate`, `record` (must contain `$type`), `swapCommit`; output `uri`, `cid`, `commit`, `validationStatus` (valid|unknown). — https://raw.githubusercontent.com/bluesky-social/atproto/main/lexicons/com/atproto/repo/createRecord.json
- **`app.bsky.feed.post`**: `text` `maxLength: 3000` bytes / `maxGraphemes: 300`; `facets[]` ("mentions, URLs, hashtags"); `reply {root, parent}`; `embed` union: images | video | gallery | external | record | recordWithMedia; `langs` max 3; `tags` max 8 (each ≤640 bytes / 64 graphemes); `createdAt` datetime required. Facets use `byteStart`/`byteEnd` on UTF-8. — https://raw.githubusercontent.com/bluesky-social/atproto/main/lexicons/app/bsky/feed/post.json ; https://atproto.com/blog/create-post
- **Images** (`app.bsky.embed.images`): `images` `maxLength: 4`; blob `accept: image/*`, `maxSize: 2000000` (lexicon); `alt` string required; optional `aspectRatio`. The older posts guide/blog say "limited to 1,000,000 bytes" — lexicon is authoritative; target ≤1 MB to be safe. — https://raw.githubusercontent.com/bluesky-social/atproto/main/lexicons/app/bsky/embed/images.json ; https://raw.githubusercontent.com/bluesky-social/bsky-docs/main/docs/advanced-guides/posts.md
- **`com.atproto.repo.uploadBlob`**: input encoding `*/*`; output `{blob}`; "The blob will be deleted if it is not referenced within a time window (eg, minutes)." Blob upload cap "52,428,800 bytes (50 MByte)". — https://raw.githubusercontent.com/bluesky-social/atproto/main/lexicons/com/atproto/repo/uploadBlob.json ; https://raw.githubusercontent.com/bluesky-social/bsky-docs/main/docs/advanced-guides/rate-limits.md
- **Threadgate** (`app.bsky.feed.threadgate`): rkey "must match the record key of the thread's root post"; `allow` union of `mentionRule` | `followerRule` | `followingRule` | `listRule`, max 5; `hiddenReplies` max 300; empty `allow` = nobody can reply; absent record = anyone. — https://raw.githubusercontent.com/bluesky-social/atproto/main/lexicons/app/bsky/feed/threadgate.json ; https://raw.githubusercontent.com/bluesky-social/bsky-docs/main/docs/tutorials/thread-gates.mdx
- **Video**: get service token via `com.atproto.server.getServiceAuth` (`aud` = `did:web:<PDS host>`, `lxm` = `com.atproto.repo.uploadBlob`, `exp` "30 minutes is recommended"); `POST https://video.bsky.app/xrpc/app.bsky.video.uploadVideo?did=…&name=…` with `Content-Type: video/mp4` → `jobStatus`; poll `app.bsky.video.getJobStatus` (states `JOB_STATE_CREATED…COMPLETED|FAILED`, `progress` 0–100, `blob`); embed as `app.bsky.embed.video {video, aspectRatio}`. `app.bsky.video.getUploadLimits` → `canUpload`, `remainingDailyVideos`, `remainingDailyBytes`, `message`, `error`. "Bluesky-hosted accounts need to have verified their account email before uploading video, and there are limits on the number of video posts which can be posted per day." Lexicon `embed.video` blob: `accept: video/mp4`, `maxSize: 300000000`; captions ≤20 (VTT ≤20,000 bytes). — https://raw.githubusercontent.com/bluesky-social/bsky-docs/main/docs/tutorials/video.mdx ; https://raw.githubusercontent.com/bluesky-social/atproto/main/lexicons/app/bsky/video/defs.json ; https://raw.githubusercontent.com/bluesky-social/atproto/main/lexicons/app/bsky/video/getUploadLimits.json ; https://raw.githubusercontent.com/bluesky-social/atproto/main/lexicons/app/bsky/embed/video.json
- **Rate limits**: 3000 req / 5 min per IP; repo writes per account **5,000 points/hour, 35,000 points/day** with CREATE 3 / UPDATE 2 / DELETE 1 (≈1,666 creates/hour); `createSession` 30 / 5 min and 300 / day per account; `updateHandle` 10 / 5 min, 50 / day. — https://raw.githubusercontent.com/bluesky-social/bsky-docs/main/docs/advanced-guides/rate-limits.md
- **Python SDK `atproto` (MarshalX)**: latest **0.0.71** (GitHub release 19 Aug; changelog "19.08.2026"), **MIT**, `Requires-Python >=3.9,<3.15`; README: "Under construction. Until the 1.0.0 release compatibility between versions is not guaranteed." Login is handle + (app) password; **no OAuth support** — issue #558 "OAuth Authentication Flow Support" is open (2025-02-25). — https://pypi.org/pypi/atproto/json ; https://github.com/MarshalX/atproto/releases ; https://raw.githubusercontent.com/MarshalX/atproto/main/CHANGES.md ; https://github.com/MarshalX/atproto/issues?q=is%3Aissue+oauth

### 9. Mastodon API

- App registration `POST /api/v1/apps` (client_name, redirect_uris — `urn:ietf:wg:oauth:2.0:oob` for no redirect —, scopes default `read`, website) → `client_id`, `client_secret`, `client_secret_expires_at`, `redirect_uris`, `scopes`; then `/oauth/token`. — https://docs.joinmastodon.org/methods/apps/
- Scopes: `write:statuses`, `write:media`, `read:statuses`, `profile`; "It is recommended that you make use of granular scopes". — https://docs.joinmastodon.org/api/oauth-scopes/
- Media: `POST /api/v2/media` (scope `write:media`; `file` multipart required; `description`, `focus`, `thumbnail`) returns **202** while processing (url null, `preview_url` usable) or 200 for small images; poll `GET /api/v1/media/:id` → **206** while processing, 200 when done; `PUT /api/v1/media/:id` to edit alt text before posting. — https://docs.joinmastodon.org/methods/media/
- Statuses: `POST /api/v1/statuses` (scope `write:statuses`) — `status`, `media_ids[]`, `in_reply_to_id`, `sensitive`, `spoiler_text`, `visibility` ∈ {`public`,`unlisted`,`private`,`direct`}, `language`, `scheduled_at` ("Must be at least 5 minutes in the future"), `quoted_status_id`, `quote_approval_policy`. Header **`Idempotency-Key`**: "Provide this header with any arbitrary string to prevent duplicate submissions of the same status. Consider using a hash or UUID generated client-side. Idempotency keys are stored for up to 1 hour." — https://docs.joinmastodon.org/methods/statuses/
- Instance limits: `GET /api/v2/instance` → `configuration.statuses.max_characters` (example **500**), `max_media_attachments` (4), `characters_reserved_per_url` (23); `configuration.media_attachments`: `supported_mime_types`, `image_size_limit` (16,777,216), `image_matrix_limit` (33,177,600), `video_size_limit` (103,809,024), `video_frame_rate_limit` (120), `video_matrix_limit` (8,294,400). Read these at runtime rather than hardcoding. — https://docs.joinmastodon.org/entities/Instance/
- Rate limits: 300 req / 5 min per account and per IP; media uploads 30 / 30 min; deletes 30 / 30 min; headers `X-RateLimit-Limit/Remaining/Reset`. — https://docs.joinmastodon.org/api/rate-limits/
- `Mastodon.py` latest **2.2.2**, uploaded **2026-08-03**, **MIT**, `requires_python` null (docs: "Python 3.7 and above"); "feature-complete … as of Mastodon version 4.5.8". — https://pypi.org/pypi/Mastodon.py/json ; https://pypi.org/project/Mastodon.py/

### 10. Discord

(discord.com/developers/docs/* now redirects to docs.discord.com/developers/*.)

- **Webhook**: `POST /webhooks/{webhook.id}/{webhook.token}`; query `wait`, `thread_id`, `with_components`; body `content` (≤2000 chars), `username`, `avatar_url`, `tts`, `embeds` (≤10), `allowed_mentions`, `files[n]`, `payload_json`, `attachments`, `flags` (SUPPRESS_EMBEDS, SUPPRESS_NOTIFICATIONS), `thread_name`, `applied_tags`, `poll`. "You must provide a value for at least one of `content`, `embeds`, `components`, `file`, or `poll`." Files via multipart/form-data. — https://docs.discord.com/developers/resources/webhook
- **allowed_mentions**: `parse` ⊆ {`roles`,`users`,`everyone`}, `roles[]`/`users[]` (≤100), `replied_user`; `"parse": []` suppresses all pings even if mention syntax appears. Use `{"parse": []}` by default for generated content. — https://docs.discord.com/developers/resources/message
- **Embed limits**: title 256, description 4096, ≤25 fields (name 256, value 1024), footer 2048, author name 256, ≤10 embeds, combined ≤6000 chars. — https://docs.discord.com/developers/resources/message
- **Attachments**: multipart `files[n]` + `payload_json`; default max **10 MiB per file** ("may be higher … by the server's Boost Tier"); reference in embeds via `attachment://filename.png`. — https://docs.discord.com/developers/reference
- **Rate limits**: global 50 req/s per bot; per-route buckets (`X-RateLimit-Limit/Remaining/Reset/Reset-After/Bucket`); 429 body `retry_after`, `global`; webhooks are their own top-level resource bucket. Exact per-webhook numbers are **not published**. — https://docs.discord.com/developers/topics/rate-limits
- **Bot alternative**: invite URL `https://discord.com/oauth2/authorize?client_id=…&scope=bot&permissions=<int>`; `bot` scope "puts the bot in the user's selected guild"; `applications.commands` included with `bot`. `messages.read` is RPC-only ("for local rpc server api access") — not for REST. Create Message needs `SEND_MESSAGES` (+`ATTACH_FILES`, `EMBED_LINKS`, `READ_MESSAGE_HISTORY` for replies); Get Channel Messages needs `VIEW_CHANNEL` + `READ_MESSAGE_HISTORY`. Gateway: `MESSAGE_CONTENT` is a **privileged intent** (with `GUILD_MEMBERS`, `GUILD_PRESENCES`); without it content fields are empty except the bot's own messages, DMs, messages that mention the bot, and context-menu targets. "Apps with fewer than 10,000 users can access privileged intents by enabling them in the Developer Portal"; above that, review required. — https://docs.discord.com/developers/topics/oauth2 ; https://docs.discord.com/developers/resources/message ; https://docs.discord.com/developers/events/gateway
- `webhook.incoming` OAuth scope can mint a webhook via auth-code flow if we want user-installed webhooks. — https://docs.discord.com/developers/topics/oauth2

### 11. Telegram Bot API

- `sendMessage`: `text` "1-4096 characters after entities parsing"; `parse_mode` ∈ MarkdownV2 | HTML | Markdown (legacy); `chat_id` may be `@channelusername`. — https://core.telegram.org/bots/api
- `sendPhoto`: ≤**10 MB**, width+height ≤10000, ratio ≤20; caption **0–1024 chars**. `sendVideo`: ≤**50 MB**. `sendMediaGroup`: **2–10 items**. General: 10 MB photos / 50 MB other files via api.telegram.org; local Bot API server raises to 2000 MB. — https://core.telegram.org/bots/api
- MarkdownV2 requires escaping `_ * [ ] ( ) ~ \` > # + - = | { } . !` outside entities. — https://core.telegram.org/bots/api
- Rate guidance (FAQ): "avoid sending more than one message per second" per chat; ≤20 messages/min per group; bulk ≈30 msg/s; downloads ≤20 MB; "Bots can currently send files of any type of up to 50 MB". — https://core.telegram.org/bots/faq
- Channel posting requires the bot to be a channel administrator: **UNVERIFIED in fetched docs** (the FAQ/features pages fetched did not state it explicitly; `can_post_messages` admin right exists in the API types but the excerpt was not retrievable).

---

## Decisions this research supports

1. **Primary local TTS = Kokoro-82M** (Apache-2.0 weights + code, ~82M params, Python 3.10–3.12, torch unpinned) because it is the only local option here that emits **word/token timestamps natively** (`Result.tokens[*].start_ts/end_ts`, English only). Keep espeak-ng (GPL) as a system/loader dependency, not vendored.
2. **Chatterbox (MIT)** is a quality upgrade path but has no timestamps, hard-pins `torch==2.6.0`/`transformers==5.2.0`, and watermarks output → run in its own venv/service, and derive timings via alignment (below).
3. **Skip Piper**: successor is GPL-3.0.
4. **Alignment fallback = WhisperX** (BSD-2, pip-installable, MIT wav2vec2 English aligner, no HF token needed without diarization). Requires CUDA 12 + cuDNN 9 for CTranslate2 ≥4.5; torch ~=2.8 → separate venv from Chatterbox. **MFA** (MIT, CC-BY-4.0 models) is more precise but conda-only; reserve for offline batch use.
5. Premium timing: **ElevenLabs** character-level alignment is the only REST TTS here returning timing; OpenAI TTS does not.
6. **Bluesky**: use app-password `createSession` via `atproto` SDK now (SDK lacks OAuth); enforce 300 graphemes, ≤4 images ≤1 MB each (lexicon allows 2 MB), alt text, byte-offset facets; budget writes against 5,000 pts/h (3 per create). Video via `video.bsky.app` service-auth flow; check `getUploadLimits` first.
7. **Mastodon**: register app once (`write:statuses write:media`), upload via `/api/v2/media` and poll until 200, post with `Idempotency-Key` (1 h window), read limits from `/api/v2/instance` at startup; `scheduled_at` ≥5 min ahead.
8. **Discord**: webhooks for posting (`allowed_mentions: {"parse": []}`, 10 MiB files, 2000 chars); a bot with `VIEW_CHANNEL`+`READ_MESSAGE_HISTORY` (REST) is enough to read replies; enable `MESSAGE_CONTENT` in the portal (self-serve under 10k users) only if consuming gateway events.
9. **Telegram**: bot in channel, `sendPhoto`/`sendVideo`/`sendMediaGroup` with 1024-char captions, video ≤50 MB, HTML parse mode (fewer escaping pitfalls than MarkdownV2).

## Open questions / UNVERIFIED

- Chatterbox VRAM requirements (no official numbers found); whether Turbo/Multilingual expose any duration/phoneme output that could be turned into timestamps.
- Kokoro CPU vs GPU real-time factor (no official benchmark in README/model card); accuracy of `join_timestamps` vs forced alignment.
- Kokoro/misaki GPL exposure: official docs are silent on espeak-ng's GPL; legal review if we redistribute a bundled binary.
- Bluesky video service limits (max size/duration/day) — only "limits exist" plus lexicon `maxSize` 300 MB were confirmed; third-party pages cite 100 MB / 3 min but no official page was fetched stating that. Image cap: lexicon 2,000,000 B vs guide 1,000,000 B.
- Bluesky OAuth for confidential clients is specified and "primary", but the Python SDK has no implementation (issue #558 open); timeline unknown.
- Discord per-webhook rate-limit numbers (not published).
- Telegram: explicit doc statement that a bot must be a channel admin to post.
- MFA 3.4.2 release year (GitHub omits year; PyPI JSON reported 2024-08-08) and whether a newer conda-forge build exists.
- CTranslate2 install docs still mention cuDNN 8 for CUDA 12 — contradicts changelog v4.5.0 (cuDNN 9); verify against the wheel actually installed.
