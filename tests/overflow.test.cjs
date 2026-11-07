// The overflow queue's decisions (NotificationLogic.planStack), driven the way
// Service.rebuildGroups drives it: the shown/waiting keys and ranks of one
// rebuild are the input of the next.
const test = require('node:test')
const assert = require('node:assert/strict')
const Logic = require('../NotificationLogic.js')
const Preferences = require('../Preferences.js')

// A model row; its key is "<timestamp>-<id>", so arrival order is timestamp order.
const row = (id, summary, urgency = 1, timestamp = 1000 + id) => ({id, originalId: id, timestamp, summary, body: '', app: 'Test', urgency, actionsJson: '[]'})

function stack(limit, options = {}) {
  const state = {rows: [], shown: {}, waiting: {}, ranks: {}, since: {}, arrivals: {}, top: 0, bottom: 0, seq: 0, pinned: new Set(), history: []}
  state.rebuild = () => {
    const groups = Preferences.groups(state.rows, true, 'browser', {browser: () => false})
    const plan = Logic.planStack(groups, {limit, shown: state.shown, waiting: state.waiting, ranks: state.ranks, since: state.since, arrivals: state.arrivals,
      top: state.top, bottom: state.bottom, seq: state.seq, queueLimit: options.queueLimit, pinned: group => state.pinned.has(group.key),
      canWait: group => !group.keys.every(key => (options.replays || new Set()).has(key))})
    state.top = plan.top; state.bottom = plan.bottom; state.seq = plan.seq
    const gone = plan.dropped.flatMap(group => group.keys).concat(plan.evicted)
    state.history.push(...gone)
    state.rows = state.rows.filter(r => gone.indexOf(Logic.imageStem(r)) < 0)
    state.shown = {}; state.waiting = {}
    plan.visible.forEach((group, i) => group.keys.forEach(key => { state.shown[key] = true; state.ranks[key] = plan.ranks[i]; state.since[key] = plan.since[i] }))
    plan.waiting.forEach(group => group.keys.forEach(key => { state.waiting[key] = true; delete state.ranks[key]; delete state.since[key] }))
    state.plan = plan
    return state
  }
  // Model order is newest first, as Service inserts arrivals at index 0.
  state.arrive = r => { state.rows.unshift(r); return state.rebuild() }
  state.leave = id => { state.rows = state.rows.filter(r => r.id !== id); return state.rebuild() }
  state.visible = () => state.plan.visible.map(group => group.summary)
  state.queue = () => state.plan.waiting.map(group => group.summary)
  return state
}

test("the user's sequence shows all four cards with the default limit of 5", () => {
  assert.equal(Preferences.defaults().maxVisible, 5)
  const s = stack(Preferences.defaults().maxVisible)
  s.arrive(row(1, 'Non-critical notification'))
  for (const i of [1, 2, 3]) s.arrive(row(1 + i, 'Critical notification ' + i, 2))
  assert.deepEqual(s.visible(), ['Critical notification 3', 'Critical notification 2', 'Critical notification 1', 'Non-critical notification'])
  assert.deepEqual(s.queue(), [])
})

test('with a limit of 3 the normal card waits for the criticals and shows when one is dismissed', () => {
  const s = stack(3)
  s.arrive(row(1, 'Non-critical notification'))
  for (const i of [1, 2, 3]) s.arrive(row(1 + i, 'Critical notification ' + i, 2))
  assert.deepEqual(s.visible(), ['Critical notification 3', 'Critical notification 2', 'Critical notification 1'])
  assert.deepEqual(s.queue(), ['Non-critical notification'])
  assert.deepEqual(s.history, [], 'nothing goes to history for lack of room')
  s.leave(3)
  // It joins the bottom: the cards on screen do not move for it.
  assert.deepEqual(s.visible(), ['Critical notification 3', 'Critical notification 1', 'Non-critical notification'])
  assert.deepEqual(s.queue(), [])
})

