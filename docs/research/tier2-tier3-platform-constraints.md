# Tier 2 / Tier 3 platform constraints for a solo self-hosted publisher

**Retrieved 2026-08-27.** Every fact below was read from the cited page on that date (via WebFetch, or via WebSearch snippet where a direct fetch was blocked — those are flagged). Anything not confirmed by a fetched page is marked **UNVERIFIED**. Nothing was installed or run.

Context: one person, own developer apps, posting to their **own** accounts. Sections follow (a) registration, (b) posting without review/audit, (c) post types & limits, (d) scheduling/drafts, (e) analytics, (f) comments/DMs, (g) automation/disclosure policy, (h) cost.

---

## 1. YouTube Data API v3 (+ YouTube Analytics API)

**(a) Registration.** Create a Google Cloud project, enable "YouTube Data API v3", create OAuth 2.0 credentials. — https://developers.google.com/youtube/v3/getting-started

**(b) Posting without audit.** Works, but uploads are forced private. Verbatim from the `videos.insert` reference: *"All videos uploaded via the `videos.insert` endpoint from unverified API projects created after 28 July 2020 will be restricted to private viewing mode. To lift this restriction, each API project must undergo an audit to verify compliance with the Terms of Service."* — https://developers.google.com/youtube/v3/docs/videos/insert
Audit = "YouTube API Services – Audit and Quota Extension Form"; audits are also the prerequisite for any quota increase. — https://developers.google.com/youtube/v3/guides/quota_and_compliance_audits
Practical consequence for a solo publisher: without a passed audit, API-uploaded videos stay private; you would have to flip them public manually in Studio (or via `videos.update` — whether an update from the same unaudited project can set `public` is **UNVERIFIED**).

**Quota (as documented today).** *"Projects that enable the YouTube Data API have a default quota allocation of 100 `search.list` calls, 100 `videos.insert` calls, and 10,000 units per day combined for all other endpoints."* Resets midnight PT. — https://developers.google.com/youtube/v3/determine_quota_cost and https://developers.google.com/youtube/v3/guides/quota_and_compliance_audits
`videos.insert`: *"Quota impact: 100 calls per day. A call to this method has a quota cost of 1 unit in the Video Uploads quota bucket."* — https://developers.google.com/youtube/v3/docs/videos/insert
NOTE: the historically cited "1600 units per upload" figure no longer appears on either page; uploads now sit in a separate 100-calls/day bucket. Treat "1600 units" as superseded.
Other costs (same quota page): `videos.update` 50, `videos.list` 1, `thumbnails.set` 50, `captions.insert` 400, `commentThreads.list` 1, `commentThreads.insert` 50, `comments.insert` 50, `comments.list` 1, `channels.list` 1, `playlistItems.insert` 50. *"Every API request, even if invalid, will cost at least one quota point."*

**(c) Post types & limits.**
- Video upload: max 256 GB, MIME `video/*` or `application/octet-stream`; scopes `youtube.upload` / `youtube` / `youtube.force-ssl` / `youtubepartner`. — https://developers.google.com/youtube/v3/docs/videos/insert
- Metadata: `snippet.title` ≤ 100 chars (no `<`/`>`), `snippet.description` ≤ 5000 bytes, `snippet.tags` ≤ 500 chars total; `status.privacyStatus` ∈ {private, public, unlisted}; `status.selfDeclaredMadeForKids`. No Shorts-specific fields exist. — https://developers.google.com/youtube/v3/docs/videos
- Thumbnail: `thumbnails.set`, ≤ 2 MB, `image/jpeg`|`image/png`; error path exists for accounts not permitted to set custom thumbnails. — https://developers.google.com/youtube/v3/docs/thumbnails/set
- Captions: `captions.insert`, ≤ 100 MB, `text/xml`/`application/octet-stream`/`*/*`, scope `youtube.force-ssl` or `youtubepartner`, 400 units. — https://developers.google.com/youtube/v3/docs/captions/insert
- Community posts: **not available** — the v3 reference lists no posts/community resource (resources: activities, captions, channelBanners, channels, channelSections, comments, commentThreads, i18n*, members, membershipsLevels, playlistItems, playlistImages, playlists, search, subscriptions, thumbnails, videoAbuseReportReasons, videoCategories, videos, watermarks). — https://developers.google.com/youtube/v3/docs

**(d) Scheduling / drafts.** `status.publishAt` (ISO 8601) schedules a premiere/publish, *only* when `status.privacyStatus=private`. A private upload is effectively a draft. — https://developers.google.com/youtube/v3/docs/videos

**(e) Analytics.** YouTube Analytics API `reports.query` with scope `yt-analytics.readonly` (or `yt-analytics-monetary.readonly`); `ids=channel==MINE`; metrics incl. views, estimatedMinutesWatched, likes, subscribersGained, averageViewDuration; dimensions day, video, country, etc. — https://developers.google.com/youtube/analytics/reference/reports/query

