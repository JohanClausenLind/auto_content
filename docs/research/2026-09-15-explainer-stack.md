# Explainer stack: versions and licence terms, read 2026-09-15

Every row was read from the linked page on 2026-09-15. Re-verify at build time: several of these
changed in the six months before this date.

| Item | Version | Licence / terms | Source |
|---|---|---|---|
| Remotion | 4.0.525 on npm (repo pins 4.0.518); 5.0 unreleased | 4.x LICENSE.md: free for an individual, a for-profit org of up to 3 employees, non-profits. 5.0 terms (take effect on release): "Remotion for Automators" covers code that calls `renderMedia`/`renderStill` programmatically, $0.01/render with $100/month minimum; contractors and part-timers count toward the 4-person threshold; free-licence holders pass `"free-license"` as the key and pay nothing. | https://github.com/remotion-dev/remotion/blob/main/LICENSE.md · https://www.remotion.dev/docs/license/terms · https://www.remotion.dev/docs/license/faq |
| Remotion agent skills | `remotion-dev/skills`, 12 skills, pushed 2026-09-15 | No LICENSE file in the repo (GitHub API `license: null`). Install: `npx skills add remotion-dev/skills`. | https://github.com/remotion-dev/skills |
| elkjs | 0.12.0 | EPL-2.0 OR GPL-3.0-or-later | https://registry.npmjs.org/elkjs/latest |
| KaTeX | 0.18.7 | MIT | https://registry.npmjs.org/katex/latest |
| d3 | 7.9.0 | ISC | https://registry.npmjs.org/d3/latest |
| Qwen3-TTS-12Hz-1.7B Base / CustomVoice / VoiceDesign, Tokenizer-12Hz | cards cite arXiv 2601.15621 | Apache-2.0, weights and tokenizer. Card: Base SEED test-en WER 1.24; long-form (CustomVoice) long-en WER 2.812. 25 Hz variants are in the paper only, not released. | https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-Base |
| Chatterbox / chatterbox-turbo | PyPI chatterbox-tts 0.1.7; Multilingual V3; Turbo 350M | Code MIT, weights MIT. Every output carries the Resemble "Perth" watermark. No commercial restriction. | https://github.com/resemble-ai/chatterbox · https://huggingface.co/ResembleAI/chatterbox-turbo |
| Qwen3.8-27B | `Qwen/Qwen3.8-27B` (2026-08-14), `-FP8` | Apache-2.0. Native image + video VLM (`Qwen3_5ForConditionalGeneration`), 262,144 context. No official INT4; community W4A16: `RedHatAI/Qwen3.8-27B-INT4` (GPTQ + AWQ smoothing, vision tower kept, 2026-09-15), `cyankiwi/Qwen3.8-27B-AWQ-INT4`, `amd/Qwen3.8-27B-Quark-AWQ-INT4-W4A16`. | https://huggingface.co/Qwen/Qwen3.8-27B · https://huggingface.co/RedHatAI/Qwen3.8-27B-INT4 |
| vLLM | 0.29.0 | Apache-2.0. Ampere (cc 8.6): AWQ and GPTQ native; FP8 checkpoints run weight-only W8A16 through FP8 Marlin (native FP8 needs cc ≥ 8.9); NVFP4 falls back to W4A16 Marlin with a warning; MXFP4 only via Marlin. | https://docs.vllm.ai/en/latest/features/quantization/ · https://docs.vllm.ai/en/latest/features/quantization/llm_compressor/fp8/ · https://docs.vllm.ai/en/latest/features/quantization/modelopt/ |
| Scoop | `@harvard-lil/scoop` 0.7.0 | MIT. Signs through an authsign-compatible endpoint (`--signing-url`, `--signing-token`); `npm run dev-signer` for a local signer. | https://github.com/harvard-lil/scoop |
| WACZ / signing spec | WACZ 1.1.1 stable; "WACZ Signing and Verification" 0.1.0 working draft | Signature in `datapackage-digest.json`; anonymous public-key or domain-identity mode with RFC 3161 timestamps. | https://specs.webrecorder.net/wacz/1.1.1/ · https://specs.webrecorder.net/wacz-auth/0.1.0/ |
| ReplayWeb.page | 2.5.3 | AGPL-3.0-or-later. Separate process only. | https://registry.npmjs.org/replaywebpage/latest |
| pywb | 2.9.1 | GPL-3.0. Separate process only. | https://pypi.org/pypi/pywb/json |
| PaddleOCR | 3.7.0 (PP-OCRv6) | Apache-2.0 | https://pypi.org/pypi/paddleocr/json |
| dots.ocr / dots.mocr | dots.mocr 3B (arXiv 2603.13032) | Card says MIT; a search snippet mentions an extra "dots.mocr LICENSE AGREEMENT" that the card does not show. Unconfirmed; re-check before bundling. | https://huggingface.co/rednote-hilab/dots.mocr |
| LightOnOCR | LightOnOCR-2-1B | Apache-2.0 | https://huggingface.co/lightonai/LightOnOCR-2-1B |
| Montreal Forced Aligner | 3.4.2 | MIT | https://pypi.org/pypi/montreal-forced-aligner/json |
| torchaudio `forced_align`, MMS_FA | torchaudio 2.11.0 | torchaudio BSD; MMS_FA weights CC-BY-NC 4.0, so not bundleable for commercial output. | https://docs.pytorch.org/audio/stable/generated/torchaudio.pipelines.MMS_FA.html |
| Voxtral TTS | `mistralai/Voxtral-4B-TTS-2603` | CC-BY-NC-4.0 weights. Excluded. | https://huggingface.co/mistralai/Voxtral-4B-TTS-2603 |
| Breeze TTS 2 | `BreezeBlue/Breeze-TTS-2`, 3B, 2026-08-25 | Code Apache-2.0; weights and self-hosted outputs under the BreezeBlue Research and Non-Commercial License. Excluded from selection; eval role only. | https://huggingface.co/BreezeBlue/Breeze-TTS-2 |
| Fish Audio S2 Pro | `fishaudio/s2-pro`, 5B | Fish Audio Research License (2026-03-07): no commercial rights without a paid agreement. Excluded. | https://huggingface.co/fishaudio/s2-pro/blob/main/LICENSE.md |
| YouTube AI disclosure | help page | Realistic altered or synthetic content must be labelled "Altered or synthetic content"; own-voice cloning, scripts, captions and clearly unrealistic content are exempt; disclosure does not affect monetisation. | https://support.google.com/youtube/answer/14328491 |
| YouTube inauthentic content | policy dated 2025-07-15; clarification 2026-07 | "Inauthentic content": repetitive or mass-produced, generic templates without creator insight, slideshows and templated storylines named. 2026-07 clarification (Creator Insider video) adds distressing content and AI personas on finance, legal or health topics. | https://support.google.com/youtube/answer/1311392 |
| YouTube paid promotion | help page | Tick "includes paid promotion"; label shows at the start of the video; the creator carries the legal disclosure duty. | https://support.google.com/youtube/answer/154235 |
| Artificial Analysis Controlled Voice Arena | leaderboard | Same 8 cloned voices (4 US, 4 UK) for every model, Elo from blind pairwise votes: the board that isolates model quality from voice choice. | https://artificialanalysis.ai/text-to-speech/leaderboard/controlled-voice |

Decisions this table forces: Remotion stays at a pinned 4.0.x with `licenseKey: "free-license"`
and a licensing note that an automated pipeline is an Automators-tier use under 5.0 once four or
more people, contractors included, operate it. The visual reviewer is Qwen3.8-27B at W4A16 from a
community export, served by vLLM with AWQ/GPTQ kernels; FP8, NVFP4 and MXFP4 are not planned. The
forced aligner is MFA (MIT) or a permissively licensed CTC model, never MMS_FA. Of the TTS
candidates only Qwen3-TTS and Chatterbox may be selected by the benchmark harness.
