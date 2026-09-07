# MTGA Coach — tarefas executáveis da primeira versão

Este arquivo é o contrato de sprint para implementação e revisão. A especificação vinculante é [spec.md](spec.md); a decomposição e as interfaces estão em [plan.md](plan.md). Não há repositório Git neste diretório no momento da criação deste arquivo; ao inicializá-lo, cada tarefa deve gerar um commit separado, sem reescrever trabalho alheio.

### T001 — Reconstruct observed game facts and card catalogue
- **Parent**: AC-2, AC-4, AC-5, AC-6, AC-8, AC-11
- **Files**: `src/mtga_coach/__init__.py`, `src/mtga_coach/config.py`, `src/mtga_coach/ingest.py`, `src/mtga_coach/reducer.py`, `src/mtga_coach/catalog.py`, `tests/fixtures/full_diff_delete.log`, `tests/fixtures/resync.log`, `tests/fixtures/unresolved_predecessor.log`, `tests/fixtures/incomplete_tail.log`, `tests/fixtures/cards.mtga`, `tests/test_ingest.py`, `tests/test_reducer.py`, `tests/test_catalog.py`
- **Depends on**: 
- **Parallel-safe**: no
- **Done when** (binary):
  - [x] Failing tests `test_diff_replaces_game_object_by_instance_id`, `test_diff_replaces_zone_by_zone_id`, `test_full_resync_replaces_previous_state`, `test_connect_response_marks_self_seat`, `test_unresolved_predecessor_blocks_tactical_segment`, `test_incomplete_tail_preserves_prior_records`, and `test_catalog_keeps_unresolved_id` are added.
  - [x] The reducer substitutes game objects by `instanceId` and zones by `zoneId`, derives `is_self` from `GREMessageType_ConnectResp.systemSeatIds`, preserves source line/warning, treats unsupported/missing lineage as `degraded` or `blocked`, and never invents a card resolution.
  - [x] The local SQLite catalogue resolves `Cards` plus `Localizations_ptBR` (fallback `Localizations_enUS`) read-only and keeps unsupported IDs explicit.
  - [x] The named behaviours are covered by `tests/test_ingest.py`, `tests/test_timeline.py` and `tests/test_catalog.py`.
  - [x] `python -m compileall -q src` passes.
  - Completion: reducer, catalogue and streaming parser implemented; the reducer test cases live in `test_ingest.py`/`test_timeline.py` rather than a separate `test_reducer.py`.

### T002 — Persist reviews and expose the local API
- **Parent**: AC-1, AC-3, AC-9, AC-10, AC-12, AC-13, AC-14, AC-15, AC-16
- **Files**: `src/mtga_coach/storage.py`, `src/mtga_coach/service.py`, `src/mtga_coach/http_api.py`, `tests/fixtures/bo3_match.log`, `tests/test_storage.py`, `tests/test_service.py`, `tests/test_http_api.py`
- **Depends on**: T001
- **Parallel-safe**: no
- **Done when** (binary):
  - [x] Failing tests `test_reimport_is_idempotent`, `test_summary_excludes_in_progress_from_result_denominator`, `test_bo3_keeps_match_and_game_results_separate`, `test_post_sideboard_configuration_is_not_experiment_version`, `test_context_excludes_future_reveal`, and `test_notes_and_experiments_round_trip` are added.
  - [x] All specified API endpoints return their documented response or `400`/`404`, and both fixed-source and byte-upload imports use the same SHA-256 idempotency path.
  - [x] Context excludes raw match identifiers, future frames/actions and raw log content while marking prior-match revelation separately from current-game knowledge.
  - [x] The named tests pass with `python -m unittest tests.test_storage tests.test_service tests.test_http_api -v`.
  - [x] `python -m compileall -q src` passes.
  - Completion: SQLite review store, loopback API, manual notes/experiments and sanitised contextual export implemented and verified.

### T003 — Present replay and manual deck review in the browser
- **Parent**: AC-7, AC-8, AC-10, AC-12, AC-16
- **Files**: `src/mtga_coach/static/index.html`, `src/mtga_coach/static/app.css`, `src/mtga_coach/static/app.js`, `tests/test_static_ui.py`, `tests/test_replay_view_model.py`
- **Depends on**: T002
- **Parallel-safe**: no
- **Done when** (binary):
  - [x] Failing tests `test_static_index_loads_without_external_script`, `test_replay_view_model_preserves_frame_order_and_quality_warning`, and `test_context_export_view_never_requests_future_frame` are added.
  - [x] The browser renders summary/list/replay/deck facts, explicit gaps and quality warnings; ineligible segments cannot export tactical context.
  - [x] Notes and user-declared deck hypotheses round-trip through the API, and context can be copied/downloaded only from the sanitised endpoint response.
  - [x] The view-model tests run with `node tests/test_ui.mjs`; the no-external-script check lives in `tests/test_security.py`.
  - [x] `python -m compileall -q src` passes.
  - Completion: replay, deck and training views implemented. The UI tests were written in `node:test` against the ES module instead of Python, because the view model is JavaScript.

