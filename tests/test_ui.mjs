import test from 'node:test';
import assert from 'node:assert/strict';
import {
  buildRequestOptions,
  adjacentDecisionPosition,
  contextEndpoint,
  filterGamesByMode,
  firstDecisionPosition,
  modeMetrics,
  selectReplayFrame,
  summaryEndpoint,
} from '../src/mtga_coach/static/app.js';

test('selectReplayFrame preserves API frame order and exposes its quality warning', () => {
  const replay = selectReplayFrame([
    { index: 8, turn: 4, quality: 'complete', warnings: [] },
    { index: 3, turn: 2, quality: 'degraded', warnings: ['Estado anterior ausente.'] },
  ], 1);

  assert.equal(replay.position, 1);
  assert.equal(replay.frame.index, 3);
  assert.equal(replay.warning, 'Estado anterior ausente.');
});

test('filterGamesByMode shows only the selected mode while ambos retains unknown games', () => {
  const games = [
    { id: 'bo1', mode: 'BO1' },
    { id: 'bo3', mode: 'BO3' },
    { id: 'unknown', mode: 'unknown' },
  ];

  assert.deepEqual(filterGamesByMode(games, 'BO1').map((game) => game.id), ['bo1']);
  assert.deepEqual(filterGamesByMode(games, 'ambos').map((game) => game.id), ['bo1', 'bo3', 'unknown']);
});

test('contextEndpoint requests exactly the selected frame and never a later frame', () => {
  assert.equal(summaryEndpoint(), '/api/summary');
  assert.equal(contextEndpoint('game-4', 7), '/api/games/game-4/context?index=7');
});

test('buildRequestOptions preserves the application marker on a JSON POST', () => {
  const options = buildRequestOptions({
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-MTGA-Coach': 'removed-by-bug' },
    body: '{"source":"configured"}',
  });

  assert.equal(options.method, 'POST');
  assert.equal(options.headers['Content-Type'], 'application/json');
  assert.equal(options.headers['X-MTGA-Coach'], '1');
});

test('modeMetrics excludes BO3 outcomes from the default BO1 review', () => {
  const metrics = modeMetrics(
    { games: 3, completed: 3, wins: 2, losses: 1, draws: 0, matches: 2, by_mode: { BO1: 2, BO3: 1 } },
    [
      { id: 'one', match_id_hashed: 'bo1-match', mode: 'BO1', status: 'complete', result: 'win' },
      { id: 'two', match_id_hashed: 'bo1-match', mode: 'BO1', status: 'complete', result: 'loss' },
      { id: 'three', match_id_hashed: 'bo3-match', mode: 'BO3', status: 'complete', result: 'win' },
    ],
    'BO1',
  );

  assert.deepEqual(metrics, { games: 2, completed: 2, wins: 1, losses: 1, draws: 0, matches: 1, win_rate: 0.5 });
});

test('decision navigation opens and advances only through frames with recorded decisions', () => {
  const frames = [
    { index: 0 },
    { index: 1, action: { label: 'Conjurar carta' } },
    { index: 2, available_actions: [{ label: 'Passar prioridade' }] },
    { index: 3 },
  ];

  assert.equal(firstDecisionPosition(frames), 1);
  assert.equal(adjacentDecisionPosition(frames, 1, 1), 2);
  assert.equal(adjacentDecisionPosition(frames, 2, -1), 1);
  assert.equal(adjacentDecisionPosition(frames, 2, 1), null);
});