**(f) Comments.** Read: `commentThreads.list` (1 unit; filter `videoId` / `allThreadsRelatedToChannelId`; `maxResults` 1–100; API key OK for public comments, OAuth needed for `moderationStatus`). — https://developers.google.com/youtube/v3/docs/commentThreads/list
Top-level comment: `commentThreads.insert` (50 units, `youtube.force-ssl`). — https://developers.google.com/youtube/v3/docs/commentThreads/insert
Reply: `comments.insert` with `snippet.parentId` (50 units, `youtube.force-ssl`). — https://developers.google.com/youtube/v3/docs/comments/insert
DMs: none in API.

**(g) Automation policy.** Developer Policies: *"you must not automate or trigger views, uploads, comments, likes, dislikes, or other actions without the user's prior specific and express consent"*; users must keep final control before submission; projects inactive 90+ days may be disabled; must not *"confuse, deceive, defraud, mislead, misrepresent, defame, abuse, stalk, threaten, spam, surprise, or harass anyone"*. — https://developers.google.com/youtube/terms/developer-policies

**(h) Cost.** No fee on any fetched page; quota extension via form (audit). Pricing not mentioned → free (UNVERIFIED that no paid tier exists, but none documented).

---

## 2. Meta — Instagram Graph API & Facebook Pages

**(a) Registration.** Meta app (Business type for Instagram API with Instagram Login). App starts in **Development mode**. — https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/get-started , https://developers.facebook.com/docs/development/build-and-test/app-modes

**(b) Posting to own accounts without App Review — YES, via roles + Standard Access.**
- *"Apps in Development mode can only request permissions from role users, and only permissions with standard or advanced access levels."* Role users = admins, developers, testers. *"Any data generated while an app is in Development mode, such as test posts, can only be seen by role users."* (This statement is about test data visibility; the fetched page does not say published IG/Page posts are hidden — **UNVERIFIED** whether real posts made by a dev-mode app are publicly visible; in practice they are published to the real account.) — https://developers.facebook.com/docs/development/build-and-test/app-modes
- *"Permissions with Standard Access can only be requested from app users who have a role on the requesting app."* Standard Access is granted automatically, no App Review. Advanced Access = any user, requires App Review + Business Verification per permission. — https://developers.facebook.com/docs/graph-api/overview/access-levels
- App Review page: review not needed *"if your app will only be used by app users who have a role on the app itself"*; *"unapproved permissions can only be requested from app users who have a role on the requesting app."* — https://developers.facebook.com/docs/resp-plat-initiatives/individual-processes/app-review
- Permission reference lists `instagram_content_publish`, `instagram_business_content_publish`, `instagram_manage_comments`, `instagram_manage_messages`, `instagram_manage_insights`, `pages_manage_posts`, `pages_read_engagement`, `pages_manage_engagement`, `pages_messaging`, `read_insights` as "App Review required" — this applies to **Advanced Access**; Standard Access for role users needs none. — https://developers.facebook.com/docs/permissions
- Rate limits: IG Platform BUC *"Calls within 24 hours = 4800 * Number of Impressions"*; Pages BUC *"4800 * Number of Engaged Users"*; app-level *"200 * Number of Users"* per hour. No separate dev-mode formula. — https://developers.facebook.com/docs/graph-api/overview/rate-limiting

**Two Instagram API variants.**
- *Instagram API with Instagram Login*: Business/Creator account, **no Facebook Page required**; publishing, comments, messaging, insights, mentions; host `graph.instagram.com`; scopes `instagram_business_basic`, `instagram_business_content_publish`, `instagram_business_manage_comments`, `instagram_business_manage_messages`, `instagram_business_manage_insights`; no hashtag search, no ads/tagging. Tokens: short-lived 1 h, long-lived 60 d (refreshable). — https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login , /get-started
- *Instagram API with Facebook Login for Business*: IG professional account linked to a Page; adds hashtag search and business discovery; scopes `instagram_basic`, `instagram_content_publish`, `pages_read_engagement`, `pages_show_list`. — https://developers.facebook.com/docs/instagram-platform

**(c) Instagram post types & limits (Content Publishing).**
- Flow: `POST /{ig-user-id}/media` (container) → poll `GET /{container-id}?fields=status_code` (`FINISHED|IN_PROGRESS|PUBLISHED|EXPIRED|ERROR`, poll once/min ≤ 5 min) → `POST /{ig-user-id}/media_publish`. Check quota at `/{ig-user-id}/content_publishing_limit`. — https://developers.facebook.com/docs/instagram-platform/content-publishing
- Rate limit: *"100 API-published posts within a 24-hour moving period"* (carousel = 1). NOTE: docs now say 100, not the older 25. — same page
- Media must be on a public server (Meta cURLs it). *"JPEG is the only image format supported."* Image ≤ 8 MB, aspect 4:5–1.91:1, width 320–1440 px. Caption ≤ 2200 chars, ≤ 30 hashtags, ≤ 20 @tags. — https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/media
- Carousel: `media_type=CAROUSEL` + `children`, items created with `is_carousel_item=true`, up to 10 items (images/videos/mixed), cropped to first image. — content-publishing page
- Reels: `media_type=REELS`, MP4/MOV, 3 s–15 min, ≤ 300 MB, 9:16 recommended; `cover_url`, `thumb_offset`, `share_to_feed`. Stories: `media_type=STORIES`. — ig-user/media reference
- Text-only posts: not supported (image or video required). Threads (multi-post chains): N/A.