### T004 — Harden local operation and document the executable workflow
- **Parent**: AC-1, AC-11, AC-12
- **Files**: `src/mtga_coach/__main__.py`, `src/mtga_coach/http_api.py`, `scripts/launch_mtga_coach.ps1`, `README.md`, `tests/test_security.py`, `tests/test_smoke.py`
- **Depends on**: T003
- **Parallel-safe**: no
- **Done when** (binary):
  - [x] Failing tests `test_server_rejects_non_loopback_bind`, `test_import_rejects_client_supplied_filesystem_path`, `test_context_contains_no_raw_log_or_sensitive_identifier`, and `test_loopback_launch_serves_summary` are added.
  - [x] Startup is fixed to `127.0.0.1:18731`, rejects arbitrary filesystem paths, oversized/unsupported upload, traversal and directory indexes, and logs no raw-log body.
  - [x] The launcher uses only local Python/runtime paths and its `-SmokeTest` completes; `README.md` states data location, import workflow, limits and the lack of an IA integration until a credential/budget decision.
  - [x] The named tests pass; `python -m unittest discover -s tests -t .`, `python -m compileall -q src`, and `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/launch_mtga_coach.ps1 -SmokeTest` pass.
  - Completion: loopback binding, upload limits and the launcher were already in place; `README.md` was missing and is now written.

---

## Segunda rodada — 07/09/2026

Origem: comparação com o estado da arte e medição contra o log real
([análise](../../analise/2026-09-07-estado-da-arte-e-lacunas.md)). A entrega A ficava inutilizável
na prática porque o Arena apaga o `Player.log` a cada reinício do cliente e o app só importava
sob demanda.

### T005 — Capturar a sessão enquanto o Arena escreve
- **Parent**: AC-1, AC-11, entrega D
- **Files**: `src/mtga_coach/scanner.py`, `src/mtga_coach/capture.py`, `tests/test_scanner.py`, `tests/test_capture.py`
- **Done when** (binary):
  - [x] Leitor incremental segura registro partido entre leituras, decodifica UTF-8 através da fronteira dos blocos e preserva a numeração de linha ao descartar o consumido.
  - [x] O seguidor detecta arquivo substituído por encolhimento ou por mudança dos bytes iniciais, sem tratar crescimento como rotação.
  - [x] `Player-prev.log` é importado uma vez; reimportação não duplica.
  - [x] Jogo concluído é liberado da memória depois de gravado.
  - [x] `python -m unittest tests.test_scanner tests.test_capture` passa.

### T006 — Ler os fatos que o protocolo já entrega
- **Parent**: AC-2, AC-13, AC-14
- **Files**: `src/mtga_coach/protocol.py`, `src/mtga_coach/ingest.py`, `src/mtga_coach/reducer.py`
- **Done when** (binary):
  - [x] Modo vem de `gameInfo.matchWinCondition`, com `mode_basis` declarando a origem; o nome do evento é só recurso de última instância.
  - [x] `finalMatchResult` resolve resultado e motivo, inclusive quando a partida terminou por concessão sem estado `GameOver`.
  - [x] Assento próprio e adversário saem de `authenticateResponse.clientId` × `reservedPlayers[].userId`.
  - [x] Quem começou, mulligans, rank, curingas e decks nomeados do jogador são gravados; os precons de catálogo são descartados.
  - [x] `AnnotationType_ObjectIdChanged` é aplicado antes da projeção, e a janela de estados do redutor é limitada.

### T007 — Linha do tempo a partir das anotações
- **Parent**: AC-10, entrega B
- **Files**: `src/mtga_coach/timeline.py`, `tests/test_timeline.py`
- **Done when** (binary):
  - [x] Transferência de zona vira evento nomeado por categoria; objeto não revelado gera evento sem identidade.
  - [x] Dano zero de contabilidade do motor não é reportado como dano.
  - [x] Tipo de anotação desconhecido é ignorado, nunca rotulado por adivinhação.
  - [x] `GET /api/games/{id}/timeline` devolve a narrativa com o nome local das cartas.