test('waiting cards show in arrival order, criticals first, and join the bottom of the stack', () => {
  const s = stack(2)
  s.arrive(row(1, 'A')); s.arrive(row(2, 'B'))
  s.arrive(row(3, 'C')); s.arrive(row(4, 'D')); s.arrive(row(5, 'E'))
  assert.deepEqual(s.visible(), ['B', 'A'])
  assert.deepEqual(s.queue(), ['C', 'D', 'E'])
  // A critical arrival goes on top and the non-critical card shown last waits
  // again, at the head of the queue (it arrived before the waiting ones).
  s.arrive(row(6, 'Alert', 2))
  assert.deepEqual(s.visible(), ['Alert', 'A'])
  assert.deepEqual(s.queue(), ['B', 'C', 'D', 'E'])
  // A waiting critical goes ahead of every waiting non-critical card.
  s.arrive(row(7, 'Second alert', 2))
  assert.deepEqual(s.visible(), ['Second alert', 'Alert'])
  assert.deepEqual(s.queue(), ['A', 'B', 'C', 'D', 'E'])
  s.leave(6)
  assert.deepEqual(s.visible(), ['Second alert', 'A'])
  s.leave(7)
  assert.deepEqual(s.visible(), ['A', 'B'])
  s.leave(1)
  assert.deepEqual(s.visible(), ['B', 'C'], 'the next card joins under the remaining one')
  assert.deepEqual(s.queue(), ['D', 'E'])
  // A new arrival with room goes on top, a promoted card under the stack.
  s.leave(4); s.leave(5)
  s.arrive(row(8, 'F'))
  assert.deepEqual(s.visible(), ['B', 'C'])
  s.leave(3)
  assert.deepEqual(s.visible(), ['B', 'F'])
  s.leave(2)
  s.arrive(row(9, 'G'))
  assert.deepEqual(s.visible(), ['G', 'F'], 'a new arrival with a free slot goes on top')
})

test('a hovered or busy card keeps its slot: the critical waits instead', () => {
  const s = stack(2)
  s.arrive(row(1, 'Read me')); s.arrive(row(2, 'Busy'))
  s.pinned.add('1001-1'); s.pinned.add('1002-2')
  s.arrive(row(3, 'Alert', 2))
  assert.deepEqual(s.visible(), ['Busy', 'Read me'])
  assert.deepEqual(s.queue(), ['Alert'])
  // Only the unpinned card gives up its slot.
  s.pinned.delete('1002-2'); s.rebuild()
  assert.deepEqual(s.visible(), ['Read me', 'Alert'])
  assert.deepEqual(s.queue(), ['Busy'])
})

test('a lowered limit (screen space) sends the card shown last back to the queue first', () => {
  const s = stack(3)
  s.arrive(row(1, 'A')); s.arrive(row(2, 'B')); s.arrive(row(3, 'C')); s.arrive(row(4, 'D'))
  assert.deepEqual(s.visible(), ['C', 'B', 'A'])
  s.leave(1)
  assert.deepEqual(s.visible(), ['C', 'B', 'D'])
  // The screen fits two: D, shown last (the one that did not fit), waits again and shows first.
  const narrow = Logic.planStack(Preferences.groups(s.rows, true, 'browser', {browser: () => false}),
    {limit: 2, shown: s.shown, waiting: s.waiting, ranks: s.ranks, since: s.since, top: s.top, bottom: s.bottom, seq: s.seq})
  assert.deepEqual(narrow.visible.map(group => group.summary), ['C', 'B'])
  assert.deepEqual(narrow.waiting.map(group => group.summary), ['D'])
  // Cards that all arrived at once: the newest, shown last, wait (first in, first shown).
  const burst = stack(2)
  burst.rows = [row(3, 'Newest'), row(2, 'Middle'), row(1, 'Oldest')]
  burst.rebuild()
  assert.deepEqual(burst.visible(), ['Middle', 'Oldest'])
  assert.deepEqual(burst.queue(), ['Newest'])
})