**(c′) Facebook Pages.**
- `POST /{page-id}/feed`: `message` or `link` required; `published=false` → unpublished; `scheduled_publish_time` *"Must be date between 10 minutes and 75 days from the time of the API request."* Perms `pages_manage_posts`, `pages_read_engagement`, `pages_show_list` + Page token with CREATE_CONTENT. — https://developers.facebook.com/docs/graph-api/reference/page/feed
- `POST /{page-id}/photos`: `url` or `source`; `.jpeg, .bmp, .png, .gif, .tiff`, ≤ 10 MB; multi-photo post = upload each with `published=false` then `/feed` with `attached_media[]`. — https://developers.facebook.com/docs/graph-api/reference/page/photos
- `POST /{page-id}/videos`: `source`/`file_url`, `title`, `description`, `published`, `scheduled_publish_time` (10 min–6 months). File size/duration limits not on fetched pages → **UNVERIFIED**. — https://developers.facebook.com/docs/graph-api/reference/page/videos
- Reels: `POST /{page-id}/video_reels` (upload_phase start/finish); MP4, 9:16, min 540×960, 3–90 s, 24–60 fps; `video_state=PUBLISHED|SCHEDULED` (+`scheduled_publish_time`, 10 min–29 days); limit *"30 API-published posts within a 24-hour moving period."* — https://developers.facebook.com/docs/video-api/guides/reels-publishing

**(d) Scheduling / drafts.** Facebook Pages: native (`scheduled_publish_time`, `published=false`). Instagram: none — containers expire after 24 h; your scheduler must call `media_publish` at the right time.

**(e) Analytics.** IG media insights (`/{ig-media-id}/insights`): views, reach, likes, comments, shares, saved, total_interactions, ig_reels_avg_watch_time, etc.; perms `instagram_business_manage_insights` (IG Login) or `instagram_manage_insights`+`instagram_basic`+`pages_read_engagement` (FB Login); data may lag up to 48 h; `impressions` deprecated for media after 2024-07-02. — https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-media/insights
Pages: `read_insights` permission. — https://developers.facebook.com/docs/permissions

**(f) Comments & DMs.**
- IG comments: read via media `comments` edge, reply `POST /{ig-comment-id}/replies`; perm `instagram_manage_comments` / `instagram_business_manage_comments`. — https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-comment
- Page comments: `GET /{object-id}/comments` (`filter=toplevel|stream`), `POST /{object-id}/comments` with `message`; perm `pages_manage_engagement` (or MODERATE task). — https://developers.facebook.com/docs/graph-api/reference/object/comments
- IG messaging (IG Login): *"Your app has 24 hours to respond to any message sent from an Instagram user to your app user."* *"Only after an Instagram user has sent your app user's Instagram professional account a message can your app send a message to the Instagram user."* Webhooks required; perm `instagram_business_manage_messages`. — https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/messaging-api
- Messenger: *"Businesses have up to 24 hours to respond to a user."* Tags (e.g. HUMAN_AGENT, 7 days) extend; some tags Messenger-only. — https://developers.facebook.com/docs/messenger-platform/policy/policy-overview

**(g) Automation policy.** Developer Policies prohibit spam incl. *"creating bots either manually or automatically, at very high frequencies"*, and *"Don't confuse, deceive, defraud, mislead, spam or surprise anyone."* No explicit bot-disclosure label requirement found. — https://developers.facebook.com/devpolicy/

**(h) Cost.** No fee mentioned on any fetched page (free).

---

## 3. TikTok Content Posting API

**(a) Registration.** TikTok for Developers account → "Connect an app" → add products (Login Kit, Content Posting API) → verify URL/domain ownership → submit for review (demo videos, up to 5 × 50 MB). **Sandbox mode** lets you test without review. — https://developers.tiktok.com/doc/getting-started-create-an-app

**(b) Posting without audit — allowed but private-only.** Verbatim: *"All content posted by unaudited clients will be restricted to private viewing mode. Once you have successfully tested your integration, to lift the restrictions on content visibility, your API client must undergo an audit to verify compliance with our Terms of Service."* Error `unaudited_client_can_only_post_to_private_accounts`. Unaudited clients: only `SELF_ONLY` privacy, max **5 users posting per 24 h**. — https://developers.tiktok.com/doc/content-posting-api-get-started , https://developers.tiktok.com/doc/content-posting-api-reference-direct-post , https://developers.tiktok.com/doc/content-sharing-guidelines
Scopes: `video.publish` (Direct Post), `video.upload` (Upload to inbox / MEDIA_UPLOAD). — get-started / upload-video pages

