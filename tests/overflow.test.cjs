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
  const state = {rows: [], shown: {}, waiting: {}, ranks: {}, since: {}, arrivals: {}, top: 0, bottom: 0, seq: 0, pinned: new Set(), history: [],
    holding: false, held: {}, released: {}}
  state.rebuild = () => {
    const groups = Preferences.groups(state.rows, true, 'browser', {browser: () => false})
    const canWait = group => !group.keys.every(key => (options.replays || new Set()).has(key))
    // As Service.rebuildGroups: hold arrivals while hovered; released ones plan as new arrivals.
    const held = Logic.heldArrivals(groups, {holding: state.holding, shown: state.shown, waiting: state.waiting, held: state.held, released: state.released, canWait})
    const waiting = Object.assign({}, state.waiting)
    for (const key in state.released) delete waiting[key]
    state.released = {}; state.held = held
    const plan = Logic.planStack(groups, {limit, shown: state.shown, waiting, ranks: state.ranks, since: state.since, arrivals: state.arrivals,
      top: state.top, bottom: state.bottom, seq: state.seq, queueLimit: options.queueLimit, pinned: group => state.pinned.has(group.key),
      canWait, held: group => group.keys.every(key => held[key])})
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
  // The pointer enters or leaves the stack (Service.holdArrivals, releaseHeld).
  state.hover = () => { state.holding = true; return state.rebuild() }
  state.release = () => { Object.assign(state.released, state.held); state.held = {}; return state.rebuild() }
  state.unhover = () => { state.holding = false; return state.release() }
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

test('with a limit of 3 the normal card keeps its slot: the oldest critical waits and shows when one is dismissed', () => {
  const s = stack(3)
  s.arrive(row(1, 'Non-critical notification'))
  for (const i of [1, 2, 3]) s.arrive(row(1 + i, 'Critical notification ' + i, 2))
  assert.deepEqual(s.visible(), ['Critical notification 3', 'Critical notification 2', 'Non-critical notification'])
  assert.deepEqual(s.queue(), ['Critical notification 1'])
  assert.deepEqual(s.history, [], 'nothing goes to history for lack of room')
  s.leave(3)
  // It joins the bottom: the cards on screen do not move for it.
  assert.deepEqual(s.visible(), ['Critical notification 3', 'Non-critical notification', 'Critical notification 1'])
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
  // One slot stays for the normal cards: the second alert rotates the first
  // back into the queue, ahead of every waiting non-critical card.
  s.arrive(row(7, 'Second alert', 2))
  assert.deepEqual(s.visible(), ['Second alert', 'A'])
  assert.deepEqual(s.queue(), ['Alert', 'B', 'C', 'D', 'E'])
  s.leave(6)
  assert.deepEqual(s.visible(), ['Second alert', 'A'])
  assert.deepEqual(s.queue(), ['B', 'C', 'D', 'E'])
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
  s.arrive(row(1, 'First alert', 2))
  // The newer critical rotates the first one back into the queue.
  s.arrive(row(2, 'Old alert', 2)); s.arrive(row(3, 'Old')); s.arrive(row(4, 'Newer')); s.arrive(row(5, 'Newest'))
  assert.deepEqual(s.history, ['1003-3'])
  assert.deepEqual(s.visible(), ['Old alert'])
  assert.deepEqual(s.queue(), ['First alert', 'Newer', 'Newest'])
  s.arrive(row(6, 'Alert 2', 2)); s.arrive(row(7, 'Alert 3', 2))
  assert.deepEqual(s.history, ['1003-3', '1004-4', '1005-5'])
  assert.deepEqual(s.queue(), ['Alert 2', 'Old alert', 'First alert'])
  s.arrive(row(8, 'Alert 4', 2))
  assert.deepEqual(s.history, ['1003-3', '1004-4', '1005-5', '1001-1'])
  assert.deepEqual(s.queue(), ['Alert 3', 'Alert 2', 'Old alert'])
  assert.deepEqual(s.visible(), ['Alert 4'])
})

test('criticals that never expire cannot starve a new one: it rotates the oldest shown critical back into the queue', () => {
  const s = stack(5)
  for (const i of [1, 2, 3, 4, 5]) s.arrive(row(i, 'Flood ' + i, 2))
  assert.deepEqual(s.visible(), ['Flood 5', 'Flood 4', 'Flood 3', 'Flood 2', 'Flood 1'])
  s.arrive(row(6, 'Genuine', 2))
  assert.deepEqual(s.visible(), ['Genuine', 'Flood 5', 'Flood 4', 'Flood 3', 'Flood 2'], 'the new critical shows on top')
  assert.deepEqual(s.queue(), ['Flood 1'], 'the oldest shown critical waits')
  assert.deepEqual(s.history, [], 'the displaced critical waits instead of going to history')
  // A freed slot shows it again, at the bottom: nothing on screen moves for it.
  s.leave(6)
  assert.deepEqual(s.visible(), ['Flood 5', 'Flood 4', 'Flood 3', 'Flood 2', 'Flood 1'])
  assert.deepEqual(s.queue(), [])
  // A pinned critical (hovered, busy, keyboard-selected) keeps its slot: the oldest unpinned one waits.
  s.pinned.add('1001-1')
  s.arrive(row(8, 'Another', 2))
  assert.deepEqual(s.visible(), ['Another', 'Flood 5', 'Flood 4', 'Flood 3', 'Flood 1'])
  assert.deepEqual(s.queue(), ['Flood 2'])
})

test('one slot stays for normal cards: five criticals that never expire cannot keep a normal one waiting', () => {
  const s = stack(5)
  for (const i of [1, 2, 3, 4, 5]) s.arrive(row(i, 'Flood ' + i, 2))
  s.arrive(row(6, 'Normal'))
  assert.deepEqual(s.visible(), ['Normal', 'Flood 5', 'Flood 4', 'Flood 3', 'Flood 2'], 'the normal card shows on top')
  assert.deepEqual(s.queue(), ['Flood 1'], 'the oldest critical waits')
  assert.deepEqual(s.history, [])
  // A later critical rotates among the criticals; the normal card keeps its slot.
  s.arrive(row(7, 'Alert', 2))
  assert.deepEqual(s.visible(), ['Alert', 'Normal', 'Flood 5', 'Flood 4', 'Flood 3'])
  assert.deepEqual(s.queue(), ['Flood 2', 'Flood 1'])
  // Freed and refilled: the next normal card takes the reserved slot, at the bottom.
  s.arrive(row(8, 'Later normal'))
  assert.deepEqual(s.queue(), ['Flood 2', 'Flood 1', 'Later normal'])
  s.leave(6)
  assert.deepEqual(s.visible(), ['Alert', 'Flood 5', 'Flood 4', 'Flood 3', 'Later normal'])
  assert.deepEqual(s.queue(), ['Flood 2', 'Flood 1'])
  // With no normal card left, criticals take every slot again.
  s.leave(8)
  assert.deepEqual(s.visible(), ['Alert', 'Flood 5', 'Flood 4', 'Flood 3', 'Flood 2'])
  assert.deepEqual(s.queue(), ['Flood 1'])
})

test('a normal card that waited behind five criticals (restored) takes the reserved slot at the bottom', () => {
  const s = stack(5)
  s.rows = [row(6, 'Waited'), ...[5, 4, 3, 2, 1].map(i => row(i, 'Flood ' + i, 2))]
  s.shown = {'1001-1': true, '1002-2': true, '1003-3': true, '1004-4': true, '1005-5': true}; s.waiting = {'1006-6': true}
  s.rebuild()
  assert.deepEqual(s.visible(), ['Flood 5', 'Flood 4', 'Flood 3', 'Flood 2', 'Waited'])
  assert.deepEqual(s.queue(), ['Flood 1'])
})

test('with a limit of 1 the critical wins', () => {
  const s = stack(1)
  s.arrive(row(1, 'Normal'))
  s.arrive(row(2, 'Alert', 2))
  assert.deepEqual(s.visible(), ['Alert'])
  assert.deepEqual(s.queue(), ['Normal'])
})

test('with no normal card criticals fill every slot', () => {
  const s = stack(3)
  s.arrive(row(1, 'Normal'))
  for (const i of [2, 3, 4]) s.arrive(row(i, 'Alert ' + i, 2))
  assert.deepEqual(s.visible(), ['Alert 4', 'Alert 3', 'Normal'])
  assert.deepEqual(s.queue(), ['Alert 2'])
  s.leave(1)
  assert.deepEqual(s.visible(), ['Alert 4', 'Alert 3', 'Alert 2'])
  assert.deepEqual(s.queue(), [])
  // A held normal card cannot take the slot, so it does not keep one free.
  s.hover(); s.arrive(row(5, 'Held'))
  assert.deepEqual(s.visible(), ['Alert 4', 'Alert 3', 'Alert 2'])
  assert.deepEqual(s.queue(), ['Held'])
})

test('a pinned critical counts toward the criticals\' slots; a pinned normal card fills the reserved one', () => {
  const s = stack(3)
  for (const i of [1, 2, 3]) s.arrive(row(i, 'Alert ' + i, 2))
  s.pinned.add('1001-1')
  s.arrive(row(4, 'Normal'))
  assert.deepEqual(s.visible(), ['Normal', 'Alert 3', 'Alert 1'])
  assert.deepEqual(s.queue(), ['Alert 2'])
  // The normal card is pinned: it is the reserved slot, and a new critical rotates among the others.
  s.pinned.delete('1001-1'); s.pinned.add('1004-4')
  s.arrive(row(5, 'Alert 5', 2))
  assert.deepEqual(s.visible(), ['Alert 5', 'Normal', 'Alert 3'])
  assert.deepEqual(s.queue(), ['Alert 2', 'Alert 1'])
})

test('waiting criticals show newest first, and join the bottom above the cards that waited behind them', () => {
  const s = stack(1)
  s.arrive(row(1, 'Normal'))
  s.arrive(row(2, 'X', 2)); s.arrive(row(3, 'Y', 2)); s.arrive(row(4, 'Z', 2))
  assert.deepEqual(s.visible(), ['Z'])
  assert.deepEqual(s.queue(), ['Y', 'X', 'Normal'], 'the queue lists what shows next first')
  s.leave(4)
  assert.deepEqual(s.visible(), ['Y'])
  assert.deepEqual(s.queue(), ['X', 'Normal'])
  // Room for all: the cards from the queue join the bottom in queue order.
  const wide = Logic.planStack(Preferences.groups(s.rows, true, 'browser', {browser: () => false}),
    {limit: 3, shown: s.shown, waiting: s.waiting, ranks: s.ranks, since: s.since, top: s.top, bottom: s.bottom, seq: s.seq})
  assert.deepEqual(wide.visible.map(group => group.summary), ['Y', 'X', 'Normal'])
  assert.deepEqual(wide.waiting, [])
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

test('while hovered, new arrivals wait even with room, criticals too, and show on top when the pointer leaves', () => {
  const s = stack(5)
  s.arrive(row(1, 'Below')); s.arrive(row(2, 'Hovered'))
  s.pinned.add('1002-2'); s.hover()
  s.arrive(row(3, 'New')); s.arrive(row(4, 'Alert', 2))
  assert.deepEqual(s.visible(), ['Hovered', 'Below'], 'nothing enters the stack under the pointer')
  assert.deepEqual(s.queue(), ['Alert', 'New'])
  assert.deepEqual(Object.keys(s.held).sort(), ['1003-3', '1004-4'])
  // The pointer leaves: they show as the new arrivals they are, newest on top.
  s.pinned.delete('1002-2'); s.unhover()
  assert.deepEqual(s.visible(), ['Alert', 'New', 'Hovered', 'Below'])
  assert.deepEqual(s.queue(), [])
  assert.deepEqual(s.held, {})
})

test('held cards released into a full stack follow the priority rules; the rest join the queue', () => {
  const s = stack(2)
  s.arrive(row(1, 'A')); s.arrive(row(2, 'B'))
  s.pinned.add('1002-2'); s.hover()
  s.arrive(row(3, 'Normal')); s.arrive(row(4, 'Alert', 2))
  assert.deepEqual(s.visible(), ['B', 'A'])
  // The hold's maximum lets them go while the pointer stays: the critical takes
  // the slot of the unpinned normal card, on top; the normal one waits.
  s.release()
  assert.deepEqual(s.visible(), ['Alert', 'B'])
  assert.deepEqual(s.queue(), ['A', 'Normal'])
  assert.deepEqual(s.held, {}, 'released cards are not held again')
  // Arrivals after the release are held again.
  s.arrive(row(5, 'Later'))
  assert.deepEqual(Object.keys(s.held), ['1005-5'])
  assert.deepEqual(s.queue(), ['A', 'Normal', 'Later'])
  // A freed slot while hovered goes to the queue's head, at the bottom; the held card waits.
  s.leave(4)
  assert.deepEqual(s.visible(), ['B', 'A'])
  assert.deepEqual(s.queue(), ['Normal', 'Later'])
  s.leave(1)
  assert.deepEqual(s.visible(), ['B', 'Normal'], 'a card that waited before joins the bottom')
  assert.deepEqual(s.queue(), ['Later'])
  // Released into a full stack, it waits like any other card and joins the bottom.
  s.pinned.delete('1002-2'); s.unhover()
  assert.deepEqual(s.queue(), ['Later'])
  s.leave(2)
  assert.deepEqual(s.visible(), ['Normal', 'Later'])
})

test('while hovered, updates to cards on screen and history replays are not held', () => {
  const replays = new Set(['900-9'])
  const s = stack(3, {replays})
  s.arrive(row(1, 'Shown')); s.hover()
  // A duplicate of a card on screen merges into it.
  s.arrive(row(2, 'Shown'))
  assert.deepEqual(s.visible(), ['Shown'])
  assert.deepEqual(s.plan.visible[0].keys, ['1002-2', '1001-1'])
  // A replacement under a new key keeps the replaced card's place (Service.inheritPlace).
  s.rows = s.rows.filter(r => r.id !== 1 && r.id !== 2)
  s.shown['2000-1'] = true; s.ranks['2000-1'] = s.ranks['1001-1']
  s.arrive(row(1, 'Shown, edited', 1, 2000))
  assert.deepEqual(s.visible(), ['Shown, edited'])
  assert.deepEqual(s.held, {})
  // A replay shows now or not at all.
  s.arrive(row(9, 'Replay', 1, 900))
  assert.deepEqual(s.visible(), ['Replay', 'Shown, edited'])
  // A held card's replacement stays held (Service.inheritPlace carries it).
  s.arrive(row(3, 'Held'))
  assert.deepEqual(s.queue(), ['Held'])
  s.rows = s.rows.filter(r => r.id !== 3)
  s.waiting['3000-3'] = true; s.held['3000-3'] = true; s.arrivals['3000-3'] = 1003
  s.arrive(row(3, 'Held, edited', 1, 3000))
  assert.deepEqual(s.queue(), ['Held, edited'])
  assert.deepEqual(s.visible(), ['Replay', 'Shown, edited'])
  s.unhover()
  assert.deepEqual(s.visible(), ['Held, edited', 'Replay', 'Shown, edited'])
})

test('cards restored waiting are not held: they keep their turn in the queue', () => {
  const s = stack(1)
  s.rows = [row(2, 'Restored queued'), row(1, 'Shown')]
  s.shown = {'1001-1': true}; s.waiting = {'1002-2': true}
  s.hover()
  assert.deepEqual(s.held, {})
  s.leave(1)
  assert.deepEqual(s.visible(), ['Restored queued'])
})
