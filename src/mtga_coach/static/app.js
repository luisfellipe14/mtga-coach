const API_MARKER = { 'X-MTGA-Coach': '1' };
const FRAME_PAGE = 25;

export const summaryEndpoint = () => '/api/summary';
export const contextEndpoint = (gameId, index) => `/api/games/${encodeURIComponent(gameId)}/context?index=${encodeURIComponent(index)}`;

export function buildRequestOptions(options = {}) {
  return { ...options, headers: { ...(options.headers ?? {}), ...API_MARKER } };
}

export function selectReplayFrame(frames, position) {
  const safePosition = Math.max(0, Math.min(Number(position) || 0, Math.max(frames.length - 1, 0)));
  const frame = frames[safePosition] ?? null;
  return { position: safePosition, frame, warning: frame?.warnings?.[0] ?? '' };
}

export function filterGamesByMode(games, mode) {
  return mode === 'ambos' ? games : games.filter((game) => game.mode === mode);
}

export function modeMetrics(summary, games, mode) {
  if (mode === 'ambos') return {
    games: summary.games ?? 0, completed: summary.completed ?? 0, wins: summary.wins ?? 0,
    losses: summary.losses ?? 0, draws: summary.draws ?? 0, matches: summary.matches ?? 0,
    win_rate: summary.win_rate ?? null,
  };
  const scoped = filterGamesByMode(games, mode);
  const completed = scoped.filter((game) => game.status === 'complete' && ['win', 'loss', 'draw'].includes(game.result));
  const wins = completed.filter((game) => game.result === 'win').length;
  const losses = completed.filter((game) => game.result === 'loss').length;
  const draws = completed.filter((game) => game.result === 'draw').length;
  const byMode = summary.by_mode?.[mode];
  const gamesCount = typeof byMode === 'number' ? byMode : (byMode?.games ?? scoped.length);
  const matches = new Set(scoped.map((game) => game.match_id_hashed).filter(Boolean)).size;
  return { games: gamesCount, completed: completed.length, wins, losses, draws, matches, win_rate: wins + losses ? wins / (wins + losses) : null };
}

// A row of the light frame index carries `has_action`; a loaded frame carries the action itself.
function isDecisionFrame(frame) {
  return Boolean(frame?.has_action || frame?.action || frame?.available_actions?.length || frame?.actions?.length);
}

export function firstDecisionPosition(frames) {
  const position = frames.findIndex(isDecisionFrame);
  return position < 0 ? 0 : position;
}

export function adjacentDecisionPosition(frames, currentPosition, direction) {
  for (let position = currentPosition + direction; position >= 0 && position < frames.length; position += direction) {
    if (isDecisionFrame(frames[position])) return position;
  }
  return null;
}

const state = {
  summary: null, games: [], mode: 'BO1', detail: null, framePosition: 0, cards: {}, deckCards: {},
  experiments: [], frameCache: new Map(), sidebarTab: 'decisao', deckReport: null, notes: [],
};
const $ = (selector) => document.querySelector(selector);

async function request(path, options = {}) {
  const response = await fetch(path, buildRequestOptions(options));
  let body = null;
  try { body = await response.json(); } catch { /* Errors without JSON keep their status. */ }
  if (!response.ok) throw new Error(body?.error || `A API respondeu ${response.status}.`);
  return body;
}

const postJson = (path, payload) => request(path, {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
});

