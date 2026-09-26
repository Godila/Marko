# SDD Progress Ledger — phase 0-1 (plan: docs/superpowers/plans/2026-09-02-phase-0-1-foundation-wb.md)

Task 0 (controller, env prep): complete — SSH key ~/.ssh/mpmt_beget on VM (password auth disabled), Docker 29.7.2 pre-installed on VM, postgres:16 container mpmt-pg on VM 127.0.0.1:5432 (dbs: mpmt, mpmt_test; password in secrets/vm-postgres.txt), SSH tunnel localhost:15432 (background bash exec_d89cc51c; restart cmd: ssh -i ~/.ssh/mpmt_beget -N -L 15432:127.0.0.1:5432 root@155.212.142.199). Local env: python=C:/Users/geor/AppData/Local/Programs/Python/Python313/python.exe (PATH python is 3.14 WITHOUT pip), node v24, no local docker.
Task 1: complete (commit 4ed5cdb, review clean; pytest 1 passed verified by controller, git status clean). Review Minors (for final review triage): [1] deps >=-only vs plan's "versions pinned" — lockfile later; [2] wb-specs/fixtures/prod contains real seller data (no creds) — decision needed before pushing to shared remote; [3] placeholder git email godila@local.
Task 2: complete (commit b3ae5c7, review clean; pytest 2 passed re-verified by controller). Review Minors: [1] setup_logging not idempotent without force=True — revisit when uvicorn lands; [2] settings/log/db interfaces lack direct unit tests (manual verified) — harden later; [3] .env CWD-relative.
Task 3: complete (commit 39d67c3, review clean). Notes for later tasks: [a] reuse conftest db fixture; [b] env.py needs journal/mt model imports in Tasks 8/12; [c] before autogenerate reset target DB to baseline; [d] audit principal_id has no FK by design; Review Minors: report wording (verbatim claim), dead drop_all line in conftest.
Task 4: complete (commits e1be11f+7113d6c after controller-resolved plan contradiction: require_scope returns PlatformToken; /v1/me needs read, returns presented token scopes; 403-test on token without read; pytest 7 passed re-verified). CONVENTION for later tasks: stage all writes, call audit() LAST (audit commits the whole session); no-token-expiry yet; scopes whitespace not normalized. Review Minors: add unknown-token 401 test eventually.
Task 5: complete (commits 8d63685 + a13870f fix per reviewer Important: TG HTTP failures logged w/o token leak; reviewer re-approved; suite 10 passed). Minor nit left: failure log omits alert text.
Task 6: complete (commits d1d9e44 + aaa3c59 fixes: backups/ ignored, alembic shipped in image, .gitattributes LF for *.sh; reviewer re-approved; pytest 10 passed). Minor for final review: backup.sh set -e without pipefail (empty dump possible on pg_dump fail); caddy 308 on http without -L.
Task 7: complete (commits 38f8d29 Caddyfile multi-line fix + 7f8460b deploy docs; LIVE: https://gis.adel-factory.ru/healthz {"status":"ok"}, UI 200, 6 containers Up, migration applied, backup smoke ok; controller smoke-verified; reviewer approved). VM: /opt/mpmt/repo on 7f8460b. PHASE 0 DONE — phase 1 starts.
Task 8: complete (commit d73ecb3; migration 0002_journal; suite 21 passed; review clean, 4 Minors). NOTES for Task 9+: [a] per-km concurrent IntegrityError possible on items PK (single worker = fine); [b] apply_event returns LIVE state on duplicates (use created flag); [c] pass fresh payload dicts (no aliasing); VM stays on 0001 until phase-1 deploy.
Task 9: complete (commits 96c489c + 92311e9 fix of reviewer CRITICAL: orders pagination stops on wb next=0 end signal + 1000-page cap; reviewer re-approved; suite 25 passed). WARNINGS for 10/11: excise gate disables 2/24h if db=None — worker MUST pass db; _gate_excise commits session (stage writes after excise_report); wb will hide orders older than 3 months (archive API) — backfill design. Review Minors: gate-before-http not test-asserted; Retry-After float-only; epoch-float stamps.
Task 10: complete (commit d6f4e52; suite 29 passed). FINDING: prod fixture has 1 byte-identical dup row (WB returned same return twice) → first all-FBW run is {"skipped_fbw":1021,"duplicates":1}, rerun all-duplicates 1022 — journal dedup proven on real data. WARNINGS for 11: [a] skip_fbw shares source_event_id with sale/return of same row — once ingested as skip_fbw, later FBS membership will NOT re-apply sale (dedup); worker MUST fetch orders/build fbs set BEFORE first ingest of a window; [b] per-event commit makes 1022-row ingest slow over tunnel (tests ~12 min; localhost on VM is fine).
Task 10: complete (commit d6f4e52; suite 29 passed; review approved). CRITICAL ordering constraint for Task 11: build fbs_rids from orders() BEFORE first ingest of an excise window (skip_fbw shares source_event_id with sale/return — journaled skip_fbw can never re-apply as sale; late FBS arrivals stay SKIPPED_FBW). Real fixture contains 1 byte-identical duplicate row (dedup absorbs). Minors: no ordering comment in ingest.py yet (add in Task 11); unknown op_type maps to return.
Task 11: complete (commit 453cc7a by controller after implementer timeout on long tunnel suite; full 31 passed; reviewer found Important cron/gate boundary race — fixed in follow-up commit: gate window 86400-120s, dead poll_orders_interval_sec removed; fast tests 11 passed). ⚠️ carried to final review: plan prose mentioned orders hourly cache + watermark — actual design fetches orders per poll (fresher), no watermark (dedup by journal).
(fix c63fa31 reviewer re-approved)
Task 12: complete (commit 3ffcf00 + test follow-up; suite 34 passed, emitter 4 passed; review approved). Known phase-1 risks (spec-prescribed, for phase-2 reconciliation): draft LK_RECEIPT usable as return primary; RECEIPT-type primary citing WB-<id>/OTHER doc; doc-level primary from products[0]; fiscal group merges different fiscal numbers (operator sees at manual submission); per-item commits non-atomic.
Task 13: complete (commits 73af7de + follow-up bounds/audit-test; suite 42 passed + api 8 passed; review approved). Notes: doc_id:0 = nothing pending (UI must handle); no offset pagination (phase-1 volumes); INN unvalidated (phase 2).
Task 14: complete (commits cfb09a9 + c90cdab fixes: empty-withdraw message persists via pre-state; Dockerfile npm ci with lockfile; reviewer re-approved; vite BUILD_OK). Carried: npmmirror lockfile URLs (integrity-pinned); token per-keystroke reload UX; no state-filter input in UI yet.
Task 15: complete (commit 46fa89e docs; LIVE phase-1: healthz ok, /v1/me owner, journal/stats {"SKIPPED_FBW":63} after first real excise poll, UI on vite bundle, migrations 0003 head, platform token in secrets/platform-token.txt + VM /root/; controller smoke-verified). Fixed during deploy: WBtoken mount path (repo/secrets vs /opt/mpmt/secrets) — doc updated. PHASE 0+1 DEPLOYED.
FINAL: whole-branch review NEEDS FIXES (4 Important) → fix commit 8e414a3 (worker crash TG alert, json-file 10m x3 log rotation all services, backup.sh integrity gate, healthz DB ping + last_poll kv marker) → reviewer re-approved READY TO TAG; full suite 45 passed; deployed to VM (8e414a3, tag phase-1); live smoke: healthz {"status":"ok","last_poll":null}, journal/stats {"SKIPPED_FBW":63}, worker awaiting 06:30 MSK. PHASE 0+1 COMPLETE.
DEFERRED to phase 2 (reviewer triage): emitter reconciliation set (doc-level primary, fiscal merge, oldest-receipt citation, non-atomic commits + SELECT FOR UPDATE), orders 3-month horizon backfill, Retry-After date form, INN validation, scopes whitespace, offset pagination, UI state filter + token UX, npmmirror lockfile URLs, setup_logging force, dead drop_all conftest, git email. CONDITIONAL: real seller data in fixtures — decide before any shared remote.
Phase-2 start: sign_gateway deployed (commit 6f0e461, migration 79f86c608c44 sign.tasks; suite 51 passed). Live smoke ok: /v1/sign/ping 200 signer-token; test-task (admin) -> lease returns task w/ payload; owner token granted admin scope on prod. SIGNER_TOKEN: secrets/signer-token.txt + VM /root/signer-token.txt (machine principal signer-agent, scope signer). Worker: signer watchdog (>2h silence -> TG). Test task 9fe80e53 left queued for the VM assistant's first e2e (signs "MPMT-SIGNER-TEST").
Signer agent (VM) LIVE: lease/results cycle works end-to-end (agent report). Controller fixed confusing healthz: now {wb_last_poll, signer_last_seen} (commits: healthz rename + prompt docs). signer_last_seen updating live from agent's leases.
Signer agent E2E SUCCESS (2026-09-03): tasks 3e656119 (auth_sign) and 1db24d4b (doc_sign) both done with CMS signatures (MII... base64), attempt=1, agent verified locally via VerifyCades. Earlier "no tasks reaching agent" explained: no test tasks existed after 9fe80e53 (agent itself closed it with error pre-cert). Platform clock NTP-synced (Beget fine; ~40s skew likely Windows VM side — advise w32tm /resync there). Full phase-2 signing loop proven: queue -> lease -> УКЭП CAdES -> results. Next: connector_mt auth smoke on ЧЗ sandbox using real auth_sign signature.
Phase 2 CORE COMPLETE (2026-09-03): connector_mt deployed (commits 8422286 + uuidToken fix + ui buttons). LIVE PROOF: platform->sign queue->agent(UKEP)->PROD ЧЗ simpleSignIn = AUTH_OK (uuid token, 36 chars, cached, expireDate honoured). ЧЗ verified our CAdES signature — the whole signing pipeline is trusted by the regulator. Full doc submit path ready (mt.docs draft->signing->submitted(external_id)->checked_ok; worker checks submitted every 10 min + TG). UI: Подать/Проверить buttons + ЧЗ uuid column. Migration 0005 external_id. Suite 57 passed. NOT yet exercised live: actual LK_RECEIPT/LP_RETURN submission (waits for first FBS sale — боевые документы не тестовые); "тормоз" >N batch confirmation effectively covered by manual button submission. Remaining phase-2 leftovers: external backup target choice; optional sandbox ЧЗ smoke of short-cis when first real doc goes (production will be the real test).
Infra note: local SSH tunnel to VM Postgres (15432) kept dropping (3x, provider kills long connections). Replaced with self-healing background loop (while true; ssh -N -L ...; sleep 3). If DB tests hang after compaction/restart — rerun the loop command from this line or check task exec_c92b9de9.

PHASE 3 PRE-EXECUTION CHECKPOINT (2026-09-03 evening, controller session):
- Sandbox (ТК) e2e discovery done: crpt-specs/tk-sandbox-e2e-discovery.md (commit 6760f06). User REGISTERED in ТК (login-kep, ТГ лёгпром) — TK auth LIVE via prod signer (uuidToken 10h). WB sandbox lacks excise-report (404) — full WB circle only with manual sale simulation. E2E script scripts/tk_e2e.py (commit 8303e8a, on VM /root/tk_e2e.py): auth→LK_RECEIPT→CHECKED_OK→LP_RETURN→CHECKED_OK, short cis 31 chars default, --full/--dry flags; GS-split bugfix (splitlines() breaks on \x1d). WAITING: user's test card moderation → order КМ in СУЗ ТК → ввод в оборот → КМ into /root/tk-km.txt (or local secrets/) → run: cat /root/tk_e2e.py | docker exec -i deploy-api-1 python - "$(cat /root/tk-km.txt)".
- NK live research (gate passed, commit 996604a): crpt-specs/nk-live-research-2026-09-03.md. KEY: НК methods live on True API base under /nk/* (NOT апи.национальный-каталог.рф/v3 — 404 there); categories/attributes need 10-DIGIT tnved («Данные не найдены» = app-level 404); brands?name= filter (YCPB=2102811; ADEL also used; 1000/page, limit≤10000); generate-gtins PROD ok (limit 11000/мес, drafts 4630...), ТК BLOCKED («только ГС1»); sandbox /nk/* full parity with prod; attr structure: attr_preset[]/attr_preset_only/attr_value_type[]/attr_multiplicity/first_layer/second_layer/dependent_attributes; 6109100000: 11 mandatory + 37 recommended (fixtures crpt-specs/nk-attrs-6109100000-{m,r}.json, nk-categories-full.json).
- Spec: docs/superpowers/specs/2026-09-03-phase3-nkmt-design.md (d6d31ab + 9cb3235 artifact + 304f5d2 UI-in-scope) — user-approved. Plan: docs/superpowers/plans/2026-09-03-phase3-nkmt.md (f30d8a5), 14 tasks TDD. Entry point for FRESH session: user says «начинай» → subagent-driven execution of the plan (mode choice pending; recommended subagent-driven as phases 0-2). Single known-unknown: «moderation» param location in feed body (dump lines 86873-88250; fake accepts both).
- Docker containers on VM are named deploy-{api,ui,worker,caddy,postgres,backup}-1 (NOT mpmt-api) + mpmt-pg (test pg). VM repo /opt/mpmt/repo; phase-3 deploy happens in Task 14.
Phase 3 execution (2026-09-02 fresh session, subagent-driven):
- Env: SSH tunnel 15432 relaunched as run_in_background self-healing loop (task exec_ae3844bc); app/.env recreated (MPMT_DATABASE_URL -> mpmt_test via tunnel; was missing -> tests hung). Baseline full suite: 58 passed (~13.5 min, tunnel latency).
- Task 1: complete (commits 296337f..6261c1d, review clean — Approved). Minor for final review: (a) unnamed UniqueConstraints in 0006 (cards.article, brand_cache.name); (b) nkmt JSON columns without MutableDict (house pattern, note for later mutating tasks). Plan-vs-repo fix: migrations live at app/alembic/versions/ (NOT app/src/mpmt/alembic).
- Task 2: complete (commits 6261c1d..a36d6b7, review clean — Approved). Minor for final review: (a) test_5xx_retries_then_ok sleeps real 5s (verbatim brief test; fix = sleeper injection in make_client); (b) no test pinning Authorization header / retry exhaustion. Deviation approved: feed() returns {"feed_id":...} without "raw" (brief self-contradiction, verbatim test wins; Task 8 uses only feed_id).
- Task 3: complete (commits a835b98..6c53ad3, review clean — Approved; predecessor agent timed out mid-task, finisher agent verified+committed). Minor for final review: unused `import time` in test_nk_dicts.py (brief-verbatim). Note: YCPB brand_cache not pre-seeded — by design, cache fills on first live resolve.
- Task 4: complete (commits 6c53ad3..8dd1f90, review clean — Approved). Minor for final review: (a) duplicate-declaration race -> 500 not 409 (pre-check + UNIQUE backstop, single-user scale ok; fix = IntegrityError catch); (b) TNVED_10 uses \d (unicode digits pass) — use [0-9]{10}; (c) unused monkeypatch arg in test. Deliberate: missing ?tnved= -> 422 (plan mandates 400 only for wrong values).
- Task 5: complete (commits 8dd1f90..6c5547f, review clean — Approved). Minor for final review: (a) defaults.get("techreg","") silent-empty vs brief's hard defaults["techreg"]; (b) header match sensitive to inner whitespace; (c) duplicate headers last-wins. NOTE downstream: openpyxl datetime cells -> "2026-01-01 00:00:00" str (declaration_date consumers beware).
- Task 6: complete (commits 6c5547f..3db2135, review clean — Approved; predecessor timed out, finisher verified+committed). Minor for final review: (a) \d unicode-digits in TNVED/DATE/GTIN regexes (same as Task 4 note — candidate batch fix [0-9]); (b) _check_preset fires on empty values (double error on already-failing rows; defaults fill prod path); (c) db=None skips RD registry check (verbatim-test-dictated; document in docstring); (d) attrs_model failures not cached -> per-row retry on same bad tnved.
- Task 7: complete (commits 3db2135..33f06c0 = feat + 2 fix rounds, review Approved after round-2 re-review). Round-1 fix: gtin clash no longer persisted on error cards + empty-gtin fill on upsert; round-2 fix: malformed row gtin gated by validate.GTIN_RE before all persistence paths; fill branch covered. Minor (deferred, reviewer confirmed): in-batch clash autoflush test, 400-on-corrupt-xlsx (now 500), file.filename=None -> 500, 502 only guards get_token, no per-article feedback on in-file dups, error_text substring vs exact.
- PROCESS CHANGE: implementer subagents time out (10 min inactivity) waiting on the ~15 min full suite. From Task 8 on: implementers run ONLY focused tests and commit on focused green; controller runs the full suite in background between tasks (serialized — single test DB).
- Task 8: complete (commits 33f06c0..c8d1466 = feat f09dbec + brand fix c8d1466, review Approved after re-review). MODERATION RESOLVED: entry-level field per dump (table 87190-87209, examples "moderation":1 at 87886/87968) -> "moderation":1 per entry. BRAND RESOLVED (plan defect, controller decision): /nk/feed brand is a STRING name (dump 87169-87185) -> validate now stores row["brand"] name in attributes["2504"]; resolve_brand kept as existence gate (id only in brand_cache). Minor (deferred): >500 chunking test; RuntimeError->502 mapping choice; mid-batch chunk-failure semantics (earlier chunks submitted, batch stays new|partial); UnknownBrand error-text branch untested at validate level. Final arbiter: Task 14 TK smoke. Suites: 84 passed.
- Task 9: complete (commits c8d1466..035e27b = feat 6cd2182 + multi-chunk fix 035e27b, review Approved after re-review). Fix (plan-mandated gap): refresh polls ALL feed_ids from stats (fallback batch.feed_id), aggregation any-Rejected-wins / all-Signed / all-Moderated / mixed-in-flight. Minor (deferred): unknown-status pin test, literal batch.status moderation on in-flight, 500-char truncation pin, guard message wording, mixed Signed+Moderated labeled "Processing", only first rejected chunk's errors surfaced (no card-chunk mapping — plan limitation). Suites: 95 passed clean. PROCESS NOTE: never run focused tests while a background full suite is running (same test DB -> false failures, seen once).
- Task 10: complete (commits 035e27b..c2aaca7 = feat 690dd74 + robustness 814edfa + result-unwrap c2aaca7, review Approved after 2 fix rounds). Round 1 (dump-driven): sign_pkcs 200-with-errors handled per-item; positional zip REPLACED by gtin pairing (dump guarantees gtin in xmls, 85502); batch published strict (no notsigned/signing/error_sign; import-time error/errors tolerated — reviewer ratified, "partial" status idea deferred). Round 2 (Critical): /nk/feed-product-document result is ARRAY [{xmls,errors}] (dump 85410-85614) -> client unwraps list-wrapped result in document+sign_pkcs; end-to-end test via real NkClient+MockTransport. Minor (deferred): signed-array positive confirm; signer TimeoutError -> 500 + stranded signing/error_sign cards lack re-sign path (recovery task needed); distinct partial status; numeric-gtin coercion pin.
- Task 11: complete (commits c2aaca7..70b27d4, review clean — Approved). 1C report endpoint: published-only, article order, xlsx (sheet GTIN) default + csv utf-8-sig BOM, 404, bad-format 400 (beneficial extra), attachment disposition. Minor (deferred): empty-report test; Response vs StreamingResponse (equivalent for BytesIO). Suite note: clean run 109 passed at HEAD (includes T10+T11); the earlier 2-failed run was a DB-parallel conflict (my process slip — never start focused tests while background suite runs; now strictly serialized).
- Task 12: complete (commits 70b27d4..dd12a70, review clean — Approved). nkmt_cycle (moderation→refresh, signing+notsigned→sign, per-batch rollback+log.exception, client/token per cycle) + _nkmt_loop (600s) + main() daemon thread. Minor (deferred): db.close() in try not finally (pattern-inherited); docstring: next-cycle signing after moderation→signing transition; weak main() wiring assert. Suite: 113 passed.
- Task 13: complete (commits dd12a70..3189b5a, review clean — Approved). UI tab catalog: import FormData, batches+cards+filter (9 statuses match models), feed/refresh/sign buttons with {detail} surfacing, report xlsx/csv blob (token in header only), declarations CRUD, defaults GET/PUT; helpers nkmt/nkmtBlob (api() untouched — Content-Type rationale); build 0 errors. Minor (deferred): date-input display quirk; revokeObjectURL timing (Safari); stale cards on token change; saveDefs silent no-op.
- Task 14: complete (deployment part, controller-executed). DEPLOYED to prod (VM repo -> aa716a6 + hotfix): bundle -> pull -> compose build api/worker/ui -> up -d -> alembic 94526be42e46->0006_nkmt on PROD DB -> healthz ok. INCIDENT+HOTFIX: prod api crash-looped at boot — POST /v1/nkmt/import (UploadFile) requires python-multipart which was NOT in pyproject (present locally, so tests were green) -> added python-multipart>=0.0.9, rebuilt, recovered in ~2 min (commit "fix(deploy): add python-multipart..."). LESSON: container-vs-local deps divergence — new deps must go into pyproject in the same task that first imports them. Scope granted: owner token id=1 now read,docs:submit,admin,nkmt:import (prod platform.tokens). Live smoke prod: GET /v1/nkmt/batches [], GET /v1/nkmt/defaults = DEFAULTS. TK smoke (scripts/tk_nkmt_smoke.py on VM): auth OK (token_len 36), attributes 6109100000/m=11, categories=1 (cat_id 214943), brands YCPB=1; feed..sign SKIP (no gtin in TK, generate-gtins blocked there) — full circle deferred to first prod batch per docs/nkmt-first-batch.md checklist.
- FINAL hardening complete (commits 949576d..c2b1a3c, whole-branch review verdict after re-review: YES — tag phase-3). All 6 Important findings closed with tests (TG finals on batch published/error; error_sign re-sign incl. worker pre-check + error_text reset; published excluded from refresh transitions; date-cell normalization; one /nk/categories call per tnved per import; [0-9] regex sweep). Suite: 122 passed (113+9). NEW deferred Minor: perpetual 10-min silent retry of persistently-failing error_sign cards (no TG — signing is not terminal; observable via UI batch stuck in signing; candidate: retry cap / repeated-failure alert). Deferred-clean list per final review unchanged (test polish, naming, single-user races, UI quirks, chunk semantics, negative caching -> phase-4 backlog).
- PHASE 3 COMPLETE: 14/14 tasks executed subagent-driven (35+ commits 296337f..c2b1a3c), every task task-reviewed + Approved, whole-branch review passed, prod deployed (gis.adel-factory.ru, migration 0006 applied, owner scope nkmt:import), TK dictionaries smoke OK. Exit gate: first supervised prod batch per docs/nkmt-first-batch.md.
- FIRST PROD BATCH COMPLETE (2026-09-04, live): full NKMT circle on production — xlsx «Шапка Тест НК» (6505009000) -> import (batch ok=1) -> generate-gtins -> feed -> REAL NK moderation Moderated -> auto-sign via prod signer -> PUBLISHED -> 1C report xlsx (GTIN|Наименование). Card: article TEST-NK-1, gtin 04630562322355, batch 2 published.
- Live-proven API facts (three hotfixes, each suite-verified before deploy: 124/125 passed):
  1) 5ab9ff0: /nk/generate-gtins drafts return 13-DIGIT gtin -> zfill(14) required; qualified attrs (35/13914) must be sent as attr_value string + attr_value_type FIELD (dump 2501 example), NOT nested dict.
  2) cfcd0c9: attr 2630 country must be ISO code "RU" (ISOCountries), not «РОССИЯ» -> DEFAULTS.country="RU"; EMPTY attr values (producer "") must be OMITTED from good_attrs entirely.
  3) 3609924: feed-product-document xmls entries carry UPPERCASE key "GTIN" and 13-digit value -> pairing normalizes key+ zfill(14).
- Data facts: declaration registry № vs latin N — Росаккредитация rejects «ЕАЭС № ...» (suggests symbol N at pos 6); correct form as user provided: «ЕАЭС N RU Д-RU.РА04.В.02095/26» (registry id=2, date 2026-05-13). Cosmetic: card shows stale error_text from a duplicate sign attempt race (published + error_text) — harmless, note for phase-4 (clear error_text on publish).
- KM thread BLOCKED-ON-USER: 5 KM codes live in /root/tk-km.txt (1st: 010463056230827421521R*4(Fk(6IU); code#3 is 30 chars — likely a lost symbol). Sandbox TK LK_RECEIPT validation now passes fields-wise (needed primary_document_custom_name for document_type=OTHER; TK /lk/documents/create returns PLAIN-TEXT uuid, /doc/{uuid}/info returns ARRAY — prod client assumptions differ, fix pending in tk_e2e.py), but TK answers «КМ не найден в базе данных» — codes not in TK DB. WAITING: were the codes ordered/emitted via SUZ sandbox (TK) or PROD SUZ? Also: user's codes GTIN 04630562308274 differs from NK test card gtin (04630562322355/04630562322348) — KM GTIN must match a TK-registered product.
- PROD KM CIRCLE IN PROGRESS (2026-09-04, user-approved: «круг на проде с кодом, записать результаты»): script scripts/prod_e2e_km.py (platform pipeline: mt.docs -> submit_doc -> check_doc; --fias arg added). LIVE FACTS from prod stand (all mock-covered phase-2 assumptions now reality-checked):
  1) PROD /lk/documents/create ALSO returns 201 text/plain BARE UUID (not JSON) -> MtClient.create_doc_signed crashed (json()["uuid"]); FIXED in d03bb9d (parse both, uuid-validated).
  2) PROD /doc/{uuid}/info returns JSON ARRAY -> doc_info/check_doc would AttributeError; FIXED in d03bb9d (unwrap first element). Suite 131 passed.
  3) LK_RECEIPT field validation on prod: primary_document_custom_name required for document_type=OTHER (same as TK) — confirmed: doc reached MOD check.
  4) BLOCKER-ON-USER: prod LK_RECEIPT (DISTANCE) requires fias_id (МОД = место деятельности): «06: МОД по указанным ИНН ... ФИАС null не найдены». /mods/info API is beer-only — cannot fetch MOD list for lp programmatically. NEED from user: ФИАС GUID of their registered place of activity (ЛК ЧЗ -> Места деятельности) or the address (we can resolve GUID via public FIAS).
  Status: two duplicate draft LK_RECEIPT docs sit CHECKED_NOT_OK (harmless, KM still in circulation); circle resumes once fias provided: rerun prod_e2e_km.py with --fias.
- PROD KM CIRCLE COMPLETE (2026-09-04 17:00 MSK): LK_RECEIPT CHECKED_OK (doc 2, uuid 0a9900e7) -> LP_RETURN CHECKED_OK (doc 3, uuid 48009df8). Code 010463056230827421521R*4(Fk(6IU withdrawn from circulation (DISTANCE sale, fias b944722c-...083c = user's production MOD) and RETURNED back into circulation. FULL PRODUCTION E2E via platform pipeline (mt.docs -> manager.submit_doc -> check_doc -> signer УКЭП -> ГИС МТ). Remaining KM: 3 usable in tk-km.txt (code#3 broken 30 chars). prod_e2e_km.py updated with 404-retry on poll (docs need seconds to index after create). MOD registered by user in ЛК ЧЗ with production address FIAS; фулфилмент later = second MOD + WB-warehouse->fias mapping (phase-4 backlog). KNOWN for phase 4: emitter must add fias_id + primary_document_custom_name to real-sale LK_RECEIPT payloads.

## 2026-09-05 — РЕБРЕНДИНГ MP-GIS_MT/mpmt → МАРКО/marko (коммит 7ca7232, сьют 141)

Прод переименован полностью, данные целы (journal=79, nkmt cards/batches, mt.docs=3):
- код: пакет `mpmt` → `marko` (git mv), env `MPMT_` → `MARKO_`, pyproject/Dockerfile/worker-CMD
- прод-стек: compose `name: marko` → контейнеры/образы **marko-{api,worker,ui,caddy,postgres,backup}-1**;
  БД и роль postgres `mpmt`→`marko` (ALTER через temp-superuser: session user не ренеймится сам + обе команды в одном -c = одна транзакция, DROP-ошибка откатывает RENAME — разделять!)
- volume: `docker volume rename` НЕ существует в этом Docker → копия tar'ом в marko_pgdata/marko_caddy_data (TLS Caddy сохранён), старые удалены
- пути VM: `/opt/mpmt` → `/opt/marko` (repo+secrets); тест-контейнер `mpmt-pg` → `marko-pg`, БД `mpmt_test` → `marko_test`, роль тоже marko; local app/.env → marko@…/marko_test
- /root/*.py на VM: sed mpmt→marko (23 файла); backup.sh → префикс marko- (проверен живьём: marko-20260905-141438.sql.gz)
- НЕ переименовано (осознанно): домен gis.adel-factory.ru, схемы БД (journal/mt/nkmt/wb/platform — доменные, не бренд), локальная папка воркспейса MP-GIS_MT (привязка сессий/памяти/codegraph), agentmemory-id mp-gis_mt, исторические доки/планы, github-репо уже Godila/Marko
- шероховатость: DNS на самой VM отвалился на gis.adel-factory.ru (наружу 200, локальный --resolve 200; воркер ходит в интернет норм) — транзиент, наблюдать

## 2026-09-05 (вечер) — домен marko.adel-factory.ru (1493b89)

Юзер переключил DNS (старый gis.* удалён). Заменены Caddyfile/settings/.env, LE-серт с первого раза, healthz/UI 200.
ВАЖНО: signer-агент (закрытая Windows-машина) ходил на старый домен → офлайн до правки его конфига на https://marko.adel-factory.ru + рестарта (руки юзера).

## PRE-COMPACT CHECKPOINT 2026-09-06

Состояние: origin/main=eec8dc1 (всё запушено); прод marko.adel-factory.ru жив, сьют 142.
Растяжка 04–06.09 закрыла: возвраты «контроль+кнопка» (монитор goods-return + UI + эмиттер fias_id/custom_name, seed kv на проде, живой полл 200 OK), ребрендинг marko (пакет/контейнеры/БД/объёмы/пути), домен marko.adel-factory.ru (LE-серт auto), UI-консоль 6 разделов (ad5c91c, параллельная сессия), разбор памятки WB 04.09 (раздел 8 дискавери).
ЖДЁТ: (1) юзер чинит signer-агент (URL → marko.adel-factory.ru, рестарт; контроль signer_last_seen); (2) WIP параллельной сессии НЕ коммитить: ui/src/App.jsx (M) + ui/logo-marko.html (??) — лого «Матрица-М» ждёт вставки в консоль по слову юзера; (3) гэпы возвратов из памятки WB: гвард двойного вывода (WB-ККТ повторной продажи) + RETAIL_RETURN в return_batch — по команде юзера; (4) гейты phase-4: первый FBS-возврат (op=2?), S3-бэкап, TG-creds.
Точка входа новой сессии: agentmemory «PRE-COMPACT CHECKPOINT 2026-09-06» + файловая память (архитектура — блок-переопределение в конце файла).

## 2026-09-06 — гварды возвратов (двойной вывод + RETAIL_RETURN)

По команде юзера «давай сделаем гварды возвратов» (закрывает гэпы 1-2 из раздела 8 дискавери):
- journal.items.withdrawn_by (''/us/wb) + миграция 0008_item_withdrawn_by
- wb_withdraw_guard (emitter/batch.py): отказ LK_RECEIPT «уже выбыл/не в обороте/retired/...» →
  withdrawn_by='wb' + journal (source=guard), не аномалия; хуки в _docs_checker и POST /docs/{id}/check
- return_batch: 'us' → REMOTE_SALE_RETURN (первичка из нашего LK_RECEIPT), 'wb' → RETAIL_RETURN
  (первичка — чек возврата: fiscal_doc_number/fiscal_dt из op=2); смешанный = 2 документа
- check_doc: CHECKED_NOT_OK теперь терминален → error (иначе гвард не увидел бы отказ)
- тесты: +6 (гвард огонь/нет, RETAIL, blocked-без-фискальных, mixed 2 дока, CHECKED_NOT_OK)
- /v1/journal отдаёт withdrawn_by (UI не трогали — там WIP параллельной сессии)
КОДРЕВЬЮ агентом (06.09, после 2ac691b): 2 high-находки исправлены (16851ad):
(1) гвард не переписывает withdrawn_by у RETURNED-позиций (гонка «возврат подан до опроса ЧЗ»);
(2) state ДО log_action в withdraw/return_batch — каждый commit оставляет консистентный снапшот,
    сбой mid-loop больше не дублит draft. Плюс document_number нормализован строкой (WB шлёт int).
Мелкие замечания ревьюа приняты как KnownUnknown (negations/per-product reasons ЧЗ — до первого
живого случая; receipts из draft-LK_RECEIPT — осознанный ponytail фазы 1). Сьют 149.
Редеплой 16851ad: api+worker, healthz ok, воркер без traceback'ов.

## 2026-09-06 (вечер) — НацКаталог: правила РД + превью импорта + шаблон (57ebef7, /feature-dev)

Юзер: (1) дефолты привязаны к одному бренду/декларации — нужны условные правила РД; (2) нет спеки xlsx;
(3) предпросмотр с апрувом перед загрузкой. Семантика (вопросы юзер пропустил — решено рекомендациями):
условие бренд(casefold)×вид товара(точно, ≥1 непустого), значения declaration_id(FK RESTRICT)+producer,
приоритет файл > правило > глоб.дефолт, из подошедших — больше условий затем больший id; дата берётся
из записи правила (пара номер-дата консистентна).
Архитектура: nkmt/resolve.py (sources/match_rule/apply_rules/resolve_rows — единый конвейер), service.py
распилен на plan_batch (read-only решения, incl gtin_status new|update|conflict) + _persist_batch;
preview/import гоняют один resolve+plan → превью не может разойтись. parse.py: ColumnSpec SPEC — единый
источник COLUMNS/REQUIRED/DEFAULTED + шаблон. template.py: xlsx «Выгрузка»+«Инструкция».
REST: GET/POST/DELETE /v1/nkmt/rules (400 без условия, 404 декларация, 409 дубль условия casefold),
guard 409 в DELETE /declarations/{id} (правило ссылается), GET /import/template, POST /import/preview
(multipart dry-run, построчно подстановки+gtin+ошибки), BAD_XLSX(+ET.ParseError) → 400.
UI: выбор файла → preview-drawer (таблица с provenance файл/правило/дефолт, апрув → повторный POST
/import), кнопка «Шаблон», карточка «Правила РД» в Справочниках. closeDrawer в ctx.
Ревью 3 агентов: блокер openDrawer не деструктурирован в Catalog (фикс), conflict никогда не ставился
(оживлён), ParseError 500→400, casefold-дубль правил, даты из записи правила, мёртвые импорты/ключ rows.
Сьют 159 (+10). Деплой: api+worker+ui, миграция 0009, смоук /rules [] + template 200 + healthz ok.

## 2026-09-06 (ночь) — НК: редизайн Справочников + справочник брендов + примерка (f18ef86, /feature-dev)

Юзер забраковал плоскую форму «Дефолты карточек» (4 карточки-грид). Через AskUserQuestion выбрано:
все 8 полей построчно + примерка; РУЧНОЙ справочник бренд→producer влияющий на импорт (не моя
рекомендация — решение юзера); 4+ под-вкладки; эндпоинт примерки.
НОВЫЙ УРОВЕНЬ ПРИОРИТЕТА: файл > правило РД > справочник бренда (nkmt.brands, миграция 0010,
Brand: name/producer/declaration_id nullable FK RESTRICT) > дефолт. resolve.py: _fill (инвариант
«слот перебивается только в default») + _stamp_decl (декларация только парой из записи реестра) +
apply_brand_dict + resolve_pipeline (общий для импорта/превью/примерки) + resolve_fields.
REST: GET/POST/DELETE /v1/nkmt/brands (409 casefold-дубль), POST /v1/nkmt/resolve {brand,product_type}
→ {поле: {value,src}} (read, без сети); guard 409 декларации под брендом; declarations_create теперь
валидирует (400 пустой номер / битая дата, strip) — закрывает дыру пустой пары.
UI Refs → chiprow-вкладки: Поля (loops-строки, декларация select из реестра с manual-фолбэком;
Примерка подстановок с подписями «введено/правило РД/справочник бренда/дефолт») · Бренды ·
Декларации · Правила (note про матчинг) · Эмиттер ЧЗ. defs/em грузятся один раз на mount —
60-секундный тик больше не затирает несохранённые правки; списки — self-healing по tick.
Ревью 3 агентов: ul.loops (невалидный li в div), поиск декларации по ПАРЕ номер+дата (тихая порча),
тик vs формы, пустая пара из реестра, таутология в тесте мутации, тексты. Всё исправлено.
Сьют 167 (+8). Деплой: api+worker+ui, миграция 0010, смоук: /brands [], /resolve живой, healthz ok.

## 2026-09-07 — НК: мультивыбор видов в правилах, управляемые дефолты, drop brands (cb86579)

4 пожелания юзера (все подтверждены AskUserQuestion): (1) дефолты — управляемый состав полей
(удалить/добавить из доступных; techreg несъёмный; убранное поле перестаёт подставляться);
(2) Примерку оставить, но объяснить → «Проверка подстановок»; (3) правила — МУЛЬТИВЫБОР видов
товара (Adel × [Шапки, Шапки-ушанки, Кепки] → декларация N); (4) справочник Бренды удалить
(роль закрыта правилами без видов).
Миграция 0011: rules.product_type(String) → product_types(JSONB) конверсией jsonb_build_array;
DROP nkmt.brands (прод был пуст: 0/0). Модель Rule: product_types JSONB + CHECK через
jsonb_array_length (урок: JSON ≠ JSONB — create_all рендерит json, jsonb-функции падают).
resolve.py: match_rule — вид входит в список; пустой список = «любой вид» (роль справочника);
apply_brand_dict/match_brand/get_brands/Brand удалены; resolve_pipeline без brands.
routes: RuleBody.product_types (список, strip+dedup, 400 если после чистки пусто и бренд пуст);
409-дубль по (brand casefold, product_types); /brands удалён (404 на проде — ок).
UI: Поля — × у поля (пара декларации удаляется вместе), «Добавить поле из доступных», note;
Правила — чипы видов (input+Enter/+), таблица join(', '); «Проверка подстановок» с пояснением;
вкладка Бренды убрана.
Тесты: brands-тесты удалены, +мультивыбор-тесты; полный сьют 162. Деплой ок, смоук: /brands 404,
/rules [], /resolve Adel×ШАПКА ок.

## 2026-09-08 — журнал КМ: FBW-шум убран + AGENTS.md (e223778, ecd9970)

Диагноз по запросу юзера: журнал = 102/102 SKIPPED_FBW (FBW-продажи из excise, вне контура FBS),
реальных событий 0. Решение юзера: чистим данные, журнал = только FBS-события.
- ingest.py: skip_fbw → log_action (аудит-событие в journal.events, БЕЗ позиции в items);
  RULES/UI/DESIGN без SKIPPED_FBW. Бонус: устранён латентный кейс «FBW-КМ позже продан
  по FBS → аномалия» — теперь Item создаётся свежим с NEW.
- Прод: DELETE items WHERE state='SKIPPED_FBW' (удалено 102, реальных не было — проверено
  до и скоупом DELETE), events=102 целы, journal пуст, healthz ok.
- AGENTS.md в корне репо: контракт координации (frontend-qa read-only протокол с санитайзером
  токена; ограничение «без Read субагент не видит скриншоты» → обязателен vision-eyes шаг;
  DoD UI; безопасность; параллельные сессии). Проба агента: 20 скриншотов прода, 0 ошибок
  консоли, vision-eyes нашёл 2 минорных дефекта вёрстки (обрезка техрегламента в «Полях»,
  плейсхолдер поиска на 900px) — в бэклог.
Сьют 161.

## 2026-09-09 — НК-справочники итерация: вид товара в дефолтах, title деклараций, UX правил (1c5b54a/0e08b25/94105c1)

По 4 замечаниям юзера: (1) product_type теперь defaultable (SPEC/DEFAULTS/DEF_FIELDS; правило
матчится по эффективному виду — семантика цела; тести DEFAULTED_KEYS обновлены);
(2) декларации: поле «Название (для себя)» — title был в модели, добавлен в форму+таблицу+опции
select'ов правил; (3) UX правил: блоки «КОГДА СРАБОТАЕТ»/«ЧТО ПОДСТАВИТЬ» + datalist-подсказки
(новый GET /v1/nkmt/dicts/hints: union пресетов вида товара 12 по kv nk_attrs:* + бренды
brand_cache+дефолт, casefold-дедуп); (4) план по 25к справочникам — в ответе юзеру (отд. сессия).
Валидация по AGENTS.md DoD: frontend-qa (все пункты ок, 0 ошибок консоли, адаптив чист) +
vision-eyes (блокеров нет; миноры: обрезка техрегламента, плейсхолдер — ОБА исправлены в 94105c1:
ellipsis+title у инпутов дефолтов, плейсхолдер короче). Синий focus-ring — норма DESIGN (info).
Сьют 163. Прод: healthz ok, hints живой (YCPB+adel, 54 вида).

## PRE-COMPACT CHECKPOINT 2026-09-14

Состояние: main=50ff05c=origin, сьют 163, прод здоров. SIGNER ОНЛАЙН (юзер починил ~14.09) — подписи ЧЗ работают.
Сессия 06–14.09 (после прошлого чекпоинта): гварды возвратов (2ac691b+16851ad); НК: правила РД мультивыбор
видов (0011, brands удалён), dry-run превью, шаблон, Проверка подстановок, управляемые дефолты +
дефолтуемый вид товара, title деклараций, /dicts/hints+datalist; ЖУРНАЛ ТОЛЬКО FBS (102 FBW items удалены);
AGENTS.md + цепочка frontend-qa→vision-eyes валидирована; исследование WB-выкупов 14.09: токен ок,
выкупы 07-14.09=122 ВСЕ FBW (FBS=0 — пустой журнал корректен), FBS-заказов 1339 в очереди, seller-ЛК:
FBW-выкупы в Аналитике→Продажи. ГЕЙТЫ: первый FBS-выкуп (1339 в очереди — близко), первый FBS-возврат,
TG-creds, S3-бэкап, УКЭП до 21.10.2026. БЭКЛОГ: фильтр FBW-шума из монитора возвратов (180/нед, 0 активных,
goods-return без supplyType), индикатор FBS-выкупов в Обзор, 25к-справочники отдельной сессией
(план: агрегатор→апрув→REST), чистка error_text при published, архив «Шапки Тест НК».
Точка входа: agentmemory «PRE-COMPACT CHECKPOINT 2026-09-14» + файловая память (архитектура, блоки внизу).

## ИНЦИДЕНТ skip_fbw → фикс классификации WB-эксайза (14.09, по /feature-dev-методологии)

Юзер опроверг вывод ресерча «выкупы все FBW»: живые выкупы FBS видны в ЛК (скрин: СЦ Кавказский
бульвар/Крыловская, «Свой склад»). Диагноз по прод-БД + живому снапшоту: 131 продажа наших КМ
(fiscal 20.08–13.09) ВСЯ в wb_excise/skip_fbw, журнал пуст. Две причины матчинга srid∈{rid
из /api/v3/orders}: (1) суффикс позиции '.n.m' расходится между эксайзом и orders (0/131 полных
совпадений, 3 по документу); (2) выкупленный заказ уходит из снапшота на 1–3 дня раньше приезда
эксайз-строки (128/131 вне снапшота; сам снапшот 1365 заказов, все fbs). Ресерч-вывод «FBS=0» —
ошибка эвристики по warehouseName (WB-фулфилмент = fbs со складом «Склад WB РФ»).

Фикс (blueprint code-explorer+code-architect, ревью code-reviewer, 4×P2 закрыты):
- ПЕРСИСТЕНТНЫЙ реестр wb.orders (order_doc=rid без '.n.m', delivery_type, nm_id, created;
  миграция 0012) — прогрев upsert_orders ДО ingest в poll + ежечасно в _wb_returns_loop
  (advisory xact-lock 912001 против дедлока двух апсертов, дедуп позиций в партии).
- ИНВЕРСИЯ: skip_fbw ТОЛЬКО при order_doc ∈ non_fbs_docs (NOT IN ('fbs','')); документ вне
  реестра = наш FBS + счётчик fbs_unknown (трипваер TG). Асимметрия рисков: ложный skip — тихая
  упущенная продажа; ложный sale — громко, поглощает wb_withdraw_guard.
- repair.py: replay_skip_fbw — delete+flush+apply_event (kind по operation_type_id, маркер
  replayed_from/orig_event_id, сортировка по fiscal_dt), фильтр non_fbs_docs; docker exec -m.
- Тесты: test_registry + test_repair (+left_fbw), test_ingest переписан (суффикс-дрейф —
  регресс инцидента; unknown→наш FBS; живая фикстура 1022 строк с раскладом), test_poll e2e.
  Сьют 174. Коммит: фикс классификации + миграция 0012.
Прод-план: деплой → 0012 → рестарт воркера (прогрев реестра) → repair --dry-run (ожидание
found=131, left_fbw=0) → repair → журнал ~131 PENDING_WITHDRAW → 18:30-слот новым кодом.

## АНОМАЛИИ ЖУРНАЛА: ПОНЯТНОСТЬ + РУЧНОЕ РАЗРЕШЕНИЕ (14.09, /feature-dev полный цикл)

Юзер: «что такое Аномалия? описание ни о чём; зачем наши статусы, если все описаны в ЧЗ/WB».
Разбор: журнал = бухгалтерия НАШИХ обязательств (какое действие подавать в ЧЗ дальше), аномалия =
«последовательность событий физически невозможна, автоматика отказывается угадывать». Разведка
3×code-explorer: аномалии терминальны И текучие (новое событие → UNKNOWN + затирка last_event);
ручного разбора нет вовсе (API read-only, «Разобрать» на Обзоре — навигация); причина №1 ложных
RESALE — применение эксайз-строк в порядке выдачи WB без сортировки по fiscal_dt.
Апрувнут подход B: объяснения + управляемое разрешение + профилактика. Реализовано:
- ingest_excise сортирует события по fiscal_dt (бездатовые — последними, как NULLS LAST в repair).
- POST /v1/journal/{km}/resolve (docs:submit): Literal-target, note≤500, гварды 404/409/422;
  state до log_action (паттерн emitter), source='manual' kind='resolve' (аудит кто/когда/почему),
  audit journal.resolve; last_event (WB-первичка) НЕ трогается; НОВЫХ статусов нет.
- Ревью-фикс P1 (3 ревьюера сошлись): resolve в PENDING_RETURN/WITHDRAWN при пустом withdrawn_by
  проставляет источник вывода ('us' по нашему LK_RECEIPT / 'wb' по чеку ККТ) — иначе главный
  пресет «продажа до запуска контура» вешал код в вечный blocked у return_batch. Для этого из
  return_batch выделен lk_receipts(db).
- UI: ANOMALY_HELP (что/почему/что делать + пресеты по типу), AnomalyCard в drawer (объяснение,
  факты события, кнопки через confirm, details/JSON второй слой), человекочитаемые чипы
  («аномалия: возврат без продажи» и т.п.), evLine показывает вид события из operation_type_id
  (opRu-хелпер, мёртвый KIND_RU удалён), поиск работает и при фильтре аномалий, даты через fmtD.
- DESIGN.md разд.8: подписи аномалий + событие manual/resolve задокументированы.
Тесты: 179 полный сьют + новые (сортировка ×3, resolve ×4: happy/гварды/422/return_batch-регресс/
withdrawn_by). Коммит: аномалии-разбор. Бэклог: with_for_update на resolve (гонка с apply_event —
окно мало), Esc для drawer (DESIGN 112), карточка разбора не видна на проде до первой реальной
аномалии (0 сейчас — все 131 в PENDING_WITHDRAW).

## PRE-COMPACT CHECKPOINT 2026-09-15

Состояние: main=origin=aa129ff, дерево чистое, прод задеплоен на aa129ff
(healthz ok: signer ВЕРНУЛСЯ после правки конфига юзером на marko.adel-factory.ru,
в stats появился fbs_unknown из 0012). Сьют 179 passed.

С чекпоинта 14.09 (уже в проде и в памяти):
- d8ff116: классификация excise FBS/FBW по персистентному реестру wb.orders
  (миграция 0012) — вместо множества rid, снятогоorders-окна.
- aa129ff: аномалии — сортировка ingest по fiscal_dt (профилактика ложных RESALE),
  POST /v1/journal/{km}/resolve (Literal-target, гварды, withdrawn_by backfill),
  UI AnomalyCard (объяснение+пресеты+confirm), человекочитаемые чипы.

Инфра-статус к compact:
- SSH-туннель 15432 (pytest → marko_test) на ноутбуке НЕ запущен — перезапускать
  while-loop рецептом из памяти (run_in_background, НЕ `&` внутри команды).
- ui/logo-marko.html + DESIGN.md — источник UI-стиля; знак встроен (Mark-компонент,
  favicon.svg мини-версия; полный знак везде, мини — только favicon).

Открытое (без изменений): with_for_update на resolve; Esc закрывает drawer;
гейты — первая живая FBS-продажа (LK_RECEIPT) и возврат; TG-creds; S3-бэкап;
УКЭП до 21.10.2026; кандидат — СУЗ (ресерч в crpt-specs); карточка разбора аномалий
не видана на проде (реальных аномалий 0 — все в PENDING_WITHDRAW, ждут первой продажи).

## ПРОВЕРКА КИЗ В ЧЗ + УДАЛЕНИЕ ЧЕРНОВИКОВ (15.09, /feature-dev; APs: 2A/3A/1=юзер-вариант)

Контекст: юзеру нужен штатный инструмент статусов КИЗ (после ручных cises/info-разборок 15.09:
WB сам выводит 84% продаж по чекам ККТ, хвост 16% — наш) и удаление черновиков (доки №4/5 было
некуда деть). Реализовано:
- MtClient.cises_info (POST v3, тело-массив ≤1000, поэлементные ошибки с HTTP 200 насквозь) +
  manager.cises_info/default_client/CISES_CHUNK.
- journal.items + cis_status/cis_product_name/cis_checked_at (миграция 0014, голова была
  0013_console_auth от параллельной сессии); journal_list отдаёт.
- journal/cis.py sync_cis_status: матчинг по ЭХУ cisInfo.cis (позиция — фолбэк; P1 ревью:
  перестановка ответа не путает статусы), RETIRED+PENDING_WITHDRAW+нет активной претензии
  (поданный LK_RECEIPT минус поданный LP_RETURN — ложный 'wb' при незакрытой перепродаже, P0
  ревью) → WITHDRAWN/'wb' + событие cz/cz_retired:{km} (идемпотентно); WITHDRAWN/'us' не
  перепомечается; ошибки-элементы позицию не трогают.
- Пре-флайт в POST /batches/withdraw: ЦИКЛ до стабилизации (переведённые уходят → сборка добирает
  хвост → проверяем и его; P1 ревью), fail-open при лежащем ЧЗ, order_by(km); ответ несёт
  preflight-статистику.
- POST /v1/journal/cis-sync (все/по списку, items-карточки); POST /v1/journal/{km}/withdraw-source
  (wb: перевод; us: люк при наличии нашей первички, иначе 409); DELETE /v1/docs/{id} (только draft;
  409 если док — СТАРЕЙШИЙ источник первички возвратного КМ; откат WITHDRAWN→PENDING_WITHDRAW /
  RETURNED→PENDING_RETURN; 'wb'-помеченные skip; события manual/docdel; audit doc.delete).
- UI: журнал 6 колонок (Наименование ell, ЧЗ-бейдж CIS_STATUS), кнопка «Обновить статусы ЧЗ»,
  KmCard (статус/наименование/проверено + Проверить в ЧЗ + Выведен WB), DocTable «Удалить»
  (состав→confirm→DELETE→тост reverted/skipped), Withdraw: колонка Наименование + preflight-тост.
  DESIGN.md р.8: словарь ЧЗ-статусов. marko.css td.ell.
- conftest: autouse _no_mt_network (default_client→MtHttpError) — пре-флайт офлайн в тестах.
Сьют: полный 215+4 passed (2 падения — дрейф ожиданий после ревью-фиксов, поправлены).
Урок: сериализация pytest — фоновый полный сьют столкнулся с целевым прогоном (оба недостоверны,
перегон). Ревью-находки закрыты: эхо-матчинг, цикл пре-флайта, us-гвард первички, точный 409
удаления, td.ell, «Наименование».

## PRE-COMPACT CHECKPOINT 2026-09-15 (вечер)
Состояние: main=5db201c=origin, сьют 219, миграции до 0014_cis_status, прод ok, signer онлайн,
слот 06:30 привёз sale=2 fbs_unknown=2 (инверсия классификации жива). Сессия 15.09 (после
прошлого чекпоинта): WB-эксайз инцидент (d8ff116: реестр wb.orders/0012, суффикс-дрейф+уход
заказов из снапшота, реплей 131→sale); доменное открытие через cises/info (WB сам выводит 84%
продаж по чекам ККТ; все продажи = старый FBO-запас; чеки из эксайза исчезли с 01.09; новая
партия 31.08 не продавалась; op=2 не появляется вовсе); аномалии-фича (aa129ff: карточки,
resolve, withdrawn_by-фикс, сортировка fiscal_dt, первый живой RESALE); КИЗ-фича (64169f7:
cises_info штатно, cis-колонки/0014, пре-флят сбора циклом+fail-open, cis-sync/withdraw-source/
DELETE docs, UI 6 колонок+KmCard+Удалить); параллельная сессия: cookie-вход (admin, 0013).
ЭКШЕН ЮЗЕРА: Удалить черновики №4/№5 → Собрать вывод (пре-флят разнесёт 112→wb/21→документ,
колонки ЧЗ/Наименование заполнятся) → Подать (сроки вывода просрочены — не тянуть).
Гейты: чистая подача LK_RECEIPT (21 код), op=2-вопрос для возвратов, продажи партии Kizi,
TG-creds, S3-бэкап, УКЭП до 21.10.2026. Бэклог: кармин-счётчик неактивного пункта, батч-
коммиты синка, with_for_update, Esc drawer, FBW-шум возвратов, 25к-справочники, чистка
error_text. Уроки: pytest строго последовательно (столкновение прогонов на общей тест-БД);
туннель 15432 по требованию; qa-оператор для frontend-qa (create_operator+root-файл, удаление).
Точка входа: agentmemory «PRE-COMPACT CHECKPOINT 2026-09-15» (+ «ДОМЕННОЕ ОТКРЫТИЕ 15.09»,
«КИЗ-ПРОВЕРКА ЧЗ+УДАЛЕНИЕ ЧЕРНОВИКОВ», «КОРРЕКЦИЯ+ФИКС 14.09», «АНОМАЛИИ ЖУРНАЛА», «SSH-ТУННЕЛЬ»)
+ файловая память + этот леджер.

## PRE-COMPACT CHECKPOINT 2026-09-17 (утро)
main=origin=2d4527e; миграции до 0015. Сессия 16.09 закрыла: (1) wb-lookup
(25c5543: GET /v1/wb/lookup + поиск-автокарточка + колонка «Заказ WB» +
EllCell-раскрытие + JOURNAL_COLUMNS-фундамент; QA живой PASS; урок — UI-регэкспы
сверять на всех реальных образцах фикстуры); (2) табличные формы (fixed-лейаут,
colgroup, .content 1440); (3) время МСК (хост tz + parseUtc, контейнеры/PG=UTC).
Прод-движение: PENDING_WITHDRAW 21→23, WITHDRAWN 114→118 — выкупы новой партии
капают. Ждём юзера: «Собрать вывод» (23) → «Подать». Гейты/бэклог — в agentmemory
«PRE-COMPACT CHECKPOINT 2026-09-17».

## PRE-COMPACT CHECKPOINT 2026-09-18 (ночь)

- main = origin = 28cde9d; прод синхронен (ui force-recreated), healthz ok, signer онлайн, сьют 251 passed.
- Сессия: (1) НК-импорт 2.0 (58da0ef: правила fields+ТНВЭД, casefold, шаблон, wide-модалка превью, /context); (2) Производители+декларации из ЧЗ (979b22c, мигр. 0017: rd/list v4 enrichment, tnved_list, ТНВЭД-контроль превью, дровер-карточка); (3) Сага таблиц → ФИНАЛ b847878: ВСЕ colgroup сплошные ширины (пропорциональный масштаб на широких), контент без max-width, бейджи по словам; правило в DESIGN.md §7/§13.
- Уроки сессии: UI-ширину проверять на 2560 юзера; в fit-гридах нет колонок без width; русские commit-сообщения через -F/- (не -m после taskkill); amend запушенного = reset --soft origin/main; MCP filechooser убивает run_code-блок — диалоги отдельными шагами.
- Открыто: fbs_unknown=6 в wb_last_poll (трипваер, не разобран); латентно get_token 'PT9H' в kv; partial-импорт из UI заблокирован (ошибка = disabled кнопка).
- Гейты: юзер «Собрать вывод»→«Подать»; TG-creds; S3-бэкап; УКЭП до 21.10.2026.

## 2026-09-18 (день) — инцидент «Унисекс»: синонимы и подсказки справочников ЧЗ (e6ffdae)

- Корень: attr 14013 в живом ЧЗ = литералы «ЖЕНСКИЙ|МУЖСКОЙ|БЕЗ УКАЗАНИЯ ПОЛА|УНИВЕРСАЛЬНЫЙ (УНИСЕКС)»; юзер писал «Унисекс»/«Универсальный» → честный отказ без подсказки.
- Фикс: PRESET_SYNONYMS + нормформа (пунктуация/тире/ё→е, guard уникальности) + «доступно: …»/«возможно: …» в тексте preset-ошибки; attr 35 шапок (46–62) → size_warning не блок; превью отдаёт target_gender/size_warning; шаблон на валидных литералах (цвет «ОЛИВА» не существует); UI: колонка «Пол», сумма превью возвращена к 1204.
- Ревью: P2 (ширины 1308) + P3×2 закрыты; ревьюерская «коллизия ТРУСЫ БИКИНИ» — галлюцинация (в списках нет).
- Тесты: +10 (синонимы/нормформа/guard/подсказки/size_warning/шаблон/пустой размер); полный сьют 258 passed + NK-поверхность зелёная на финальном коде.
- QA: мок-превью PASS (2560/1536 без скролла, 900 — штатный twrap-скролл; наездов нет); живой прод-превью-чек 3 строк PASS. Деплой e6ffdae, healthz ok, signer онлайн.
- Хвосты: правило юзера №3 (ADEL: «Шарф»/«Снуд») не сматчится — валидны только ШАРФ-СНУД/ШАРФ-КАПЮШОН/ШАРФ-ХОМУТ (сообщить); риск Rejected-фида размером «one size» у шапок проверить первой живой подачей.

## 2026-09-22 · Фича «Трассировка КМ» v1→v2.1 + редизайн (закрыто, 93159b6)

- v1 (98a881e): раздел «Трассировка», GET /v1/trace (read-only агрегатор), wb-meta (orders/meta sgtin), мигр. 0018 wb.orders.order_id, normalize_km (GS/'!'/слитный криптохвост, регистр серийника значим), сьют 276.
- Фидбек «1 запись — ни о чём» → v2 (2e9d03f..4a5ce59): live-ЧЗ при поиске (sync snapshot=True = полный cisInfo: даты производства/эмиссии/ввода, производитель, декларация), лента заказов WB order-feed (квота 1/3ч, kv-кэш-троттлер на кабинет), живые грабли WB: путь /api/analytics/v1/order-feed + обёртка data.orders; 283 зелёных.
- v2.1 (2acae87): кэш ленты АВТО в ответе трассировки (без сети), этап «Отгрузка на склад WB» (supplies: scanDt=приёмка; мигр. 0019 supply_id; воркер-кэш wb_supplies — свой образ у worker, build отдельно!).
- Границы WB API (живые пробы): пошаговой истории статусов заказа НЕТ; состав закрытых поставок НЕ отдаётся → исторические связки код↔поставка/числовой id невосстановимы (заработает для заказов после 22.09).
- Редизайн ($frontend-design, fc62fe8→93159b6): рельса «Путь кода» (3 фазы, точки-состояния done/wait/todo/na, один смысловой цвет), детали в чип-табах, пояснения пустых этапов; 3 фикс-итерации: равная сетка 132px → сквозная линия → display:block точек (inline-span игнорировал размеры; метрика позиций без РАЗМЕРОВ пропускала).
- Хвосты: юзер финальную рельсу живьём не подтверждал; квота ленты 1/3ч; withdrawDate в cisInfo не подтверждён; старые (правило №3 ADEL, one-size шапки, fbs_unknown, get_token PT9H, УКЭП 21.10.2026).

## 2026-09-22 (вечер) — журнал: период/сортировки + аномалии=норма + два пространства

- Разобраны аномалии: 5/5 живых = «перепродажа возврата» (WB перевыставляет возвраты дешевле; op=2 не приходит; чек ККТ выводит/возвратный возвращает) → стейт-машина: sale идемпотентен обязательству (PW→PW, WITHDRAWN→WITHDRAWN); ANOMALY_RESALE = легаси.
- Ревью-урок P1: UNKNOWN бывает рождён возвратом → retired там норма → НЕ в авто-перевод; TRANSLATE_ON_RETIRED=(PW, RESALE).
- cis-sync: авто-вычистка легаси-RESALE при retired без претензии (payload +from); прод: 4 закроет кнопка «Обновить статусы ЧЗ», 1 introduced — пресет.
- Журнал: milestones.py (sale_dt/order_dt, 2 bulk, item_row чист) + миграция 0020 (ix_events_km_kind); UI: колонка «Выкуп», sortable th (aria-sort+Enter, nulls-last), период по сигналу, ширины=1236.
- Консоль: NAV_SECTIONS «Оборот и маркетплейсы»/«Нацкаталог» (.nav-cap); «Эмиттер ЧЗ» → EmitterDefaults в «Выводе»; Refs=4 таба; DESIGN.md §1/§6/§7/§8/§12.
- Тесты 293+86 passed; QA мок-прогон 2560/1440/900 (DOM-метрики, консоль 0); деплой: 3136b1a+d846432, миграция 0020, healthz ok; прод отдаёт sale_dt живьём.

## 2026-09-23 (вечер) — ФИЧА: детектор клиентских возвратов (sales R) + редизайн «Возвратов»
Дискавери → доктрина v4 (две эпохи WB-чеков: до 01.09 с КМ — WB сам выводил/возвращал;
после — без КМ, вывод только наш LK_RECEIPT, возврат виден только в finance sales R;
FBO-остатки «Склад WB РФ» — зона WB, новая партия Kizi = FBS-фулфилмент). Реализация:
WBClient.sales (гейт 1/2ч) → wb.client_returns стейджинг (мигр 0021) → матчинг
order_doc (точный srid → детерминированный фолбэк, один КМ на R-строку — P1 ревью
многопозиционные заказы) → kind=client_return: PENDING_WITHDRAW→RETURNED (снятие
ложного обязательства), WITHDRAWN+'us'→PENDING_RETURN (LP_RETURN), 'wb' — наблюдение.
UI: карточка «Возвраты покупателей», дедлайн-фолбэк ready+7д, KPI без вечно-нулевой.
Ревью: P1+P2+5×P3 закрыты до коммита; деплой-баг (goods-return 429 глушит sales-блок)
пойман живым логом → fix cb9d1a0. Коммиты: backend+мигр (52f7d74^), UI, fix; push.
Тесты: test_client_returns 8 + state_machine 2 + api/trace — зелёные; сьют смежных
104 passed (1 тест-фикс ассерта после смены source_event_id).
Воркер ждёт открытия WB-квоты sales (сожжена исследовательскими запросами 23.09,
первый успешный проход — следующим часовым циклом); стейджинг наполнится сам.

## 2026-09-23 (ночь) — ФИЧА: контур FBS/FBO в записях и формах (2978f01)
Юзер: «передавай идентификатор контура, чтобы оператор сразу понимал чей заказ». delivery_type
из реестра wb.orders (единственный источник флага — sales/goods-return/excise его не отдают,
проверено спекой+payload): журнал (подпись под заказом), возвраты покупателей (contour:
реестр побеждает склад, фолбэк «Склад WB РФ»→FBO), невыкупы (437/500 fbs живьём). Терминология
унифицирована до FBO. Ревью 0×P1/P2, P3 (тест-дискриминация) закрыт. Деплой: api+worker+ui, healthz ok.

## 2026-09-23/24 (сессия «возвраты», финал) — контур, честные плашки, UX-фиксы
После детектора возвратов (запись выше): контур FBS/FBO в 3 формах + фильтр журнала + KmCard
(2978f01, e445478); доктрина v5 — излом 01.09 = пропажа НОМЕРОВ чеков в эксаизе, выводы кассами
WB продолжаются 85/15 (юзер поймал на eAL.rfa7: чек пуст, код retired); check_doc: 404 ГИС МТ
сразу после подачи = pending (9a742d0) + авто-refresh cis-плашек при CHECKED_OK (1365f72);
ЧЕСТНЫЕ ПЛАШКИ doc_ref: «вывод: черновик/подан №N» → «выведен · №N» только после принятия,
«выведен (WB)» — DESIGN.md §8 правило (244c548). Прод 26.09: детектор жив (1230 R / 169
применено → 22 PENDING_RETURN + 4 RETURNED), №9 checked_ok. Хвост: черновик №8 не подан +
по его позициям уже пришли возвраты (выбор юзера: подать→LP_RETURN или удалить→снятие
обязательств R-правилом); DUNGR52; Caddy no-cache. Параллельные сессии добавили lookup
справочник и печать этикеток — не пересекается.

## 26.09 (вечер) — /feature-dev: правка справочников (37caa3f + 954fccd, деплой 26.09)

Боль юзера: опечатка в декларации/неполный состав правила = удалить и завести заново.
Инвентарь: правки не было у 3 справочников (дефолты и эмиттер уже PUT). Реализация:
PUT /v1/nkmt/{declarations,producers,rules}/{id} (scope nkmt:import, аудит
nkmt.<entity>.update was/now); нормализация вынесена в _norm_*/_dup_* — POST и PUT
не разъезжаются; дубль-чеки exclude_id «кроме себя»; смена пары декларации сбрасывает
rich-поля ЧЗ + пере-обогащение best-effort (title-only не трогает). UI: производители
и правила — форма создания = режим правки («Сохранить изменения»/«Отмена», «Изменить»
в строке, колонка действий 176px); декларации — «Изменить» в drawer-карточке, тост
ветвится по факту смены пары. Ревью: P2 .hint вне card-h был нестилизован → глобальное
12px muted; P3: сброс edit-режима при удалении редактируемой строки. Тесты: 16
справочник + полный сьют 357 passed. QA Playwright (моки): prefill чипов/полей, PUT
тела, 900px twrap-скролл, 0 обрезок, консоль чистая; скриншоты marko-qa-screens/
2026-09-22-refs-edit. Прод: healthz ok, PUT-маршруты 401 (живые). Миграций нет.

## 26.09-2 (22.09, вечер) — дизайн фичи «Наборы» (sets) в НК

Брейншторм по /feature-dev-методологии, реализации НЕТ — только спека
crpt-specs/sets-design-2026-09-22.md (коммит ниже). Живая прод-проба (read-only):
/nk/attributes?is_set=true работает — набор лёгпрома = 6 атрибутов (2478/2504/3959/
23768/16271 «Состав набора»/23821 «Кол-во маркированных»), БЕЗ декларации/вида/цвета;
/nk/product отдаёт is_set/set_gtins. Ключевое: наборы создаются обычным POST /nk/feed
(is_set + set_gtins[{gtin,quantity}]), конвейер МАРКО переиспользуется целиком;
GTIN — существующий generate-gtins. Решения: привязанный набор по умолчанию (unbound
чекбоксом), UI = таб «Наборы» в Каталоге НК, мастер 3 шага в wide-модалке, drawer по
клику на строку, правка только до подачи, «Собрать аналог» после публикации, статусы
не расширяем. СУЗ/КИН/SETS_AGGREGATION = фаза 2 (гейт: отдаст ли WB КиЗ на набор).

## 26.09-3 — фича «Наборы (sets)» в НК: реализация фазы 1

Спека crpt-specs/sets-design-2026-09-22.md v2 (обе двери: конструктор + xlsx).
Бэк: миграция 0022 (cards.is_set + nkmt.set_items), nkmt/sets.py (build_set_row
— единые валидации обоих входов: резолв ref артикул-приоритет/внешний GTIN,
двухфазный гард «не опубликован — черновик можно, подача ждёт», анти-дубли ×4),
роуты /v1/nkmt/sets* (11 шт, scope nkmt:import, аудит nkmt.set.*), NkClient
attributes(is_set)+product (/nk/product), кэши nk_attrs_set:/nk_products:,
шаблон «Наборы» (SPEC_SETS, packed-колонка «Компоненты» с known_article —
артикул HX2 не режется количеством), feed-гард (батч блокируется целиком с
перечнем), _feed_entry(card, db): is_set+set_gtins (gtin резолв на подаче).
UI: таб-чип «Наборы» в Каталоге НК (SetsTab/SetCard/SetBuilder×3 шага/
SetsImportPreview, fit+colgroup 108/140/300/440/76/104, drawer-карточка,
«Собрать аналог», бейдж «без привязки»). Тесты: 17 новых (test_api_nkmt_sets).
Ревью: P1 переимпорт сбрасывал поданный набор → read-only гвард в build_set_row
+ dup-скип в plan_sets + регресс-тест; 4×P3 (мёртвый parse_xlsx, Обновить
только fed/moderation, тост feed-ошибки отдельным сценарием, превью=импорт
«артикул занят набором» в plan_batch). QA Playwright-моки: грид/мастер все 3
шага/превью импорта с ошибочной строкой/900px/консоль чистая; скриншоты
marko-qa-screens/2026-09-26-sets. Урок: два pytest на одной тест-БД (туннель
15432) роняют друг друга — сьют строго один (память: сериализация).
