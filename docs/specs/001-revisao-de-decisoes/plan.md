# MTGA Coach — primeira versão executável: plano de implementação

## Scope

Esta primeira versão executável prova a cadeia local completa: importar um `Player.log` previamente configurado ou enviado como bytes, reconstruir fatos e lacunas de jogos BO1/BO3 no SQLite privado, resolver as cartas observadas quando a base local permitir, e apresentar replay, notas, hipótese experimental de deck e contexto tático sanitizado para revisão manual. O servidor usa apenas Python 3.14 stdlib e atende somente `127.0.0.1:18731`; ele não chama serviço externo nem simula uma análise por IA. Uma integração de IA futura dependerá de credencial e decisão de orçamento separadas.

## Affected layers

| Layer | Responsibility in this version |
|---|---|
| Source adapters | Read the fixed `Player.log`, accept a raw-body upload, normalize GRE/client records, and inspect the local `Raw_CardDatabase_*.mtga`. |
| Domain reducer | Establish full/diff state lineage; preserve source line, warning and quality; derive game/match/deck facts without inventing missing facts. |
| Persistence | SQLite under `%LOCALAPPDATA%/mtga-coach`; idempotent import, frames, observed deck configurations, notes and experiments. |
| Local API | JSON endpoints specified below plus static-file delivery; input limits and localhost-only binding. |
| Browser UI | Vanilla HTML/CSS/JS replay, game list, card labels, manual notes and experiment ledger, plus context download/copy for an external review chosen by the user. |
| Operations | A Windows launcher, local configuration, documentation and a smoke command. |

## Relevant ADRs

No ADR constrains this project: `docs/adr/README.md` does not exist. Likewise, `docs/architecture/overview.md` and `docs/specs/README.md` do not exist. The binding rules therefore come from [spec.md](spec.md), especially that the reader reconstructs facts, unresolved card IDs remain explicit, later revelations never enter a decision context, and a future IA suggestion cannot claim an optimal line or a win probability without appropriate evidence.

## New ADR required?

No. The first release is a private, local, stdlib-only application with no shared system interface and no decision record infrastructure yet. Create an ADR before adding a cloud sync, an external card/metagame source, a paid IA provider, desktop packaging, or a compatibility-breaking schema migration.

## Data model changes

Create the private SQLite database `%LOCALAPPDATA%/mtga-coach/mtga-coach.sqlite3`; use migrations encoded as ordered SQL files only after an ADR exists. The initial schema belongs in `src/mtga_coach/storage.py` and has these records:

| Record | Required fields and invariant |
|---|---|
| `imports` | `sha256` unique, source kind (`configured`/`upload`), byte count, imported timestamp, parse warning. The raw upload is copied only to `%LOCALAPPDATA%/mtga-coach/sources/<sha256>.log`. |
| `matches` | Stable public `id`, `match_id_hash`, `mode` (`BO1`/`BO3`/`unknown`), `format`, `event_id`, `status`, and separate match result. Do not persist a raw account identifier. |
| `games` | Public `id`, match FK, game number, deck ID, result (`win`/`loss`/`draw`/`unknown`), status (`complete`/`in_progress`), turns, decision count and quality. Game result and match result remain separate. |
| `deck_configs` and `deck_cards` | Hash of observed main/sideboard composition, card ID, quantity, and relation to match/game. A BO3 post-sideboard configuration is not an experimental deck version. |
| `frames` | Game FK, sequential index, `state_id`, turn/phase/step, active/priority player, reduced visible-state JSON, action JSON, source line, quality and warnings. The full source record is not exposed through the API. |
| `cards` | Card ID, resolved name/text from `Localizations_ptBR` (fallback `Localizations_enUS`), faces/variant JSON, source database SHA and resolution status. Resolve `Cards` by `GrpId`, use `TitleId`, `AbilityIds`, `LinkedFaceGrpIds`, `IsRebalanced` and the localization tables read-only; if resolution fails, retain only ID and status `unresolved`. |
| `notes` | Game FK, optional frame index, body, tags JSON, created timestamp. |
| `experiments` | Deck ID, hypothesis, baseline composition hash, candidate composition hash where supplied, status and timestamps. It records the user's hypothesis; it never asserts causal performance. |

The stable API contract is:

| Endpoint | Contract |
|---|---|
| `GET /api/summary` | Counts by result/status/mode and per-deck aggregates; win/loss denominators exclude `in_progress` and unknown results. |
| `GET /api/games` | Paginated `game summary` records: `{id, match_id_hashed, game_number, mode, format, event_id, deck_id, result, status, turns, decision_count, quality}`. |
| `GET /api/games/{id}` | The summary plus `deck:{main:[{id,quantity}],sideboard:[...]}`, frames, and notes. Each frame has `{index,state_id,turn,phase,step,active_player,priority_player,players:[{seat,life,is_self}],zones:[{type,owner,objects:[{instance_id,card_id,tapped,power,toughness,attack_state}],hidden_count}],action:{type,label,card_ids},quality,warnings,source_line}`. |
| `GET /api/cards?ids=...` | Returns resolved/unresolved entries only for requested numeric card IDs; no name or rules text is fabricated. |
| `POST /api/import` | JSON `{"source":"configured"}` imports the configured fixed `Player.log`; `application/octet-stream` imports a raw user-selected log. No request field accepts a filesystem path. |
| `POST /api/notes` | Stores a bounded manual note tied to an existing game and optional frame. |
| `POST /api/experiments`, `GET /api/experiments` | Create/list manual deck hypotheses and observed deck version references. |
| `GET /api/games/{id}/context?index=...` | Returns a bounded, sanitised tactical export. It includes only knowledge available by that frame, previously revealed opponent information distinguished from current-game confirmation, resolved card data or explicit gaps, and no raw log/account/credential/hover/future frame data. |

