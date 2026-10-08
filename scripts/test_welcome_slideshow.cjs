/* Verify automatic carousel timing without browser packages. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../static/js/mini-home.js'), 'utf8');
function fixture(count, reduced = false) {
  const target = () => ({events:{}, attrs:{}, addEventListener(k,fn){(this.events[k] ||= []).push(fn);},
    emit(k,event={}){for(const fn of this.events[k]||[]) fn(event);},
    setAttribute(k,v){this.attrs[k]=v;}});
  let now=0, id=0; const timers=new Map(), moves=[];
  const dots=Array.from({length:count},target);
  const track=Object.assign(target(),{clientWidth:320,scrollLeft:0,
    children:Array.from({length:count},()=>({image:{loading:'lazy'},querySelector(){return this.image;}})),
    scrollTo(options){moves.push(options);this.scrollLeft=options.left;}});
  const document=Object.assign(target(),{hidden:false,getElementById:()=>track,querySelectorAll:()=>dots});
  const window=target();
  vm.runInNewContext(source,{document,window,matchMedia:()=>({matches:reduced}),
    setTimeout:(fn,delay)=>{timers.set(++id,{at:now+delay,fn});return id;},
    clearTimeout:key=>timers.delete(key)});
  const advance = ms => {
    const until=now+ms;
    while(true){const next=[...timers].sort((a,b)=>a[1].at-b[1].at)[0];
      if(!next || next[1].at>until) break;
      now=next[1].at;timers.delete(next[0]);next[1].fn();}
    now=until;
  };
  return {track,dots,document,window,timers,moves,advance};
}
for(const count of [2,5]) {
  const f=fixture(count);
  f.advance(2999);assert.equal(f.track.scrollLeft,0);
  f.advance(1);assert.equal(f.track.scrollLeft,320);assert.equal(f.moves.at(-1).behavior,'smooth');
  for(let i=2;i<=count;i++){f.advance(3000);assert.equal(f.track.scrollLeft,(i%count)*320);}
  assert.equal(f.dots[0].attrs['data-active'],'true');
  assert.equal(f.track.children[0].tabIndex,0);assert.equal(f.track.children[1].tabIndex,-1);
  assert.deepEqual(Object.keys(f.track.events),[]);
  assert.ok(f.dots.every(dot=>!Object.keys(dot.events).length));
}
let f=fixture(1);f.advance(30000);assert.equal(f.moves.length,0);assert.equal(f.timers.size,0);
f=fixture(5);f.document.hidden=true;f.document.emit('visibilitychange');f.advance(9000);assert.equal(f.track.scrollLeft,0);
f.document.hidden=false;f.document.emit('visibilitychange');f.advance(3000);assert.equal(f.track.scrollLeft,320);
f=fixture(2,true);f.advance(3000);assert.equal(f.track.scrollLeft,320);assert.equal(f.moves.at(-1).behavior,'instant');
console.log('Welcome slideshow: exact 3-second timing, 2/5-photo loops, single photo, automatic-only controls, visibility and reduced motion passed.');
