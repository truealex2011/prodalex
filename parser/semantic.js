import {pipeline,env} from '@huggingface/transformers';
import {homedir} from 'node:os';
import {join} from 'node:path';
import {readFile} from 'node:fs/promises';
import {plan} from './planner.js';
import {requestedBody} from './body-types.js';

env.cacheDir=join(homedir(),'.cache','avtoradar','models');
let encoder,index;
export async function embed(texts){
  encoder??=pipeline('feature-extraction','Xenova/multilingual-e5-small',{dtype:'q8',session_options:{intraOpNumThreads:3,interOpNumThreads:1}}).catch(e=>{encoder=null;throw e});
  const p=await encoder;
  const out=await p(texts,{pooling:'mean',normalize:true});
  return out.data;
}
export const docsFile=new URL('./catalog/models.json',import.meta.url);
export function slug(s){return s.toLowerCase().replace(/[^a-z0-9-]+/g,'_').replace(/^_|_$/g,'');}
export async function loadIndex(){
  if(!index) index=(async()=>{
    const {models,vectors}=JSON.parse(await readFile(docsFile,'utf8'));
    const bytes=Buffer.from(vectors,'base64');
    if(bytes.length!==models.length*384) throw Error('Неполный индекс моделей');
    const counts=await readFile(new URL('./catalog/market.json',import.meta.url),'utf8').then(s=>JSON.parse(s).counts).catch(()=>({}));
    const tags=models.map(m=>new Set(english(m.description)));
    return {models,counts,tags,vec:new Int8Array(bytes.buffer,bytes.byteOffset,bytes.length)};
  })().catch(e=>{index=null;throw e});return index;
}
const stop=new Set('авто автомобиль автомобили машина машины нужен нужна хочу чтобы ищу для мне такая такой просто рублей лучше можно который'.split(' '));
const keywords=s=>(s.toLowerCase().replaceAll('ё','е').match(/[а-яa-z0-9-]{3,}/g)||[]).filter(w=>!stop.has(w)).map(w=>w.length>5?w.slice(0,-2):w);
async function webMatches(query){
  const u=new URL('https://ru.wikipedia.org/w/api.php');
  u.search=new URLSearchParams({action:'query',list:'search',srsearch:query,srlimit:'35',format:'json'});
  const response=await fetch(u,{signal:AbortSignal.timeout(5000)});if(!response.ok)return [];
  const data=await response.json();return data.query?.search||[];
}
async function translation(query){
  if(!/[а-яё]/i.test(query))return query;
  const u=new URL('https://api.mymemory.translated.net/get');u.search=new URLSearchParams({q:query.slice(0,240),langpair:'ru|en'});
  const r=await fetch(u,{signal:AbortSignal.timeout(4500)});if(!r.ok)return '';
  const j=await r.json();return Number(j.responseStatus)===200?String(j.responseData?.translatedText||'').slice(0,320):'';
}
const commonEn=new Set('the and for that with this have from want some would like where about there need when only into than looking vehicles vehicle cars automobile car drive drives model models summer used driving during should which suitable dream dreams dreaming prefer buy buying wish'.split(' '));
const english=text=>[...new Set((text.toLowerCase().match(/[a-z]{3,}/g)||[]).filter(w=>!commonEn.has(w)).map(w=>w.replace(/(?:ies)$/,'y').replace(/s$/,'')))];
export async function discover(query){
  const [{models,counts,tags:tagSets,vec},interpretation,web]=await Promise.all([loadIndex(),plan(query),webMatches(query).catch(()=>[])]);
  const openAir=requestedBody(query)==='kabriolet';
  const expanded=[openAir?'convertible cabriolet roadster folding roof':interpretation?.terms,await translation(query).catch(()=>'')].filter(Boolean).join('; ');
  const suggested=new Set((interpretation?.models||[]).map(slug));
  const embedding=await embed(['query: '+query.slice(0,500)+(expanded?' — '+expanded:'')]).catch(()=>null);
  const online=new Set(web.map(x=>x.title.toLowerCase())), terms=[...new Set(keywords(query))];
  const en=english(expanded);
  const df=en.map(t=>tagSets.reduce((n,set)=>n+Number(set.has(t)),0));
  const scored=models.map((m,i)=>{
    let similarity=0;if(embedding)for(let j=0;j<384;j++)similarity+=vec[i*384+j]*embedding[j]/127;
    const text=(m.title+' '+m.description).toLowerCase();
    const overlap=terms.filter(t=>text.includes(t)).length/Math.max(1,terms.length);
    const prior=suggested.has(slug(m.title)), tags=Math.min(10,en.reduce((s,t,j)=>s+(tagSets[i].has(t)?Math.log(1+models.length/(1+df[j])):0),0));
    return {...m,similarity,score:similarity+0.07*overlap+0.025*tags+(prior?0.10:0)+(online.has(m.title.toLowerCase())?0.07:0), available:!!counts[m.path],eligible:prior||tags>0||(embedding && similarity>=0.75)||overlap>=0.5||online.has(m.title.toLowerCase())};
  }).filter(m=>m.eligible && (!openAir || /convertibl|cabriolet|roadster|кабриолет|родстер/i.test(m.description+' '+m.title))).sort((a,b)=>b.score-a.score);
  return {models:scored,total:models.length,mode:embedding?'semantic':'text',expanded};
}

export async function scoreListings(query,items){
  if(!items.length)return;
  const q=await embed(['query: '+query.slice(0,500)]);
  for(let i=0;i<items.length;i+=8){
    const batch=items.slice(i,i+8);
    const v=await embed(batch.map(c=>'passage: '+[c.title,...Object.values(c.facts),c.description?.slice(0,500),c.profile?.description?.slice(0,300)].filter(Boolean).join('. ')));
    batch.forEach((item,k)=>{let s=0;for(let j=0;j<384;j++)s+=q[j]*v[k*384+j];item.listingScore=s});
  }
}
