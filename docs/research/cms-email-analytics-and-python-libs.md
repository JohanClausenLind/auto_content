# CMS, email, analytics/attribution and Python library research

**Retrieved 2026-08-27.** All facts below were read from the cited page on that date via web fetch. Nothing was installed or run. Items that could not be confirmed from an official page are marked **UNVERIFIED**. Where a summariser returned paraphrase rather than a quotation, the wording is marked *(paraphrased)*.

---

## A. Article destinations

### 1. WordPress REST API (self-hosted)

**Authentication**
- "As of 5.6, WordPress has shipped with Application Passwords"; they are generated from "an Edit User page (wp-admin -> Users -> Edit User)"; "credentials can be passed along to REST API requests served over https:// using Basic Auth" (i.e. `Authorization: Basic base64(username:application-password)`). — https://developer.wordpress.org/rest-api/using-the-rest-api/authentication/
- Cookie auth requires a nonce with action `wp_rest`, sent via `_wpnonce` or the `X-WP-Nonce` header; "If no nonce is provided the API will set the current user to 0, turning the request into an unauthenticated request". — same page.

**`POST /wp/v2/posts`** — https://developer.wordpress.org/rest-api/reference/posts/
- `title` "The title for the post."; `content` "The content for the post."; `excerpt`; `slug` "An alphanumeric identifier for the post unique to its type."
- `status` "A named status for the post." One of: `publish`, `future`, `draft`, `pending`, `private`. The reference lists `future` but gives no prose explaining that `future` + a future `date` schedules the post (behaviour is core WP semantics; **UNVERIFIED from this page**).
- `date` "The date the post was published, in the site's timezone."; `date_gmt` also accepted.
- `categories` "The terms assigned to the post in the category taxonomy."; `tags` "The terms assigned to the post in the post_tag taxonomy." (integer term IDs).
- `featured_media` "The ID of the featured media for the post."; `meta` "Meta fields."; also `author`, `format`, `sticky`, `template`, `comment_status`, `ping_status`, `password`.

**`POST /wp/v2/media`** — https://developer.wordpress.org/rest-api/reference/media/
- Creatable fields include `title`, `alt_text`, `caption`, `post`, `date`, `slug`, `status`. The reference page itself does not describe the upload transport; the controller code does:
  - Multipart: `WP_REST_Attachments_Controller::upload_from_file()` "Handles an upload via multipart/form-data ($_FILES)", reading `$files['file']`. — https://developer.wordpress.org/reference/classes/wp_rest_attachments_controller/upload_from_file/
  - Raw body: `upload_from_data()` errors with `rest_upload_no_content_type` "No Content-Type supplied." and `rest_upload_no_content_disposition` "No Content-Disposition supplied."; `rest_upload_invalid_disposition` says Content-Disposition "needs to be formatted as `attachment; filename=\"image.png\"`". — https://developer.wordpress.org/reference/classes/wp_rest_attachments_controller/upload_from_data/

**Post meta / SEO fields**
- Registering meta with `register_meta`/`register_post_meta` and `'show_in_rest' => true` means "that key will be accessible through the REST API" and "that field's value will be exposed on a `.meta` key in the endpoint response, and WordPress will handle setting up the callbacks for reading and writing". — https://developer.wordpress.org/rest-api/extending-the-rest-api/modifying-responses/
- Yoast adds `yoast_head` (an "escaped, _prefabricated_ blob" of meta tags/schema) and `yoast_head_json` ("the _raw data_, as a series of key/value pairs"). "The Yoast REST API is currently read-only, and doesn't currently support `POST` or `PUT` calls to update the data." — https://developer.yoast.com/customization/apis/rest-api/
- Writing Yoast's `_yoast_wpseo_title` / `_yoast_wpseo_metadesc` via the `meta` field requires site-side `register_post_meta` for those keys (a plugin/snippet on the WordPress site). **UNVERIFIED** — not documented on either official page above.

### 2. Ghost Admin API

Source: https://docs.ghost.org/admin-api/ (ghost.org/docs/admin-api/ 301-redirects here) and the `.md` sub-pages below.