**Mandatory per-post UX (Content Sharing Guidelines).** Must call `POST /v2/post/publish/creator_info/query/` before each post and use its `privacy_level_options`, `comment_disabled`, `duet_disabled`, `stitch_disabled`, `max_video_post_duration_sec`, `creator_nickname`, `creator_avatar_url` (20 req/min/token). *"users must manually select the privacy status from a dropdown and there should be no default value."* Interaction toggles: *"Users must manually turn on these interaction settings and none should be checked by default."* Commercial-content toggle off by default; "Your Brand" → label *"Promotional content"*, "Branded Content" → *"Paid partnership"*; publish button disabled until a choice is made when toggle on. Consent text: *"By posting, you agree to TikTok's Music Usage Confirmation."* (+ Branded Content Policy when applicable). Must show preview; no watermarks/logos; user may edit text/hashtags. — https://developers.tiktok.com/doc/content-posting-api-reference-query-creator-info , https://developers.tiktok.com/doc/content-sharing-guidelines

**(c) Post types & limits.**
- Video Direct Post: `POST /v2/post/publish/video/init/`; `post_info`: `title` ≤ 2200 UTF-16 runes, `privacy_level` (PUBLIC_TO_EVERYONE | MUTUAL_FOLLOW_FRIENDS | FOLLOWER_OF_CREATOR | SELF_ONLY), `disable_duet/comment/stitch`, `video_cover_timestamp_ms`, `brand_content_toggle`, `brand_organic_toggle`, `is_aigc`; `source_info`: `FILE_UPLOAD` (chunked) or `PULL_FROM_URL` (verified domain). **6 requests/min per user token.** — direct-post reference
- Video Upload (inbox/draft): `POST /v2/post/publish/inbox/video/init/`, scope `video.upload`; user must finish in TikTok app via inbox notification. 6 req/min. — https://developers.tiktok.com/doc/content-posting-api-reference-upload-video
- Video specs: MP4 (rec.)/WebM/MOV; H.264 (rec.)/H.265/VP8/VP9; 360–4096 px; 23–60 fps; ≤ 4 GB; up to 10 min via API (account limit governs); chunks 5–64 MB (last ≤ 128 MB), 1–1000 chunks, sequential. — https://developers.tiktok.com/doc/content-posting-api-media-transfer-guide
- Photo posts: `POST /v2/post/publish/content/init/`, `media_type=PHOTO`, `post_mode=DIRECT_POST|MEDIA_UPLOAD`; `PULL_FROM_URL` only; up to **35 photos**, `photo_cover_index`; title ≤ 90 runes, description ≤ 4000 runes; WebP/JPEG, ≤ 20 MB each, ≤ 1080p; `brand_content_toggle`/`brand_organic_toggle` required. — https://developers.tiktok.com/doc/content-posting-api-reference-photo-post , media-transfer guide
- Text-only posts / carousel-of-videos: not supported.

**(d) Scheduling / drafts.** No API scheduling. Draft = MEDIA_UPLOAD / inbox upload (user completes in-app).

**(e) Analytics.** Display API `/v2/video/list/` (scope `video.list`) returns `view_count`, `like_count`, `comment_count`, `share_count`, `duration`, `create_time`, `cover_image_url`, `share_url` for the user's **public** videos; ≤ 20 per page. — https://developers.tiktok.com/doc/tiktok-api-v2-video-list

**(f) Comments/DMs.** No comment read/reply or DM endpoint found in Content Posting or Display API docs. — https://developers.tiktok.com/doc/display-api-overview (**UNVERIFIED** whether any other TikTok product exposes creator comments)

**(g) Disclosure.** Built into API: `brand_content_toggle`, `brand_organic_toggle`, `is_aigc`; label text mandated (see above). — content-sharing-guidelines

**(h) Cost.** No fees mentioned on registration page (free).

---

## 4. X (Twitter) API v2

**(a) Registration.** Developer Console (console.x.com) — credit-based. — https://docs.x.com/x-api/getting-started/pricing

**(b) Posting to own account.** No app review; you need credits. `POST /2/tweets` requires `tweet.write` (+ `tweet.read`, `users.read`). — https://docs.x.com/x-api/posts/creation-of-a-post