### T008 — Matemática de deck e de amostra
- **Parent**: entrega C
- **Files**: `src/mtga_coach/analysis.py`, `src/mtga_coach/catalog.py`, `tests/test_analysis.py`, `tests/test_service_analysis.py`
- **Done when** (binary):
  - [x] Hipergeométrico, intervalo de Wilson e tamanho de amostra conferem contra caso calculado à mão.
  - [x] Fontes por cor usam a tabela publicada do Karsten, com o piso hipergeométrico rotulado à parte.
  - [x] Terra conta pela identidade de cor, não pela cor da carta.
  - [x] Custo em curingas por raridade, com o que não foi resolvido declarado.
  - [x] Toda taxa exposta pela API vem com intervalo e `n`.

### T009 — Armazenamento normalizado e comprimido
- **Parent**: AC-1, AC-10
- **Files**: `src/mtga_coach/storage.py`, `src/mtga_coach/service.py`, `src/mtga_coach/http_api.py`
- **Done when** (binary):
  - [x] Esquema versão 2 com migração automática da versão 1, sem perda de partida.
  - [x] Quadros em blocos comprimidos com índice leve; resumo e listas nunca descomprimem quadro.
  - [x] `GET /api/games/{id}/frames?start=&limit=` pagina o replay.
  - [x] Cartas reveladas pelo adversário gravadas por jogo, lidas das zonas e não do fluxo de eventos.

### T010 — Conferir um BO3 real *(aberta)*
- **Parent**: AC-13, AC-15, AC-16
- **Depends on**: T005
- **Done when** (binary):
  - [ ] Um confronto BO3 real capturado com o acompanhamento ligado.
  - [ ] Resultado de jogo e de confronto conferidos separadamente contra o que o Arena mostrou.
  - [ ] Troca de sideboard entre jogos registrada como configuração, sem criar versão experimental.
  - [ ] Revisão do jogo 2 distingue carta vista no jogo 1 de carta confirmada no jogo atual.

---

## Terceira rodada — 07/09/2026

Origem: pedidos do operador na mesma sessão — imagens de carta, import/export de decks no
formato do Arena, conexão de IA, e a decisão de que o produto é **GTM**, com inglês como
idioma padrão.

### T011 — Arte de carta pelo Scryfall, opcional e em cache
- **Files**: `src/mtga_coach/art.py`, `tests/test_extras.py`
- **Done when** (binary):
  - [x] Desligado por padrão; sai da máquina só código de coleção e número da carta.
  - [x] Uma requisição em lote resolve o deck inteiro; carta só-Arena cai no `/cards/arena`.
  - [x] Carta sem impressão em papel é lembrada e não volta a ser pedida; falha de rede **não** é confundida com ausência de imagem.
  - [x] Imagem baixada uma vez e servida do disco por `/art/{id}.jpg`.
  - [x] Nenhum teste toca a rede (fetcher isolado).

### T012 — Import/export de deck no formato do Arena
- **Files**: `src/mtga_coach/decklist.py`, `src/mtga_coach/catalog.py`
- **Done when** (binary):
  - [x] Export escreve quantidade, nome em inglês, coleção e número; carta não resolvida sai como comentário.
  - [x] Import lê seções, quantidades e reporta a linha que não reconheceu.
  - [x] Coleção/número que não batem com o nome da linha caem para o nome, com aviso — um código de outro site não resolve silenciosamente para outra carta.
  - [x] Lista colada recebe a mesma análise (curva, base de mana, curingas) sem ser gravada.

### T013 — Leitura por IA sobre números já calculados
- **Files**: `src/mtga_coach/coach.py`, `src/mtga_coach/secrets.py`
- **Done when** (binary):
  - [x] O prompt de sistema proíbe recalcular, inventar texto de carta e afirmar jogada certa.
  - [x] Quatro modos: explicar, perguntar, alternativas e deck.
  - [x] Material = contexto sanitizado + números do app; nada de log bruto ou identificador de conta.
  - [x] Chave cifrada por DPAPI, fora do banco e do git; ausência de pacote ou de chave devolve motivo legível.

### T014 — Inglês como idioma do produto
- **Done when** (binary):
  - [x] Interface, mensagens de API, rótulos de evento, contexto exportado e prompts em inglês.
  - [x] Texto de carta segue a tabela de localização do próprio Arena, `enUS` por padrão.
  - [x] Rótulo e formato derivados na leitura, para linha gravada por build anterior ler igual.
  - [ ] Camada de i18n de verdade (troca de idioma pela interface) — não feita; hoje o padrão é fixo.