- Base URL: `https://{admin_domain}/ghost/api/admin/`. Version pinning via header `Accept-Version: v{major}.{minor}`.
- Auth: an Admin API key is "an id and secret, separated by a colon" from a Custom Integration. JWT header `{"alg": "HS256", "kid": {id}, "typ": "JWT"}`; payload `exp` (maximum 5 minutes after `iat`), `iat` (seconds), `aud: "/admin/"`; sign with the secret after you "Decode the hexadecimal secret into the original binary byte array". Send `Authorization: Ghost $token`. — https://docs.ghost.org/admin-api.md
- Post object attributes include `slug, title, lexical, html, feature_image, featured, status, visibility, published_at, custom_excerpt, meta_title, meta_description, canonical_url, og_image, og_title, og_description, twitter_image, twitter_title, twitter_description, tags, authors, newsletter, email_segment, email_only`; status values `draft`, `published`, `scheduled`, `sent`. — https://docs.ghost.org/admin-api/posts/overview.md
- `POST /admin/posts/`: only `title` is required. Lexical is the native/default format; "The post creation endpoint is also able to convert HTML into Lexical" via `?source=html`; "this operation is lossy". Raw HTML can be preserved by wrapping in `<!--kg-card-begin: html-->…<!--kg-card-end: html-->`. Tags "that cannot be matched are automatically created"; unmatched author falls back to the owner. — https://docs.ghost.org/admin-api/posts/creating-a-post.md
- `PUT /admin/posts/{id}/`: "The `updated_at` field is required as it is used to handle collision detection". — https://docs.ghost.org/admin-api/posts/updating-a-post.md
- Sending as newsletter: "the `newsletter` query parameter must be passed when publishing or scheduling the post, containing the newsletter's `slug`"; optional `email_segment` "containing a valid NQL filter for members" (examples `status:free`, `status:-free`, `all` default). — https://docs.ghost.org/admin-api/posts/sending-a-post.md
- Requirement that `status: scheduled` needs a future `published_at`: **UNVERIFIED** (not in fetched text).
- `POST /ghost/api/admin/images/upload/`: `multipart/form-data`; field `file` (required); `purpose` (default `image`; also `profile_image`, `icon`); `ref` echoed back; formats WEBP, JPEG, GIF, PNG, SVG (ICO for icons). Response `{"images":[{"url": "...", "ref": "..."}]}`. — https://docs.ghost.org/admin-api/images/uploading-an-image.md
- Official JS client `@tryghost/admin-api` (constructor `url`, `key`, `version`; `api.posts.add({title, html}, {source: 'html'})`; `api.images.upload({file})`; "designed for server-side usage only"). — https://docs.ghost.org/admin-api/javascript
- Python: no official client is listed in Ghost docs. Web search surfaces only community projects (rycus86/ghost-client, milnomada/ghost-cli, educationwarehouse/edwh-ghost). Confirmed: **no official Python SDK**.

### 3. Markdown/HTML export — skipped per brief.

---

## B. Newsletter / email

### 4. Listmonk

- License: "listmonk is licensed under the AGPL v3 license." — https://github.com/knadh/listmonk
- Latest release: **v6.2.0**, 2026-06-26 ("has important security fixes"). — https://github.com/knadh/listmonk/releases
- Auth: "HTTP API requests support BasicAuth and a Authorization `token` headers": `curl -u "api_user:token"` or `Authorization: token api_user:token`. API users are created in Admin → Users. — https://listmonk.app/docs/apis/apis/
- API users/roles exist "starting with v4.0.0"; "API users are meant for interacting with the listmonk APIs programmatically" and get "an automatically generated secret token". — https://listmonk.app/docs/roles-and-permissions/ ; v4 upgrade note: "go to Settings -> Users and create a new API user with the necessary permissions" and remove legacy `admin_username`/`admin_password`. — https://listmonk.app/docs/upgrade/
- `POST /api/campaigns` — https://listmonk.app/docs/apis/campaigns/ — required: `name`, `subject`, `lists` (IDs), `type` (`regular`|`optin`), `content_type` (`richtext`, `html`, `markdown`, `plain`, `visual`), `body`. Optional: `from_email`, `altbody` ("Alternate plain text body for HTML (and richtext) emails"), `send_at` ("Timestamp to schedule campaign"), `template_id`, `tags`, `headers`, `messenger`.
- `PUT /api/campaigns/{campaign_id}/status` — `status` one of `scheduled`, `running`, `paused`, `cancelled`.
- `POST /api/campaigns/{campaign_id}/test` — same params as create plus required `subscribers` ("List of subscriber e-mails").
- `GET /api/templates` ("Retrieve all templates"); template `type` is `campaign`, `campaign_visual` or `tx`; also `GET /api/templates/{id}`, `/preview`, `POST`, `PUT`, `PUT .../default`, `DELETE`. — https://listmonk.app/docs/apis/templates/