test('duplicates and replacements update a waiting card in place', () => {
  const s = stack(1)
  s.arrive(row(1, 'Shown')); s.arrive(row(2, 'Waiting')); s.arrive(row(3, 'Later'))
  assert.deepEqual(s.queue(), ['Waiting', 'Later'])
  // A duplicate merges into the waiting group, which keeps its turn.
  s.arrive(row(4, 'Waiting'))
  assert.deepEqual(s.queue(), ['Waiting', 'Later'])
  assert.deepEqual(s.plan.waiting[0].keys, ['1004-4', '1002-2'])
  // A replacement under a new key keeps the replaced card's arrival (Service.inheritPlace).
  s.rows = s.rows.filter(r => r.id !== 3)
  s.arrivals['2000-3'] = 1003; s.waiting['2000-3'] = true
  s.arrive(row(3, 'Later, edited', 1, 2000))
  s.arrive(row(5, 'Newest'))
  assert.deepEqual(s.queue(), ['Waiting', 'Later, edited', 'Newest'])
})

test('history replays show what fits, newest first, and never wait', () => {
  const replays = new Set(['900-1', '901-2', '902-3'])
  const s = stack(2, {replays})
  s.rows = [row(3, 'Replay newest', 1, 902), row(2, 'Replay middle', 1, 901), row(1, 'Replay oldest', 1, 900)]
  s.rebuild()
  assert.deepEqual(s.visible(), ['Replay newest', 'Replay middle'])
  assert.deepEqual(s.queue(), [])
  assert.deepEqual(s.history, ['900-1'])
  // A real arrival takes precedence later; the replay it displaces leaves instead of waiting.
  s.arrive(row(4, 'Alert', 2))
  assert.deepEqual(s.visible(), ['Alert', 'Replay middle'])
  assert.deepEqual(s.queue(), [])
  assert.deepEqual(s.history, ['900-1', '902-3'])
})

test('the queue bound sends the oldest waiting non-critical notification to history, then the oldest critical', () => {
  const s = stack(1, {queueLimit: 3})
  s.arrive(row(1, 'Shown', 2))
  s.arrive(row(2, 'Old alert', 2)); s.arrive(row(3, 'Old')); s.arrive(row(4, 'Newer')); s.arrive(row(5, 'Newest'))
  assert.deepEqual(s.history, ['1003-3'])
  assert.deepEqual(s.queue(), ['Old alert', 'Newer', 'Newest'])
  s.arrive(row(6, 'Alert 2', 2)); s.arrive(row(7, 'Alert 3', 2))
  assert.deepEqual(s.history, ['1003-3', '1004-4', '1005-5'])
  assert.deepEqual(s.queue(), ['Old alert', 'Alert 2', 'Alert 3'])
  s.arrive(row(8, 'Alert 4', 2))
  assert.deepEqual(s.history, ['1003-3', '1004-4', '1005-5', '1002-2'])
  assert.deepEqual(s.queue(), ['Alert 2', 'Alert 3', 'Alert 4'])
  assert.deepEqual(s.visible(), ['Shown'])
})

test('restored cards keep their place: shown ones stay, queued ones wait', () => {
  const s = stack(2)
  s.rows = [row(3, 'Restored queued'), row(2, 'Restored shown'), row(1, 'Restored shown too')]
  s.shown = {'1002-2': true, '1001-1': true}; s.waiting = {'1003-3': true}
  s.rebuild()
  assert.deepEqual(s.visible(), ['Restored shown', 'Restored shown too'])
  assert.deepEqual(s.queue(), ['Restored queued'])
  s.leave(2)
  assert.deepEqual(s.visible(), ['Restored shown too', 'Restored queued'])
})

test('popup files carry the queued flag through parsing', () => {
  const [entry] = Logic.parsePopupFiles(JSON.stringify({...row(1, 'Waiting'), queued: true}) + '\n', 1)
  assert.equal(entry.queued, true)
  const [plain] = Logic.parsePopupFiles(JSON.stringify(row(2, 'Shown')) + '\n', 1)
  assert.equal(plain.queued, undefined)
  // A queued card never ran its clock: only restorePopups' age check applies to it.
  assert.equal(Logic.popupExpired(plain, 1000, 1002 + 5000), true)
})