**(c) Post types & limits.**
- Text: 280 weighted chars; URLs = 23; emoji/CJK = 2. — https://docs.x.com/resources/fundamentals/counting-characters
- Long posts: changelog says API can *"create longform Posts with a length of 25k instead of 4k"* (2024-08-20). — https://docs.x.com/changelog . That long posts require an X Premium account is stated only in official dev-forum threads (search snippet) → **partially verified**: https://devcommunity.x.com/t/how-to-post-280-4000-characters-via-api-v2/191156
- Media: `media.media_ids` 1–4; poll 2–4 options, 5–10 080 min; `quote_tweet_id` **Enterprise-only**; self-serve: max 1 cashtag; *"Replies are only permitted if the original post's author has explicitly summoned the replying account by @mentioning them or quoting one of their posts"* (from manage-posts intro — scope of this rule is unclear; **UNVERIFIED** whether it applies to replying to your own posts). — https://docs.x.com/x-api/posts/creation-of-a-post , https://docs.x.com/x-api/posts/manage-tweets/introduction
- Threads: `reply.in_reply_to_tweet_id` chaining. — same pages
- Media upload v2: `POST /2/media/upload` INIT/APPEND/FINALIZE + STATUS; categories `tweet_image|tweet_gif|tweet_video|amplify_video`; image 5 MB, GIF 15 MB, video 512 MB. — https://docs.x.com/x-api/media/quickstart/media-upload-chunked , https://docs.x.com/x-api/media/introduction . v2 media launched 2025-01-16 (changelog). v1.1 `media/upload.json` sunset **2025-06-09** per official forum announcement (search snippet; page fetch 403) — https://devcommunity.x.com/t/media-upload-endpoints-update-and-extended-migration-deadline/241818
- Rate limits: `POST /2/tweets` 100/15 min per user, 10 000/24 h per app; `DELETE` 50/15 min; `GET /2/users/:id/tweets` 900/15 min user. — https://docs.x.com/x-api/fundamentals/rate-limits

**(d) Scheduling / drafts.** None in the API (no field on creation endpoint). — creation-of-a-post

**(e) Analytics.** Reads are billed (see h); `GET /2/users/:id/tweets` returns public metrics. "Owned Reads" of your own data cost $0.001/resource. — pricing page

**(f) Engagement.** Replies via `POST /2/tweets` w/ `reply`; DM create $0.015/request; DM reads $0.010/resource. — pricing page

**(g) Automation policy (Developer Guidelines).** *"Enable the 'Automated' profile label"*; *"State clearly that it's a bot and who operates it. Example: 'Bot by @yourcompany'"*; must be *"associated with a human-managed account"*; allowed: *"scheduled content (news, weather, quotes)"* with *"no unsolicited @mentions"*; replies only *"if user engaged first. Max 1 reply per interaction"*; prohibited: *"Posting identical content across multiple accounts"*, auto-likes (*"Likes must be directly initiated by the authenticated user"*), bulk follows, auto-DMs. — https://docs.x.com/developer-guidelines (help.x.com automation page returned 403)

**(h) Cost — pay-per-use only (official launch 2026-02-06).** No subscriptions, no free tier on the pricing page. Post create **$0.015/request**; post *with URL* **$0.200/request**; post read $0.005/resource; user read $0.010; owned reads $0.001; DM create $0.015; webhook events $0.005–0.010. Cap: *"Pay-per-usage plans are capped at 3 million Post reads per monthly billing cycle."* Enterprise via form. xAI credit bonus 10–20 % above $200 spend. *"Prices are subject to change."* — https://docs.x.com/x-api/getting-started/pricing , https://docs.x.com/changelog (2025-10-20 pilot; 2026-02-06 launch; 2026-04-16 owned-reads + removal of Following/Likes/Quote-Posts from self-serve). Legacy Free/Basic/Pro tiers: not documented anywhere fetched → treat as retired (**UNVERIFIED** retirement date).

---

## 5. Threads API

**(a) Registration.** Meta app with **Threads use case**; separate Threads app ID/secret; base `graph.threads.net`. Testing requires the Threads user to accept a **Threads tester** invite (Threads app → Settings → Website permissions). — https://developers.facebook.com/docs/threads/get-started , /overview

**(b) Without App Review.** Dev mode + Threads tester role grants permissions immediately; App Review + publishing needed only *"to make your app available to general users."* — get-started. Tokens: short-lived 1 h → long-lived 60 d, refresh via `GET /refresh_access_token`. Permissions: `threads_basic`, `threads_content_publish`, `threads_manage_replies`, `threads_read_replies`, `threads_manage_insights`, `threads_delete`, `threads_location_tagging`, `threads_profile_discovery`. — overview