### 5. Buttondown

- Auth: "passing the token key in the `Authorization` HTTP header, prepended with the string `Token `"; keys managed at buttondown.com/keys. — https://docs.buttondown.com/api-authentication
- Versioning: date-based; "The current version is `2026-04-01`"; pin with header `X-API-Version`; otherwise the newsletter's pinned version, else "the latest version". — https://docs.buttondown.com/api-versioning
- `POST /emails` (`/v1/emails`) body `{"subject", "body", "status"}`; body is Markdown by default, HTML auto-detected. Optional header `X-Buttondown-Live-Dangerously: true` to allow bodies beginning with `---`. — https://docs.buttondown.com/api-emails-create and https://docs.buttondown.com/drafting-emails-via-the-api
- Scheduling: `{"status": "scheduled", "publish_date": "<future ISO-8601>"}`. — https://docs.buttondown.com/scheduling-emails-via-the-api
- Enums (official OpenAPI repo enums.json): **EmailStatus** `about_to_send` ("queued to send… within a few minutes"), `deleted`, `draft`, `errored`, `imported`, `in_flight`, `managed_by_rss`, `paused`, `resending`, `scheduled`, `sent`, `suppressed`, `throttled`, `transactional`. **EmailType** `archival`, `churned`, `free`, `premium`, `private` ("sent to all subscribers but not viewable in web archives"), `public` ("sent to all subscribers and available in web archives"). — https://raw.githubusercontent.com/buttondown/openapi/main/enums.json (repo: https://github.com/buttondown/openapi)
- Pricing/tier: buttondown.com/features/api FAQ says "The API is available on all plans, including free." — https://buttondown.com/features/api. However the docs pages above carry a `{% paidFeature feature="api" /%}` marker, and the pricing page (free up to 100 subscribers, add-ons $9/$29/$79/mo) does not mention API at all. **Conflict — see Open questions.**

### 6. Mailchimp Marketing API v3

- "The root url for the API is `https://<dc>.api.mailchimp.com/3.0/`"; Basic auth with username `anystring` and the API key as password; the dc is the suffix of the key ("`…-us6`, then the data center subdomain is `us6`"). "The Marketing API is currently on version 3.0." — https://mailchimp.com/developer/marketing/docs/fundamentals/
- `POST /campaigns` — `type` required, enum `regular`, `plaintext`, `absplit`, `rss`, `variate`; `recipients.list_id` required ("the unique list id"); `settings.subject_line`, `settings.preview_text`, `settings.title`, `settings.from_name` ("not an email address"), `settings.reply_to` ("required for sending"). — https://us22.api.mailchimp.com/schema/3.0/Definitions/Campaigns/POST.json (official JSON schema referenced by https://api.mailchimp.com/schema/3.0/Swagger.json)
- `PUT /campaigns/{campaign_id}/content` — `html` "The raw HTML for the campaign."; `plain_text` "If left unspecified, we'll generate this automatically."; also `template`, `url`, `archive`. — https://us22.api.mailchimp.com/schema/3.0/Definitions/Campaigns/Content/PUT.json
- `POST /campaigns/{campaign_id}/actions/schedule` — `schedule_time` "The UTC date and time to schedule the campaign for delivery in ISO 8601 format. Campaigns may only be scheduled to send on the quarter-hour (:00, :15, :30, :45)."; `timewarp` and `batch_delivery` are mutually exclusive. — https://us22.api.mailchimp.com/schema/3.0/Definitions/Campaigns/Actions/Schedule.json
- `POST /campaigns/{campaign_id}/actions/test` — `test_emails` array, `send_type` enum `html`|`plaintext`. — https://us22.api.mailchimp.com/schema/3.0/Definitions/Campaigns/Actions/Test.json
- `POST /campaigns/{campaign_id}/actions/send` — "All other campaigns will send immediately". — https://mailchimp.com/developer/marketing/api/campaigns/
- Merge tags: `*|UNSUB|*` "is required by law and our Terms of Use". — https://mailchimp.com/help/all-the-merge-tags-cheat-sheet/
- Python SDK `mailchimp-marketing`: latest **3.0.80**, uploaded 2022-11-02; PyPI `license`/`classifiers`/`requires_python` are empty; "generated by Swagger Codegen". — https://pypi.org/pypi/mailchimp-marketing/json. Repo LICENSE is the proprietary "Client Library License Agreement… between you… and The Rocket Science Group LLC" (not OSI). — https://raw.githubusercontent.com/mailchimp/mailchimp-marketing-python/master/LICENSE ; README says "Python 2.7 and 3.4+" and repo is autogenerated. — https://github.com/mailchimp/mailchimp-marketing-python