function setMessage(text, kind = 'info') {
  const element = $('#app-message');
  element.hidden = !text;
  element.textContent = text || '';
  element.dataset.kind = kind;
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function empty(container, title, detail) {
  container.replaceChildren();
  const box = element('div', 'empty-state');
  box.append(element('strong', null, title), element('p', null, detail));
  container.append(box);
}

function qualityLabel(quality) {
  return ({ complete: 'Sem lacuna detectada', degraded: 'Lacuna na reconstrução', blocked: 'Trecho bloqueado' })[quality] ?? 'Qualidade não informada';
}

function resultLabel(result) { return ({ win: 'Vitória', loss: 'Derrota', draw: 'Empate', unknown: 'Sem desfecho' })[result] ?? 'Sem desfecho'; }
function startLabel(onPlay) { return onPlay === true ? 'Jogou primeiro' : onPlay === false ? 'Jogou depois' : 'Ordem não registrada'; }
function cardName(id) { const card = state.cards[id] || state.deckCards[id]; return card?.name || card?.name_en || `Carta não resolvida #${id}`; }
function listText(values) { return values?.length ? values.join(' · ') : '—'; }
function percent(value) { return typeof value === 'number' ? `${(value * 100).toFixed(1).replace('.', ',')}%` : '—'; }
function intervalText(interval) {
  if (!interval) return 'sem amostra';
  return `${percent(interval.rate)} · IC95 ${percent(interval.low)}–${percent(interval.high)} · n=${interval.n}`;
}
function bytesText(value) { return typeof value === 'number' ? `${(value / 1e6).toFixed(1).replace('.', ',')} MB` : '—'; }

function appendMetric(container, value, label, detail = '') {
  const card = element('article', 'metric');
  card.append(element('strong', null, value), element('span', null, label));
  if (detail) card.append(element('small', null, detail));
  container.append(card);
}

function appendStageMetric(container, label, stage) {
  if (!stage || typeof stage !== 'object') return;
  appendMetric(container, percent(stage.win_rate), label, `${stage.games ?? 0} jogos · ${stage.wins ?? 0} vitórias · ${stage.losses ?? 0} derrotas`);
}

function renderSummary() {
  const metrics = $('#metrics');
  metrics.replaceChildren();
  const summary = state.summary;
  if (!summary) { empty(metrics, 'Carregando resumo…', 'Consultando os logs locais já importados.'); return; }
  renderCaptureLine(summary.capture);
  const scoped = modeMetrics(summary, state.games, state.mode);
  const bo1 = modeMetrics(summary, state.games, 'BO1');
  const bo3 = modeMetrics(summary, state.games, 'BO3');
  const modeLabel = state.mode === 'ambos' ? 'TODOS OS MODOS' : `REVISÃO ${state.mode}`;
  $('#hero-mode-label').textContent = state.mode === 'ambos' ? 'COMPARAÇÃO POR MODO' : modeLabel;
  $('#hero-rate-label').textContent = state.mode === 'ambos' ? ' taxas separadas' : ` taxa de vitória ${state.mode}`;
  if (state.mode === 'ambos') {
    $('#bo1-rate').textContent = 'BO1 × BO3';
    $('#bo1-detail').textContent = `BO1: ${percent(bo1.win_rate)} em ${bo1.games} jogos · BO3: ${percent(bo3.win_rate)} em ${bo3.games} jogos.`;
    appendMetric(metrics, summary.games ?? 0, 'jogos observados', 'BO1 e BO3 apresentados separadamente');
    appendMetric(metrics, percent(bo1.win_rate), 'taxa BO1', `${bo1.wins} vitórias · ${bo1.losses} derrotas`);
    appendMetric(metrics, percent(bo3.win_rate), 'taxa BO3', `${bo3.wins} vitórias · ${bo3.losses} derrotas`);
    appendMetric(metrics, summary.matches ?? 0, 'confrontos observados', 'sem taxa mista');
    return;
  }
  appendMetric(metrics, scoped.games, `jogos ${state.mode}`);
  appendMetric(metrics, percent(scoped.win_rate), 'taxa de vitória', `${scoped.wins} vitórias · ${scoped.losses} derrotas`);
  appendMetric(metrics, scoped.completed, 'com desfecho', `${scoped.draws} empates`);
  appendMetric(metrics, scoped.matches, 'confrontos', `recorte ${state.mode}`);
  if (state.mode === 'BO3') {
    const bo3Summary = summary.by_mode?.BO3;
    const stages = bo3Summary?.by_stage;
    appendMetric(metrics, percent(bo3Summary?.match_win_rate), 'taxa por confronto', typeof bo3Summary?.match_wins === 'number' ? `${bo3Summary.match_wins} vitórias · ${bo3Summary.match_losses ?? 0} derrotas` : 'Resultado de confronto não recebido.');
    appendStageMetric(metrics, 'Jogo 1', stages?.game1);
    appendStageMetric(metrics, 'Jogos 2/3', stages?.post_sideboard);
  }
  $('#bo1-rate').textContent = percent(scoped.win_rate);
  $('#bo1-detail').textContent = scoped.games ? `${scoped.games} jogos observados · ${scoped.wins} vitórias · ${scoped.losses} derrotas.` : `Nenhum jogo ${state.mode} nesta importação.`;
}

function renderCaptureLine(capture) {
  const line = $('#capture-line');
  const button = $('#capture-toggle');
  if (!capture) { line.textContent = ''; return; }
  const running = Boolean(capture.running);
  button.textContent = running ? 'Parar acompanhamento' : 'Acompanhar partidas';
  button.classList.toggle('primary', running);
  const detail = running
    ? `acompanhando · ${bytesText(capture.bytes_read)} lidos · ${capture.sessions ?? 0} sessão(ões)`
    : 'acompanhamento parado — o Arena apaga o log ao reiniciar';
  line.textContent = ` · ${detail}${capture.error ? ` · ${capture.error}` : ''}`;
}

function gameCard(game) {
  const row = element('button', `game-row quality-${game.quality}`, '');
  row.type = 'button';
  const identity = element('div', 'game-identity');
  identity.append(element('strong', null, `${game.deck_label || 'Composição'} · jogo ${game.game_number ?? '—'}`),
    element('span', null, `${game.format || 'Formato não informado'} · ${game.mode || 'Modo não informado'}${game.opponent_name ? ` · contra ${game.opponent_name}` : ''}`));
  const meta = element('div', 'game-meta');
  meta.append(element('span', `result ${game.result}`, resultLabel(game.result)),
    element('span', null, startLabel(game.on_play)),
    element('span', 'quality-badge', qualityLabel(game.quality)),
    element('span', null, `${game.turns ?? 0} turnos`),
    element('span', null, `${game.decision_count ?? 0} decisões`));
  row.append(identity, meta);
  row.addEventListener('click', () => openGame(game.id));
  return row;
}

function renderGames() {
  const target = $('#game-list');
  const games = filterGamesByMode(state.games, state.mode);
  $('#games-count').textContent = `${games.length} jogo${games.length === 1 ? '' : 's'}`;
  target.replaceChildren();
  if (!games.length) return empty(target, 'Nenhum jogo neste filtro.', state.games.length ? 'Escolha BO1, BO3 ou Ambos.' : 'Importe seus logs locais para iniciar a revisão.');
  games.forEach((game) => target.append(gameCard(game)));
}

async function loadCards(ids, target = state.cards) {
  const missing = ids.filter((id) => id !== null && id !== undefined && !target[id]);
  if (!missing.length) return;
  const payload = await request(`/api/cards?ids=${encodeURIComponent(missing.join(','))}`);
  Object.assign(target, payload.cards ?? payload);
}

// ---------------------------------------------------------------- replay

async function frameAt(position) {
  if (state.frameCache.has(position)) return state.frameCache.get(position);
  const start = Math.floor(position / FRAME_PAGE) * FRAME_PAGE;
  const payload = await request(`/api/games/${encodeURIComponent(state.detail.id)}/frames?start=${start}&limit=${FRAME_PAGE}`);
  (payload.frames ?? []).forEach((frame, offset) => state.frameCache.set(start + offset, frame));
  return state.frameCache.get(position) ?? null;
}

function zoneBlock(zone, title) {
  const collapsible = ['Library', 'Graveyard', 'Exile', 'Revealed', 'Command'].includes(zone.type);
  const block = element(collapsible ? 'details' : 'section', `zone zone-${String(zone.type).toLowerCase()}`);
  if (collapsible) block.open = false;
  block.append(element(collapsible ? 'summary' : 'h4', null, title));
  const objects = zone.objects ?? [];
  if (objects.length) {
    const cards = element('div', 'card-strip');
    objects.forEach((object) => cards.append(cardTile(object)));
    block.append(cards);
  }
  if (zone.hidden_count) block.append(element('small', 'hidden-count', `${zone.hidden_count} carta${zone.hidden_count === 1 ? '' : 's'} oculta${zone.hidden_count === 1 ? '' : 's'}`));
  if (!objects.length && !zone.hidden_count) block.append(element('small', 'empty-zone', 'Sem objetos conhecidos.'));
  return block;
}

function cardTile(object) {
  const card = state.cards[object.card_id];
  const colors = card?.colors?.length ? card.colors.join('').toLowerCase() : 'unknown';
  const tile = element('button', `card-tile mana-${colors}`, ''); tile.type = 'button';
  tile.title = 'Inspecionar carta';
  tile.append(element('span', 'mana-line', card?.mana_cost || '—'), element('strong', null, cardName(object.card_id)));
  const hasPower = object.power !== null && object.power !== undefined && String(object.power).trim() !== '';
  const hasToughness = object.toughness !== null && object.toughness !== undefined && String(object.toughness).trim() !== '';
  const bits = [object.tapped ? 'Virada' : null, hasPower || hasToughness ? `${hasPower ? object.power : '?'}/${hasToughness ? object.toughness : '?'}` : null].filter(Boolean);
  if (bits.length) tile.append(element('small', null, bits.join(' · ')));
  tile.addEventListener('click', () => inspectCard(object.card_id));
  return tile;
}

function battlefieldZones(frame) {
  const battlefield = frame.zones?.find((zone) => zone.type === 'Battlefield' && zone.owner === 0);
  if (!battlefield) return [];
  const selfSeat = state.detail.self_seat;
  const controller = (object) => object.controller ?? object.owner;
  const own = { ...battlefield, objects: battlefield.objects?.filter((object) => controller(object) === selfSeat) ?? [] };
  const opponent = { ...battlefield, objects: battlefield.objects?.filter((object) => !own.objects.includes(object)) ?? [] };
  return [zoneBlock(own, 'Seu campo'), zoneBlock(opponent, 'Campo adversário')];
}

async function renderReplay() {
  const target = $('#replay-main');
  const detail = state.detail;
  if (!detail) return;
  const index = detail.frame_index ?? [];
  const position = Math.max(0, Math.min(state.framePosition, Math.max(index.length - 1, 0)));
  state.framePosition = position;
  const frame = index.length ? await frameAt(position) : null;
  target.replaceChildren();
  if (!frame) { empty(target, 'Sem quadros reconstruídos.', 'O log não registrou uma posição segura para replay.'); renderSidebar(null); return; }
  await loadCards(frameCardIds(frame));
  const controls = element('div', 'replay-controls');
  const back = element('button', 'icon-button', '←'); back.type = 'button'; back.disabled = position === 0; back.addEventListener('click', () => changeFrame(position - 1));
  const next = element('button', 'icon-button', '→'); next.type = 'button'; next.disabled = position >= index.length - 1; next.addEventListener('click', () => changeFrame(position + 1));
  const slider = document.createElement('input'); slider.type = 'range'; slider.min = '0'; slider.max = String(Math.max(index.length - 1, 0)); slider.value = String(position); slider.setAttribute('aria-label', 'Quadro do replay'); slider.addEventListener('change', () => changeFrame(Number(slider.value)));
  controls.append(back, slider, next, element('span', 'frame-count', `Quadro ${position + 1}/${index.length}`)); target.append(controls);
  const heading = element('div', 'position-heading');
  heading.append(element('strong', null, `Turno ${frame.turn ?? '—'}`), element('span', null, listText([frame.phase, frame.step])), element('span', null, `Linha de origem ${frame.source_line ?? 'não informada'}`));
  target.append(heading);
  const warning = frame.warnings?.[0] ?? '';
  if (frame.quality !== 'complete' || warning) target.append(element('div', `quality-notice ${frame.quality}`, warning || qualityLabel(frame.quality)));
  const lives = element('div', 'life-row');
  (frame.players ?? []).forEach((player) => lives.append(element('div', player.is_self ? 'life self' : 'life', `${player.is_self ? 'Você' : 'Adversário'} · ${player.life ?? '—'} vida`)));
  target.append(lives);
  if (frame.events?.length) {
    const strip = element('div', 'event-strip');
    strip.append(element('h4', null, 'Aconteceu neste estado'));
    frame.events.forEach((event) => strip.append(element('span', `event-chip event-${event.kind}`, describeEvent(event))));
    target.append(strip);
  }
  const board = element('div', 'board');
  battlefieldZones(frame).forEach((zone) => board.append(zone));
  const otherZones = frame.zones?.filter((zone) => !(zone.type === 'Battlefield' && zone.owner === 0)) ?? [];
  otherZones.forEach((zone) => {
    const owner = zone.owner === 0 ? 'Zona compartilhada' : (zone.owner === state.detail.self_seat ? 'Você' : 'Adversário');
    board.append(zoneBlock(zone, `${owner} · ${zone.type}`));
  });
  target.append(board);
  renderSidebar(frame);
}

function frameCardIds(frame) {
  const ids = new Set();
  frame.zones?.forEach((zone) => zone.objects?.forEach((object) => ids.add(object.card_id)));
  frame.action?.card_ids?.forEach((id) => ids.add(id));
  frame.events?.forEach((event) => { if (event.card_id) ids.add(event.card_id); });
  return [...ids];
}

function describeEvent(event) {
  const who = event.seat ? (event.seat === state.detail?.self_seat ? 'Você' : 'Adversário') : '';
  const name = event.card_id ? cardName(event.card_id) : '';
  if (event.kind === 'life') return `${who}: vida ${event.amount > 0 ? '+' : ''}${event.amount}`;
  if (event.kind === 'damage') return `${name} causou ${event.amount} de dano`;
  if (event.kind === 'turn') return `Turno de ${who || '—'}`;
  if (event.kind === 'draw' && !event.card_id) return `${who} comprou (não revelada)`;
  return `${event.label ?? event.kind}${name ? `: ${name}` : ''}`;
}

// ---------------------------------------------------------------- sidebar

const SIDEBAR_TABS = [
  ['decisao', 'Decisão'], ['biblioteca', 'Biblioteca'], ['adversario', 'Adversário'], ['linha', 'Linha do tempo'],
];

function renderSidebar(frame) {
  const target = $('#decision-sidebar');
  target.replaceChildren();
  const tabs = element('div', 'sidebar-tabs');
  SIDEBAR_TABS.forEach(([key, label]) => {
    const button = element('button', `sidebar-tab${state.sidebarTab === key ? ' active' : ''}`, label);
    button.type = 'button';
    button.addEventListener('click', () => { state.sidebarTab = key; renderSidebar(frame); });
    tabs.append(button);
  });
  target.append(tabs);
  const body = element('div', 'sidebar-body');
  target.append(body);
  if (!frame) { body.append(element('p', 'subtle', 'Selecione um quadro com dados de decisão.')); return; }
  if (state.sidebarTab === 'decisao') renderDecisionTab(body, frame);
  if (state.sidebarTab === 'biblioteca') renderLibraryTab(body, frame);
  if (state.sidebarTab === 'adversario') renderOpponentTab(body);
  if (state.sidebarTab === 'linha') renderTimelineTab(body);
}

function actionText(action, sourceLine) {
  const names = action.card_ids?.map((id) => cardName(id)).join(', ');
  const label = action.label || action.type || 'Ação sem rótulo';
  const source = action.source_line ?? sourceLine;
  return [label, names, source !== null && source !== undefined ? `linha ${source}` : null].filter(Boolean).join(' · ');
}

function actionList(title, actions, sourceLine) {
  const area = element('div', 'action-list');
  area.append(element('h4', null, title));
  if (!actions?.length) area.append(element('p', 'subtle', 'Nenhuma ação registrada.'));
  else actions.forEach((action) => area.append(element('p', null, actionText(action, sourceLine))));
  return area;
}

function renderDecisionTab(target, frame) {
  target.append(element('p', 'eyebrow', 'DECISÃO SELECIONADA'), element('h3', null, `Quadro ${frame.index}`));
  target.append(actionList('Realizada', frame.action ? [frame.action] : [], frame.source_line),
    actionList('Disponíveis', frame.available_actions ?? frame.actions ?? [], frame.source_line));
  const index = state.detail.frame_index ?? [];
  const previousDecision = adjacentDecisionPosition(index, state.framePosition, -1);
  const nextDecision = adjacentDecisionPosition(index, state.framePosition, 1);
  const navigation = element('div', 'decision-navigation');
  const previous = element('button', 'text-button', '← Decisão anterior'); previous.type = 'button'; previous.disabled = previousDecision === null; previous.addEventListener('click', () => changeFrame(previousDecision));
  const next = element('button', 'text-button', 'Próxima decisão →'); next.type = 'button'; next.disabled = nextDecision === null; next.addEventListener('click', () => changeFrame(nextDecision));
  navigation.append(previous, next); target.append(navigation);
  const safe = frame.quality === 'complete';
  target.append(element('p', safe ? 'eligibility yes' : 'eligibility no', safe ? 'Contexto elegível para revisão externa.' : 'Trecho não elegível: a qualidade impede contexto tático.'));
  const prompts = element('div', 'review-prompts');
  prompts.append(element('h4', null, 'Perguntas de revisão manual'), element('p', null, 'Que informação já era conhecida?'), element('p', null, 'Que alternativa registrada merecia comparação?'), element('p', null, 'Que hipótese você quer testar?'));
  target.append(prompts);
  const noteForm = document.createElement('form'); noteForm.className = 'note-form';
  const text = document.createElement('textarea'); text.name = 'body'; text.maxLength = 2000; text.required = true; text.placeholder = 'Sua observação sobre esta posição…';
  const save = element('button', 'button secondary', 'Salvar nota'); save.type = 'submit';
  noteForm.append(element('label', null, 'Nota manual'), text, save);
  noteForm.addEventListener('submit', (event) => saveNote(event, frame.index, text));
  target.append(noteForm);
  const context = element('button', 'button primary', 'Copiar contexto'); context.type = 'button'; context.disabled = !safe;
  context.addEventListener('click', () => copyContext(frame.index)); target.append(context);
  const inspect = element('div', 'card-inspector'); inspect.id = 'card-inspector';
  inspect.append(element('h4', null, 'Carta selecionada'), element('p', 'subtle', 'Clique em uma carta conhecida para ler o texto do catálogo local.'));
  target.append(inspect);
}

async function renderLibraryTab(target, frame) {
  target.append(element('p', 'eyebrow', 'SEU GRIMÓRIO NESTE INSTANTE'));
  target.append(element('p', 'subtle', 'Calculado a partir da lista registrada menos cada cópia já vista. Só vale para o seu deck.'));
  const holder = element('div', 'library-body', 'Consultando…');
  target.append(holder);
  try {
    const payload = await request(`/api/games/${encodeURIComponent(state.detail.id)}/library?index=${frame.index}`);
    holder.replaceChildren();
    if (!payload.eligible) { holder.append(element('p', 'gap', payload.reason || 'Indisponível.')); return; }
    holder.append(element('h3', null, `${payload.size} cartas restantes`));
    holder.append(element('p', 'subtle', `${payload.lands} terras · ${percent(payload.land_ratio)} do grimório${payload.matches_report ? '' : ' · divergente da contagem do log'}`));
    const list = element('div', 'library-list');
    payload.entries.slice(0, 24).forEach((entry) => {
      const row = element('div', 'library-row');
      row.append(element('strong', null, `${entry.quantity}× ${entry.name}`),
        element('span', null, `próxima compra ${percent(entry.next_draw)} · em 3 compras ${percent(entry.within_three)}`));
      list.append(row);
    });
    holder.append(list);
  } catch (error) { holder.replaceChildren(element('p', 'gap', error.message)); }
}

async function renderOpponentTab(target) {
  target.append(element('p', 'eyebrow', 'O QUE O ADVERSÁRIO MOSTROU'));
  const holder = element('div', 'opponent-body', 'Consultando…');
  target.append(holder);
  try {
    const payload = await request(`/api/games/${encodeURIComponent(state.detail.id)}/opponent`);
    holder.replaceChildren();
    holder.append(element('p', 'subtle', payload.note));
    const colours = Object.entries(payload.colours ?? {});
    holder.append(element('p', null, colours.length ? `Cores vistas: ${colours.map(([colour, count]) => `${colour} (${count})`).join(' · ')}` : 'Nenhuma cor identificada ainda.'));
    if (payload.prior_games?.length) holder.append(element('p', 'gap', `${payload.prior_games.length} carta(s) vistas em jogos anteriores deste confronto.`));
    const list = element('div', 'library-list');
    (payload.cards ?? []).forEach((card) => {
      const row = element('div', 'library-row');
      row.append(element('strong', null, card.name), element('span', null, `${card.mana_cost || '—'} · ${card.type_line || 'tipo não resolvido'}`));
      list.append(row);
    });
    holder.append((payload.cards ?? []).length ? list : element('p', 'subtle', 'Nada revelado até aqui.'));
  } catch (error) { holder.replaceChildren(element('p', 'gap', error.message)); }
}

async function renderTimelineTab(target) {
  target.append(element('p', 'eyebrow', 'LINHA DO TEMPO'));
  const holder = element('div', 'timeline-body', 'Consultando…');
  target.append(holder);
  try {
    const payload = await request(`/api/games/${encodeURIComponent(state.detail.id)}/timeline`);
    holder.replaceChildren();
    if (!payload.events?.length) { holder.append(element('p', 'subtle', 'Este jogo foi importado antes da leitura de anotações. Reimporte o log para gerar a linha do tempo.')); return; }
    let turn = null;
    payload.events.forEach((event) => {
      if (event.turn !== turn) { turn = event.turn; holder.append(element('h4', null, `Turno ${turn}`)); }
      const row = element('button', `timeline-row${event.is_self ? ' self' : ''}`, event.text);
      row.type = 'button';
      row.addEventListener('click', () => changeFrame(event.frame_index));
      holder.append(row);
    });
  } catch (error) { holder.replaceChildren(element('p', 'gap', error.message)); }
}

function inspectCard(id) {
  const target = $('#card-inspector'); if (!target) return;
  target.replaceChildren(); const card = state.cards[id];
  target.append(element('h4', null, cardName(id)));
  if (!card || card.resolved === false) { target.append(element('p', 'gap', `ID #${id} não resolvido pelo catálogo local.`)); return; }
  target.append(element('p', 'mana-line', `${card.mana_cost || '—'} · valor ${card.mana_value ?? '—'}`), element('p', 'type-line', card.type_line || 'Tipo não informado'), element('p', 'rules-text', card.text || 'Texto não disponível no catálogo local.'));
  const hasPower = card.power !== null && card.power !== undefined && String(card.power).trim() !== '';
  const hasToughness = card.toughness !== null && card.toughness !== undefined && String(card.toughness).trim() !== '';
  if (hasPower || hasToughness) target.append(element('p', null, `${hasPower ? card.power : '?'}/${hasToughness ? card.toughness : '?'}`));
}

function changeFrame(position) { state.framePosition = position; renderReplay(); }

async function openGame(id) {
  setMessage('Carregando replay…');
  try {
    state.detail = await request(`/api/games/${encodeURIComponent(id)}`);
    state.frameCache = new Map();
    state.framePosition = firstDecisionPosition(state.detail.frame_index ?? []);
    Object.assign(state.cards, state.detail.cards ?? {});
    $('#replay-section').hidden = false;
    await renderReplay();
    $('#replay-section').scrollIntoView({ behavior: reducedMotion() ? 'auto' : 'smooth', block: 'start' });
    setMessage('');
    renderTraining();
  } catch (error) { setMessage(`Não foi possível abrir o replay: ${error.message}`, 'error'); }
}

async function saveNote(event, frameIndex, input) {
  event.preventDefault(); const body = input.value.trim(); if (!body) return;
  try {
    await postJson('/api/notes', { game_id: state.detail.id, frame_index: frameIndex, body, tags: [] });
    input.value = '';
    state.detail = await request(`/api/games/${encodeURIComponent(state.detail.id)}`);
    await renderReplay(); await loadNotes(); setMessage('Nota manual salva.');
  } catch (error) { setMessage(`A nota não foi salva: ${error.message}`, 'error'); }
}

async function copyContext(index) {
  try {
    const response = await request(contextEndpoint(state.detail.id, index));
    if (!response.eligible) throw new Error('O servidor informou que este quadro não é elegível.');
    const text = response.text || JSON.stringify(response.context ?? {}, null, 2);
    await navigator.clipboard.writeText(text);
    setMessage('Contexto sanitizado copiado.');
  } catch (error) { setMessage(`O contexto não foi copiado: ${error.message}`, 'error'); }
}

// ---------------------------------------------------------------- decks

function renderDecks() {
  const list = $('#deck-list'); const detail = $('#deck-detail');
  list.replaceChildren(); detail.replaceChildren();
  const decks = state.summary?.decks ?? [];
  const select = $('#experiment-deck'); select.replaceChildren();
  if (!decks.length) {
    empty(list, 'Nenhum deck observado.', 'Importe uma partida que registre a composição do deck.');
    empty(detail, 'Sem composição para comparar.', 'O catálogo será consultado somente para cartas registradas.');
    return;
  }
  decks.forEach((deckEntry, index) => {
    const scoped = deckModeMetrics(deckEntry);
    const option = element('option', null, deckEntry.label || deckEntry.id); option.value = deckEntry.id; select.append(option);
    const button = element('button', `deck-version ${index === 0 ? 'active' : ''}`, '');
    button.type = 'button';
    button.append(element('strong', null, deckEntry.label || `Versão ${deckEntry.id}`),
      element('span', null, `${deckEntry.main_count ?? 0} principais · ${scoped.games} jogos ${state.mode === 'ambos' ? '' : state.mode}`),
      element('small', null, percent(scoped.win_rate)));
    button.addEventListener('click', () => { list.querySelectorAll('.deck-version').forEach((item) => item.classList.remove('active')); button.classList.add('active'); showDeck(deckEntry.id); });
    list.append(button);
  });
  showDeck(decks[0].id);
}

function deckModeMetrics(entry) {
  if (state.mode === 'ambos') return { games: entry.game_count ?? 0, wins: entry.wins ?? 0, losses: entry.losses ?? 0, win_rate: entry.win_rate ?? null };
  const reported = entry.by_mode?.[state.mode];
  if (reported && typeof reported === 'object') return { games: reported.games ?? 0, wins: reported.wins ?? 0, losses: reported.losses ?? 0, win_rate: reported.win_rate ?? null };
  const scoped = state.games.filter((game) => game.deck_id === entry.id && game.mode === state.mode);
  const completed = scoped.filter((game) => game.status === 'complete' && ['win', 'loss'].includes(game.result));
  const wins = completed.filter((game) => game.result === 'win').length;
  const losses = completed.filter((game) => game.result === 'loss').length;
  return { games: typeof reported === 'number' ? reported : scoped.length, wins, losses, win_rate: wins + losses ? wins / (wins + losses) : null };
}

async function showDeck(deckId) {
  const target = $('#deck-detail');
  target.replaceChildren(element('p', 'subtle', 'Analisando a composição…'));
  let report;
  try { report = await request(`/api/decks/${encodeURIComponent(deckId)}`); }
  catch (error) { target.replaceChildren(element('p', 'gap', error.message)); return; }
  state.deckReport = report;
  Object.assign(state.deckCards, report.cards ?? {});
  target.replaceChildren();
  target.append(element('p', 'eyebrow', 'COMPOSIÇÃO OBSERVADA'), element('h2', null, report.label || `Versão ${report.deck_id}`));
  target.append(element('p', 'subtle', `${report.wins} vitórias · ${report.losses} derrotas em ${report.games} jogos. ${intervalText(report.interval)}.`));
  target.append(bindDeckControl(report));
  target.append(curveBlock(report));
  target.append(manaBaseBlock(report));
  target.append(wildcardBlock(report));
  target.append(cardStatsBlock(report.card_stats));
  target.append(deckSection('Principal', report.deck?.main ?? []), deckSection('Sideboard', report.deck?.sideboard ?? []));
}

function bindDeckControl(report) {
  const box = element('div', 'bind-deck');
  const named = state.summary?.named_decks ?? [];
  box.append(element('h4', null, 'Nome do deck'));
  if (!named.length) { box.append(element('p', 'subtle', 'Nenhum deck nomeado foi lido do log ainda.')); return box; }
  const select = document.createElement('select');
  select.append(element('option', null, 'Escolher deck salvo no Arena…'));
  named.forEach((deck) => { const option = element('option', null, `${deck.name} (${deck.format || 'formato não informado'})`); option.value = deck.uid; select.append(option); });
  const apply = element('button', 'button secondary', 'Vincular'); apply.type = 'button';
  apply.addEventListener('click', async () => {
    if (!select.value) return;
    try { await postJson('/api/decks/bind', { deck_id: report.deck_id, deck_uid: select.value }); setMessage('Composição vinculada ao deck salvo.'); await refresh(); renderDecks(); }
    catch (error) { setMessage(`O vínculo falhou: ${error.message}`, 'error'); }
  });
  box.append(select, apply);
  box.append(element('small', null, 'O log não liga a lista jogada ao deck salvo; o vínculo é sua escolha e fica registrado.'));
  return box;
}

function curveBlock(report) {
  const curve = element('div', 'mana-curve');
  curve.append(element('h3', null, 'Curva de mana · principal sem terras'));
  curve.append(element('small', null, `${report.lands} terras · valor médio ${report.average_mana_value ?? '—'}${report.lands_recommended ? ` · regressão publicada sugere ${report.lands_recommended}` : ''}`));
  if (!report.curve) curve.append(element('p', 'gap', `Curva indisponível: ${report.unresolved.length} carta(s) do principal não foram resolvidas no catálogo local.`));
  else Object.entries(report.curve).forEach(([mana, quantity]) => curve.append(element('span', 'curve-bar', `${mana}: ${quantity}`)));
  return curve;
}

function manaBaseBlock(report) {
  const box = element('section', 'deck-section');
  box.append(element('h3', null, 'Base de mana'));
  const requirements = report.colour_requirements ?? [];
  if (!requirements.length) {
    box.append(element('p', 'subtle', 'Nenhuma cor obrigatória nesta lista — só custos genéricos, híbridos ou phyrexianos.'));
    if (report.flexible_costs?.length) box.append(element('small', null, `Custos flexíveis: ${report.flexible_costs.join(', ')}.`));
    return box;
  }
  requirements.forEach((item) => {
    const row = element('div', `deck-card${item.shortfall ? ' shortfall' : ''}`);
    row.append(element('strong', null, `${item.colour} ×${item.pips} no turno ${item.turn}: ${item.have} de ${item.needed} fontes`),
      element('span', null, item.shortfall ? `faltam ${item.shortfall} — exigência puxada por ${item.driver}` : `atende — exigência puxada por ${item.driver}`));
    box.append(row);
  });
  if (report.flexible_costs?.length) box.append(element('small', null, `Fora da conta, porque a cor não é obrigatória: ${report.flexible_costs.join(', ')} (mana híbrido ou phyrexiano).`));
  box.append(element('small', null, report.karsten_citation));
  box.append(element('small', null, report.bo1_caveat));
  return box;
}

function wildcardBlock(report) {
  const box = element('section', 'deck-section');
  box.append(element('h3', null, 'Custo em curingas'));
  const cost = report.wildcards?.cost ?? {};
  const owned = state.summary?.inventory ?? {};
  const map = { common: ['comuns', 'WildCardCommons'], uncommon: ['incomuns', 'WildCardUnCommons'], rare: ['raras', 'WildCardRares'], mythic: ['míticas', 'WildCardMythics'] };
  Object.entries(map).forEach(([key, [label, inventoryKey]]) => {
    const row = element('div', 'deck-card');
    row.append(element('strong', null, `${cost[key] ?? 0} ${label}`),
      element('span', null, owned[inventoryKey] !== undefined ? `você tem ${owned[inventoryKey]}` : 'estoque não lido'));
    box.append(row);
  });
  box.append(element('small', null, 'Custo da lista inteira. O Arena parou de publicar a coleção no log, então o app não sabe quais cópias você já possui.'));
  return box;
}

function cardStatsBlock(stats) {
  const box = element('section', 'deck-section');
  box.append(element('h3', null, 'Cartas na mão × resultado'));
  if (!stats?.rows?.length) { box.append(element('p', 'subtle', 'Sem partidas concluídas com mão registrada nesta composição.')); return box; }
  stats.rows.slice(0, 20).forEach((row) => {
    const line = element('div', 'deck-card');
    line.append(element('strong', null, row.name), element('span', null, intervalText(row.interval)));
    box.append(line);
  });
  box.append(element('small', null, `${stats.note} Separar 55% de 50% exigiria cerca de ${stats.games_to_detect_five_points} partidas por braço.`));
  return box;
}

function deckSection(title, entries) {
  const section = element('section', 'deck-section');
  section.append(element('h3', null, title));
  if (!entries.length) section.append(element('p', 'subtle', 'Nenhuma carta registrada.'));
  entries.forEach(({ id, quantity }) => {
    const card = state.deckCards[id];
    const row = element('div', 'deck-card');
    row.append(element('strong', null, `${quantity}× ${cardName(id)}`), element('span', null, card?.type_line || `ID #${id} não resolvido`));
    section.append(row);
  });
  return section;
}

async function saveExperiment(event) {
  event.preventDefault();
  const formElement = event.currentTarget;
  const payload = Object.fromEntries(new FormData(formElement).entries());
  payload.changes = { description: payload.changes || 'Sem alteração detalhada.' };
  payload.status = 'planned';
  try { await postJson('/api/experiments', payload); formElement.reset(); await loadExperiments(); setMessage('Hipótese registrada como experimento do usuário.'); }
  catch (error) { setMessage(`A hipótese não foi registrada: ${error.message}`, 'error'); }
}

async function loadExperiments() {
  try { const payload = await request('/api/experiments'); state.experiments = payload.experiments ?? []; }
  catch (error) { state.experiments = []; setMessage(`Os experimentos não carregaram: ${error.message}`, 'error'); }
  renderExperiments();
}

function renderExperiments() {
  const target = $('#experiments'); target.replaceChildren();
  if (!state.experiments.length) { target.append(element('p', 'subtle', 'Nenhuma hipótese registrada.')); return; }
  state.experiments.forEach((experiment) => {
    const item = element('article', 'experiment');
    item.append(element('strong', null, experiment.title || 'Hipótese sem título'), element('p', null, experiment.hypothesis || ''), element('small', null, `Deck ${experiment.deck_id ?? '—'} · ${experiment.status ?? 'planned'}`));
    target.append(item);
  });
}

// ---------------------------------------------------------------- statistics

function statRow(label, metrics) {
  const row = element('div', 'deck-card');
  row.append(element('strong', null, label), element('span', null, intervalText(metrics?.interval)));
  return row;
}

function renderStats() {
  const target = $('#stats-body'); target.replaceChildren();
  const summary = state.summary;
  if (!summary) { empty(target, 'Sem dados.', 'Importe um log para calcular estatísticas.'); return; }
  const rank = summary.rank;
  if (rank?.constructedClass) {
    const box = element('section', 'deck-section');
    box.append(element('h3', null, 'Rank construído'));
    box.append(element('p', null, `${rank.constructedClass} ${rank.constructedLevel ?? ''} · temporada ${rank.constructedSeasonOrdinal ?? '—'} · ${rank.constructedMatchesWon ?? 0} vitórias e ${rank.constructedMatchesLost ?? 0} derrotas registradas pelo cliente`));
    target.append(box);
  }
  const start = element('section', 'deck-section');
  start.append(element('h3', null, 'Quem começou'));
  start.append(statRow('Jogando primeiro', summary.by_start?.on_play), statRow('Jogando depois', summary.by_start?.on_draw));
  if (summary.by_start?.unknown) start.append(element('small', null, `${summary.by_start.unknown} jogo(s) sem a ordem registrada.`));
  target.append(start);

  const mull = element('section', 'deck-section');
  mull.append(element('h3', null, 'Mulligans'));
  const kept = state.games.filter((game) => game.mulligans_self === 0).length;
  const mulled = state.games.filter((game) => (game.mulligans_self ?? 0) > 0).length;
  mull.append(element('p', null, `${kept} mão(s) mantidas de sete · ${mulled} jogo(s) com mulligan.`));
  target.append(mull);

  const modes = element('section', 'deck-section');
  modes.append(element('h3', null, 'Por modo'));
  modes.append(statRow('BO1', summary.by_mode?.BO1), statRow('BO3 (jogos)', summary.by_mode?.BO3));
  target.append(modes);

  const health = element('section', 'deck-section');
  health.append(element('h3', null, 'Base local'));
  health.append(element('p', null, `${summary.imports ?? 0} importação(ões) · banco com ${bytesText(summary.database_bytes)} · ${summary.named_decks?.length ?? 0} deck(s) nomeados lidos do log.`));
  if (summary.capture) health.append(element('p', 'subtle', summary.capture.running ? `Acompanhamento ativo: ${bytesText(summary.capture.bytes_read)} lidos em ${summary.capture.sessions} sessão(ões). Logs detalhados: ${summary.capture.detailed_logs === false ? 'DESLIGADOS no Arena' : summary.capture.detailed_logs ? 'ligados' : 'não determinado'}.` : 'Acompanhamento parado. O Arena apaga o Player.log a cada reinício do cliente: sem acompanhamento, a sessão se perde.'));
  target.append(health);

  if (summary.warnings?.length) {
    const warn = element('section', 'deck-section');
    warn.append(element('h3', null, 'Avisos da importação'));
    summary.warnings.forEach((warning) => warn.append(element('p', 'gap', warning)));
    target.append(warn);
  }
}

// ---------------------------------------------------------------- training

async function loadNotes() {
  try { const payload = await request('/api/notes'); state.notes = payload.notes ?? []; }
  catch { state.notes = []; }
  renderTraining();
}

function renderTraining() {
  const target = $('#training-notes'); target.replaceChildren();
  if (!state.notes.length) return empty(target, 'Nenhuma nota registrada.', 'Abra uma partida, escolha uma posição e registre a reflexão na barra lateral do replay.');
  const byGame = new Map(state.games.map((game) => [game.id, game]));
  state.notes.slice().reverse().forEach((note) => {
    const game = byGame.get(note.game_id);
    const item = element('article', 'training-note');
    const open = element('button', 'text-button', `${game ? `${game.deck_label} · ${resultLabel(game.result)}` : note.game_id} · quadro ${note.frame_index ?? '—'}`);
    open.type = 'button';
    open.addEventListener('click', async () => { await openGame(note.game_id); if (note.frame_index !== null) changeFrame(note.frame_index); setView('partidas'); });
    item.append(open, element('p', null, note.body), element('small', null, listText(note.tags)));
    target.append(item);
  });
}

// ---------------------------------------------------------------- shell

async function importConfigured() {
  setMessage('Importando o arquivo configurado…');
  try {
    const result = await postJson('/api/import', { source: 'configured' });
    setMessage(result.created ? `Importado: ${result.games} jogo(s) em ${result.record_count} registros.` : 'Este arquivo já tinha sido importado.');
    await refresh();
  } catch (error) { setMessage(`A importação falhou: ${error.message}`, 'error'); }
}

async function uploadLog(event) {
  const file = event.target.files?.[0]; if (!file) return;
  setMessage(`Enviando ${file.name}…`);
  try {
    const result = await request('/api/import', { method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body: await file.arrayBuffer() });
    setMessage(result.created ? `Importado: ${result.games} jogo(s).` : 'Este arquivo já tinha sido importado.');
    await refresh();
  } catch (error) { setMessage(`O arquivo não foi importado: ${error.message}`, 'error'); }
  finally { event.target.value = ''; }
}

async function toggleCapture() {
  const running = Boolean(state.summary?.capture?.running);
  setMessage(running ? 'Parando o acompanhamento…' : 'Iniciando o acompanhamento do log…');
  try {
    const capture = await postJson(running ? '/api/capture/stop' : '/api/capture/start', {});
    renderCaptureLine(capture);
    setMessage(capture.running ? 'Acompanhando o Player.log. Deixe o app aberto enquanto joga.' : 'Acompanhamento parado.');
    await refresh();
  } catch (error) { setMessage(`O acompanhamento falhou: ${error.message}`, 'error'); }
}

function setView(name) {
  document.querySelectorAll('.view').forEach((view) => view.classList.toggle('active', view.id === `view-${name}`));
  document.querySelectorAll('.nav-item').forEach((button) => button.classList.toggle('active', button.dataset.view === name));
  if (name === 'decks') { renderDecks(); loadExperiments(); }
  if (name === 'estatisticas') renderStats();
  if (name === 'treino') loadNotes();
}

function reducedMotion() { return window.matchMedia?.('(prefers-reduced-motion: reduce)').matches; }

async function refresh() {
  try {
    const [summary, games] = await Promise.all([request(summaryEndpoint()), request('/api/games')]);
    state.summary = summary;
    state.games = games.games ?? [];
    renderSummary(); renderGames();
    if ($('#view-decks').classList.contains('active')) renderDecks();
    if ($('#view-estatisticas').classList.contains('active')) renderStats();
  } catch (error) {
    state.summary = null; state.games = [];
    renderSummary(); renderGames();
    setMessage(`Não foi possível acessar o serviço local: ${error.message}`, 'error');
  }
}

function boot() {
  document.querySelectorAll('.nav-item').forEach((button) => button.addEventListener('click', () => setView(button.dataset.view)));
  document.querySelectorAll('.mode').forEach((button) => button.addEventListener('click', () => {
    state.mode = button.dataset.mode;
    document.querySelectorAll('.mode').forEach((item) => item.classList.toggle('active', item === button));
    renderSummary(); renderGames();
    if ($('#view-decks').classList.contains('active')) renderDecks();
  }));
  $('#import-configured').addEventListener('click', importConfigured);
  $('#capture-toggle').addEventListener('click', toggleCapture);
  $('#log-upload').addEventListener('change', uploadLog);
  $('#close-replay').addEventListener('click', () => { $('#replay-section').hidden = true; });
  $('#experiment-form').addEventListener('submit', saveExperiment);
  document.addEventListener('keydown', (event) => {
    if (!state.detail || $('#replay-section').hidden || (event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement)) return;
    if (event.key === 'ArrowLeft') { event.preventDefault(); changeFrame(state.framePosition - 1); }
    if (event.key === 'ArrowRight') { event.preventDefault(); changeFrame(state.framePosition + 1); }
  });
  refresh();
  setInterval(() => { if (state.summary?.capture?.running) refresh(); }, 15000);
}

if (typeof document !== 'undefined') document.addEventListener('DOMContentLoaded', boot);
