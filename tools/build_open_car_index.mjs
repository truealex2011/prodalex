// Read Objaverse's published metadata, not Sketchfab's viewer internals.
// Keep small, attributable GLB candidates. No meshes downloaded by this command.
import {gunzipSync} from 'node:zlib';
import {readFile,writeFile,rename} from 'node:fs/promises';
const base='https://huggingface.co/datasets/allenai/objaverse/resolve/main/';
async function get(url){for(let i=0;;i++){try{const r=await fetch(url,{signal:AbortSignal.timeout(60000)});if(!r.ok)throw Error(String(r.status));return Buffer.from(await r.arrayBuffer());}catch(e){if(i===2)throw e;}}}
const paths=JSON.parse(gunzipSync(await get(base+'object-paths.json.gz')));
const shards=[...new Set(Object.values(paths).map(p=>p.split('/')[1]))].sort();
const counts=JSON.parse(await readFile(new URL('../parser/catalog/market.json',import.meta.url))).counts;
const makes=new Set([...Object.keys(counts).map(k=>k.split('/')[0]),'daewoo','moskvich','уаз','лада','ваз','bmw','ford']);
let pos=0;const rows=[];
async function worker(){while(pos<shards.length){const id=shards[pos++];const meta=JSON.parse(gunzipSync(await get(base+`metadata/${id}.json.gz`)));
 for(const m of Object.values(meta)){if(!paths[m.uid]||!['by','by-sa','by-nc','by-nc-sa','cc0'].includes(m.license)||m.isAgeRestricted||!m.name||!m.user?.displayName)continue;
 const words=m.name.toLowerCase().split(/[^\p{L}\p{N}-]+/u);if(!words.some(w=>makes.has(w)) && !m.categories?.some(c=>c.name==='cars-vehicles'))continue;
 rows.push([m.uid,m.name,m.user.displayName,m.license,m.faceCount||0,m.likeCount||0,paths[m.uid]]);
 }}}
await Promise.all(Array.from({length:4},worker));const target=new URL('../data/catalog/open_car_index.json',import.meta.url);
const tmp=new URL(target.href+'.tmp');await writeFile(tmp,JSON.stringify(rows.sort((a,b)=>a[0].localeCompare(b[0]))));await rename(tmp,target);console.log('Indexed GLB assets:',rows.length);