## Test strategy

Use `unittest` only: `python -m unittest discover -s tests -v`. Write fixtures before production code: a Full→Diff→delete chain where game objects replace by `instanceId` and zones replace by `zoneId`, a full resynchronisation, a `ConnectResp` with `systemSeatIds`, an unresolved predecessor/search boundary, an incomplete trailing record, a BO3 synthetic match, future-reveal context, duplicate import, card resolution and unresolved ID. Every task first adds named failing tests, then implements only enough to pass. Each task also runs `python -m compileall -q src` and the focused test module; T004 runs the full suite and a subprocess localhost smoke test.

The real snapshot documented in [2026-09-06-log-audit.json](../../discovery/2026-09-06-log-audit.json) validates ingestion only after fixture tests pass: first game (full resync and complete), second game (unresolved predecessors 30/138) and third game (in progress). A real BO3 capture remains a release blocker for any claim that BO3 reconstruction has been validated.

## Risks

- The validated `Raw_CardDatabase_*.mtga` schema is SQLite, but a client update can change it. The read-only adapter must degrade to `unresolved` rather than couple replay correctness to card-text parsing.
- GRE diff semantics vary by field. Game-object updates replace the record by `instanceId`, and zone updates replace the list by `zoneId`; a generic JSON deep merge is prohibited. Unsupported changes must lower frame quality and preserve a warning.
- The two missing predecessors in the second real game may be recoverable from search messages. Until an evidence-backed recovery exists, tactical analysis across that boundary is blocked.
- The configured `Player.log` can contain credentials and personal identifiers. The app must keep raw files local, never log their body, and never send them to an external service.
- `http.server` offers no production authentication. Fixed loopback binding, no arbitrary path import, bounded request bodies and no directory listing are mandatory.
- The project is currently not a Git repository. Preserve one-task/one-commit intent once its owner initializes Git; no task may initialize or publish a repository as a side effect.

## Step-by-step tasks

### T001 — Reconstruct observed game facts and card catalogue
- **Parent**: AC-2, AC-4, AC-5, AC-6, AC-8, AC-11
- **Files**: `src/mtga_coach/__init__.py`, `src/mtga_coach/config.py`, `src/mtga_coach/ingest.py`, `src/mtga_coach/reducer.py`, `src/mtga_coach/catalog.py`, `tests/fixtures/full_diff_delete.log`, `tests/fixtures/resync.log`, `tests/fixtures/unresolved_predecessor.log`, `tests/fixtures/incomplete_tail.log`, `tests/fixtures/cards.mtga`, `tests/test_ingest.py`, `tests/test_reducer.py`, `tests/test_catalog.py`
- **Depends on**: 
- **Parallel-safe**: no
- **Interfaces**: produces `parse_log(bytes, source_sha256) -> list[NormalizedRecord]`, `reduce_game(records) -> ReducedGame`, `resolve_cards(ids, card_database_path) -> dict[int, CardResolution]`, and quality values `complete`, `degraded`, `blocked` for T002.
- **Done when** (binary):
  - [ ] Failing tests `test_diff_replaces_game_object_by_instance_id`, `test_diff_replaces_zone_by_zone_id`, `test_full_resync_replaces_previous_state`, `test_connect_response_marks_self_seat`, `test_unresolved_predecessor_blocks_tactical_segment`, `test_incomplete_tail_preserves_prior_records`, and `test_catalog_keeps_unresolved_id` are added using only deterministic fixtures.
  - [ ] The parser attributes records to a game/match and derives `is_self` from `GREMessageType_ConnectResp.systemSeatIds`; the reducer replaces entities by their GRE identity, retains `source_line`, and emits a warning instead of guessing on an unsupported or missing predecessor.
  - [ ] The catalogue opens the configured `Raw_CardDatabase_*.mtga` as SQLite read-only, resolves `Cards` and `Localizations_ptBR` (fallback `Localizations_enUS`) through an isolated adapter, and returns an explicit unresolved entry for every unrecognised/unsupported ID.
  - [ ] The seven named tests pass with `python -m unittest tests.test_ingest tests.test_reducer tests.test_catalog -v`.
  - [ ] `python -m compileall -q src` passes.