### 7. MJML

- npm `mjml` latest **5.4.0**, license **MIT**; no `engines` field in the package manifest (Node requirement **UNVERIFIED**). — https://registry.npmjs.org/mjml/latest ; https://github.com/mjmlio/mjml/blob/master/packages/mjml/package.json ; GitHub release v5.4.0 page shows "29 Jun" (year not printed; **UNVERIFIED**). — https://github.com/mjmlio/mjml/releases/tag/v5.4.0
- Python alternative `mjml-python` **1.4.1** (2026-06-30), MIT, "A Python wrapper for MRML (Rust port of MJML)"; "without a Node.js service, external API, or subprocess"; abi3 wheels for Windows/macOS/Linux incl. musllinux and arm64; CPython 3.8+. — https://pypi.org/project/mjml-python/

---

## C. Analytics / attribution

### 8. GA4 Measurement Protocol and Data API

**Measurement Protocol** — https://developers.google.com/analytics/devguides/collection/protocol/ga4/reference , …/sending-events , …/validating-events , …/ga4
- Endpoint `POST https://www.google-analytics.com/mp/collect?measurement_id=…&api_secret=…` (EU: `https://region1.google-analytics.com/mp/collect`). Web streams use `measurement_id`; app streams use `firebase_app_id`. Returns 2xx if received (no validation).
- Body: `client_id` (required for web), optional `user_id`, `timestamp_micros` ("backdated up to 72 hours"), `user_properties`, `consent`, `events[]` each with `name` and `params`. Recommended params `session_id` and `engagement_time_msec` "to ensure accurate session metrics".
- Limits: "Up to 25 events can be sent per request."; "Up to 25 parameters can be sent per event."; 25 user properties/request; event & param names ≤40 chars; param values ≤100 chars (500 for GA360); body <130 kB.
- Debug: `https://www.google-analytics.com/debug/mp/collect` returns `{"validationMessages": [...]}` (empty on success).
- Intent: "The intent of the Measurement Protocol is to augment automatic collection through gtag, Tag Manager, and Google Analytics for Firebase, not to replace it."; "only partial reporting may be available" when used alone.

