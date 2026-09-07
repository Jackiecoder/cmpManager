const test = require('node:test');
const assert = require('node:assert/strict');
require('../static/timeline.js');
const {notesForProject, sectionColors} = globalThis.CmpTimeline;

test('timeline excludes opted-out notes, retains legacy notes and orders by event date', () => {
  const records = [
    {id:'a',kind:'notes',project_id:'p',date:'2026-09-06',created_at:'2026-09-08',subsection_id:'s'},
    {id:'b',kind:'notes',project_id:'p',date:'2026-09-07',created_at:'2026-09-07',show_on_timeline:true},
    {id:'c',kind:'notes',project_id:'p',date:'2026-09-09',show_on_timeline:false},
    {id:'d',kind:'notes',project_id:'other',date:'2026-09-10'},
  ];
  assert.deepEqual(notesForProject(records,'p').map(n=>n.id),['b','a']);
  assert.deepEqual(notesForProject(records,'p','s').map(n=>n.id),['a']);
  assert.deepEqual(notesForProject(records,'p','unassigned').map(n=>n.id),['b']);
  assert.equal(records[0].id,'a');
});

test('section colors are distinct and stable after renaming, reordering and appending', () => {
  const sections = Array.from({length:20},(_,i)=>({id:'s'+i,kind:'subsections',project_id:'p',created_at:`2026-09-${String(i+1).padStart(2,'0')}`,title:'Section '+i}));
  const colors = sectionColors(sections,'p');
  assert.equal(new Set(Object.values(colors)).size,21);
  assert.deepEqual(sectionColors(sections.toReversed().map(s=>({...s,title:'Renamed'})),'p'),colors);
  const extended = sectionColors([...sections,{id:'new',kind:'subsections',project_id:'p',created_at:'2026-10-01'}],'p');
  for (const id of Object.keys(colors)) assert.equal(extended[id],colors[id]);
});
