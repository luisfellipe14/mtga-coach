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
  return mode === 'both' ? games : games.filter((game) => game.mode === mode);
}

export function modeMetrics(summary, games, mode) {
  if (mode === 'both') return {
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
  experiments: [], frameCache: new Map(), sidebarTab: 'decision', deckReport: null, notes: [],
  coachAnswer: null, coachMode: 'explain', coachBusy: false, stepMode: 'all',
  draft: null, draftTimer: null, draftStamp: '', deck: null, deckOpen: false, review: null,
  signals: null, primer: null, momentsFor: null, moments: null,
  live: null, liveTimer: null, liveStamp: '', liveAll: false, rankTrack: 'constructed', grades: {}, gradeSet: '', handle: '',
};
const $ = (selector) => document.querySelector(selector);

async function request(path, options = {}) {
  const response = await fetch(path, buildRequestOptions(options));
  let body = null;
  try { body = await response.json(); } catch { /* Errors without JSON keep their status. */ }
  if (!response.ok) throw new Error(body?.error || `The API answered ${response.status}.`);
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
  return ({ complete: 'No gap detected', degraded: 'Gap in the reconstruction', blocked: 'Blocked stretch' })[quality] ?? 'Quality not reported';
}

function resultLabel(result) { return ({ win: 'Win', loss: 'Loss', draw: 'Draw', unknown: 'No result' })[result] ?? 'No result'; }
function startLabel(onPlay) { return onPlay === true ? 'On the play' : onPlay === false ? 'On the draw' : 'Order not recorded'; }
function cardName(id) { const card = state.cards[id] || state.deckCards[id]; return card?.name || card?.name_en || `Unresolved card #${id}`; }
function listText(values) { return values?.length ? values.join(' · ') : '—'; }
function percent(value) { return typeof value === 'number' ? `${(value * 100).toFixed(1)}%` : '—'; }
function intervalText(interval) {
  if (!interval) return 'no sample';
  return `${percent(interval.rate)} · 95% CI ${percent(interval.low)}–${percent(interval.high)} · n=${interval.n}`;
}
function bytesText(value) { return typeof value === 'number' ? `${(value / 1e6).toFixed(1)} MB` : '—'; }

const MANA_ORDER = 'WUBRG';

function deckColours(deckId) {
  return state.summary?.decks?.find((deck) => deck.id === deckId)?.colours ?? [];
}

function colourSpine(colours) {
  const spine = element('div', 'colour-spine');
  const stops = (colours.length ? colours : ['C']).map((colour) => `var(--mana-${colour.toLowerCase()})`);
  spine.style.background = stops.length === 1 ? stops[0]
    : `linear-gradient(to bottom, ${stops.map((stop, at) => `${stop} ${Math.round(at * 100 / stops.length)}% ${Math.round((at + 1) * 100 / stops.length)}%`).join(', ')})`;
  return spine;
}

function colourPips(colours) {
  const box = element('span', 'deck-colours');
  (colours.length ? colours : ['C']).forEach((colour) => box.append(element('span', `pip pip-${colour}`)));
  return box;
}

function appendMetric(container, value, label, detail = '') {
  const card = element('article', 'metric');
  card.append(element('strong', null, value), element('span', null, label));
  if (detail) card.append(element('small', null, detail));
  container.append(card);
}

function appendStageMetric(container, label, stage) {
  if (!stage || typeof stage !== 'object') return;
  appendMetric(container, percent(stage.win_rate), label, `${stage.games ?? 0} games · ${stage.wins ?? 0} wins · ${stage.losses ?? 0} losses`);
}

function renderSummary() {
  const metrics = $('#metrics');
  metrics.replaceChildren();
  const summary = state.summary;
  if (!summary) { empty(metrics, 'Loading summary…', 'Reading the logs already imported.'); return; }
  renderCaptureLine(summary.capture);
  renderFoot(summary);
  const scoped = modeMetrics(summary, state.games, state.mode);
  const bo1 = modeMetrics(summary, state.games, 'BO1');
  const bo3 = modeMetrics(summary, state.games, 'BO3');
  const modeLabel = state.mode === 'both' ? 'ALL MODES' : `${state.mode} REVIEW`;
  $('#hero-mode-label').textContent = state.mode === 'both' ? 'MODE COMPARISON' : modeLabel;
  $('#hero-rate-label').textContent = state.mode === 'both' ? ' rates kept apart' : ` ${state.mode} win rate`;
  if (state.mode === 'both') {
    $('#hero-rate').textContent = 'BO1 × BO3';
    $('#hero-detail').textContent = `BO1: ${percent(bo1.win_rate)} over ${bo1.games} games · BO3: ${percent(bo3.win_rate)} over ${bo3.games} games.`;
    appendMetric(metrics, summary.games ?? 0, 'games observed', 'BO1 and BO3 reported separately');
    appendMetric(metrics, percent(bo1.win_rate), 'BO1 rate', `${bo1.wins} wins · ${bo1.losses} losses`);
    appendMetric(metrics, percent(bo3.win_rate), 'BO3 rate', `${bo3.wins} wins · ${bo3.losses} losses`);
    appendMetric(metrics, summary.matches ?? 0, 'matches observed', 'no blended rate');
    return;
  }
  appendMetric(metrics, scoped.games, `${state.mode} games`);
  appendMetric(metrics, percent(scoped.win_rate), 'win rate', `${scoped.wins} wins · ${scoped.losses} losses`);
  appendMetric(metrics, scoped.completed, 'with a result', `${scoped.draws} draws`);
  appendMetric(metrics, scoped.matches, 'matches', `${state.mode} only`);
  if (state.mode === 'BO3') {
    const bo3Summary = summary.by_mode?.BO3;
    const stages = bo3Summary?.by_stage;
    appendMetric(metrics, percent(bo3Summary?.match_win_rate), 'match win rate', typeof bo3Summary?.match_wins === 'number' ? `${bo3Summary.match_wins} wins · ${bo3Summary.match_losses ?? 0} losses` : 'No match-level result received.');
    appendStageMetric(metrics, 'Game 1', stages?.game1);
    appendStageMetric(metrics, 'Games 2-3', stages?.post_sideboard);
  }
  $('#hero-rate').textContent = percent(scoped.win_rate);
  $('#hero-detail').textContent = scoped.games ? `${scoped.games} games observed · ${scoped.wins} wins · ${scoped.losses} losses.` : `No ${state.mode} game in this import.`;
}

function renderFoot(summary) {
  const foot = document.querySelector('.nav-foot small');
  if (!foot) return;
  const coach = summary.coach ?? {};
  foot.textContent = coach.ready ? `AI: ${coach.model}` : 'AI not connected';
}

function renderCaptureLine(capture) {
  const line = $('#capture-line');
  const button = $('#capture-toggle');
  if (!capture) { line.textContent = ''; return; }
  const running = Boolean(capture.running);
  button.textContent = running ? 'Stop following' : 'Follow matches';
  button.classList.toggle('primary', running);
  const detail = running
    ? `following · ${bytesText(capture.bytes_read)} read · ${capture.sessions ?? 0} session(s)`
    : 'not following — Arena wipes the log when the client restarts';
  line.textContent = ` · ${detail}${capture.error ? ` · ${capture.error}` : ''}`;
}

function gameCard(game) {
  const row = element('button', `game-row quality-${game.quality}`, '');
  row.type = 'button';
  const colours = deckColours(game.deck_id);
  const identity = element('div', 'game-identity');
  const title = element('strong', null, `${game.deck_label || 'Composition'} · game ${game.game_number ?? '—'}`);
  title.append(colourPips(colours));
  identity.append(title, element('span', null,
    `${game.format || 'Format unknown'} · ${game.mode || 'Mode unknown'}${game.opponent_name ? ` · vs ${game.opponent_name}` : ''}`));
  const meta = element('div', 'game-meta');
  meta.append(element('span', `result-badge ${game.result}`, resultLabel(game.result)));
  if (game.on_play !== null && game.on_play !== undefined) {
    meta.append(element('span', 'start-glyph', game.on_play ? '▶ on the play' : '◀ on the draw'));
  }
  if (game.quality !== 'complete') meta.append(element('span', 'quality-badge', qualityLabel(game.quality)));
  meta.append(element('span', null, `${game.turns ?? 0} turns`), element('span', null, `${game.decision_count ?? 0} decisions`));
  row.append(colourSpine(colours), identity, meta);
  row.addEventListener('click', () => openGame(game.id));
  return row;
}

function renderGames() {
  const target = $('#game-list');
  const games = filterGamesByMode(state.games, state.mode);
  $('#games-count').textContent = `${games.length} game${games.length === 1 ? '' : 's'}`;
  target.replaceChildren();
  if (!games.length) return empty(target, 'No game in this filter.', state.games.length ? 'Pick BO1, BO3 or Both.' : 'Import your local logs to start reviewing.');
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

function artEnabled() { return Boolean(state.summary?.art?.enabled); }

function applyArt(node, cardId) {
  if (!artEnabled() || !cardId) return;
  node.classList.add('has-art');
  node.style.backgroundImage = `linear-gradient(to right, rgba(12,22,18,.92) 42%, rgba(12,22,18,.45)), url("/art/${encodeURIComponent(cardId)}.jpg")`;
}

// Untapped lands the player controls, by the colour the card can produce. This is what the
// board shows; it is not a claim about total available mana (creatures, rocks and abilities
// also make mana), so the interface says "untapped lands" and never "you could cast this".
function untappedMana(frame, seat) {
  const battlefield = zoneOf(frame, 'Battlefield', 0);
  const lands = objectsFor(battlefield, seat).filter((object) => {
    const card = state.cards[object.card_id];
    return card?.is_land && !object.tapped;
  });
  const colours = {};
  lands.forEach((object) => {
    (state.cards[object.card_id]?.colors ?? []).forEach((colour) => {
      colours[colour] = (colours[colour] ?? 0) + 1;
    });
  });
  return { count: lands.length, colours };
}

function manaLine(frame, seat) {
  const mana = untappedMana(frame, seat);
  const parts = Object.entries(mana.colours).sort().map(([colour, count]) => `${colour}×${count}`);
  const line = element('p', 'mana-available');
  line.append(element('strong', null, `${mana.count}`), element('span', null,
    ` untapped land${mana.count === 1 ? '' : 's'}${parts.length ? ` · ${parts.join(' ')}` : ''}`));
  return line;
}

const ATTACK_STATES = new Set(['AttackState_Attacking', 'AttackState_Declared']);

function combatPanel(frame, selfSeat) {
  if (!['DeclareAttack', 'DeclareBlock', 'CombatDamage'].includes(frame.step)) return null;
  const battlefield = zoneOf(frame, 'Battlefield', 0);
  const attackers = (battlefield?.objects ?? []).filter((object) => ATTACK_STATES.has(object.attack_state));
  if (!attackers.length) return null;
  const attackerSeat = attackers[0].controller ?? attackers[0].owner;
  const defenderSeat = attackerSeat === selfSeat ? (selfSeat === 1 ? 2 : 1) : selfSeat;
  const defenderLife = frame.players?.find((player) => player.seat === defenderSeat)?.life;
  const blocked = attackers.filter((object) => object.block_state === 'BlockState_Blocked');
  const power = (object) => (Number.isFinite(Number(object.power)) ? Number(object.power) : 0);
  const total = attackers.reduce((sum, object) => sum + power(object), 0);
  const unblockedDamage = attackers.filter((object) => object.block_state !== 'BlockState_Blocked')
    .reduce((sum, object) => sum + power(object), 0);

  const panel = element('section', 'combat-panel');
  panel.append(element('h4', null, attackerSeat === selfSeat ? 'You are attacking' : 'You are being attacked'));
  const list = element('div', 'combat-list');
  attackers.forEach((object) => {
    const row = element('div', `combat-row${object.block_state === 'BlockState_Blocked' ? ' blocked' : ''}`);
    row.append(element('strong', null, cardName(object.card_id)),
      element('span', null, `${object.power ?? '?'}/${object.toughness ?? '?'}${object.block_state === 'BlockState_Blocked' ? ' · blocked' : ' · unblocked'}`));
    list.append(row);
  });
  panel.append(list);
  const summary = element('p', 'combat-summary');
  summary.append(element('span', null,
    `${attackers.length} attacker${attackers.length === 1 ? '' : 's'} · ${total} power declared · ${blocked.length} blocked`));
  if (typeof defenderLife === 'number') {
    const lethal = unblockedDamage >= defenderLife;
    summary.append(element('span', lethal ? 'combat-lethal' : null,
      ` · ${unblockedDamage} would land on ${defenderLife} life${lethal ? ' — lethal as blocked here' : ''}`));
  }
  panel.append(summary);
  panel.append(element('small', null,
    'Power as recorded in this frame. Combat tricks, first strike and damage prevention are not simulated.'));
  return panel;
}

// Stepping one game state at a time means 500+ stops; most carry no decision and no event.
const STEP_MODES = [['all', 'All frames'], ['decision', 'Decisions'], ['event', 'Events']];

function stepMatches(row, mode) {
  if (mode === 'decision') return Boolean(row.has_action);
  if (mode === 'event') return Boolean(row.event_kinds?.length);
  return true;
}

function nextStep(index, position, direction) {
  for (let at = position + direction; at >= 0 && at < index.length; at += direction) {
    if (stepMatches(index[at], state.stepMode)) return at;
  }
  return null;
}

function stepControls(index, position) {
  const box = element('div', 'step-modes');
  box.append(element('span', 'turn-strip-label', 'Step by'));
  STEP_MODES.forEach(([key, label]) => {
    const count = key === 'all' ? index.length : index.filter((row) => stepMatches(row, key)).length;
    const button = element('button', `turn-mark${state.stepMode === key ? ' active' : ''}`, `${label} (${count})`);
    button.type = 'button';
    button.addEventListener('click', () => { state.stepMode = key; renderReplay(); });
    box.append(button);
  });
  return box;
}

function zoneOf(frame, kind, owner) {
  return (frame.zones ?? []).find((zone) => zone.type === kind && zone.owner === owner) ?? null;
}

function objectsFor(zone, seat, key = 'controller') {
  return (zone?.objects ?? []).filter((object) => (object[key] ?? object.owner) === seat);
}

const COMBAT_CLASS = {
  AttackState_Attacking: 'attacking', AttackState_Declared: 'attacking',
  BlockState_Blocked: 'blocked',
};

function cardTile(object, entered) {
  const card = state.cards[object.card_id];
  const colors = card?.colors?.length ? card.colors.join('').toLowerCase() : 'unknown';
  const classes = ['card-tile', `mana-${colors}`];
  if (object.tapped) classes.push('tapped');
  if (entered) classes.push('entered');
  const combat = COMBAT_CLASS[object.attack_state] || COMBAT_CLASS[object.block_state];
  if (combat) classes.push(combat);
  if (object.object_type === 'GameObjectType_Token') classes.push('token');
  const tile = element('button', classes.join(' '), ''); tile.type = 'button';
  applyArt(tile, object.card_id);
  tile.title = 'Inspect card';
  const top = element('span', 'tile-top');
  top.append(element('span', 'mana-line', card?.mana_cost || '—'));
  const marks = [
    object.tapped ? '↻' : null,
    COMBAT_CLASS[object.attack_state] ? '⚔' : null,
    COMBAT_CLASS[object.block_state] ? '🛡' : null,
    object.summoning_sickness ? '✦' : null,
  ].filter(Boolean).join(' ');
  if (marks) top.append(element('span', 'tile-marks', marks));
  tile.append(top, element('strong', null, cardName(object.card_id)));
  const hasPower = object.power !== null && object.power !== undefined && String(object.power).trim() !== '';
  const hasToughness = object.toughness !== null && object.toughness !== undefined && String(object.toughness).trim() !== '';
  const bits = [
    hasPower || hasToughness ? `${hasPower ? object.power : '?'}/${hasToughness ? object.toughness : '?'}` : null,
    object.damage ? `${object.damage} dmg` : null,
    object.object_type === 'GameObjectType_Token' ? 'token' : null,
  ].filter(Boolean);
  if (bits.length) tile.append(element('small', null, bits.join(' · ')));
  tile.addEventListener('click', () => inspectCard(object.card_id));
  return tile;
}

function cardStrip(objects, entered, emptyText) {
  if (!objects.length) return element('p', 'zone-empty', emptyText);
  const strip = element('div', 'card-strip');
  objects.forEach((object) => strip.append(cardTile(object, entered.has(object.instance_id))));
  return strip;
}

// A zone the player cannot read is a number, not an empty box. Clicking one that holds
// visible cards opens it; a purely hidden zone has nothing to open.
function zoneChip(label, zone, entered, expandable = true) {
  const total = zone?.total_count ?? ((zone?.objects?.length ?? 0) + (zone?.hidden_count ?? 0));
  const visible = zone?.objects?.length ?? 0;
  const chip = element(visible && expandable ? 'details' : 'div', 'zone-chip');
  const head = element(visible && expandable ? 'summary' : 'span', 'zone-chip-head');
  head.append(element('span', 'zone-chip-name', label), element('strong', null, String(total)));
  chip.append(head);
  if (visible && expandable) chip.append(cardStrip(zone.objects, entered, ''));
  if (!total) chip.classList.add('empty');
  return chip;
}

function lifeDelta(frame, previous, seat) {
  const now = frame.players?.find((player) => player.seat === seat)?.life;
  const before = previous?.players?.find((player) => player.seat === seat)?.life;
  if (typeof now !== 'number' || typeof before !== 'number' || now === before) return null;
  return now - before;
}

function sideBand(frame, seat, isSelf, entered, previous) {
  const band = element('section', `board-band ${isSelf ? 'self' : 'opponent'}`);
  const head = element('header', 'band-head');
  const player = frame.players?.find((item) => item.seat === seat);
  const title = element('div', 'band-title');
  title.append(element('strong', null, isSelf ? 'You' : 'Opponent'));
  if (frame.active_player === seat) title.append(element('span', 'band-flag', 'active turn'));
  if (frame.priority_player === seat) title.append(element('span', 'band-flag', 'priority'));
  const life = element('div', 'band-life', `${player?.life ?? '—'}`);
  const delta = lifeDelta(frame, previous, seat);
  if (delta !== null) life.append(element('span', `life-delta ${delta > 0 ? 'up' : 'down'}`, `${delta > 0 ? '+' : ''}${delta}`));
  head.append(title, life);
  band.append(head);

  const battlefield = zoneOf(frame, 'Battlefield', 0);
  band.append(manaLine(frame, seat));
  band.append(cardStrip(objectsFor(battlefield, seat), entered, 'Nothing on the battlefield.'));

  const hand = zoneOf(frame, 'Hand', seat);
  if (isSelf) {
    const label = element('p', 'band-label', `Hand · ${hand?.total_count ?? 0}`);
    band.append(label, cardStrip(hand?.objects ?? [], entered, 'Empty hand.'));
  }

  const exile = zoneOf(frame, 'Exile', 0);
  const chips = element('div', 'zone-chips');
  if (!isSelf) chips.append(zoneChip('Hand', hand, entered, false));
  chips.append(zoneChip('Library', zoneOf(frame, 'Library', seat), entered, false));
  chips.append(zoneChip('Graveyard', zoneOf(frame, 'Graveyard', seat), entered));
  chips.append(zoneChip('Exile', { objects: objectsFor(exile, seat, 'owner'), hidden_count: 0 }, entered));
  chips.append(zoneChip('Revealed', zoneOf(frame, 'Revealed', seat), entered));
  chips.append(zoneChip('Sideboard', zoneOf(frame, 'Sideboard', seat), entered));
  band.append(chips);
  return band;
}

function sharedBand(frame, entered) {
  const stack = zoneOf(frame, 'Stack', 0);
  const command = zoneOf(frame, 'Command', 0);
  const band = element('section', 'board-band shared');
  const objects = [...(stack?.objects ?? []), ...(command?.objects ?? [])];
  band.append(element('p', 'band-label', `Stack · ${stack?.total_count ?? 0}`));
  band.append(cardStrip(objects, entered, 'Stack empty.'));
  return band;
}

function enteredSince(frame, previous) {
  if (!previous) return new Set();
  const before = new Set();
  (previous.zones ?? []).forEach((zone) => (zone.objects ?? []).forEach((object) => before.add(object.instance_id)));
  const now = new Set();
  (frame.zones ?? []).forEach((zone) => (zone.objects ?? []).forEach((object) => {
    if (!before.has(object.instance_id)) now.add(object.instance_id);
  }));
  return now;
}

function turnStrip(index, position) {
  // 538 raw frames mean nothing to read; the turn each one belongs to does.
  const first = new Map();
  index.forEach((row, order) => { if (!first.has(row.turn)) first.set(row.turn, order); });
  const strip = element('div', 'turn-strip');
  strip.append(element('span', 'turn-strip-label', 'Turn'));
  const currentTurn = index[position]?.turn;
  [...first.entries()].forEach(([turn, start]) => {
    const button = element('button', `turn-mark${turn === currentTurn ? ' active' : ''}`, String(turn));
    button.type = 'button';
    button.title = `Jump to the start of turn ${turn}`;
    button.addEventListener('click', () => changeFrame(start));
    strip.append(button);
  });
  return strip;
}

async function renderReplay() {
  const target = $('#replay-main');
  const detail = state.detail;
  if (!detail) return;
  const index = detail.frame_index ?? [];
  const position = Math.max(0, Math.min(state.framePosition, Math.max(index.length - 1, 0)));
  state.framePosition = position;
  const frame = index.length ? await frameAt(position) : null;
  const previous = position > 0 ? await frameAt(position - 1) : null;
  target.replaceChildren();
  if (!frame) { empty(target, 'No reconstructed frames.', 'The log recorded no position safe enough to replay.'); renderSidebar(null); return; }
  await loadCards(frameCardIds(frame));
  const controls = element('div', 'replay-controls');
  const previousStep = nextStep(index, position, -1);
  const followingStep = nextStep(index, position, 1);
  const back = element('button', 'icon-button', '←'); back.type = 'button'; back.disabled = previousStep === null; back.addEventListener('click', () => changeFrame(previousStep));
  const next = element('button', 'icon-button', '→'); next.type = 'button'; next.disabled = followingStep === null; next.addEventListener('click', () => changeFrame(followingStep));
  const slider = document.createElement('input'); slider.type = 'range'; slider.min = '0'; slider.max = String(Math.max(index.length - 1, 0)); slider.value = String(position); slider.setAttribute('aria-label', 'Replay frame'); slider.addEventListener('change', () => changeFrame(Number(slider.value)));
  controls.append(back, slider, next, element('span', 'frame-count', `Frame ${position + 1}/${index.length}`));
  target.append(controls, turnStrip(index, position), stepControls(index, position));
  const heading = element('div', 'position-heading');
  heading.append(element('strong', null, `Turn ${frame.turn ?? '—'}`), element('span', null, listText([frame.phase, frame.step])), element('span', null, `Source line ${frame.source_line ?? 'unknown'}`));
  target.append(heading);
  const warning = frame.warnings?.[0] ?? '';
  if (frame.quality !== 'complete' || warning) target.append(element('div', `quality-notice ${frame.quality}`, warning || qualityLabel(frame.quality)));
  if (frame.events?.length) {
    const strip = element('div', 'event-strip');
    strip.append(element('h4', null, 'What happened here'));
    frame.events.forEach((event) => strip.append(element('span', `event-chip event-${event.kind}`, describeEvent(event))));
    target.append(strip);
  }
  const entered = enteredSince(frame, previous);
  const seats = (frame.players ?? []).map((player) => player.seat);
  const selfSeat = detail.self_seat;
  const opponentSeat = seats.find((seat) => seat !== selfSeat) ?? (selfSeat === 1 ? 2 : 1);
  const board = element('div', 'board');
  board.append(sideBand(frame, opponentSeat, false, entered, previous));
  const combat = combatPanel(frame, selfSeat);
  board.append(combat ?? sharedBand(frame, entered));
  if (combat) board.append(sharedBand(frame, entered));
  board.append(sideBand(frame, selfSeat, true, entered, previous));
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
  const who = event.seat ? (event.seat === state.detail?.self_seat ? 'You' : 'Opponent') : '';
  const name = event.card_id ? cardName(event.card_id) : '';
  if (event.kind === 'life') return `${who}: life ${event.amount > 0 ? '+' : ''}${event.amount}`;
  if (event.kind === 'damage') return `${name} dealt ${event.amount} damage`;
  if (event.kind === 'turn') return `${who || '—'}'s turn`;
  if (event.kind === 'draw' && !event.card_id) return `${who} drew (undisclosed)`;
  return `${event.label ?? event.kind}${name ? `: ${name}` : ''}`;
}

// ---------------------------------------------------------------- sidebar

const SIDEBAR_TABS = [
  ['decision', 'Decision'], ['moments', 'Moments'], ['library', 'Library'],
  ['opponent', 'Opponent'], ['timeline', 'Timeline'], ['reading', 'AI reading'],
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
  if (!frame) { body.append(element('p', 'subtle', 'Pick a frame that carries decision data.')); return; }
  if (state.sidebarTab === 'decision') renderDecisionTab(body, frame);
  if (state.sidebarTab === 'moments') renderMomentsTab(body);
  if (state.sidebarTab === 'library') renderLibraryTab(body, frame);
  if (state.sidebarTab === 'opponent') renderOpponentTab(body);
  if (state.sidebarTab === 'timeline') renderTimelineTab(body);
  if (state.sidebarTab === 'reading') renderCoachTab(body, frame);
}

// The chess-review shape people ask for cannot be built: grading a play needs an engine
// that evaluates the position, and Magic is Turing-complete, so none exists. What this tab
// does instead is take you back to the turns where the log recorded something a player
// rarely means to do. It never says whether it was wrong.
const MOMENT_LABEL = {
  land_drop: 'Land held',
  unused_mana: 'Mana left over',
  stranded: 'Left in hand',
};

function renderMomentsTab(target) {
  target.append(element('p', 'eyebrow', 'TURNS TO LOOK AT AGAIN'));
  if (state.momentsFor !== state.detail?.id) {
    target.append(element('p', 'subtle', 'Reading the game…'));
    loadMoments();
    return;
  }
  const data = state.moments;
  if (!data?.eligible) {
    target.append(element('p', 'gap', data?.reason ?? 'This game cannot be read.'));
    return;
  }
  if (!data.found?.length) {
    target.append(element('p', null,
      `Nothing to flag across your ${data.turns} turn(s): no land sat in hand, no turn ended with mana idle, and your hand was empty at the end.`));
    target.append(element('small', null, data.note));
    return;
  }
  target.append(element('p', null,
    `${data.moments} moment(s) across your ${data.turns} turn(s).`));
  data.found.forEach((moment) => {
    const box = element('article', `moment moment-${moment.kind}`);
    const open = element('button', 'text-button', `Turn ${moment.turn ?? '—'} · ${MOMENT_LABEL[moment.kind] ?? moment.kind}`);
    open.type = 'button';
    open.addEventListener('click', () => {
      if (moment.frame !== null && moment.frame !== undefined) changeFrame(moment.frame);
    });
    box.append(open);
    box.append(element('p', null, moment.detail));
    box.append(element('small', null, moment.blind_spot));
    // The rules found the position and checked the facts; whether it was actually a mistake
    // is a judgement, and judgement is what the reading is for. It goes to the same place
    // as any other reading — his own key, his own machine, only when he asks.
    if (moment.frame !== null && moment.frame !== undefined) {
      const ask = element('button', 'text-button moment-ask', 'Take this position to the reading');
      ask.type = 'button';
      ask.addEventListener('click', () => {
        state.sidebarTab = 'reading';
        state.coachMode = 'explain';
        changeFrame(moment.frame);
      });
      box.append(ask);
    }
    target.append(box);
  });
  target.append(element('small', 'moment-note', data.note));
}

async function loadMoments() {
  const id = state.detail?.id;
  if (!id) return;
  try {
    state.moments = await request(`/api/games/${encodeURIComponent(id)}/moments`);
  } catch (error) {
    state.moments = { eligible: false, reason: error.message };
  }
  state.momentsFor = id;
  if (state.sidebarTab === 'moments') renderReplay();
}

function actionText(action, sourceLine) {
  const names = action.card_ids?.map((id) => cardName(id)).join(', ');
  const label = action.label || action.type || 'Unlabelled action';
  const source = action.source_line ?? sourceLine;
  return [label, names, source !== null && source !== undefined ? `line ${source}` : null].filter(Boolean).join(' · ');
}

function actionList(title, actions, sourceLine) {
  const area = element('div', 'action-list');
  area.append(element('h4', null, title));
  if (!actions?.length) area.append(element('p', 'subtle', 'No action recorded.'));
  else actions.forEach((action) => area.append(element('p', null, actionText(action, sourceLine))));
  return area;
}

function renderDecisionTab(target, frame) {
  target.append(element('p', 'eyebrow', 'SELECTED DECISION'), element('h3', null, `Frame ${frame.index}`));
  target.append(actionList('Taken', frame.action ? [frame.action] : [], frame.source_line),
    actionList('Available', frame.available_actions ?? frame.actions ?? [], frame.source_line));
  const index = state.detail.frame_index ?? [];
  const previousDecision = adjacentDecisionPosition(index, state.framePosition, -1);
  const nextDecision = adjacentDecisionPosition(index, state.framePosition, 1);
  const navigation = element('div', 'decision-navigation');
  const previous = element('button', 'text-button', '← Previous decision'); previous.type = 'button'; previous.disabled = previousDecision === null; previous.addEventListener('click', () => changeFrame(previousDecision));
  const next = element('button', 'text-button', 'Next decision →'); next.type = 'button'; next.disabled = nextDecision === null; next.addEventListener('click', () => changeFrame(nextDecision));
  navigation.append(previous, next); target.append(navigation);
  const safe = frame.quality === 'complete';
  target.append(element('p', safe ? 'eligibility yes' : 'eligibility no', safe ? 'Context eligible for external review.' : 'Not eligible: the reconstruction quality blocks tactical context.'));
  const prompts = element('div', 'review-prompts');
  prompts.append(element('h4', null, 'Questions to ask yourself'), element('p', null, 'What did you already know here?'), element('p', null, 'Which recorded alternative deserved a look?'), element('p', null, 'What hypothesis do you want to test?'));
  target.append(prompts);
  const noteForm = document.createElement('form'); noteForm.className = 'note-form';
  const text = document.createElement('textarea'); text.name = 'body'; text.maxLength = 2000; text.required = true; text.placeholder = 'Your note on this position…';
  const drill = document.createElement('input'); drill.type = 'checkbox'; drill.id = 'note-drill';
  const drillLabel = element('label', 'drill-toggle', '');
  drillLabel.append(drill, document.createTextNode(' Come back to this one as a drill'));
  const save = element('button', 'button secondary', 'Save note'); save.type = 'submit';
  noteForm.append(element('label', null, 'Manual note'), text, drillLabel, save);
  noteForm.addEventListener('submit', (event) => saveNote(event, frame.index, text, drill.checked));
  target.append(noteForm);
  const context = element('button', 'button primary', 'Copy context'); context.type = 'button'; context.disabled = !safe;
  context.addEventListener('click', () => copyContext(frame.index)); target.append(context);
  const inspect = element('div', 'card-inspector'); inspect.id = 'card-inspector';
  inspect.append(element('h4', null, 'Selected card'), element('p', 'subtle', 'Click a known card to read its text from the local catalogue.'));
  target.append(inspect);
}

async function renderLibraryTab(target, frame) {
  target.append(element('p', 'eyebrow', 'YOUR LIBRARY AT THIS INSTANT'));
  target.append(element('p', 'subtle', 'Registered list minus every copy already seen. Exact for your deck only.'));
  const holder = element('div', 'library-body', 'Loading…');
  target.append(holder);
  try {
    const payload = await request(`/api/games/${encodeURIComponent(state.detail.id)}/library?index=${frame.index}`);
    holder.replaceChildren();
    if (!payload.eligible) { holder.append(element('p', 'gap', payload.reason || 'Unavailable.')); return; }
    holder.append(element('h3', null, `${payload.size} cards left`));
    holder.append(element('p', 'subtle', `${payload.lands} lands · ${percent(payload.land_ratio)} of the library${payload.matches_report ? '' : ' · disagrees with the log count'}`));
    const list = element('div', 'library-list');
    payload.entries.slice(0, 24).forEach((entry) => {
      const row = element('div', 'library-row');
      row.append(element('strong', null, `${entry.quantity}× ${entry.name}`),
        element('span', null, `next draw ${percent(entry.next_draw)} · within 3 draws ${percent(entry.within_three)}`));
      list.append(row);
    });
    holder.append(list);
  } catch (error) { holder.replaceChildren(element('p', 'gap', error.message)); }
}

async function renderOpponentTab(target) {
  target.append(element('p', 'eyebrow', 'WHAT THE OPPONENT SHOWED'));
  const holder = element('div', 'opponent-body', 'Loading…');
  target.append(holder);
  try {
    const payload = await request(`/api/games/${encodeURIComponent(state.detail.id)}/opponent`);
    holder.replaceChildren();
    holder.append(element('p', 'subtle', payload.note));
    const colours = Object.entries(payload.colours ?? {});
    holder.append(element('p', null, colours.length ? `Colours seen: ${colours.map(([colour, count]) => `${colour} (${count})`).join(' · ')}` : 'No colour identified yet.'));
    if (payload.prior_games?.length) holder.append(element('p', 'gap', `${payload.prior_games.length} card(s) seen in earlier games of this match.`));
    const list = element('div', 'library-list');
    (payload.cards ?? []).forEach((card) => {
      const row = element('div', 'library-row');
      row.append(element('strong', null, card.name), element('span', null, `${card.mana_cost || '—'} · ${card.type_line || 'type unresolved'}`));
      list.append(row);
    });
    holder.append((payload.cards ?? []).length ? list : element('p', 'subtle', 'Nothing revealed so far.'));
  } catch (error) { holder.replaceChildren(element('p', 'gap', error.message)); }
}

async function renderTimelineTab(target) {
  target.append(element('p', 'eyebrow', 'TIMELINE'));
  const holder = element('div', 'timeline-body', 'Loading…');
  target.append(holder);
  try {
    const payload = await request(`/api/games/${encodeURIComponent(state.detail.id)}/timeline`);
    holder.replaceChildren();
    if (!payload.events?.length) { holder.append(element('p', 'subtle', 'This game was imported before annotations were read. Re-import the log to build the timeline.')); return; }
    let turn = null;
    payload.events.forEach((event) => {
      if (event.turn !== turn) { turn = event.turn; holder.append(element('h4', null, `Turn ${turn}`)); }
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
  if (!card || card.resolved === false) { target.append(element('p', 'gap', `Id #${id} was not resolved by the local catalogue.`)); return; }
  if (artEnabled()) {
    const art = document.createElement('img');
    art.className = 'card-art'; art.loading = 'lazy'; art.alt = '';
    art.src = `/art/${encodeURIComponent(id)}.jpg`;
    art.addEventListener('error', () => art.remove());
    target.append(art);
  }
  target.append(element('p', 'mana-line', `${card.mana_cost || '—'} · MV ${card.mana_value ?? '—'}`), element('p', 'type-line', card.type_line || 'Type unknown'), element('p', 'rules-text', card.text || 'No rules text in the local catalogue.'));
  const hasPower = card.power !== null && card.power !== undefined && String(card.power).trim() !== '';
  const hasToughness = card.toughness !== null && card.toughness !== undefined && String(card.toughness).trim() !== '';
  if (hasPower || hasToughness) target.append(element('p', null, `${hasPower ? card.power : '?'}/${hasToughness ? card.toughness : '?'}`));
  showRulings(target, id);
}

async function showRulings(target, id) {
  if (!state.summary?.rulings?.enabled) return;
  try {
    const first = (await request(`/api/rulings?ids=${encodeURIComponent(id)}`)).rulings?.[id];
    if (first === undefined) await postJson('/api/rulings/fetch', { card_ids: [id] });
    const answer = (await request(`/api/rulings?ids=${encodeURIComponent(id)}`)).rulings?.[id] ?? [];
    if (!answer.length) return;
    const box = element('div', 'rulings');
    box.append(element('h4', null, 'Rulings'));
    answer.forEach((item) => box.append(element('p', null, item.text)));
    box.append(element('small', null, state.summary?.rulings_credit || ''));
    target.append(box);
  } catch { /* Rulings are an extra; a failure must not disturb the review. */ }
}

function changeFrame(position) { state.framePosition = position; renderReplay(); }

async function openGame(id) {
  setMessage('Loading replay…');
  try {
    state.detail = await request(`/api/games/${encodeURIComponent(id)}`);
    state.frameCache = new Map();
    state.framePosition = firstDecisionPosition(state.detail.frame_index ?? []);
    Object.assign(state.cards, state.detail.cards ?? {});
    warmArt(Object.keys(state.detail.cards ?? {}).map(Number));
    $('#replay-section').hidden = false;
    await renderReplay();
    $('#replay-section').scrollIntoView({ behavior: reducedMotion() ? 'auto' : 'smooth', block: 'start' });
    setMessage('');
    renderTraining();
  } catch (error) { setMessage(`Could not open the replay: ${error.message}`, 'error'); }
}

async function saveNote(event, frameIndex, input, asDrill = false) {
  event.preventDefault(); const body = input.value.trim(); if (!body) return;
  try {
    await postJson('/api/notes', { game_id: state.detail.id, frame_index: frameIndex, body, tags: asDrill ? ['drill'] : [] });
    input.value = '';
    state.detail = await request(`/api/games/${encodeURIComponent(state.detail.id)}`);
    await renderReplay(); await loadNotes(); setMessage('Note saved.');
  } catch (error) { setMessage(`The note was not saved: ${error.message}`, 'error'); }
}

async function copyContext(index) {
  try {
    const response = await request(contextEndpoint(state.detail.id, index));
    if (!response.eligible) throw new Error('The server reports this frame is not eligible.');
    const text = response.text || JSON.stringify(response.context ?? {}, null, 2);
    await navigator.clipboard.writeText(text);
    setMessage('Sanitised context copied.');
  } catch (error) { setMessage(`The context was not copied: ${error.message}`, 'error'); }
}

async function warmArt(cardIds) {
  if (!artEnabled() || !cardIds?.length) return;
  try { await postJson('/api/art/fetch', { card_ids: cardIds.slice(0, 200) }); }
  catch { /* Art is decoration: a failure must not disturb the review. */ }
}

// ---------------------------------------------------------------- leitura por IA

const COACH_MODES = [
  ['explain', 'Explain the numbers'],
  ['ask', 'Ask me questions'],
  ['alternatives', 'Comment on alternatives'],
];

function renderCoachTab(target, frame) {
  const coach = state.summary?.coach;
  target.append(element('p', 'eyebrow', 'AI READING'));
  if (frame.quality !== 'complete') {
    target.append(element('p', 'gap', 'This stretch has a gap: the context is not eligible for a reading.'));
    return;
  }
  target.append(element('p', 'subtle', 'The app has already computed the numbers. A model only reads them — it is told not to recompute, not to invent card text, and never to call a play correct.'));

  const picker = element('div', 'coach-modes');
  COACH_MODES.forEach(([key, label]) => {
    const button = element('button', `sidebar-tab${state.coachMode === key ? ' active' : ''}`, label);
    button.type = 'button';
    button.addEventListener('click', () => { state.coachMode = key; renderSidebar(frame); });
    picker.append(button);
  });
  target.append(picker);

  // The free path first: the whole question as text, for any chat the player already has.
  const copy = element('button', 'button primary', 'Copy the question');
  copy.type = 'button';
  copy.addEventListener('click', () => copyPrompt({ kind: 'position', game_id: state.detail.id, index: frame.index }));
  target.append(copy);
  target.append(element('small', null, 'Paste it into Claude, ChatGPT or any assistant you already use. Free, no account here, nothing sent from this app.'));

  const shortcut = element('div', 'coach-shortcut');
  if (coach?.ready) {
    const run = element('button', 'button secondary', state.coachBusy ? 'Reading…' : `Or ask ${coach.model} from here`);
    run.type = 'button'; run.disabled = state.coachBusy;
    run.addEventListener('click', () => askCoach({ kind: 'position', game_id: state.detail.id, index: frame.index }, frame));
    shortcut.append(run);
    shortcut.append(element('small', null, 'Your own API key, billed to you, a fraction of a cent per reading.'));
  } else {
    shortcut.append(element('small', null, 'Optional: add an Anthropic key in Settings to skip the copy-and-paste. Everything else in the app works without it.'));
  }
  target.append(shortcut);
  if (state.coachAnswer) target.append(coachAnswerBlock(state.coachAnswer));
}

async function copyPrompt(payload) {
  try {
    const answer = await postJson('/api/coach/prompt', { mode: state.coachMode, ...payload });
    await navigator.clipboard.writeText(answer.text);
    setMessage(`Question copied (${Math.round(answer.characters / 1000)} k characters). Paste it into any assistant.`);
  } catch (error) { setMessage(`The question was not copied: ${error.message}`, 'error'); }
}

function coachAnswerBlock(answer) {
  const box = element('article', 'coach-answer');
  if (answer.error) { box.append(element('p', 'gap', answer.error)); return box; }
  answer.text.split(/\n{2,}/).forEach((paragraph) => box.append(element('p', null, paragraph.trim())));
  box.append(element('small', null, `${answer.disclaimer} · ${answer.usage.input}+${answer.usage.output} tokens · US$ ${answer.cost_usd.toFixed(4)}`));
  return box;
}

async function askCoach(payload, frame) {
  state.coachBusy = true; state.coachAnswer = null;
  if (frame) renderSidebar(frame); else renderSettings();
  try {
    state.coachAnswer = await postJson('/api/coach/review', { mode: state.coachMode, ...payload });
  } catch (error) { state.coachAnswer = { error: error.message }; }
  state.coachBusy = false;
  if (frame) renderSidebar(frame); else showDeck(payload.deck_id);
}

// ---------------------------------------------------------------- decks

function renderDecks() {
  const list = $('#deck-list'); const detail = $('#deck-detail');
  list.replaceChildren(); detail.replaceChildren();
  const decks = state.summary?.decks ?? [];
  const select = $('#experiment-deck'); select.replaceChildren();
  if (!decks.length) {
    empty(list, 'No deck observed.', 'Import a match that records the deck composition.');
    empty(detail, 'No composition to compare.', 'The catalogue is only consulted for recorded cards.');
    return;
  }
  decks.forEach((deckEntry, index) => {
    const scoped = deckModeMetrics(deckEntry);
    const option = element('option', null, deckEntry.label || deckEntry.id); option.value = deckEntry.id; select.append(option);
    const button = element('button', `deck-version ${index === 0 ? 'active' : ''}`, '');
    button.type = 'button';
    const heading = element('strong', null, deckEntry.label || `Version ${deckEntry.id}`);
    heading.append(colourPips(deckEntry.colours ?? []));
    button.append(heading,
      element('span', null, `${deckEntry.main_count ?? 0} main · ${scoped.games} ${state.mode === 'both' ? '' : state.mode} games`),
      element('small', null, percent(scoped.win_rate)));
    button.addEventListener('click', () => { list.querySelectorAll('.deck-version').forEach((item) => item.classList.remove('active')); button.classList.add('active'); showDeck(deckEntry.id); });
    list.append(button);
  });
  showDeck(decks[0].id);
}

function deckModeMetrics(entry) {
  if (state.mode === 'both') return { games: entry.game_count ?? 0, wins: entry.wins ?? 0, losses: entry.losses ?? 0, win_rate: entry.win_rate ?? null };
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
  target.replaceChildren(element('p', 'subtle', 'Analysing the composition…'));
  let report;
  try { report = await request(`/api/decks/${encodeURIComponent(deckId)}`); }
  catch (error) { target.replaceChildren(element('p', 'gap', error.message)); return; }
  state.deckReport = report;
  Object.assign(state.deckCards, report.cards ?? {});
  target.replaceChildren();
  target.append(element('p', 'eyebrow', 'OBSERVED COMPOSITION'), element('h2', null, report.label || `Version ${report.deck_id}`));
  target.append(element('p', 'subtle', `${report.wins} wins · ${report.losses} losses over ${report.games} games. ${intervalText(report.interval)}.`));
  target.append(bindDeckControl(report));
  target.append(curveBlock(report));
  target.append(manaBaseBlock(report));
  target.append(wildcardBlock(report));
  target.append(cardStatsBlock(report.card_stats));
  target.append(deckCoachBlock(report));
  target.append(exportBlock(report));
  target.append(deckSection('Principal', report.deck?.main ?? []), deckSection('Sideboard', report.deck?.sideboard ?? []));
  warmArt(Object.keys(report.cards ?? {}).map(Number));
}

function bindDeckControl(report) {
  const box = element('div', 'bind-deck');
  const named = state.summary?.named_decks ?? [];
  box.append(element('h4', null, 'Deck name'));
  if (!named.length) { box.append(element('p', 'subtle', 'No named deck has been read from the log yet.')); return box; }
  const select = document.createElement('select');
  select.append(element('option', null, 'Pick a deck saved in Arena…'));
  named.forEach((deck) => { const option = element('option', null, `${deck.name} (${deck.format || 'format unknown'})`); option.value = deck.uid; select.append(option); });
  const apply = element('button', 'button secondary', 'Link'); apply.type = 'button';
  apply.addEventListener('click', async () => {
    if (!select.value) return;
    try { await postJson('/api/decks/bind', { deck_id: report.deck_id, deck_uid: select.value }); setMessage('Composition linked to the saved deck.'); await refresh(); renderDecks(); }
    catch (error) { setMessage(`Linking failed: ${error.message}`, 'error'); }
  });
  box.append(select, apply);
  box.append(element('small', null, 'The log never links the list played to the saved deck; this link is your call and it is remembered.'));
  return box;
}

function curveBlock(report) {
  const curve = element('section', 'deck-section');
  curve.append(element('h3', null, 'Mana curve · main deck, lands excluded'));
  if (!report.curve) {
    curve.append(element('p', 'gap', `No curve: ${report.unresolved.length} main-deck card(s) were not resolved by the local catalogue.`));
  } else {
    const entries = Object.entries(report.curve).map(([mana, quantity]) => [Number(mana), quantity]);
    const peak = Math.max(...entries.map(([, quantity]) => quantity), 1);
    const chart = element('div', 'curve-chart');
    entries.sort((a, b) => a[0] - b[0]).forEach(([mana, quantity]) => {
      const column = element('div', 'curve-col');
      const area = element('div', 'curve-area');
      const fill = element('div', 'curve-fill');
      fill.style.height = `${Math.round((quantity / peak) * 100)}%`;
      fill.title = `${quantity} card(s) at mana value ${mana}`;
      area.append(fill);
      column.append(element('strong', null, String(quantity)), area, element('span', null, String(mana)));
      chart.append(column);
    });
    curve.append(chart);
  }
  const lands = report.lands ?? 0;
  const wanted = report.lands_recommended;
  curve.append(element('small', null, `${lands} lands · average mana value ${report.average_mana_value ?? '—'}`
    + (wanted ? ` · the published regression suggests ${wanted}${Math.abs(lands - wanted) >= 1 ? ` (${lands < wanted ? 'you run fewer' : 'you run more'})` : ''}` : '')));
  return curve;
}

function manaBaseBlock(report) {
  const box = element('section', 'deck-section');
  box.append(element('h3', null, 'Mana base'));
  const requirements = report.colour_requirements ?? [];
  if (!requirements.length) {
    box.append(element('p', 'subtle', 'No colour is mandatory in this list — only generic, hybrid or Phyrexian costs.'));
    if (report.flexible_costs?.length) box.append(element('small', null, `Flexible costs: ${report.flexible_costs.join(', ')}.`));
    return box;
  }
  const widest = Math.max(...requirements.map((item) => Math.max(item.have, item.needed)), 1);
  requirements.forEach((item) => {
    const row = element('div', `source-row ${item.shortfall ? 'short' : 'met'}`);
    const head = element('div', 'source-head');
    const name = element('strong', null, '');
    name.append(colourPips([item.colour]), document.createTextNode(` ×${item.pips} by turn ${item.turn}`));
    head.append(name, element('span', null, item.shortfall
      ? `${item.have} of ${item.needed} — ${item.shortfall} short · ${item.driver}`
      : `${item.have} of ${item.needed} — met · ${item.driver}`));
    const track = element('div', 'source-track');
    const have = element('div', 'source-have');
    have.style.width = `${Math.round((item.have / widest) * 100)}%`;
    const need = element('div', 'source-need');
    need.style.left = `${Math.round((item.needed / widest) * 100)}%`;
    need.title = `${item.needed} sources wanted`;
    track.append(have, need);
    row.append(head, track);
    box.append(row);
  });
  if (report.flexible_costs?.length) box.append(element('small', null, `Left out because the colour is not mandatory: ${report.flexible_costs.join(', ')} (hybrid or Phyrexian mana).`));
  box.append(element('small', null, report.karsten_citation));
  box.append(element('small', null, report.bo1_caveat));
  return box;
}

function wildcardBlock(report) {
  const box = element('section', 'deck-section');
  box.append(element('h3', null, 'Wildcard cost'));
  const cost = report.wildcards?.cost ?? {};
  const owned = state.summary?.inventory ?? {};
  const map = { common: ['commons', 'WildCardCommons'], uncommon: ['uncommons', 'WildCardUnCommons'], rare: ['rares', 'WildCardRares'], mythic: ['mythics', 'WildCardMythics'] };
  // Two bounds, not a number: Arena stopped writing the collection, so the honest answer
  // is what the list costs from nothing and what it costs after the copies the app has
  // actually watched him register. The second is tight for a list built out of cards he
  // already plays, which is the case that matters when tuning a deck.
  const net = report.wildcards?.net ?? cost;
  const grid = element('div', 'wildcards');
  Object.entries(map).forEach(([key, [label, inventoryKey]]) => {
    const need = cost[key] ?? 0;
    const owe = net[key] ?? need;
    const held = owned[inventoryKey];
    const card = element('article', `wildcard${typeof held === 'number' && held < owe ? ' short' : ''}`);
    card.append(element('strong', null, owe === need ? String(need) : `${owe}–${need}`),
                element('span', null, label));
    card.append(element('small', null, typeof held === 'number' ? `you hold ${held}` : 'stock not read'));
    grid.append(card);
  });
  box.append(grid);
  const proven = report.wildcards?.proven_owned ?? 0;
  if (proven) {
    box.append(element('p', null,
      `The log has watched you register ${proven} of these copies already, so the lower figure is what you would still spend.`));
  }
  box.append(element('small', null, report.wildcards?.note
    ?? 'Cost of the whole list. Arena stopped publishing the collection in the log.'));
  return box;
}

function cardStatsBlock(stats) {
  const box = element('section', 'deck-section');
  box.append(element('h3', null, 'Cards in hand vs result'));
  if (!stats?.rows?.length) { box.append(element('p', 'subtle', 'No finished game with a recorded hand for this composition.')); return box; }
  stats.rows.slice(0, 20).forEach((row) => {
    const line = element('div', 'deck-card');
    line.append(element('strong', null, row.name), element('span', null, intervalText(row.interval)));
    box.append(line);
  });
  box.append(element('small', null, `${stats.note} Telling 55% from 50% would take about ${stats.games_to_detect_five_points} games per arm.`));
  return box;
}

function exportBlock(report) {
  const box = element('section', 'deck-section');
  box.append(element('h3', null, 'Take it back to Arena'));
  const copy = element('button', 'button secondary', 'Copy list in Arena format');
  copy.type = 'button';
  copy.addEventListener('click', async () => {
    try {
      const exported = await request(`/api/decks/${encodeURIComponent(report.deck_id)}/export`);
      await navigator.clipboard.writeText(exported.text);
      setMessage(exported.unresolved?.length
        ? `List copied. ${exported.unresolved.length} card(s) the catalogue could not name went out as comments.`
        : 'List copied. In Arena: Decks → Import.');
    } catch (error) { setMessage(`The list was not copied: ${error.message}`, 'error'); }
  });
  box.append(copy);
  box.append(element('small', null, 'Pastes straight into the Arena importer: quantity, English name, set and number.'));
  return box;
}

function deckCoachBlock(report) {
  const box = element('section', 'deck-section');
  box.append(element('h3', null, 'AI reading'));
  const coach = state.summary?.coach;
  const copy = element('button', 'button primary', 'Copy the question');
  copy.type = 'button';
  copy.addEventListener('click', () => { state.coachMode = 'deck'; copyPrompt({ kind: 'deck', deck_id: report.deck_id }); });
  box.append(copy);
  box.append(element('small', null, 'Paste it into any assistant you already use.'));
  if (coach?.ready) {
    const run = element('button', 'button secondary', state.coachBusy ? 'Reading…' : `Or ask ${coach.model} from here`);
    run.type = 'button'; run.disabled = state.coachBusy;
    run.addEventListener('click', () => { state.coachMode = 'deck'; askCoach({ kind: 'deck', deck_id: report.deck_id }, null); });
    box.append(run);
  }
  if (state.coachAnswer) box.append(coachAnswerBlock(state.coachAnswer));
  return box;
}

function deckSection(title, entries) {
  const section = element('section', 'deck-section');
  section.append(element('h3', null, title));
  if (!entries.length) section.append(element('p', 'subtle', 'No card recorded.'));
  entries.forEach(({ id, quantity }) => {
    const card = state.deckCards[id];
    const row = element('div', 'deck-card');
    applyArt(row, id);
    row.append(element('strong', null, `${quantity}× ${cardName(id)}`), element('span', null, card?.type_line || `Id #${id} unresolved`));
    section.append(row);
  });
  return section;
}

async function saveExperiment(event) {
  event.preventDefault();
  const formElement = event.currentTarget;
  const payload = Object.fromEntries(new FormData(formElement).entries());
  payload.changes = { description: payload.changes || 'No detail given.' };
  payload.status = 'planned';
  try { await postJson('/api/experiments', payload); formElement.reset(); await loadExperiments(); setMessage('Hypothesis recorded as your own experiment.'); }
  catch (error) { setMessage(`The hypothesis was not recorded: ${error.message}`, 'error'); }
}

async function loadExperiments() {
  try { const payload = await request('/api/experiments'); state.experiments = payload.experiments ?? []; }
  catch (error) { state.experiments = []; setMessage(`Experiments did not load: ${error.message}`, 'error'); }
  renderExperiments();
}

function renderExperiments() {
  const target = $('#experiments'); target.replaceChildren();
  if (!state.experiments.length) { target.append(element('p', 'subtle', 'No hypothesis recorded.')); return; }
  state.experiments.forEach((experiment) => {
    const item = element('article', 'experiment');
    item.append(element('strong', null, experiment.title || 'Untitled hypothesis'), element('p', null, experiment.hypothesis || ''), element('small', null, `Deck ${experiment.deck_id ?? '—'} · ${experiment.status ?? 'planned'}`));
    target.append(item);
  });
}

// ---------------------------------------------------------------- statistics

function statRow(label, metrics) {
  const interval = metrics?.interval;
  const row = element('div', 'rate-bar');
  const head = element('div', 'rate-head');
  head.append(element('strong', null, label), element('span', null, intervalText(interval)));
  row.append(head);
  if (!interval) return row;
  const track = element('div', 'rate-track');
  const band = element('div', 'rate-band');
  band.style.left = `${interval.low * 100}%`;
  band.style.width = `${Math.max((interval.high - interval.low) * 100, 1)}%`;
  band.title = `95% interval: ${percent(interval.low)} to ${percent(interval.high)}`;
  const point = element('div', 'rate-point');
  point.style.left = `${interval.rate * 100}%`;
  track.append(element('div', 'rate-half'), band, point);
  const scale = element('div', 'rate-scale');
  scale.append(element('span', null, '0%'), element('span', null, '50%'), element('span', null, '100%'));
  row.append(track, scale);
  return row;
}

function renderStats() {
  const target = $('#stats-body'); target.replaceChildren();
  const summary = state.summary;
  if (!summary) { empty(target, 'No data.', 'Import a log to compute statistics.'); return; }
  const climb = element('section', 'deck-section');
  climb.append(element('h3', null, 'The climb'));
  climb.append(element('div', 'rank-body', 'Reading…'));
  target.append(climb);
  loadRank();

  const versus = element('section', 'deck-section');
  versus.append(element('h3', null, 'Against what the opponent showed'));
  versus.append(element('div', 'matchup-body', 'Reading…'));
  target.append(versus);
  loadMatchups();

  const start = element('section', 'deck-section');
  start.append(element('h3', null, 'Who went first'));
  start.append(statRow('On the play', summary.by_start?.on_play), statRow('On the draw', summary.by_start?.on_draw));
  if (summary.by_start?.unknown) start.append(element('small', null, `${summary.by_start.unknown} game(s) with no recorded order.`));
  target.append(start);

  const mull = element('section', 'deck-section');
  mull.append(element('h3', null, 'Mulligans'));
  const kept = state.games.filter((game) => game.mulligans_self === 0).length;
  const mulled = state.games.filter((game) => (game.mulligans_self ?? 0) > 0).length;
  mull.append(element('p', null, `${kept} hand(s) kept at seven · ${mulled} game(s) with a mulligan.`));
  target.append(mull);

  const modes = element('section', 'deck-section');
  modes.append(element('h3', null, 'By mode'));
  modes.append(statRow('BO1', summary.by_mode?.BO1), statRow('BO3 (games)', summary.by_mode?.BO3));
  target.append(modes);

  const cost = element('section', 'deck-section');
  cost.append(element('h3', null, 'Does a draft pay for itself?'));
  cost.append(element('div', 'economy-body', 'Reading…'));
  target.append(cost);
  loadEconomy();

  const economy = element('section', 'deck-section');
  economy.append(element('h3', null, 'Gems and gold'));
  economy.append(element('p', 'subtle', 'Measured from the balance the log restates while you play. Draft and sealed cost currency per entry, so this is the front where a new player bleeds without noticing.'));
  economy.append(element('div', 'wallet-body', 'Reading…'));
  target.append(economy);
  loadWallet();

  const health = element('section', 'deck-section');
  health.append(element('h3', null, 'Local store'));
  health.append(element('p', null, `${summary.imports ?? 0} import(s) · database ${bytesText(summary.database_bytes)} · ${summary.named_decks?.length ?? 0} named deck(s) read from the log.`));
  if (summary.capture) health.append(element('p', 'subtle', summary.capture.running ? `Following: ${bytesText(summary.capture.bytes_read)} read across ${summary.capture.sessions} session(s). Detailed logs: ${summary.capture.detailed_logs === false ? 'OFF in Arena' : summary.capture.detailed_logs ? 'on' : 'undetermined'}.` : 'Not following. Arena wipes Player.log every time the client starts: without the follower, the session is lost.'));
  target.append(health);

  if (summary.warnings?.length) {
    const warn = element('section', 'deck-section');
    warn.append(element('h3', null, 'Import warnings'));
    summary.warnings.forEach((warning) => warn.append(element('p', 'gap', warning)));
    target.append(warn);
  }
}

// ---------------------------------------------------------------- ajustes

function renderSettings() {
  const target = $('#settings-body'); target.replaceChildren();
  const summary = state.summary;
  if (!summary) { empty(target, 'No data.', 'Import a log first.'); return; }

  const art = element('section', 'deck-section');
  art.append(element('h3', null, 'Card art'));
  art.append(element('p', 'subtle', 'Fetches art from Scryfall once per card and keeps it on this PC. All that leaves is a set code and a collector number — never a match, a deck or an account.'));
  const toggle = element('button', `button ${summary.art?.enabled ? 'primary' : 'secondary'}`,
    summary.art?.enabled ? 'Card art on — switch off' : 'Switch card art on');
  toggle.type = 'button';
  toggle.addEventListener('click', async () => {
    try { await postJson('/api/art', { enabled: !summary.art?.enabled }); await refresh(); renderSettings(); }
    catch (error) { setMessage(`Could not change it: ${error.message}`, 'error'); }
  });
  art.append(toggle);
  art.append(element('p', null, `${summary.art?.cached ?? 0} image(s) cached · ${bytesText(summary.art?.bytes)} · ${summary.art?.without_image ?? 0} card(s) with no paper printing.`));
  art.append(element('small', null, summary.art_credit || ''));
  target.append(art);

  const rules = element('section', 'deck-section');
  rules.append(element('h3', null, 'Card rulings'));
  rules.append(element('p', 'subtle', "Official rulings for the cards in front of you — what this card actually does in this spot. Fetched once per card from Scryfall and kept on this PC. The full Comprehensive Rules are deliberately not bundled: they would cost more per question than everything else combined, and they answer a different question."));
  const rulesToggle = element('button', `button ${summary.rulings?.enabled ? 'primary' : 'secondary'}`,
    summary.rulings?.enabled ? 'Rulings on — switch off' : 'Switch rulings on');
  rulesToggle.type = 'button';
  rulesToggle.addEventListener('click', async () => {
    try { await postJson('/api/rulings', { enabled: !summary.rulings?.enabled }); await refresh(); renderSettings(); }
    catch (error) { setMessage(`Could not change it: ${error.message}`, 'error'); }
  });
  rules.append(rulesToggle);
  rules.append(element('p', null, `${summary.rulings?.cards_checked ?? 0} card(s) checked · ${summary.rulings?.cards_with_rulings ?? 0} have rulings.`));
  rules.append(element('small', null, summary.rulings_credit || ''));
  target.append(rules);

  const ai = element('section', 'deck-section');
  ai.append(element('h3', null, 'AI reading'));
  const coach = summary.coach ?? {};
  ai.append(element('p', 'subtle', `The app computes the numbers and ${coach.model} reads them. It is instructed not to recompute, not to invent card text, and never to call a play correct.`));
  if (!coach.package) ai.append(element('p', 'gap', `Missing package. Run in a terminal: ${coach.install}`));
  const form = document.createElement('form'); form.className = 'note-form';
  const field = document.createElement('input');
  field.type = 'password'; field.name = 'key'; field.placeholder = coach.key ? `key stored (${coach.key_hint})` : 'sk-ant-...';
  field.autocomplete = 'off'; field.maxLength = 200;
  const save = element('button', 'button primary', 'Save key'); save.type = 'submit';
  form.append(element('label', null, 'Anthropic API key'), field, save);
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    try { await postJson('/api/coach/key', { key: field.value }); field.value = ''; await refresh(); renderSettings(); setMessage('Key stored, encrypted for this Windows account.'); }
    catch (error) { setMessage(`The key was not stored: ${error.message}`, 'error'); }
  });
  ai.append(form);
  if (coach.key) {
    const forget = element('button', 'text-button', 'Forget the key'); forget.type = 'button';
    forget.addEventListener('click', async () => {
      try { await postJson('/api/coach/key', { key: '' }); await refresh(); renderSettings(); setMessage('Key deleted.'); }
      catch (error) { setMessage(error.message, 'error'); }
    });
    ai.append(forget);
  }
  ai.append(element('small', null, 'The key is encrypted with Windows DPAPI and lives in %LOCALAPPDATA%/mtga-coach. It never reaches the database or git.'));
  target.append(ai);

  const list = element('section', 'deck-section');
  list.append(element('h3', null, 'Analyse a standalone list'));
  list.append(element('p', 'subtle', 'Paste a list in Arena format to see curve, mana base and wildcard cost before you build it. Nothing is stored.'));
  const paste = document.createElement('textarea');
  paste.rows = 6; paste.placeholder = 'Deck\n4 Thoughtseize (LRW) 145\n...';
  const analyse = element('button', 'button secondary', 'Analyse'); analyse.type = 'button';
  const answer = element('div', 'list-analysis');
  analyse.addEventListener('click', async () => {
    answer.replaceChildren(element('p', 'subtle', 'Reading the list…'));
    try { renderListAnalysis(answer, await postJson('/api/decks/analyze', { text: paste.value })); }
    catch (error) { answer.replaceChildren(element('p', 'gap', error.message)); }
  });
  list.append(paste, analyse, answer);
  target.append(list);
}

function renderListAnalysis(target, payload) {
  target.replaceChildren();
  target.append(element('p', null, `${payload.main_count} cards in the main deck · ${payload.deck?.sideboard?.length ?? 0} sideboard entries.`));
  (payload.problems ?? []).forEach((problem) => target.append(element('p', 'gap', `line ${problem.line}: ${problem.reason}`)));
  const report = payload.analysis;
  if (!report) { target.append(element('p', 'subtle', payload.note)); return; }
  target.append(element('p', null, `${report.lands} lands · average MV ${report.average_mana_value ?? '—'} · published regression suggests ${report.lands_recommended ?? '—'}.`));
  if (report.curve) {
    const curve = element('div', 'mana-curve');
    Object.entries(report.curve).forEach(([mana, quantity]) => curve.append(element('span', 'curve-bar', `${mana}: ${quantity}`)));
    target.append(curve);
  }
  (report.colour_requirements ?? []).forEach((item) => {
    target.append(element('p', item.shortfall ? 'gap' : 'subtle',
      `${item.colour} ×${item.pips} by turn ${item.turn}: ${item.have} of ${item.needed} sources${item.shortfall ? ` — ${item.shortfall} short (${item.driver})` : ' — met'}`));
  });
  const cost = report.wildcards?.cost ?? {};
  const net = report.wildcards?.net ?? cost;
  const span = (key) => (net[key] ?? 0) === (cost[key] ?? 0)
    ? String(cost[key] ?? 0) : `${net[key] ?? 0}–${cost[key] ?? 0}`;
  target.append(element('p', null,
    `Wildcards: ${span('common')} common · ${span('uncommon')} uncommon · ${span('rare')} rare · ${span('mythic')} mythic.`));
  if (report.wildcards?.proven_owned) {
    target.append(element('small', null, report.wildcards.note));
  }
  target.append(element('small', null, payload.note));
}

// The break-even rate is the part that does not depend on an assumption: it falls out of
// the published prize structure alone. Everything to the right of it does depend on one.
async function loadEconomy() {
  const holder = document.querySelector('.economy-body');
  if (!holder) return;
  try {
    const data = await request('/api/economy');
    holder.replaceChildren();
    const yours = data.measured
      ? `Your limited record: ${data.limited_wins}–${data.limited_games - data.limited_wins} · ${intervalText(data.measured)}`
      : 'No completed limited game recorded yet.';
    holder.append(element('p', data.enough ? null : 'gap', yours));
    data.events.forEach((row) => {
      const box = element('div', 'economy-row');
      box.append(element('strong', null, `${row.label} — breaks even at ${row.break_even === null ? 'no rate' : `${(row.break_even * 100).toFixed(1)}%`}`));
      const table = element('div', 'economy-grid');
      const shown = row.yours ? [row.yours, ...row.reference] : row.reference;
      shown.forEach((item, at) => {
        const cell = element('div', `economy-cell${row.yours && at === 0 ? ' mine' : ''}${item.ratio >= 1 ? ' good' : ''}`);
        cell.append(element('strong', null, `${(item.win_rate * 100).toFixed(0)}%`));
        cell.append(element('span', null, `${item.ratio.toFixed(2)}×`));
        cell.append(element('small', null, `${item.net_gems >= 0 ? '+' : ''}${Math.round(item.net_gems)} gems`));
        if (row.yours && at === 0) cell.append(element('small', 'mine-label', 'yours'));
        table.append(cell);
      });
      box.append(table);
      box.append(element('small', null, `Entry ${row.reference[0].entry_gems} gems or ${row.reference[0].entry_gold} gold · ${row.reference[0].packs_included} pack(s) included · packs counted at ${data.pack_gems} gems`));
      holder.append(box);
    });
    holder.append(element('p', 'gap', data.unverified));
    holder.append(element('small', null, data.note));
  } catch (error) { holder.replaceChildren(element('p', 'gap', error.message)); }
}

async function loadWallet() {
  const holder = document.querySelector('.wallet-body');
  if (!holder) return;
  try {
    const wallet = await request('/api/wallet');
    holder.replaceChildren();
    if (!wallet.points?.length) { holder.append(element('p', 'subtle', wallet.note)); return; }
    const grid = element('div', 'wildcards');
    [['Gems', wallet.gems], ['Gold', wallet.gold]].forEach(([label, value]) => {
      const card = element('article', `wildcard${value.change < 0 ? ' short' : ''}`);
      card.append(element('strong', null, String(value.last ?? '—')), element('span', null, label));
      card.append(element('small', null, `${value.change >= 0 ? '+' : ''}${value.change} since the first reading`));
      grid.append(card);
    });
    holder.append(grid);
    holder.append(element('p', null, `${wallet.readings} reading(s) · ${wallet.games_between} game(s) in between.`));
    holder.append(element('small', null, wallet.note));
  } catch (error) { holder.replaceChildren(element('p', 'gap', error.message)); }
}

// ---------------------------------------------------------------- live game

// A panel beside the game, never on top of it: this app reads a log and draws in a browser
// window. It lags the game by the follower's polling interval and says so.
const LIVE_POLL_MS = 3000;

async function loadLive() {
  const panel = $('#live-panel');
  if (!panel) return;
  try {
    const live = await request('/api/live');
    const stamp = `${live.game_id ?? ''}|${live.frame ?? ''}|${live.playing}`;
    if (stamp === state.liveStamp) return;
    state.liveStamp = stamp;
    state.live = live;
    renderLive(panel, live);
  } catch { panel.hidden = true; }
}

function renderLive(panel, live) {
  panel.hidden = false;
  panel.replaceChildren();
  // A panel that simply is not there when it has nothing to show is indistinguishable from
  // a panel that was never built. So it says what it is waiting for, and when the reason is
  // that nobody turned the follower on, it offers the switch.
  if (!live.live || !live.playing) {
    panel.classList.add('waiting');
    const head = element('div', 'live-head');
    head.append(element('span', 'live-badge', live.live ? 'WAITING' : 'NOT FOLLOWING'));
    head.append(element('strong', null, live.live
      ? 'This fills in on the first turn of your next game.'
      : 'Turn on following and this fills in while you play.'));
    panel.append(head);
    panel.append(element('p', 'subtle', live.live
      ? 'What is left in your deck, and the chance of each card on the next draw. Read from the log as Arena writes it.'
      : 'Arena wipes Player.log every time the client restarts, so the app has to be reading while you play. Nothing is sent anywhere.'));
    if (!live.live) {
      const start = element('button', 'button primary', 'Follow matches');
      start.type = 'button';
      start.addEventListener('click', toggleCapture);
      panel.append(start);
    }
    return;
  }
  panel.classList.remove('waiting');

  const head = element('div', 'live-head');
  head.append(element('span', 'live-badge on', 'PLAYING'));
  head.append(element('strong', null, `Turn ${live.turn ?? '—'}`));
  head.append(element('span', 'live-phase', listText([live.phase, live.step])));
  (live.players ?? []).forEach((player) => {
    const life = element('span', `live-life${player.is_self ? ' mine' : ''}`, String(player.life ?? '?'));
    life.title = player.is_self ? 'your life' : "the opponent's life";
    head.append(life);
  });
  head.append(element('span', 'live-deck', live.deck_label || 'Composition'));
  panel.append(head);

  const library = live.library ?? {};
  if (!library.eligible) {
    panel.append(element('p', 'subtle', library.reason || 'The library cannot be counted for this game.'));
    return;
  }

  // The land ratio is the number a player is actually deciding on when they look at all:
  // whether to keep the land, whether to play around a flood. It gets its own line.
  const body = element('div', 'live-body');
  const lands = element('div', 'live-lands');
  const ratio = library.land_ratio ?? 0;
  const bar = element('div', 'live-bar');
  const fill = element('div', 'live-bar-fill');
  fill.style.width = `${Math.round(ratio * 100)}%`;
  bar.append(fill);
  lands.append(element('strong', null, `${library.lands} lands in ${library.size}`),
               bar,
               element('span', null, `${percent(ratio)} of the next card`));
  if (!library.matches_report) {
    lands.append(element('span', 'gap', 'disagrees with the count the log reports'));
  }
  body.append(lands);

  const entries = library.entries ?? [];
  const grid = element('div', 'live-grid');
  grid.append(element('span', 'live-col', 'card'),
              element('span', 'live-col right', 'next'),
              element('span', 'live-col right', 'in three'));
  const shown = state.liveAll ? entries : entries.slice(0, 8);
  shown.forEach((entry) => {
    const card = state.cards[entry.card_id];
    const name = element('span', `live-name${entry.is_land ? ' land' : ''}`, `${entry.quantity}× ${entry.name}`);
    if (card?.mana_cost) name.title = card.mana_cost;
    grid.append(name,
                element('span', 'live-odds right', percent(entry.next_draw)),
                element('span', 'live-odds soft right', percent(entry.within_three)));
  });
  body.append(grid);
  panel.append(body);

  if (entries.length > 8) {
    const more = element('button', 'text-button live-more', state.liveAll
      ? 'Show the top eight' : `Show the other ${entries.length - 8}`);
    more.type = 'button';
    more.addEventListener('click', () => { state.liveAll = !state.liveAll; renderLive(panel, live); });
    panel.append(more);
  }
  panel.append(element('small', null, live.note));
}

function livePolling(on) {
  if (state.liveTimer) { clearInterval(state.liveTimer); state.liveTimer = null; }
  if (on) { loadLive(); state.liveTimer = setInterval(loadLive, LIVE_POLL_MS); }
}

// ---------------------------------------------------------------- draft

// The draft is the one screen that has to keep up with the game: a pick has a timer on it.
// Polling is cheap here because the answer comes from a table already on disk.
const DRAFT_POLL_MS = 2000;

async function loadDraft() {
  try {
    const draft = await request('/api/draft');
    const stamp = `${draft.updated_at ?? ''}|${draft.pack ?? ''}|${draft.pick ?? ''}|${draft.pack_cards?.length ?? 0}`;
    $('#draft-dot').hidden = !draft.live;
    if (stamp === state.draftStamp && state.draft) return;
    state.draftStamp = stamp;
    state.draft = draft;
    Object.assign(state.cards, draft.cards ?? {});
    warmArt((draft.pack_cards ?? []).concat(draft.pool ?? []));
    if (draft.expansion && draft.expansion !== state.gradeSet) await loadGrades(draft.expansion);
    renderDraft();
  } catch (error) {
    if (!state.draft) empty($('#draft-body'), 'The draft could not be read.', error.message);
  }
}

function draftPolling(on) {
  if (state.draftTimer) { clearInterval(state.draftTimer); state.draftTimer = null; }
  if (on) state.draftTimer = setInterval(loadDraft, DRAFT_POLL_MS);
}

function renderDraft() {
  const target = $('#draft-body');
  const draft = state.draft;
  target.replaceChildren();
  if (!draft?.active) {
    $('#draft-position').textContent = '—';
    empty(target, 'No draft read yet.', draft?.hint ?? 'Turn Follow matches on before you enter the draft.');
    if (draft?.diagnostics?.unrecognised_shapes?.length) target.append(draftDiagnostics(draft.diagnostics));
    return;
  }
  $('#draft-position').textContent = draft.pack ? `Pack ${draft.pack} · pick ${draft.pick ?? '—'}` : 'Draft stored';

  const head = element('section', 'draft-head');
  head.append(element('span', draft.live ? 'live-badge on' : 'live-badge', draft.live ? 'LIVE' : 'LAST DRAFT STORED'));
  head.append(element('span', null, `${draft.event_name || 'Event not named'} · ${draft.expansion || 'set unknown'} · ${draft.pool?.length ?? 0} picked`));
  target.append(head);

  if (!draft.advice) target.append(ratingsPrompt(draft));
  else {
    if (draft.table?.note) target.append(element('p', 'gap', draft.table.note));
    target.append(recommendation(draft.advice));
    target.append(packGrid(draft.advice, draft.pack_cards ?? []));
  }
  target.append(poolBlock(draft));
  target.append(primerBlock());
  target.append(signalsBlock());
  target.append(deckBlock());
  target.append(reviewBlock());
  target.append(gradeBlock(draft));
  target.append(element('small', 'draft-credit', draft.credit));
}

function ratingsPrompt(draft) {
  const box = element('section', 'deck-section');
  box.append(element('h3', null, 'No ranking yet'));
  box.append(element('p', null, draft.ratings?.note ?? 'The public table for this set is not stored.'));
  if (!draft.expansion) {
    box.append(element('p', 'gap', 'The set could not be read from the cards in the pack, so there is nothing to fetch.'));
    return box;
  }
  const action = element('button', 'button primary', `Fetch the 17Lands table for ${draft.expansion}`);
  action.type = 'button';
  action.addEventListener('click', async () => {
    action.disabled = true;
    setMessage(`Fetching ${draft.expansion} ratings…`);
    try {
      const result = await postJson('/api/limited/fetch', { expansion: draft.expansion, event: draft.limited_event });
      setMessage(result.note || `${result.cards} cards stored for ${result.expansion} · ${result.event}.`);
      state.draftStamp = '';
      await loadDraft();
    } catch (error) { setMessage(`The table was not fetched: ${error.message}`, 'error'); action.disabled = false; }
  });
  box.append(action);
  box.append(element('small', null, 'One request, then it is read from disk for a day.'));
  return box;
}

function recommendation(advice) {
  const box = element('section', 'draft-pick');
  if (!advice.pick) {
    box.append(element('h2', null, 'No card in this pack carries a published rate.'));
    return box;
  }
  box.append(element('p', 'eyebrow', 'THE PICK'));
  box.append(element('h2', null, advice.pick_name));
  const why = element('ul', 'why-list');
  advice.ranked[0].why.forEach((reason) => why.append(element('li', null, reason)));
  box.append(why);
  if (advice.close_calls.length) {
    const names = advice.close_calls.map((id) => cardName(id)).join(', ');
    box.append(element('p', 'draft-close',
      `Close call with ${names}: ${advice.margin?.toFixed(2)} of a win-rate point apart. Take the one that fits the deck you want.`));
  }
  advice.notes.forEach((note) => box.append(element('p', 'subtle', note)));
  box.append(element('small', null, advice.caveat));
  return box;
}

function packGrid(advice, packIds) {
  const box = element('section', 'deck-section');
  box.append(element('h3', null, `The pack · ${packIds.length} cards`));
  const grid = element('div', 'pack-grid');
  const top = advice.ranked[0]?.score ?? 0;
  const floor = advice.ranked[advice.ranked.length - 1]?.score ?? top;
  advice.ranked.forEach((item, position) => grid.append(pickCard(item, position, top, floor)));
  advice.unrated.forEach((item) => {
    const card = element('article', 'pick-card unrated');
    card.append(element('strong', null, item.name), element('small', null, 'no published rate'));
    grid.append(card);
  });
  box.append(grid);
  return box;
}

function pickCard(item, position, top, floor) {
  const card = element('article', `pick-card${position === 0 ? ' best' : ''}`);
  applyArt(card, item.card_id);
  const head = element('div', 'pick-head');
  head.append(element('span', 'pick-rank', String(position + 1)), element('strong', null, item.name));
  card.append(head);
  const bar = element('div', 'pick-bar');
  const fill = element('div', 'pick-fill');
  fill.style.width = `${Math.round(((item.score - floor) / Math.max(top - floor, 0.5)) * 92) + 8}%`;
  bar.append(fill);
  card.append(bar);
  const numbers = element('div', 'pick-numbers');
  numbers.append(element('span', null, percent(item.gih_wr)));
  if (item.colour_adjustment) numbers.append(element('span', 'gap', `${item.colour_adjustment.toFixed(1)} off colour`));
  if (item.colours?.length) numbers.append(colourPips(item.colours));
  card.append(numbers);
  const why = element('ul', 'why-list');
  item.why.slice(0, 2).forEach((reason) => why.append(element('li', null, reason)));
  card.append(why);
  card.addEventListener('click', () => inspectCard(item.card_id));
  return card;
}

function poolBlock(draft) {
  const box = element('section', 'deck-section');
  const pool = draft.pool ?? [];
  box.append(element('h3', null, `Your pool · ${pool.length} cards`));
  if (draft.advice?.lane?.length) {
    const lane = element('p', null, 'Colours the pool is paying for: ');
    lane.append(colourPips(draft.advice.lane));
    lane.append(document.createTextNode(` · commitment ${Math.round((draft.advice.commitment ?? 0) * 100)}%`));
    box.append(lane);
  }
  if (!pool.length) { box.append(element('p', 'subtle', 'Nothing picked yet.')); return box; }
  const counts = new Map();
  pool.forEach((id) => counts.set(id, (counts.get(id) ?? 0) + 1));
  const list = element('div', 'pool-list');
  [...counts.entries()].forEach(([id, quantity]) => {
    const entry = element('button', 'pool-card', '');
    entry.type = 'button';
    entry.append(element('strong', null, cardName(id)));
    if (quantity > 1) entry.append(element('span', null, `×${quantity}`));
    entry.addEventListener('click', () => inspectCard(id));
    list.append(entry);
  });
  box.append(list);
  const copy = element('button', 'button secondary', 'Copy the pool as a deck list');
  copy.type = 'button';
  copy.addEventListener('click', async () => {
    try {
      const payload = await request('/api/draft/export');
      await navigator.clipboard.writeText(payload.text);
      setMessage(`${payload.cards} cards copied. Paste them into the deck importer in Arena.`);
    } catch (error) { setMessage(`The pool was not copied: ${error.message}`, 'error'); }
  });
  box.append(copy);
  const passed = (draft.picks ?? []).filter((entry) => (entry.pack_cards ?? []).length > 1);
  if (passed.length) {
    box.append(element('h3', null, 'What you passed'));
    box.append(element('p', 'subtle', 'Each pick with the pack it came from. This is the part worth reading after the draft.'));
    const history = element('div', 'pool-list');
    passed.slice(-12).reverse().forEach((entry) => {
      const row = element('article', 'pool-card wide');
      row.append(element('strong', null, `P${entry.pack ?? '—'}p${entry.pick ?? '—'}: ${cardName(entry.card_id)}`));
      const others = entry.pack_cards.filter((id) => id !== entry.card_id).slice(0, 6).map((id) => cardName(id));
      row.append(element('small', null, `over ${others.join(', ')}`));
      history.append(row);
    });
    box.append(history);
  }
  return box;
}

// A pool is not a deck, and the step between them is where a new player gives away the
// most games. The build is offered, never applied: the list is his to change.
function deckBlock() {
  const box = element('section', 'deck-section');
  box.append(element('h3', null, 'Deck to register'));
  const action = element('button', 'button primary', state.deck ? 'Build it again' : 'Build the deck from this pool');
  action.type = 'button';
  action.addEventListener('click', () => loadDeck(box));
  box.append(action);
  const stage = element('div', 'deck-stage');
  box.append(stage);
  if (state.deck) renderDeck(stage, state.deck);
  return box;
}

async function loadDeck(box) {
  const stage = box.querySelector('.deck-stage');
  stage.replaceChildren(element('p', 'subtle', 'Building…'));
  try {
    state.deck = await request('/api/draft/deck');
    Object.assign(state.cards, state.deck.cards ?? {});
    renderDeck(stage, state.deck);
  } catch (error) {
    stage.replaceChildren(element('p', 'gap', error.message));
  }
}

function renderDeck(stage, deck) {
  stage.replaceChildren();
  if (!deck.pair) {
    stage.append(element('p', 'gap', deck.reason ?? 'No deck could be built from this pool.'));
    return;
  }
  const head = element('p', 'deck-head');
  head.append(colourPips(deck.pair.split('')));
  head.append(document.createTextNode(
    ` ${deck.spells.length} spells + ${deck.lands} lands · ${deck.creatures} creatures`
    + (deck.short ? ` · ${deck.short} short of a full deck` : '')));
  stage.append(head);

  if (deck.basis === 'structure') {
    const warn = element('p', 'gap', deck.note);
    stage.append(warn);
  } else {
    stage.append(element('p', 'subtle',
      `Ordered by 17Lands win rate · ${deck.coverage.covered} of ${deck.coverage.of_pool} pool cards covered.`));
  }

  if (deck.alternatives?.length) {
    const alts = deck.alternatives.map((item) =>
      `${item.pair} ${item.total}${item.short ? ` (${item.short} short)` : ''}`).join(' · ');
    stage.append(element('p', 'subtle', `Pairs that lost: ${alts}. Yours totalled ${deck.total}.`));
  }

  const list = element('div', 'deck-columns');
  const byMana = new Map();
  deck.spells.forEach((item) => {
    const key = item.mana_value ?? 0;
    if (!byMana.has(key)) byMana.set(key, []);
    byMana.get(key).push(item);
  });
  [...byMana.keys()].sort((a, b) => a - b).forEach((mana) => {
    const column = element('div', 'deck-column');
    column.append(element('h4', null, `${mana} mana · ${byMana.get(mana).length}`));
    byMana.get(mana).sort((a, b) => b.score - a.score).forEach((item) => {
      const row = element('button', 'deck-line', '');
      row.type = 'button';
      row.append(element('strong', null, item.name));
      row.append(element('small', null, `${item.score} · ${(item.why ?? []).slice(0, 2).join(', ') || 'no reason recorded'}`));
      row.addEventListener('click', () => inspectCard(item.card_id));
      column.append(row);
    });
    list.append(column);
  });
  stage.append(list);

  const lands = Object.entries(deck.land_base.basics).map(([colour, quantity]) => `${quantity} ${colour}`).join(' · ');
  const nonbasic = deck.land_base.nonbasic.map((item) => `${item.quantity} ${item.name}`).join(', ');
  stage.append(element('p', null, `Mana base: ${lands}${nonbasic ? ` · ${nonbasic}` : ''}`));
  (deck.mana ?? []).forEach((item) => {
    stage.append(element('p', item.shortfall ? 'gap' : 'subtle',
      `${item.colour} ×${item.pips} by turn ${item.turn}: ${item.have} of ${item.needed} sources`
      + (item.shortfall ? ` — ${item.shortfall} short, wanted by ${item.driver}` : ' — met')
      + ` (${item.basis})`));
  });

  if (deck.left_out?.length) {
    const cut = element('details', 'deck-cut');
    cut.append(element('summary', null, `Left out · ${deck.left_out.length} of the best`));
    deck.left_out.forEach((item) => cut.append(element('p', 'subtle',
      `${item.name} · ${item.score} · ${item.colours.join('') || 'colourless'}`)));
    stage.append(cut);
  }

  const copy = element('button', 'button secondary', 'Copy the deck for Arena');
  copy.type = 'button';
  copy.addEventListener('click', async () => {
    try {
      const payload = await request('/api/draft/deck/export');
      await navigator.clipboard.writeText(payload.text);
      setMessage(`${payload.cards} cards copied. Paste them into the deck importer in Arena.`);
    } catch (error) { setMessage(`The deck was not copied: ${error.message}`, 'error'); }
  });
  stage.append(copy);
  stage.append(element('small', null,
    'This decides the mechanical part only — the pair, the best cards in it, and lands for the pips they ask for. The archetype and the card that is only good against one opponent are yours.'));
}

// The card database the client already installed is the richest open data a player has:
// complete on release day, offline, identical for everybody, and it answers the question a
// new drafter actually has — not which card is better, but what this pair is even doing.
function primerBlock() {
  const box = element('section', 'deck-section');
  box.append(element('h3', null, 'What each pair is for in this set'));
  const action = element('button', 'button secondary', state.primer ? 'Read it again' : 'Read the set');
  action.type = 'button';
  action.addEventListener('click', () => loadPrimer(box));
  box.append(action);
  const stage = element('div', 'primer-stage');
  box.append(stage);
  if (state.primer) renderPrimer(stage, state.primer);
  return box;
}

async function loadPrimer(box) {
  const stage = box.querySelector('.primer-stage');
  stage.replaceChildren(element('p', 'subtle', 'Reading the card database…'));
  try {
    state.primer = await request('/api/primer');
    renderPrimer(stage, state.primer);
  } catch (error) { stage.replaceChildren(element('p', 'gap', error.message)); }
}

function renderPrimer(stage, data) {
  stage.replaceChildren();
  if (!data.pairs?.length) {
    stage.append(element('p', 'subtle', data.reason ?? 'Nothing to read.'));
    return;
  }
  stage.append(element('p', 'subtle',
    `${data.expansion} · ${data.cards_read} cards read from the database Arena installed here`
    + (data.set_mechanics?.length
      ? ` · mechanics: ${data.set_mechanics.map((item) => `${item.name} (${item.cards})`).join(', ')}`
      : '')));
  const lane = new Set(state.draft?.advice?.lane ?? []);
  data.pairs.forEach((pair) => {
    const yours = pair.pair.split('').every((colour) => lane.has(colour));
    const box = element('article', `primer-pair${yours ? ' yours' : ''}`);
    const head = element('div', 'primer-head');
    head.append(colourPips(pair.pair.split('')));
    if (yours) head.append(element('span', 'primer-mine', 'your pair'));
    box.append(head);
    box.append(element('p', null, pair.reading));
    if (pair.leanings?.length) {
      box.append(element('small', null, 'Also returns to: ' + pair.leanings
        .map((item) => `${item.about} (${item.cards} cards, ${item.lift}× the set)`).join(' · ')));
    }
    stage.append(box);
  });
  stage.append(element('small', null, data.note));
}

// The one piece of draft advice that needs no outside data: counting the packs that
// reached him. It survives on a set nobody measures, which is where the app is most of
// the time, and it happens to be the skill a new drafter is missing.
function signalsBlock() {
  const box = element('section', 'deck-section');
  box.append(element('h3', null, 'What the packs were passing'));
  box.append(element('p', 'subtle',
    'Counted from your own packs, from the fifth pick of each one. Nothing here comes from anybody else’s data.'));
  const action = element('button', 'button secondary', state.signals ? 'Count again' : 'Read the signals');
  action.type = 'button';
  action.addEventListener('click', () => loadSignals(box));
  box.append(action);
  const stage = element('div', 'signals-stage');
  box.append(stage);
  if (state.signals) renderSignals(stage, state.signals);
  return box;
}

async function loadSignals(box) {
  const stage = box.querySelector('.signals-stage');
  stage.replaceChildren(element('p', 'subtle', 'Counting…'));
  try {
    state.signals = await request('/api/draft/signals');
    Object.assign(state.cards, state.signals.cards ?? {});
    renderSignals(stage, state.signals);
  } catch (error) { stage.replaceChildren(element('p', 'gap', error.message)); }
}

function renderSignals(stage, data) {
  stage.replaceChildren();
  if (!data.packs?.length) {
    stage.append(element('p', 'subtle', data.reason ?? 'Nothing to count.'));
    return;
  }
  if (data.lane?.length) {
    const lane = element('p', null, 'You ended in ');
    lane.append(colourPips(data.lane));
    stage.append(lane);
  }

  data.packs.forEach((pack) => {
    const box = element('article', 'signal-pack');
    box.append(element('h4', null, `Pack ${pack.pack}`));
    box.append(element('p', pack.enough ? null : 'gap', pack.reading));
    if (!pack.enough) { stage.append(box); return; }
    const rows = element('div', 'signal-rows');
    // The bar length is the signal itself — how far the colour ran above or below its own
    // baseline — because a bar showing the share invites the eye to compare shares, and a
    // colour the set simply prints more of would then look like the open one.
    const reach = Math.max(...pack.colours.map((item) => Math.abs(item.delta)), 0.02);
    pack.colours.forEach((item) => {
      const row = element('div', 'signal-row');
      const name = element('span', 'signal-name', '');
      name.append(colourPips([item.colour]));
      name.append(document.createTextNode(` ${item.seen}`));
      name.title = `${item.seen} of the late cards were ${item.name}`;
      row.append(name);
      const track = element('div', 'signal-track');
      track.append(element('div', 'signal-zero'));
      const fill = element('div', `signal-fill${item.delta >= 0.1 ? ' strong' : item.delta >= 0.05 ? ' mild' : item.delta < 0 ? ' under' : ''}`);
      const size = Math.round((Math.abs(item.delta) / reach) * 50);
      fill.style.width = `${size}%`;
      fill.style.left = item.delta >= 0 ? '50%' : `${50 - size}%`;
      fill.title = `${percent(item.share)} of the late cards, against ${percent(item.baseline)} of the draft`;
      track.append(fill);
      row.append(track);
      row.append(element('span', `signal-delta${item.delta > 0 ? ' up' : ''}`,
        `${item.delta > 0 ? '+' : ''}${(item.delta * 100).toFixed(1)}`));
      rows.append(row);
    });
    box.append(rows);
    box.append(element('small', null,
      'Each bar is how far that colour ran above (right) or below (left) its share of this draft. The number beside it is that gap in points.'));
    stage.append(box);
  });

  const lane = new Set(data.lane ?? []);
  const mine = (data.wheeled ?? []).filter((item) => item.colours.some((colour) => lane.has(colour)));
  if (mine.length) {
    const box = element('article', 'signal-pack');
    box.append(element('h4', null, `Came back around in your colours · ${mine.length}`));
    box.append(element('p', 'subtle',
      'You passed these and they were still there eight picks later. Nobody between you and the pack wanted them.'));
    const list = element('div', 'pool-list');
    mine.slice(0, 14).forEach((item) => {
      const entry = element('button', 'pool-card', '');
      entry.type = 'button';
      entry.append(element('strong', null, item.name));
      entry.append(element('span', null, `P${item.pack} · ${item.first_seen}→${item.came_back}`));
      entry.addEventListener('click', () => inspectCard(item.card_id));
      list.append(entry);
    });
    box.append(list);
    stage.append(box);
  }

  if (data.bot_draft) stage.append(element('p', 'gap', data.bot_caveat));
  stage.append(element('small', null, data.note));
}

// The record of the draft, pick by pick. Whether it carries a second opinion depends on
// whether the app has one worth reading: on a set nothing measures, it does not.
function reviewBlock() {
  const box = element('section', 'deck-section');
  box.append(element('h3', null, 'How the picks went'));
  const action = element('button', 'button secondary', state.review ? 'Read it again' : 'Replay the draft');
  action.type = 'button';
  action.addEventListener('click', () => loadReview(box));
  box.append(action);
  const stage = element('div', 'review-stage');
  box.append(stage);
  if (state.review) renderReview(stage, state.review);
  return box;
}

async function loadReview(box) {
  const stage = box.querySelector('.review-stage');
  stage.replaceChildren(element('p', 'subtle', 'Replaying…'));
  try {
    state.review = await request('/api/draft/review');
    Object.assign(state.cards, state.review.cards ?? {});
    renderReview(stage, state.review);
  } catch (error) {
    stage.replaceChildren(element('p', 'gap', error.message));
  }
}

function renderReview(stage, review) {
  stage.replaceChildren();
  if (!review.picks?.length) {
    stage.append(element('p', 'subtle', review.reason ?? 'No pick recorded with its pack.'));
    return;
  }
  if (review.compares) {
    stage.append(element('p', null,
      `The app would have taken the same card in ${review.agreed} of ${review.of} picks.`));
    if (review.biggest?.length) {
      stage.append(element('h4', null, 'Where it read the pack differently'));
      review.biggest.forEach((row) => {
        const item = element('article', 'review-gap');
        item.append(element('strong', null,
          `P${row.pack}p${row.pick}: you took ${row.taken_name}, it would have taken ${row.suggested_name}`));
        item.append(element('small', null,
          `your card ranked ${row.rank_of_yours} of ${row.options} · ${row.gap} apart`));
        (row.why ?? []).slice(0, 2).forEach((reason) => item.append(element('p', 'subtle', reason)));
        stage.append(item);
      });
    }
  } else {
    stage.append(element('p', 'gap', review.note));
  }

  const list = element('div', 'review-list');
  review.picks.slice().reverse().forEach((row) => {
    const item = element('article', `review-row${row.agreed ? ' agreed' : ''}`);
    const head = element('button', 'text-button', `P${row.pack ?? '—'}p${row.pick ?? '—'}: ${row.taken_name}`);
    head.type = 'button';
    head.addEventListener('click', () => inspectCard(row.taken));
    item.append(head);
    if (review.compares && row.suggested && !row.agreed) {
      item.append(element('small', null, `it would have taken ${row.suggested_name}`));
    }
    item.append(element('small', 'review-lane', `${row.options} cards in the pack${row.lane?.length ? ` · pool in ${row.lane.join('')}` : ''}`));
    list.append(item);
  });
  stage.append(list);
  if (review.compares) stage.append(element('small', null, review.note));
}

// The write end of the open data. A grade stays on this machine until its author decides
// to send it, and what leaves is a file anyone can read, fork or ignore.
function gradeBlock(draft) {
  const box = element('section', 'deck-section');
  box.append(element('h3', null, 'Grade the cards'));
  if (!draft.expansion) {
    box.append(element('p', 'subtle', 'The set could not be read from the pool, so a grade would have nowhere to go.'));
    return box;
  }
  box.append(element('p', 'subtle',
    `${draft.expansion} · 0.0 to 5.0. 3.0 is a card you are happy to maindeck; 2.0 is filler; 4.0 and up wins games on its own. Grades are used only while 17Lands has no number for the set, and are replaced the moment it does.`));
  const handle = document.createElement('input');
  handle.placeholder = 'your handle, optional — it travels into the public file';
  handle.maxLength = 32;
  handle.value = state.handle;
  handle.addEventListener('change', () => { state.handle = handle.value.trim(); });
  box.append(handle);
  const list = element('div', 'grade-list');
  const seen = new Set();
  [...(draft.pack_cards ?? []), ...(draft.pool ?? [])].forEach((id) => {
    if (seen.has(id)) return;
    seen.add(id);
    list.append(gradeRow(draft.expansion, id));
  });
  box.append(list);
  const send = element('button', 'button secondary', 'Copy the file for a pull request');
  send.type = 'button';
  send.addEventListener('click', async () => {
    try {
      const payload = await request(`/api/community/export?set=${encodeURIComponent(draft.expansion)}`);
      await navigator.clipboard.writeText(payload.text);
      setMessage(`${payload.cards} grade(s) copied. Paste into ${payload.filename} and open a pull request.`);
    } catch (error) { setMessage(`Nothing to export: ${error.message}`, 'error'); }
  });
  box.append(send);
  return box;
}

function gradeRow(expansion, cardId) {
  const row = element('div', 'grade-row');
  const name = element('button', 'text-button', cardName(cardId));
  name.type = 'button';
  name.addEventListener('click', () => inspectCard(cardId));
  row.append(name);
  const select = document.createElement('select');
  select.append(new Option('—', ''));
  for (let value = 0; value <= 50; value += 5) {
    select.append(new Option((value / 10).toFixed(1), (value / 10).toFixed(1)));
  }
  const current = state.grades[String(cardId)];
  if (current) select.value = Number(current.grade).toFixed(1);
  const note = document.createElement('input');
  note.placeholder = 'why, in one line';
  note.maxLength = 400;
  if (current?.note) note.value = current.note;
  const store = async () => {
    if (!select.value) return;
    try {
      const answer = await postJson('/api/community/grade', {
        expansion, card_id: cardId, grade: Number(select.value),
        note: note.value.trim(), by: state.handle,
      });
      state.grades[String(cardId)] = answer.entry;
      row.classList.add('graded');
    } catch (error) { setMessage(`The grade was not saved: ${error.message}`, 'error'); }
  };
  select.addEventListener('change', store);
  note.addEventListener('change', store);
  if (current) row.classList.add('graded');
  row.append(select, note);
  return row;
}

async function loadGrades(expansion) {
  try {
    const payload = await request(`/api/community?set=${encodeURIComponent(expansion || '')}`);
    state.grades = payload.grades ?? {};
    state.gradeSet = payload.expansion || '';
  } catch { state.grades = {}; }
}

function draftDiagnostics(diagnostics) {
  const box = element('section', 'deck-section');
  box.append(element('h3', null, 'What the reader saw'));
  box.append(element('p', 'subtle',
    'Key names only, never values. It is here so an unfamiliar draft dialect can be read off the screen instead of guessed at.'));
  box.append(element('p', null, `Matched: ${listText(diagnostics.matched_keys)}`));
  diagnostics.unrecognised_shapes.forEach((shape) => box.append(element('code', 'shape', shape)));
  return box;
}

// ---------------------------------------------------------------- training

// Arena restates the rank instead of reporting a change, exactly like the wallet, so the
// curve begins at the first session this app followed and never claims to be a history.
async function loadRank() {
  const holder = document.querySelector('.rank-body');
  if (!holder) return;
  try {
    const rank = await request(`/api/rank?track=${encodeURIComponent(state.rankTrack)}`);
    holder.replaceChildren();
    if (!rank.points?.length) {
      holder.append(rankTracks());
      holder.append(element('p', 'subtle', rank.note));
      return;
    }
    holder.append(rankTracks());
    const head = element('p', null,
      `${rank.first} → ${rank.last} · ${rank.readings} readings · ${rank.wins ?? 0} wins and ${rank.losses ?? 0} losses as the client counts them`);
    holder.append(head);
    holder.append(rankChart(rank));
    holder.append(element('small', null, rank.note));
  } catch (error) { holder.replaceChildren(element('p', 'gap', error.message)); }
}

// Limited keeps its own ladder, and a draft never touches the constructed one — so a
// climb has two stories and the screen has to let you pick which one you are reading.
function rankTracks() {
  const row = element('div', 'rank-tracks', '');
  [['constructed', 'Constructed'], ['limited', 'Limited']].forEach(([key, label]) => {
    const button = element('button', `mode${state.rankTrack === key ? ' active' : ''}`, label);
    button.type = 'button';
    button.addEventListener('click', () => {
      if (state.rankTrack === key) return;
      state.rankTrack = key;
      loadRank();
    });
    row.append(button);
  });
  return row;
}

function rankChart(rank) {
  const chart = element('div', 'rank-chart');
  // Matches on the horizontal axis, not the clock. A rank only moves when a ranked match
  // ends, so a time axis spends most of its width drawing the hours you were asleep or
  // drafting — twelve of this account's twenty-four hours were one flat line. Counting
  // matches instead makes every pixel a game that was played.
  const seen = new Map();
  rank.points.forEach((point) => {
    const played = (point.wins ?? 0) + (point.losses ?? 0);
    seen.set(played, point);
  });
  const points = [...seen.entries()].sort((a, b) => a[0] - b[0]);
  const rungs = rank.rungs ?? [];
  const values = points.map(([, point]) => point.position).concat(rungs.map((r) => r.position));
  const low = Math.min(...values) - 0.2;
  const high = Math.max(...values) + 0.35;
  const span = Math.max(high - low, 0.5);
  const firstMatch = points[0][0];
  const lastMatch = points[points.length - 1][0];
  const reach = Math.max(lastMatch - firstMatch, 1);
  const x = (played) => ((played - firstMatch) / reach) * 100;
  const y = (position) => 100 - ((position - low) / span) * 100;

  const svgns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(svgns, 'svg');
  svg.setAttribute('viewBox', '0 0 100 100');
  svg.setAttribute('preserveAspectRatio', 'none');
  svg.setAttribute('role', 'img');
  svg.setAttribute('aria-label', `Rank from ${rank.first} to ${rank.last} over ${reach} matches`);

  rungs.forEach((rung) => {
    const line = document.createElementNS(svgns, 'line');
    line.setAttribute('x1', '0'); line.setAttribute('x2', '100');
    line.setAttribute('y1', y(rung.position)); line.setAttribute('y2', y(rung.position));
    line.setAttribute('class', 'rank-rung');
    svg.append(line);
  });

  // Alternating columns ten matches wide: something for the eye to count against when it
  // wants to know where in the run a dip happened.
  for (let band = Math.ceil(firstMatch / 10) * 10; band < lastMatch; band += 20) {
    const rect = document.createElementNS(svgns, 'rect');
    rect.setAttribute('x', x(band));
    rect.setAttribute('width', Math.max(x(Math.min(band + 10, lastMatch)) - x(band), 0));
    rect.setAttribute('y', '0');
    rect.setAttribute('height', '100');
    rect.setAttribute('class', 'rank-band');
    svg.append(rect);
  }

  const line = document.createElementNS(svgns, 'polyline');
  line.setAttribute('points', points.map(([played, point]) =>
    `${x(played)},${y(point.position)}`).join(' '));
  line.setAttribute('class', 'rank-line');
  svg.append(line);
  chart.append(svg);

  // A mark where the deck changed. The shape of a climb says little without knowing which
  // list was doing the climbing, and the log knows: the game that ran before each reading.
  const marks = element('div', 'rank-decks');
  let previous = null;
  let lastLabel = -100;
  points.forEach(([played, point]) => {
    const label = point.deck_label;
    if (!label || label === previous) return;
    previous = label;
    const at = x(played);
    const mark = element('span', 'rank-deck', '');
    mark.style.left = `${at}%`;
    mark.style.top = `${y(point.position)}%`;
    mark.title = `${label} · from match ${played}`;
    mark.append(element('i', null, ''));
    // Two deck changes a few matches apart would print their names on top of each other.
    // The dot is always drawn, because the change happened; the name waits for room, and
    // the tooltip carries it either way.
    if (at - lastLabel >= 16) {
      const name = element('em', null, label);
      if (y(point.position) > 60) name.classList.add('above');
      mark.append(name);
      lastLabel = at;
    }
    marks.append(mark);
  });
  chart.append(marks);

  const labels = element('div', 'rank-rungs');
  rungs.forEach((rung) => {
    const tag = element('span', 'rank-rung-label', rung.label);
    tag.style.top = `${y(rung.position)}%`;
    labels.append(tag);
  });
  chart.append(labels);

  const scale = element('div', 'rank-scale');
  scale.append(element('span', null, `match ${firstMatch}`),
               element('span', null, `${points.length} readings`),
               element('span', null, `match ${lastMatch}`));
  chart.append(scale);
  return chart;
}

async function loadMatchups() {
  const holder = document.querySelector('.matchup-body');
  if (!holder) return;
  try {
    const data = await request('/api/matchups');
    holder.replaceChildren();
    if (!data.rows?.length) {
      holder.append(element('p', 'subtle', `No matchup counted yet. ${data.note}`));
      return;
    }
    data.rows.forEach((row) => {
      const line = element('div', 'rate-bar');
      const head = element('div', 'rate-head');
      const name = element('strong', null, '');
      name.append(colourPips(row.colours === 'C' ? [] : row.colours.split('')));
      name.append(document.createTextNode(` ${row.wins}–${row.losses}${row.draws ? `–${row.draws}` : ''}`));
      head.append(name, element('span', null, intervalText(row.interval)));
      line.append(head);
      if (row.interval) {
        const track = element('div', 'rate-track');
        const band = element('div', 'rate-band');
        band.style.left = `${row.interval.low * 100}%`;
        band.style.width = `${Math.max((row.interval.high - row.interval.low) * 100, 1)}%`;
        const point = element('div', 'rate-point');
        point.style.left = `${row.interval.rate * 100}%`;
        track.append(element('div', 'rate-half'), band, point);
        line.append(track);
      }
      holder.append(line);
    });
    holder.append(element('small', null, data.note));
  } catch (error) { holder.replaceChildren(element('p', 'gap', error.message)); }
}

async function loadNotes() {
  try { const payload = await request('/api/notes'); state.notes = payload.notes ?? []; }
  catch { state.notes = []; }
  renderTraining();
}

function renderTraining() {
  const target = $('#training-notes'); target.replaceChildren();
  if (!state.notes.length) {
    return empty(target, 'No note recorded.',
      'Open a game, pick a position, write what you were thinking and tick "come back to this one as a drill".');
  }
  const drills = state.notes.filter((note) => (note.tags ?? []).includes('drill') && note.frame_index !== null);
  const rest = state.notes.filter((note) => !drills.includes(note));

  if (drills.length) {
    const box = element('section', 'deck-section');
    box.append(element('h3', null, `Drills · ${drills.length}`));
    box.append(element('p', 'subtle', 'The position comes back without your move and without what happened next. Decide first, then reveal.'));
    drills.slice().reverse().forEach((note) => box.append(drillRow(note)));
    target.append(box);
  }

  if (rest.length) {
    const box = element('section', 'deck-section');
    box.append(element('h3', null, 'Notes'));
    rest.slice().reverse().forEach((note) => box.append(noteRow(note)));
    target.append(box);
  }
  target.append(element('div', 'drill-stage'));
}

function noteLabel(note) {
  const game = state.games.find((item) => item.id === note.game_id);
  return `${game ? `${game.deck_label} · ${resultLabel(game.result)}` : note.game_id} · frame ${note.frame_index ?? '—'}`;
}

function noteRow(note) {
  const item = element('article', 'training-note');
  const open = element('button', 'text-button', noteLabel(note));
  open.type = 'button';
  open.addEventListener('click', async () => {
    await openGame(note.game_id);
    if (note.frame_index !== null) changeFrame(note.frame_index);
    setView('matches');
  });
  item.append(open, element('p', null, note.body), element('small', null, listText(note.tags)));
  return item;
}

function drillRow(note) {
  const item = element('article', 'training-note drill');
  const open = element('button', 'text-button', noteLabel(note));
  open.type = 'button';
  open.addEventListener('click', () => startDrill(note));
  item.append(open, element('small', null, 'Your note stays hidden until you answer.'));
  return item;
}

// The point of the drill is that the answer is not on screen: the board is shown at the
// marked frame, the move that was made and the note are withheld until the player commits.
async function startDrill(note) {
  const stage = $('.drill-stage');
  stage.replaceChildren(element('p', 'subtle', 'Loading the position…'));
  try {
    const detail = await request(`/api/games/${encodeURIComponent(note.game_id)}`);
    const page = await request(`/api/games/${encodeURIComponent(note.game_id)}/frames?start=${note.frame_index}&limit=1`);
    const frame = page.frames?.[0];
    if (!frame) throw new Error('That frame is no longer stored.');
    Object.assign(state.cards, detail.cards ?? {});
    await loadCards(frameCardIds(frame));
    warmArt(frameCardIds(frame));
    stage.replaceChildren();
    stage.append(element('p', 'eyebrow', 'DRILL'),
      element('h3', null, `Turn ${frame.turn ?? '—'} · ${listText([frame.phase, frame.step])}`));

    const entered = new Set();
    const seats = (frame.players ?? []).map((player) => player.seat);
    const selfSeat = detail.self_seat;
    const opponentSeat = seats.find((seat) => seat !== selfSeat) ?? (selfSeat === 1 ? 2 : 1);
    const board = element('div', 'board');
    board.append(sideBand(frame, opponentSeat, false, entered, null));
    board.append(sharedBand(frame, entered));
    board.append(sideBand(frame, selfSeat, true, entered, null));
    stage.append(board);

    const form = document.createElement('form'); form.className = 'note-form';
    const answer = document.createElement('textarea');
    answer.maxLength = 2000; answer.required = true;
    answer.placeholder = 'What do you play here, and what are you afraid of?';
    const submit = element('button', 'button primary', 'Reveal what you did'); submit.type = 'submit';
    form.append(element('label', null, 'Your answer, before looking'), answer, submit);
    form.addEventListener('submit', (event) => {
      event.preventDefault();
      revealDrill(stage, frame, note, answer.value.trim());
    });
    stage.append(form);
    stage.scrollIntoView({ behavior: reducedMotion() ? 'auto' : 'smooth', block: 'start' });
  } catch (error) {
    stage.replaceChildren(element('p', 'gap', error.message));
  }
}

function revealDrill(stage, frame, note, answer) {
  const box = element('section', 'drill-reveal');
  box.append(element('h4', null, 'What the log records'));
  box.append(actionList('Taken', frame.action ? [frame.action] : [], frame.source_line));
  box.append(actionList('Available', frame.available_actions ?? frame.actions ?? [], frame.source_line));
  box.append(element('h4', null, 'What you wrote at the time'));
  box.append(element('p', null, note.body));
  if (answer) {
    box.append(element('h4', null, 'What you just said'));
    box.append(element('p', null, answer));
  }
  box.append(element('small', null, 'The app does not score this. Comparing the two is the exercise.'));
  const again = element('button', 'text-button', 'Open this position in the replay');
  again.type = 'button';
  again.addEventListener('click', async () => {
    await openGame(note.game_id);
    changeFrame(note.frame_index);
    setView('matches');
  });
  box.append(again);
  stage.append(box);
  box.scrollIntoView({ behavior: reducedMotion() ? 'auto' : 'smooth', block: 'start' });
}

// ---------------------------------------------------------------- shell

async function importConfigured() {
  setMessage('Importing the configured file…');
  try {
    const result = await postJson('/api/import', { source: 'configured' });
    setMessage(result.created ? `Imported: ${result.games} game(s) from ${result.record_count} records.` : 'This file had already been imported.');
    await refresh();
  } catch (error) { setMessage(`The import failed: ${error.message}`, 'error'); }
}

async function uploadLog(event) {
  const file = event.target.files?.[0]; if (!file) return;
  setMessage(`Uploading ${file.name}…`);
  try {
    const result = await request('/api/import', { method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body: await file.arrayBuffer() });
    setMessage(result.created ? `Imported: ${result.games} game(s).` : 'This file had already been imported.');
    await refresh();
  } catch (error) { setMessage(`The file was not imported: ${error.message}`, 'error'); }
  finally { event.target.value = ''; }
}

async function toggleCapture() {
  const running = Boolean(state.summary?.capture?.running);
  setMessage(running ? 'Stopping the follower…' : 'Starting the log follower…');
  try {
    const capture = await postJson(running ? '/api/capture/stop' : '/api/capture/start', {});
    renderCaptureLine(capture);
    setMessage(capture.running ? 'Following Player.log. Leave the app open while you play.' : 'Follower stopped.');
    await refresh();
  } catch (error) { setMessage(`The follower failed: ${error.message}`, 'error'); }
}

function setView(name) {
  document.querySelectorAll('.view').forEach((view) => view.classList.toggle('active', view.id === `view-${name}`));
  document.querySelectorAll('.nav-item').forEach((button) => button.classList.toggle('active', button.dataset.view === name));
  if (name === 'decks') { renderDecks(); loadExperiments(); }
  if (name === 'stats') renderStats();
  if (name === 'settings') renderSettings();
  if (name === 'training') loadNotes();
  if (name === 'draft') { loadDraft(); draftPolling(true); } else draftPolling(false);
}

function reducedMotion() { return window.matchMedia?.('(prefers-reduced-motion: reduce)').matches; }

async function refresh() {
  try {
    const [summary, games] = await Promise.all([request(summaryEndpoint()), request('/api/games')]);
    state.summary = summary;
    state.games = games.games ?? [];
    renderSummary(); renderGames();
    if ($('#view-decks').classList.contains('active')) renderDecks();
    if ($('#view-stats').classList.contains('active')) renderStats();
  } catch (error) {
    state.summary = null; state.games = [];
    renderSummary(); renderGames();
    setMessage(`Could not reach the local service: ${error.message}`, 'error');
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
    const index = state.detail.frame_index ?? [];
    if (event.key === 'ArrowLeft') { event.preventDefault(); const at = nextStep(index, state.framePosition, -1); if (at !== null) changeFrame(at); }
    if (event.key === 'ArrowRight') { event.preventDefault(); const at = nextStep(index, state.framePosition, 1); if (at !== null) changeFrame(at); }
  });
  refresh();
  setInterval(() => { if (state.summary?.capture?.running) refresh(); }, 15000);
  // The dot in the sidebar is what tells him a pack is on screen while he is on another
  // tab, so the draft is checked on a slow beat even when its view is closed.
  setInterval(() => { if (state.summary?.capture?.running && !state.draftTimer) loadDraft(); }, 10000);
  livePolling(true);
}

// A module script can finish executing after DOMContentLoaded has already fired, and then
// the listener never runs and nothing on the page responds. Binding only while the document
// is still parsing, and booting straight away otherwise, removes that race.
if (typeof document !== 'undefined') {
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
}