**Data API v1** — https://developers.google.com/analytics/devguides/reporting/data/v1/…
- `POST https://analyticsdata.googleapis.com/v1beta/{property=properties/*}:runReport`; scopes `analytics.readonly` or `analytics`; body `dimensions[]`, `metrics[]`, `dateRanges[]`, `dimensionFilter`, `limit` (default 10,000, max 250,000). — …/rest/v1beta/properties/runReport
- Dimensions: `sessionSource` "The source that acquired the session", `sessionMedium`, `sessionCampaignName` "The marketing campaign name for a session", `sessionCampaignId`, `sessionDefaultChannelGroup`. — …/api-schema
- Metrics: `sessions`, `keyEvents`, `totalRevenue` used in the Traffic-acquisition predefined report (…/predefined-reports); `keyEvents` "The count of key events" (…/realtime-api-schema). Changelog 2024-05-06: `conversions` → `keyEvents`, `isConversionEvent` → `isKeyEvent`. — …/changelog
- Auth: service account / ADC; the account email must be granted access on the GA4 property; Python client `google.analytics.data_v1beta.BetaAnalyticsDataClient`. — …/quickstart
- Quotas (standard / 360): 200,000 / 2,000,000 tokens per property per day; 40,000 / 400,000 per hour; 14,000 / 140,000 per project per property per hour; 10 / 50 concurrent requests; 10 / 50 server errors per hour. — …/quotas
- Status: v1beta is the production surface; v1alpha carries experimental features (latest changelog entry 2026-04-23 adds conversion reporting to alpha). — …/changelog

### 9. Shopify webhooks and attribution

- Versioning: "Version names are date-based (for example, `2026-04`)"; new version "every three months at the beginning of the quarter"; "supported for a minimum of 12 months". Webhooks reference currently displays **2026-07**. — https://shopify.dev/docs/api/usage/versioning ; https://shopify.dev/docs/api/webhooks
- Topics (GraphQL enum, 2026-07): `ORDERS_CREATE` "Occurs whenever an order is created. Requires at least one of the following scopes: read_orders, read_marketplace_orders."; `ORDERS_PAID` "Occurs whenever an order is paid."; `CHECKOUTS_CREATE` "Occurs whenever a checkout is created. Requires the `read_orders` scope." — https://shopify.dev/docs/api/admin-graphql/latest/enums/WebhookSubscriptionTopic
- Verification: "base64-encoded HMAC signature in the `X-Shopify-Hmac-SHA256` header"; "compute HMAC-SHA256 of the raw request body using your app's client secret as the key, then compare it to the decoded header value" (timing-safe compare). Dedupe with `X-Shopify-Webhook-Id`. — https://shopify.dev/docs/apps/build/webhooks/verify-deliveries
- Delivery: respond `200 OK`; "one-second connection timeout and a five-second timeout for the entire request"; **"If Shopify receives no response or an error, it retries 8 times over the next 4 hours. After 8 consecutive failures, the subscription is automatically deleted if it was configured using the Admin API"**. The brief's "19 attempts over 48 h" is NOT what current docs say. — https://shopify.dev/docs/apps/build/webhooks/subscribe/https ; also "up to eight times in a four-hour period" — https://shopify.dev/docs/apps/build/webhooks/troubleshooting-webhooks
- Attribution fields: REST Order has `landing_site`, `referring_site`, `note_attributes`, `source_name` *(paraphrased)*; "The REST Admin API is a legacy API as of October 1, 2024. Starting April 1, 2025, all new public apps must be built exclusively with the GraphQL Admin API." — https://shopify.dev/docs/api/admin-rest/2026-07/resources/order
- GraphQL `Order.customerJourneySummary` "The customer's visits and interactions with the online store before placing the order."; `Order.landingPageUrl`/`referrerUrl` are deprecated; `customAttributes` replaces note attributes. — https://shopify.dev/docs/api/admin-graphql/latest/objects/Order . `CustomerJourneySummary` has `firstVisit`, `lastVisit`, `momentsCount`, `customerOrderIndex`, `daysToConversion`, `ready`. — …/objects/CustomerJourneySummary . `CustomerVisit` has `landingPage`, `referrerUrl`, `referralCode`, `source`, `sourceType`, `sourceDescription`, `occurredAt`, `utmParameters {campaign, content, medium, source, term}`. — …/objects/CustomerVisit

### 10. Shlink

