const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('static/js/app.js','utf8').split('// All videos on this page')[1].split('/* Ads load only')[0];
const handlers={},buttons=[];
class Video {
 constructor(id){this.dataset={videoSrc:'/media/'+id+'?q=480'};this.paused=true;this.events={};this.classList={add(){}};this.loads=0;this.poster='';}
 before(){} closest(){return{classList:{add(){}}};} addEventListener(name,fn){this.events[name]=fn;}
 pause(){this.paused=true;} getAttribute(name){return this[name]||null;} canPlayType(){return '';}
 load(){this.loads++;} play(){this.paused=false;handlers.play({target:this});return Promise.resolve();}
}
const videos=[new Video('a'),new Video('b')];
vm.runInNewContext('//'+source,{document:{querySelectorAll:()=>videos,addEventListener(k,f){handlers[k]=f;},createElement(tag){const n={append(){},addEventListener(k,f){this[k]=f;}};if(tag==='button')buttons.push(n);return n;}},HTMLVideoElement:Video,URL,location:{href:'http://localhost/'}});
assert.deepEqual(buttons.map(x=>x.textContent),['Play Video','Play Video']);
assert.equal(videos[0].src,undefined);
buttons[0].click();assert.equal(videos[0].paused,false);
buttons[1].click();assert.equal(videos[0].paused,true);assert.equal(videos[1].paused,false);
assert.equal(buttons[0].textContent,'Play Video');assert.equal(buttons[0].disabled,false);
// Late playing event from the old buffered source cannot take ownership back.
videos[0].paused=false;handlers.playing({target:videos[0]});videos[0].events.playing();
assert.equal(videos[0].paused,true);assert.notEqual(buttons[0].hidden,true);
buttons[0].click();assert.equal(videos[1].paused,true);assert.equal(videos[0].paused,false);
assert.equal(videos[0].loads,1,'resume existing source without downloading again');
assert.equal(new URL(videos[0].src).searchParams.get('q'),'480');
console.log('Exclusive video playback and resume: passed');