**(c) Post types & limits.** `POST /{threads-user-id}/threads` (`media_type` TEXT | IMAGE | VIDEO | CAROUSEL) → `POST /{threads-user-id}/threads_publish`. *"Text posts are limited to 500 characters."* Media must be *"on a public server"*. Image JPEG/PNG ≤ 8 MB, width 320–1440, ≤ 10:1 aspect. Video MOV/MP4, H.264/HEVC, ≤ 1 GB, ≤ 5 min. Carousel *"minimum of two children"*, *"up to 20 images, videos, or a mix"*. `reply_control` ∈ {everyone, accounts_you_follow, mentioned_only, parent_post_author_only, followers_only}; `enable_reply_approvals`. Rate: *"250 API-published posts within a 24-hour moving period"*; 1000 replies/24 h; 100 deletes/24 h; check `/threads_publishing_limit`. — https://developers.facebook.com/docs/threads/posts , /overview , /reply-management
Container status: `GET /{container-id}?fields=status,error_message` (EXPIRED | ERROR | FINISHED | IN_PROGRESS | PUBLISHED), poll once/min ≤ 5 min; containers expire after 24 h. — https://developers.facebook.com/docs/threads/troubleshooting

**(d) Scheduling / drafts.** No API scheduling; overview only tells apps that schedule to *"enforce the publishing rate limit."* Draft = unpublished container (24 h).

**(e) Analytics.** Media insights: views, likes, replies, reposts, quotes, shares. User insights: views, likes, followers_count, follower_demographics (≥ 100 followers). Perm `threads_manage_insights`. — https://developers.facebook.com/docs/threads/insights

**(f) Replies.** `GET /{media-id}/replies` (top-level) and `GET /{media-id}/conversation` (flattened all depths), fields id/text/username/timestamp/media_type/has_replies/root_post/replied_to/is_reply/hide_status, `reverse`. Reply = create container with `reply_to_id` → publish. Hide: `POST /{reply-id}/manage_reply?hide=true`. Pending approvals: `/pending_replies`, `/manage_pending_reply`. No DMs. — https://developers.facebook.com/docs/threads/retrieve-and-manage-replies/replies-and-conversations/ , /reply-management

**(g) Automation.** Governed by Meta Developer Policies (see §2g). No Threads-specific bot label found.

**(h) Cost.** Free (no fee mentioned).

---

## 6. Pinterest API v5

Note: most `developers.pinterest.com` reference pages are JS-rendered and returned only navigation to WebFetch; facts below come from the pages that did render, plus search snippets of official pages (flagged).

**(a) Registration & tiers.** Create app → **Trial access** first; **Standard access** requires application with *"a video recording of your app completing an action using the Pinterest API"*, OAuth demo, privacy policy. — https://developers.pinterest.com/docs/key-concepts/access-tiers/

