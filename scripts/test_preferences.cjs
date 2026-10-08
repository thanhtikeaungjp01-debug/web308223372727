/* Exercise Telegram version/gesture/event boundaries without browser packages. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../static/js/preferences.js'), 'utf8');
function fixture({ telegram = false, supported = true, saved = 'small', blockedStorage = false, fullscreen = false } = {}) {
  const button = (dataset) => ({ dataset, attrs: {}, setAttribute(k,v) { this.attrs[k] = v; }, addEventListener(k,fn) { this[k] = fn; } });
  const sizes = ['small','large'].map(appSize => button({appSize}));
  const themes = ['dark','light','blue'].map(theme => button({theme}));
  const root = { dataset: { theme:'dark', appSize:saved } };
  const status = { textContent:'' };
  const calls = [], events = {}, storage = {};
  const tg = { initData: telegram ? 'synthetic' : '', isFullscreen: fullscreen,
    isVersionAtLeast: () => supported,
    expand: () => calls.push('expand'),
    requestFullscreen: () => calls.push('request'), exitFullscreen: () => calls.push('exit'),
    setHeaderColor: () => {}, setBackgroundColor: () => {},
    onEvent: (name,fn) => { events[name] = fn; } };
  vm.runInNewContext(source, {
    window:{Telegram:{WebApp:tg}},
    document:{documentElement:root, querySelectorAll:sel => sel === '.theme-choice' ? themes : sizes,
      getElementById:() => status, querySelector:()=>null},
    localStorage:{setItem:(k,v)=>{if(blockedStorage) throw new Error('blocked');storage[k]=v;}},
  });
  return { root, status, calls, events, storage, tg, sizes, themes };
}
let f = fixture();
f.sizes[1].click(); assert.equal(f.root.dataset.appSize,'large'); assert.deepEqual(f.calls,[]);
f.themes[1].click(); assert.equal(f.root.dataset.theme,'light'); assert.equal(f.themes[1].attrs['aria-pressed'],'true');
assert.equal(f.themes[0].attrs['aria-pressed'],'false'); assert.equal(f.storage['waifu-theme'],'light');
f = fixture({telegram:true, saved:'large'});
assert.deepEqual(f.calls,['expand']); // Reload must not request fullscreen without a gesture.
f.sizes[1].click(); assert.deepEqual(f.calls,['expand','expand','request']);
f.tg.isFullscreen=true; f.events.fullscreenChanged(); assert.equal(f.sizes[1].attrs['aria-pressed'],'true');
f.sizes[0].click(); assert.equal(f.calls.at(-1),'exit');
f.tg.isFullscreen=false; f.events.fullscreenChanged(); assert.equal(f.storage['waifu-app-size'],'small');
f.events.fullscreenFailed(); assert.match(f.status.textContent,/unavailable/);
f.tg.requestFullscreen=()=>{throw new Error('unsupported device');};
f.sizes[1].click(); assert.match(f.status.textContent,/unavailable/);
f = fixture({telegram:true,supported:false});
f.sizes[1].click(); f.sizes[0].click(); assert.deepEqual(f.calls,['expand']); assert.match(f.status.textContent,/Swipe down/);
f = fixture({blockedStorage:true}); f.sizes[1].click(); f.themes[2].click(); assert.equal(f.root.dataset.theme,'blue');
f = fixture({telegram:true,fullscreen:true}); assert.equal(f.root.dataset.appSize,'large');
console.log('Display preferences: browser, persistence, blocked storage, Telegram fullscreen/exit/failure and older clients passed.');