### T002 — Persist reviews and expose the local API
- **Parent**: AC-1, AC-3, AC-9, AC-10, AC-12, AC-13, AC-14, AC-15, AC-16
- **Files**: `src/mtga_coach/storage.py`, `src/mtga_coach/service.py`, `src/mtga_coach/http_api.py`, `tests/fixtures/bo3_match.log`, `tests/test_storage.py`, `tests/test_service.py`, `tests/test_http_api.py`
- **Depends on**: T001
- **Parallel-safe**: no
- **Interfaces**: consumes T001 normalized/reduced records and card resolutions; produces `CoachService.import_configured()`, `import_upload(body: bytes)`, `summary()`, `games()`, `game_detail(game_id)`, `cards(ids)`, `save_note(payload)`, `save_experiment(payload)`, and `decision_context(game_id, index)` used by T003.
- **Done when** (binary):
  - [ ] Failing tests `test_reimport_is_idempotent`, `test_summary_excludes_in_progress_from_result_denominator`, `test_bo3_keeps_match_and_game_results_separate`, `test_post_sideboard_configuration_is_not_experiment_version`, `test_context_excludes_future_reveal`, and `test_notes_and_experiments_round_trip` are added.
  - [ ] SQLite records exactly one import per SHA-256 and persists summaries, frames, observed deck configuration, notes and experiments under `%LOCALAPPDATA%/mtga-coach`.
  - [ ] `GET /api/summary`, `/api/games`, `/api/games/{id}`, `/api/cards?ids=...`, `/api/games/{id}/context?index=...`, plus the specified import/note/experiment POST routes return documented JSON or `400`/`404`; raw uploaded bytes and configured-source reads share the same idempotent import path.
  - [ ] Context output contains only frame-time knowledge and clearly labels prior-match revelation versus current-game confirmation; it contains neither a raw match ID nor a future frame/action.
  - [ ] The six named tests pass with `python -m unittest tests.test_storage tests.test_service tests.test_http_api -v`.
  - [ ] `python -m compileall -q src` passes.

### T003 — Present replay and manual deck review in the browser
- **Parent**: AC-7, AC-8, AC-10, AC-12, AC-16
- **Files**: `src/mtga_coach/static/index.html`, `src/mtga_coach/static/app.css`, `src/mtga_coach/static/app.js`, `tests/test_static_ui.py`, `tests/test_replay_view_model.py`
- **Depends on**: T002
- **Parallel-safe**: no
- **Interfaces**: consumes only T002 endpoints; produces browser routes/views for summary, game list, game replay, notes, experiments and a downloadable/copyable context JSON. The UI must not read the log, database or card database directly.
- **Done when** (binary):
  - [ ] Failing tests `test_static_index_loads_without_external_script`, `test_replay_view_model_preserves_frame_order_and_quality_warning`, and `test_context_export_view_never_requests_future_frame` are added.
  - [ ] The game list renders result/status/mode/quality, replay navigation renders selected frame facts, deck composition and explicit card gaps, and degraded/blocked segments disable tactical-export controls for that segment.
  - [ ] The UI saves a bounded manual note and a deck hypothesis through the documented POST endpoints; it labels hypotheses as user experiments and offers no generated tactical verdict.
  - [ ] The user can copy or download the server-sanitised context for a selected eligible decision, with the UI visibly identifying unknown information and prior-match revelation.
  - [ ] The three named tests pass with `python -m unittest tests.test_static_ui tests.test_replay_view_model -v`.
  - [ ] `python -m compileall -q src` passes.

### T004 — Harden local operation and document the executable workflow
- **Parent**: AC-1, AC-11, AC-12
- **Files**: `src/mtga_coach/__main__.py`, `src/mtga_coach/http_api.py`, `scripts/launch_mtga_coach.ps1`, `README.md`, `tests/test_security.py`, `tests/test_smoke.py`
- **Depends on**: T003
- **Parallel-safe**: no
- **Interfaces**: starts with `python -m mtga_coach --host 127.0.0.1 --port 18731`; launcher calls this exact command and opens `http://127.0.0.1:18731/` only after readiness succeeds.
- **Done when** (binary):
  - [ ] Failing tests `test_server_rejects_non_loopback_bind`, `test_import_rejects_client_supplied_filesystem_path`, `test_context_contains_no_raw_log_or_sensitive_identifier`, and `test_loopback_launch_serves_summary` are added.
  - [ ] Startup defaults to and rejects deviation from `127.0.0.1:18731`, rejects oversized/unsupported uploads and path traversal, emits no raw-log body in response/error logs, and serves no directory index.
  - [ ] The PowerShell launcher creates only `%LOCALAPPDATA%/mtga-coach` runtime directories, starts the module, waits for the localhost readiness endpoint, and opens the browser; it does not install packages or transmit data.
  - [ ] `README.md` documents prerequisites (Python 3.14, detailed MTGA log), fixed source configuration, raw upload, local data locations, context-export limits, known replay/BO3 limits and the explicit absence of IA integration until a credential/budget decision.
  - [ ] The four named tests pass; `python -m unittest discover -s tests -v`, `python -m compileall -q src`, and `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/launch_mtga_coach.ps1 -SmokeTest` pass.