- License **MIT**; Docker image `shlinkio/shlink` (tags `stable`, `latest`, `X.X.X`). — https://github.com/shlinkio/shlink ; https://shlink.io/documentation/install-docker-image/
- Latest release **v5.1.5** ("22 Jun" — year not printed on page; **UNVERIFIED**). — https://github.com/shlinkio/shlink/releases
- Auth: "always provide a `X-Api-Key: {api_key}` header on every request"; keys via `shlink api-key:generate`; since v4.3 keys are hashed at rest; roles since v2.5.0. — https://shlink.io/documentation/api-docs/authentication/
- OpenAPI: servers `/rest/v{version}/`, spec info.version 3.0; paths include `/rest/v{version}/short-urls`, `/short-urls/{shortCode}`, `/short-urls/{shortCode}/visits`, `/visits`, `/tags/{tag}/visits`. — https://raw.githubusercontent.com/shlinkio/shlink/develop/docs/swagger/swagger.json
- `POST /rest/v3/short-urls` body: `longUrl` (required), `customSlug`, `tags` ("The list of tags to set to the short URL."), `title`, `validSince`, `validUntil`, `maxVisits`, `findIfExists`, `domain`, `shortCodeLength` (≥4, default 5), `crawlable`, `forwardQuery`, `pathPrefix`. — …/paths/v1_short-urls.json and …/definitions/ShortUrlEdition.json
- `GET /rest/v3/short-urls/{shortCode}/visits` ("List visits for short URL"): query `domain`, `startDate`, `endDate`, `page`, `itemsPerPage`, `excludeBots`; visits have `referer`, `date`, `userAgent`, `visitLocation`, `potentialBot`, `visitedUrl`. — …/paths/v1_short-urls_{shortCode}_visits.json
- Docker env: `DEFAULT_DOMAIN`, `IS_HTTPS_ENABLED`, `INITIAL_API_KEY` (since 3.3.0), `DB_*`, optional `GEOLITE_LICENSE_KEY`.

### 11. UTM parameters (GA4)

https://support.google.com/analytics/answer/10917952
- `utm_id` "Campaign ID."; `utm_source` "Referrer, for example: google, newsletter4, billboard"; `utm_medium` "Marketing medium, for example: cpc, banner, email"; `utm_campaign` "Product, slogan, promo code, for example: spring_sale"; `utm_term` "Paid keyword"; `utm_content` "Use to differentiate creatives…"; `utm_source_platform` "The platform responsible for directing traffic…"; `utm_creative_format` and `utm_marketing_tactic` exist but each "isn't currently reported in Google Analytics properties."

---

## D. Python libraries (PyPI, latest stable, license, Python 3.12)

Source for each row: `https://pypi.org/project/<name>/` (or `/pypi/<name>/json`) unless noted. "3.12" = `Programming Language :: Python :: 3.12` classifier present.

