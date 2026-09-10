# Mixkit sound-effects licence — what the terms actually permit

Retrieved 2026-09-09

Research behind the `mixkit` pack in `assets/sfx` (295 sounds, recipe
`skills/audio/sfx/mixkit.json`). Every quote below was fetched on 2026-09-09 from the URL given
and is reproduced verbatim. Sections marked **DECISION** are ours, not sourced.

Companion to `docs/research/2026-09-07-video-sfx-and-ambience-library.md`, which covers what the
library needs and how a bed is made loopable; this file covers only whether we are allowed to have
it.

---

## A. Where the licence text lives

The licence index at <https://mixkit.co/license/> renders only cards with "View License" buttons;
the text itself is fetched by the page's own JavaScript from `/license/modal/<licenseKey>/`. For
sound effects the key is `sfxFree`, so the operative document is

    https://mixkit.co/license/modal/sfxFree/          (fetched 2026-09-09)

reachable in a browser as <https://mixkit.co/license/#sfxFree>. Both `/licenses/sfx_free/` and
`/license/sfxFree/` return 404 — worth recording, because a future check that finds a 404 has not
found a revoked licence.

## B. Mixkit Sound Effects Free License — verbatim

> Items under the Mixkit Sound Effects Free License can be used in your commercial and
> non-commercial projects for free.
>
> You are licensed to use the Item to create an End Product that incorporates the Item as well as
> other things, so that it is larger in scope and different in nature than the Item. You're
> permitted to download, copy, modify, distribute and publicly perform the Sound Effect Items on
> any web or social media platform, in podcasts and in video games, as well as in films and
> presentations distributed on CDs, DVDs, via TV or radio broadcast or internet based video on
> demand services.
>
> You can't redistribute the Item on its own, as stock, in a tool or template, or with source
> files. You're also not allowed to claim them as your own or register them on any rights
> management service.
>
> There are some important limits to these rights, described in our User Terms.

The page also lists permitted uses explicitly: "YouTube videos", "Social Media video posts",
"Online marketing ads", "Educational Purposes", "Music videos", "Commercial projects",
"Filmmaking", "Video games", "TV & Radio broadcasts", "CDs & DVDs".

**Attribution is not mentioned anywhere in the licence.** Neither is a project count, a term, or a
territory. Absence is not the same as a written grant, so the honest statement is that the licence
imposes no attribution obligation, not that it "grants perpetual worldwide rights".

## C. Envato User Terms — the incorporated limits

From <https://mixkit.co/terms/> (fetched 2026-09-09). Clause 9, "Restrictions", is the operative
one. You must not:

> rent, license, sublicense, sell, resell or otherwise commercially exploit or make Mixkit or any
> Item available to any third party (except as expressly contemplated by these User Terms)
> including aggregate or collate an Item(s) and make available on a stock or inventory basis;

> use Mixkit or its related systems and networks, or any Item, to build a similar or competitive
> product or service;

> sell physical or digital copies of Items without first altering them by applying human skill and
> effort, and incorporating other elements (merely printing an Item on an object such as a mug or
> shirt is not sufficient alteration);

> use scripts or bots to mass download Items (this includes using any means whatsoever to
> scrape/download the entire library and/or database of Items)

Clause 2 states the user receives "a non-exclusive license to use that Item. You do not acquire any
rights of ownership in that Item." Clause 11 lets Envato terminate the licence on breach. Clause 3
also incorporates "Envato's Acceptable Use Policy" and "Envato's Fair Use Policy" by reference;
neither renders as static text at `envato.com/acceptable-use-policy/` or
`elements.envato.com/acceptable-use-policy` (both returned an empty document to a plain fetch on
2026-09-09), so **their contents have not been read and nothing here relies on them**.

### AI training

**No clause in either document mentions artificial intelligence, machine learning, training or
datasets.** This is unlike the #GameAudioGDC bundle, whose agreement bans AI training explicitly.
The absence is recorded here rather than treated as permission — see the decision below.

## D. What this repository does with them — DECISION

Four consequences, all of which are already true of how `assets/sfx` is built and used:

1. **A rendered deliverable is exactly the licensed use.** "An End Product that incorporates the
   Item as well as other things, so that it is larger in scope and different in nature than the
   Item" is a film with a sound in it. Commercial use is granted in terms.
2. **`assets/sfx` must never be published as a library.** Clause 9's "aggregate or collate an
   Item(s) and make available on a stock or inventory basis" describes a public copy of this
   directory precisely. `.gitignore` already bars `/assets/**/*.flac` and the source pack lives
   outside the repo at `/mnt/fast/sound-libraries/mixkit`; both stay that way. This matches the
   restriction already recorded for the #GameAudioGDC half, so the rule does not become
   per-supplier: **no half of `assets/sfx` is redistributable.**
3. **No sound in `assets/sfx` feeds a model.** The Mixkit terms do not ban it and the
   #GameAudioGDC agreement does, and the library is a single mixed directory whose halves are
   deliberately interchangeable in a mix. A per-supplier rule inside one directory is a rule that
   will be got wrong, so the stricter one governs the whole: `assets/sfx` stays excluded from the
   `condition_sound` path and is never a conditioning, reference or training input to Stable
   Audio 3, MMAudio, ACE-Step or FoleyCrafter.
4. **The library must not become a competing service.** Clause 9's "build a similar or competitive
   product or service" bars shipping this as a stock sound service. It does not bar a production
   pipeline that consumes sounds, which is what this is.

Levelling, trimming, high-passing and loop-wrapping are the "modify" right being exercised. The
recordings stay Mixkit's: `manifest.json` records the supplier, the delivered filename, its
sha256, and the licence text on every entry.

## E. The `local-renders` pack — no third-party terms

The other 19 sounds ingested by the same builder (`skills/audio/sfx/local_renders.json`) were
rendered locally by the operator before ingest and carry no third-party licence.

**They are the one part of `assets/sfx` that is not reproducible from a recipe.** No generator,
prompt or seed was recorded for them, so `build_library.py`'s guarantee — same recipe, same bytes —
does not hold; they can only be re-ingested from the staged source files at
`/mnt/fast/sound-libraries/local-renders`. Their `use` lines were written from measurement
(fundamental, spectral centroid, decay to −20 dB re peak) rather than from a prompt, so nothing in
the README claims to know what made them.

Because their provenance is "the operator says so", they must not be treated as clear-of-rights
material for anything beyond this repo's own deliverables until their origin is established.