**(b) Posting under Trial.** Allowed, but *"all Pins and Boards created with Trial access are only visible to their creator as Sandbox entities"* → real publishing needs Standard access. Trial *"rate limited based on calls per day/per app"*; Standard *"calls per minute/per user/per app."* — same page. Numbers (search snippet of https://developers.pinterest.com/docs/reference/rate-limits/ — direct fetch 404): Trial **1000 requests/day**, Standard per-user per-minute categories. **UNVERIFIED** exact Standard numbers.

**(c) Post types.** `POST /pins`: `board_id`, `title`, `description`, `link`, `alt_text`, `media_source.source_type` ∈ {`image_url`, `image_base64`, `multiple_image_urls`, `multiple_image_base64` (carousel), `video_id` (+`cover_image_url`)}. Video: `POST /media` → upload to `upload_url` → poll `GET /media/{media_id}` → create pin. Scopes `pins:write`, `boards:write`. Character/size/duration limits not on fetched page → **UNVERIFIED**. — https://developers.pinterest.com/docs/work-with-organic-content-and-users/create-boards-and-pins/

**(d) Scheduling.** No `publish_time`/scheduling field found in create-pin docs. Developer Terms (search snippet, https://developers.pinterest.com/terms/): if your app schedules Pins, *the end user must choose each Pin to be published*. **UNVERIFIED** existence of any scheduled-pin API.

**(e) Analytics.** Endpoints exist: `GET /pins/{pin_id}/analytics`, `GET /user_account/analytics`, top-pins, multi-pin (search results: https://developers.pinterest.com/docs/api/v5/pins-analytics/ , /user_account-analytics/); metrics incl. `impression`, `pin_click`, `save` (snippet). Page bodies not fetchable → details **UNVERIFIED**.

**(f) Comments/DMs.** No comment or message endpoints found. **UNVERIFIED** (likely absent).

**(g) Automation.** Terms clause on scheduling (above); nothing else fetched.

**(h) Cost.** Free (no fee found).

---

## 7. LinkedIn

**(a) Registration.** Developer Portal app (needs an associated Company Page — **UNVERIFIED** on fetched pages). **Open, self-serve products:** "Sign in with LinkedIn using OpenID Connect" (`profile`, `email`) and **"Share on LinkedIn" (`w_member_social`)** — *"available to all developers... via self-service."* Everything else (Community Management API, Advertising API) is *"vetted"* and requires approval. — https://learn.microsoft.com/en-us/linkedin/shared/authentication/getting-access

**(b) Posting to own profile without approval — YES** with `w_member_social`. Share on LinkedIn doc shows `POST https://api.linkedin.com/v2/ugcPosts` (text / ARTICLE / IMAGE / VIDEO via `assets?action=registerUpload`); rate limit **150 requests/member/day, 100 000/app/day**. — https://learn.microsoft.com/en-us/linkedin/consumer/integrations/self-serve/share-on-linkedin
The versioned **Posts API** (`POST /rest/posts`, headers `Linkedin-Version: YYYYMM`, `X-Restli-Protocol-Version: 2.0.0`) *"replaces the ugcPosts API"* and lists `w_member_social` as a valid permission. Whether a Share-on-LinkedIn-only app may call `/rest/*` (vs `/v2/ugcPosts`) is **UNVERIFIED** (Posts API lives under Community Management docs). — https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/posts-api , /contentapi-migration-guide
Organization (Page) posting needs `w_organization_social` → Community Management API (Development tier → Standard tier with screencast; must upgrade within 12 months; Dev tier default limits 500/app, 100/member). — https://learn.microsoft.com/en-us/linkedin/marketing/community-management/community-management-overview

**(c) Post types & limits (Posts API).** Organic: text, image, video, document, article, **multiImage (2–20 images)**, poll; **carousel = sponsored only**. `lifecycleState`: *"PUBLISHED is the only accepted field during creation"* (DRAFT appears only in responses). `visibility` PUBLIC | CONNECTIONS | LOGGED_IN | CONTAINER. Commentary in "little" text format (escape `|{}@[]()<>#\*_~`). Text max **3000 chars** — stated on the legacy UGC Post page (search snippet: https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/ugc-post-api); Posts schema page gives no number → **partially verified**. — posts-api, post-api-schema, multiimage-post-api, little-text-format
Images: `POST /rest/images?action=initializeUpload` (owner person/org URN) → PUT to `uploadUrl`; < 36 152 320 px, JPG/GIF/PNG, GIF ≤ 250 frames; `w_member_social` tokens are write-only for `/rest/images`. — https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/images-api
Video: `POST /rest/videos?action=initializeUpload` → 4 MB parts → `finalizeUpload`; 3 s–30 min, 75 KB–500 MB (spec table; `fileSizeBytes` max 5 GB), MP4; optional captions (SRT, English) & thumbnail. — https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/videos-api

**(d) Scheduling / drafts.** None (creation must be PUBLISHED). — post-api-schema

**(e) Analytics.** Member analytics (follower/post/video statistics) are Community Management API features; `r_member_social` is *"closed"* (*"We're not accepting access requests"*). Org analytics need `r_organization_social`/`rw_organization_admin`. Self-serve `w_member_social` gives **no read/analytics**. — community-management-overview, getting-access

**(f) Comments.** `GET /rest/socialActions/{urn}/comments`, `POST .../comments` (`actor`, `message`), nested via `parentComment`; reading member posts' comments requires the restricted `r_member_social(_feed)`; writing via `w_member_social`. — https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/network-update-social-actions . No DM API.

**Tokens.** Access token **60 days** (`expires_in` 5184000). *"LinkedIn does not generate long-lived access tokens."* Programmatic refresh tokens (365 d) *"for all approved Marketing Developer Platform (MDP) partners"* / *"a limited set of partners"* — not for self-serve; otherwise re-run OAuth (screen bypassed if still logged in & token unexpired). — https://learn.microsoft.com/en-us/linkedin/shared/authentication/authorization-code-flow , /programmatic-refresh-tokens

**(g) Automation.** Nothing bot-specific fetched (**UNVERIFIED**; LinkedIn API Terms of Use not fetched).

**(h) Cost.** Free (no fee on fetched pages).

---

## 8. Reddit (Data API)

Direct fetches of reddit.com / redditinc.com / support.reddithelp.com were blocked; sources are the official GitHub archive wiki and official pages read through a reader proxy (flagged).

**(a) Registration.** App types: web app, installed app, **script** (personal-use, runs on developer-controlled hardware, supports password grant for the developer's own account). Tokens 1 h; `duration=permanent` yields refresh token. — https://github.com/reddit-archive/reddit/wiki/OAuth2
Data API access is requested via Reddit's support form; commercial use via enterprise form. — https://support.reddithelp.com/hc/en-us/articles/14945211791892-Developer-Platform-Accessing-Reddit-Data (via proxy)

**(b) Posting without review.** No app review for script apps; free for non-commercial: *"You cannot use any Reddit developer tools and services for commercial purposes without first getting our permission."* Commercial = *"any use of our services by a business or on behalf of a business or as part of a monetized product."* — same page (via proxy)

**(c) Post types.** `POST /api/submit`: `kind` ∈ {link, self, image, video, videogif}; `title` ≤ 300 chars; `text` (markdown), `url`, `sr`, `nsfw`, `spoiler`, `flair_id` (≤ 36), `sendreplies`; scope `submit`. Gallery (`/api/submit_gallery_post`) and media lease (`/api/media/asset.json`) **not in the documented API** → **UNVERIFIED/undocumented**. — https://www.reddit.com/dev/api (via proxy)

**(d) Scheduling / drafts.** None in Data API.

**(e) Analytics.** None beyond post fields (score, num_comments via `read`). **UNVERIFIED** (no analytics endpoint in docs).

**(f) Comments/DMs.** `GET /comments/{article}` (scope `read`); `POST /api/comment` with `thing_id`, `text` (scope `submit`; `privatemessages` for message replies); `GET /api/v1/me` (`identity`). — /dev/api (via proxy)

**(g) Rules.** Rate: *"100 queries per minute (QPM) per OAuth client ID"* averaged over 10 min; unauthenticated traffic heavily throttled; *"Clients must authenticate with a registered OAuth token. We can and will freely throttle or block unidentified Data API users."* User-Agent `<platform>:<app ID>:<version string> (by /u/<reddit username>)`. — https://support.reddithelp.com/hc/en-us/articles/16160319875092-Reddit-Data-API-Wiki (via proxy). Archive rules: *"NEVER lie about your user-agent"* (spoofing → ban). — https://github.com/reddit-archive/reddit/wiki/API . Must comply with Developer Terms, Data API Terms, Responsible Builder Policy (terms text itself not fetched → **UNVERIFIED** wording).

**(h) Cost.** Free for non-commercial; commercial requires agreement (a per-call price circulates in third-party sources; **UNVERIFIED** from official page).

---

## Tier table (solo developer, own accounts, as of 2026-08-27)

| Platform | Solo-feasible posting today | Draft / scheduling in API | Engagement API | Cost |
|---|---|---|---|---|
| YouTube | Upload works; **private-only until audit**; 100 uploads/day bucket + 10k units | `publishAt` (private→public); private = draft | Comments read/reply (commentThreads/comments); no DMs | Free |
| Instagram (IG Login or FB Login) | Full publish (image, carousel ≤10, Reels, Stories) in dev mode for role users; 100 posts/24h; public media URLs | None (24 h containers) | Comments read/reply; DMs reply-only within 24 h window | Free |
| Facebook Page | Feed/photos/videos/Reels for role users | Native `scheduled_publish_time`, `published=false` | Comments read/reply; Messenger 24 h window | Free |
| TikTok | Video + photo (≤35) Direct Post but **SELF_ONLY until audit**; or Upload-to-inbox draft; 6 req/min; strict per-post UX rules | Inbox draft only; no scheduling | None found | Free |
| X | Post/thread/media; 280 chars (25k needs Premium acct) | None | Replies; DMs (paid reads) | Pay-per-use: $0.015/post, $0.20/post with URL, $0.005/post read |
| Threads | Text/image/video/carousel ≤20; 500 chars; 250/24h; tester role | None (24 h containers) | Replies read/reply/hide; no DMs | Free |
| Pinterest | Trial = sandbox-only pins; **Standard access needed** for real pins; 1000 req/day trial | None found | None found | Free |
| LinkedIn (member) | Text/image/video/multiImage via `w_member_social`; 150 req/member/day; 60-day token, manual re-auth | None | Write comments; reading needs closed `r_member_social` | Free |
| Reddit | Script app; self/link/image/video via `/api/submit`; 100 QPM | None | Comments read/reply | Free (non-commercial) |

## Open questions / UNVERIFIED

1. YouTube: can `videos.update` from an unaudited project set a video to `public`, or is the private restriction enforced at update too? Is the "1600 units" figure gone for all projects or only new-quota-model projects?
2. Meta: confirm that posts published by a Development-mode app to a role user's real IG account/Page are publicly visible (the app-modes page only says *test data* is limited to role users).
3. Facebook Page video (`/videos`) max file size / duration — not on fetched pages.
4. X: whether the "replies only when summoned" rule applies to replying to one's own posts (thread building); the exact retirement date of Free/Basic/Pro; Premium requirement for 25k-char posts (only forum evidence).
5. X: v1.1 media sunset date (2025-06-09) comes from an official forum post snippet, not fetched.
6. Pinterest: Standard-access per-minute limits; pin title/description/image/video limits; analytics metric list; any scheduled-pin endpoint; comment endpoints.
7. LinkedIn: whether a Share-on-LinkedIn-only app can call versioned `/rest/posts`, `/rest/images`, `/rest/videos` (vs legacy `/v2/ugcPosts` + `assets`); the 3000-char limit for Posts API (only stated on legacy UGC page); Company Page prerequisite for app creation; API Terms on automation.
8. Reddit: gallery posts and `/api/media/asset.json` lease are undocumented; Data API Terms wording (bot disclosure, commercial pricing) not fetched.
9. TikTok: existence of any comment API for creators; whether audit approval is realistic for a single-user personal tool.
10. Google/Meta/TikTok/LinkedIn/Pinterest pricing: no fee found on any page, but "free" is inferred from absence, not from an explicit statement.