| Package | Latest | Released | License (as stated) | Requires-Python | 3.12 |
|---|---|---|---|---|---|
| temporalio | 1.32.0 | "24 Aug" (year not shown; UNVERIFIED) | MIT | 3.10+ (badge) | yes |
| fastapi | 0.141.1 | 2026-07-29 | MIT | >=3.10 | yes |
| uvicorn | 0.52.4 | 2026-08-19 | BSD-3-Clause | >=3.10 | yes |
| pydantic | 2.13.4 | 2026-05-06 | MIT (GitHub sidebar) | 3.9+ (docs) | yes |
| pydantic-settings | 2.15.0 | 2026-08-07 | MIT | >=3.10 | yes |
| SQLAlchemy | 2.0.52 | 2026-08-11 | MIT | >=3.7 | yes |
| alembic | 1.19.1 | 2026-08-08 | MIT | >=3.10 | yes |
| asyncpg | 0.31.0 | 2025-11-24 | Apache-2.0 | >=3.9.0 | yes |
| psycopg (v3) | 3.3.4 | 2026-05-01 | LGPL-3.0-only | >=3.10 | yes |
| httpx | 0.28.1 | 2024-12-06 | BSD-3-Clause | >=3.8 | yes |
| typer | 0.27.1 | 2026-08-03 | MIT | >=3.10 | yes |
| rich | 15.0.0 | 2026-04-12 | MIT | >=3.9.0 | yes |
| structlog | 26.1.0 | 2026-06-06 | MIT OR Apache-2.0 | >=3.10 | yes |
| Authlib | 1.7.2 | 2026-05-06 | BSD-3-Clause (dual: BSD / commercial) | >=3.10 | yes |
| litellm | 1.98.0 | 2026-08-22 | MIT; "Features under the LiteLLM Commercial License" (enterprise) | >=3.10,<3.15 | yes |
| instructor | 1.16.0 | 2026-08-27 | MIT | >=3.9,<4.0 | classifiers empty |
| PyNaCl | 1.6.2 | 2026-01-01 | Apache-2.0 | >=3.8 | yes |
| cryptography | 50.0.1 | 2026-08-25 | Apache-2.0 OR BSD-3-Clause | >=3.9 (!=3.9.0,!=3.9.1) | yes |
| boto3 | 1.43.81 | 2026-08-26 | Apache-2.0 | >=3.10 | yes |
| argon2-cffi | 25.1.0 | 2025-06-03 | MIT | >=3.8 | yes |
| webauthn (py_webauthn) | 3.0.0 | 2026-06-29 | BSD-3-Clause | >=3.10 | only `Python :: 3` |
| pyotp | 2.10.0 | 2026-06-14 | MIT | >=3.8 | yes |
| polars | 1.44.1 | 2026-08-26 | MIT | >=3.10 | yes |
| duckdb | 1.5.5 | 2026-07-22 | MIT | >=3.10.0 | yes |
| Pint | 0.25.3 | 2026-03-19 | BSD | >=3.11 | yes |
| trafilatura | 2.2.0 | 2026-07-31 | Apache-2.0; "Versions prior to v1.8.0 are under GPLv3+ license." | >=3.10 | yes |
| pypdf | 6.16.2 | 2026-08-23 | BSD-3-Clause | >=3.9 | yes |
| pillow | 12.3.0 | 2026-07-01 | MIT-CMU | >=3.10 | yes |
| opencv-python-headless | 5.0.0.93 | 2026-07-02 | Apache-2.0 | >=3.6 | yes |
| python-magic | 0.4.27 | 2022-06-07 | MIT; needs system libmagic | >=2.7,!=3.0–3.4 | no (classifiers stop at 3.9) |
| OpenTimelineIO | 0.18.1 | 2025-11-09 | Apache-2.0 | >3.9.0 | yes |
| pywebpush | 2.4.0 | 2026-08-06 | MPL-2.0 | >=3.10 | only `Python :: 3` |
| pytest | 9.1.1 | 2026-06-19 | MIT | >=3.10 | yes |
| pytest-asyncio | 1.4.0 | 2026-05-26 | Apache-2.0 | >=3.10 | yes |
| hypothesis | 6.165.10 | 2026-08-16 | MPL-2.0 | >=3.10 | yes |
| respx | 0.23.1 | 2026-04-08 | BSD-3-Clause | >=3.8 | yes |
| ruff | 0.16.5 | 2026-08-27 | MIT | >=3.7 | yes |
| pyright | 1.1.411 | 2026-06-25 | MIT; downloads Node.js at runtime (recommends `pyright[nodejs]`) | >=3.7 | yes |
| mjml-python | 1.4.1 | 2026-06-30 | MIT | 3.8+ | wheels abi3 |
| mailchimp-marketing | 3.0.80 | 2022-11-02 | none on PyPI; proprietary Mailchimp Client Library License in repo | unspecified | no classifiers |

Notes: litellm PyPI page states MIT plus a commercial-license feature set; the repo `enterprise/` directory licensing was not fetched (**UNVERIFIED** beyond the PyPI statement). temporalio classifiers list 3.10–3.14. pydantic PyPI meta sidebar was not captured; license taken from the GitHub sidebar (MIT) and Python floor from https://pydantic.dev/docs/validation/latest/get-started/install/ ("Python 3.9+").

---

## Decisions this research supports

