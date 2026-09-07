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
  const identity = element('div', 'game-identity');
  identity.append(element('strong', null, `${game.deck_label || 'Composition'} · game ${game.game_number ?? '—'}`),
    element('span', null, `${game.format || 'Format unknown'} · ${game.mode || 'Mode unknown'}${game.opponent_name ? ` · vs ${game.opponent_name}` : ''}`));
  const meta = element('div', 'game-meta');
  meta.append(element('span', `result ${game.result}`, resultLabel(game.result)),
    element('span', null, startLabel(game.on_play)),
    element('span', 'quality-badge', qualityLabel(game.quality)),
    element('span', null, `${game.turns ?? 0} turns`),
    element('span', null, `${game.decision_count ?? 0} decisions`));
  row.append(identity, meta);
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
  ['decision', 'Decision'], ['library', 'Library'], ['opponent', 'Opponent'],
  ['timeline', 'Timeline'], ['reading', 'AI reading'],
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
  if (state.sidebarTab === 'library') renderLibraryTab(body, frame);
  if (state.sidebarTab === 'opponent') renderOpponentTab(body);
  if (state.sidebarTab === 'timeline') renderTimelineTab(body);
  if (state.sidebarTab === 'reading') renderCoachTab(body, frame);
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
  const save = element('button', 'button secondary', 'Save note'); save.type = 'submit';
  noteForm.append(element('label', null, 'Manual note'), text, save);
  noteForm.addEventListener('submit', (event) => saveNote(event, frame.index, text));
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

async function saveNote(event, frameIndex, input) {
  event.preventDefault(); const body = input.value.trim(); if (!body) return;
  try {
    await postJson('/api/notes', { game_id: state.detail.id, frame_index: frameIndex, body, tags: [] });
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
  if (!coach?.ready) {
    target.append(element('p', 'subtle', coach?.package === false
      ? `Missing package. Run: ${coach?.install}`
      : 'No Anthropic key stored. Open Settings to paste yours.'));
    const link = element('button', 'text-button', 'Open Settings'); link.type = 'button';
    link.addEventListener('click', () => setView('settings'));
    target.append(link);
    return;
  }
  if (frame.quality !== 'complete') {
    target.append(element('p', 'gap', 'This stretch has a gap: the context is not eligible for a reading.'));
    return;
  }
  const picker = element('div', 'coach-modes');
  COACH_MODES.forEach(([key, label]) => {
    const button = element('button', `sidebar-tab${state.coachMode === key ? ' active' : ''}`, label);
    button.type = 'button';
    button.addEventListener('click', () => { state.coachMode = key; renderSidebar(frame); });
    picker.append(button);
  });
  target.append(picker);
  const run = element('button', 'button primary', state.coachBusy ? 'Reading…' : 'Ask for a reading');
  run.type = 'button'; run.disabled = state.coachBusy;
  run.addEventListener('click', () => askCoach({ kind: 'position', game_id: state.detail.id, index: frame.index }, frame));
  target.append(run);
  target.append(element('small', null, `Model ${coach.model}. The app sends only the sanitised position and the numbers it computed itself.`));
  if (state.coachAnswer) target.append(coachAnswerBlock(state.coachAnswer));
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
    button.append(element('strong', null, deckEntry.label || `Version ${deckEntry.id}`),
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
  const curve = element('div', 'mana-curve');
  curve.append(element('h3', null, 'Mana curve · main deck, lands excluded'));
  curve.append(element('small', null, `${report.lands} lands · average MV ${report.average_mana_value ?? '—'}${report.lands_recommended ? ` · published regression suggests ${report.lands_recommended}` : ''}`));
  if (!report.curve) curve.append(element('p', 'gap', `No curve: ${report.unresolved.length} main-deck card(s) were not resolved by the local catalogue.`));
  else Object.entries(report.curve).forEach(([mana, quantity]) => curve.append(element('span', 'curve-bar', `${mana}: ${quantity}`)));
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
  requirements.forEach((item) => {
    const row = element('div', `deck-card${item.shortfall ? ' shortfall' : ''}`);
    row.append(element('strong', null, `${item.colour} ×${item.pips} by turn ${item.turn}: ${item.have} of ${item.needed} sources`),
      element('span', null, item.shortfall ? `${item.shortfall} short — demand set by ${item.driver}` : `met — demand set by ${item.driver}`));
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
  Object.entries(map).forEach(([key, [label, inventoryKey]]) => {
    const row = element('div', 'deck-card');
    row.append(element('strong', null, `${cost[key] ?? 0} ${label}`),
      element('span', null, owned[inventoryKey] !== undefined ? `you hold ${owned[inventoryKey]}` : 'stock not read'));
    box.append(row);
  });
  box.append(element('small', null, 'Cost of the whole list. Arena stopped publishing the collection in the log, so the app cannot know which copies you already own.'));
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
  if (!coach?.ready) {
    box.append(element('p', 'subtle', 'Add your key in Settings to ask for a reading of this list.'));
    return box;
  }
  const run = element('button', 'button secondary', state.coachBusy ? 'Reading…' : 'Suggest swaps from the numbers');
  run.type = 'button'; run.disabled = state.coachBusy;
  run.addEventListener('click', () => { state.coachMode = 'deck'; askCoach({ kind: 'deck', deck_id: report.deck_id }, null); });
  box.append(run);
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
  const row = element('div', 'deck-card');
  row.append(element('strong', null, label), element('span', null, intervalText(metrics?.interval)));
  return row;
}

function renderStats() {
  const target = $('#stats-body'); target.replaceChildren();
  const summary = state.summary;
  if (!summary) { empty(target, 'No data.', 'Import a log to compute statistics.'); return; }
  const rank = summary.rank;
  if (rank?.constructedClass) {
    const box = element('section', 'deck-section');
    box.append(element('h3', null, 'Constructed rank'));
    box.append(element('p', null, `${rank.constructedClass} ${rank.constructedLevel ?? ''} · season ${rank.constructedSeasonOrdinal ?? '—'} · ${rank.constructedMatchesWon ?? 0} wins and ${rank.constructedMatchesLost ?? 0} losses as recorded by the client`));
    target.append(box);
  }
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
  target.append(element('p', null, `Wildcards: ${cost.common ?? 0} common · ${cost.uncommon ?? 0} uncommon · ${cost.rare ?? 0} rare · ${cost.mythic ?? 0} mythic.`));
  target.append(element('small', null, payload.note));
}

// ---------------------------------------------------------------- training

async function loadNotes() {
  try { const payload = await request('/api/notes'); state.notes = payload.notes ?? []; }
  catch { state.notes = []; }
  renderTraining();
}

function renderTraining() {
  const target = $('#training-notes'); target.replaceChildren();
  if (!state.notes.length) return empty(target, 'No note recorded.', 'Open a game, pick a position, and write the note in the replay sidebar.');
  const byGame = new Map(state.games.map((game) => [game.id, game]));
  state.notes.slice().reverse().forEach((note) => {
    const game = byGame.get(note.game_id);
    const item = element('article', 'training-note');
    const open = element('button', 'text-button', `${game ? `${game.deck_label} · ${resultLabel(game.result)}` : note.game_id} · frame ${note.frame_index ?? '—'}`);
    open.type = 'button';
    open.addEventListener('click', async () => { await openGame(note.game_id); if (note.frame_index !== null) changeFrame(note.frame_index); setView('matches'); });
    item.append(open, element('p', null, note.body), element('small', null, listText(note.tags)));
    target.append(item);
  });
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
}

if (typeof document !== 'undefined') document.addEventListener('DOMContentLoaded', boot);