1. **WordPress**: use Application Passwords over HTTPS with Basic auth; create posts with `status=draft|publish|future` (+`date`), set `categories`/`tags` as term IDs and `featured_media` from a prior `POST /wp/v2/media` (multipart `file` or raw body with `Content-Disposition: attachment; filename="…"` + `Content-Type`). SEO fields cannot be written through Yoast's REST surface; plan a small site-side `register_post_meta` shim if Yoast title/description must be set.
2. **Ghost**: build a tiny HS256 JWT (PyJWT) — no official Python SDK; post HTML with `POST /ghost/api/admin/posts/?source=html`, upload images via multipart to `/images/upload/`, send email with `?newsletter=<slug>&email_segment=<nql>` at publish/schedule time; always pass `Accept-Version`.
3. **Newsletter**: Listmonk (AGPL-3.0, self-hosted, v6.2.0) fits a fully self-hosted stack — Basic auth with an API user, `content_type=html`, `send_at` + `PUT …/status scheduled`, `POST …/test`. Buttondown is the simplest hosted option (Token auth, Markdown body, date-versioned API). Mailchimp is viable but its Python SDK is stale (2022) and proprietary-licensed; call the REST API with httpx instead and honour the quarter-hour `schedule_time` rule and `*|UNSUB|*`.
4. **Email templating**: `mjml-python` (MIT, mrml wheels) avoids a Node runtime in the control plane; keep `mjml` npm 5.4.0 only if Node-based tooling is already present.
5. **Attribution**: canonical UTM set = `utm_source/medium/campaign/content/term/id` (+`utm_source_platform`); skip `utm_creative_format`/`utm_marketing_tactic` (not reported). GA4 MP supplements gtag only — pair with `session_id`/`engagement_time_msec`; pull results via Data API v1beta `runReport` on `sessionSource/sessionMedium/sessionCampaignName` × `sessions/keyEvents/totalRevenue`. Shopify: subscribe to `orders/create`, `orders/paid`, `checkouts/create` (2026-07), verify `X-Shopify-Hmac-SHA256`, respond within 5 s, dedupe on `X-Shopify-Webhook-Id`, and design for **8 retries / 4 h** (not 48 h); read UTMs from GraphQL `customerJourneySummary.{first,last}Visit.utmParameters`. Shlink (MIT, `shlinkio/shlink`) gives per-link click logs via `X-Api-Key`.
6. **Python stack**: every core library (temporalio, fastapi, uvicorn, pydantic v2, sqlalchemy 2, alembic, httpx, typer, rich, structlog, boto3, pytest family, ruff, pyright) is permissively licensed and declares 3.12. Licensing watch-list: psycopg is LGPL-3.0 (dynamic linking fine, note it); pywebpush and hypothesis are MPL-2.0; Authlib is BSD with a commercial option; litellm has a commercial enterprise tier. Prefer `cryptography` (Apache/BSD, very active) for envelope encryption; `pynacl` (Apache-2.0) remains acceptable. `python-magic` is unmaintained since 2022 and lacks 3.12 classifiers — treat as a risk.

## Open questions / UNVERIFIED

- WordPress `future` status semantics (post publishes at `date`) — not described on the reference page fetched.
- Writing Yoast `_yoast_wpseo_*` meta via `meta` — requires site-side `register_post_meta`; not documented by Yoast.
- Ghost: whether `status: scheduled` mandates a future `published_at` (expected, not in fetched text); official statement that no Python client exists (docs simply list none).
- Buttondown API tier: features page says "available on all plans, including free"; docs pages mark API how-tos as a paid feature; pricing page silent. Confirm with Buttondown before relying on the free tier. The generic `openapi.json` summary returned a different (smaller) enum set than `enums.json`; `enums.json` was treated as authoritative.
- MJML Node.js minimum version — no `engines` in package.json; MJML 5.4.0 release year not printed.
- Shlink v5.1.5 and temporalio 1.32.0 release years not printed on GitHub pages.
- GA4 Data API `sessions` metric description text — the api-schema page truncated before the metrics table; name confirmed via predefined-reports and basics pages only.
- Shopify: exact current "latest stable" per versioning table (page lists 2025-07…2027-01 incl. upcoming); the webhooks reference shows 2026-07 as current. Order REST `landing_site`/`referring_site` descriptions were paraphrased by the fetch tool.
- litellm `enterprise/` directory license — only the PyPI "Commercial License" statement was confirmed.
- Ghost `email_segment` full grammar beyond the three documented examples.
