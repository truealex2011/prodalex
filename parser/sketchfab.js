import {assetPaths,registerModel,savedMatch} from './glb-store.js';
const normal=s=>String(s||'').normalize('NFKD').replace(/\p{M}/gu,'').toLowerCase().replace(/[^\p{L}\p{N}]+/gu,' ').trim();
const bodies=[['convertible','cabriolet','roadster','кабриолет','родстер'],['coupe','купе'],['hatchback','хэтчбек'],['sedan','saloon','седан'],['wagon','estate','универсал'],['pickup','пикап']];
const body=s=>bodies.find(group=>group.some(word=>(' '+normal(s)+' ').includes(' '+word+' ')));
const parts=/\b(?:wheels?|rims?|brakes?|chassis|engines?|bumpers?|headlights?|steering|dashboard|seats?|bodykit|pack|collection|interior only)\b|(?:двигатель|бампер|диски|салон отдельно)/i;

export function assetIdentity(c){
  let name=c.vehicleName;
  if(!name && c.source==='drom'){
    const parts=new URL(c.link).pathname.split('/').filter(Boolean);
    name=parts.slice(-3,-1).join(' ').replace(/_/g,' ');
  }
  name ||= c.title.replace(/^Продажа\s+/i,'').split(/,|\s+(?:19|20)\d{2}(?:\s|$)/)[0].trim();
  return {name:name.slice(0,100), year:String(c.facts.year||''), body:String(c.facts.body||'').slice(0,40)};
}

export function bestModel(candidates,query){
  const name=normal(query.name), tokens=name.split(' '), targetBody=body(query.body), year=Number(query.year);
  const choices=[];
  for(const m of candidates){
    const title=normal(m.name), words=title.split(' ');
    if(!/^[a-f\d]{32}$/i.test(m.uid)||m.isAgeRestricted||parts.test(title)||!tokens.every(t=>words.includes(t)))continue;
    const suffix=words.filter(w=>!tokens.includes(w)).join(' ');
    const years=suffix.match(/\b(?:19|20)\d{2}\b/g)?.map(Number)||[];
    const range=m.name.match(/\b((?:19|20)\d{2})\s*[-–—]\s*((?:19|20)\d{2})\b/);
    const yearMatch=year && (years.includes(year)||(range && year>=+range[1] && year<=+range[2]));
    if(year && years.length && !yearMatch)continue;
    const candidateBody=body(suffix);
    if(targetBody && candidateBody && targetBody!==candidateBody)continue;
    const bodyMatch=!!(targetBody && candidateBody===targetBody);
    // Match first, then use bounded quality indicators: polygons alone can favour an unoptimised mesh.
    const quality=Math.min(6,Math.log10(1+(m.faceCount||0)))*2 + Math.min(4,Math.log10(1+(m.likeCount||0))) + (m.staffpickedAt?3:0) + (title===name?3:0) - (/\b(low poly|lowpoly|psx|stylized|cartoon|wreck|tuned|custom|turbo|sport type)\b/.test(suffix)?7:0);
    choices.push({m,score:(yearMatch?100:0)+(bodyMatch?30:0)+quality,yearMatch:!!yearMatch,bodyMatch});
  }
  const top=choices.sort((a,b)=>b.score-a.score)[0];if(!top)return null;
  const {m,yearMatch,bodyMatch}=top;
  return {uid:m.uid,title:m.name,author:m.user?.displayName||m.user?.username||'Sketchfab',license:m.license?.label,licenseURL:m.license?.url,yearMatch,bodyMatch};
}

const cache=new Map();let cooldown=0;
async function candidates(q){
  if(cooldown>Date.now())throw Error('rate limited');
  const key=normal(q),hit=cache.get(key);if(hit?.until>Date.now())return hit.promise;
  const promise=(async()=>{
    const u=new URL('https://api.sketchfab.com/v3/search');u.search=new URLSearchParams({type:'models',q,count:'48',downloadable:'true',restricted:'false'});
    const r=await fetch(u,{signal:AbortSignal.timeout(8000)});
    if(r.status===429)cooldown=Date.now()+Math.max(60,Number(r.headers.get('retry-after'))||0)*1000;
    if(!r.ok)throw Error('Sketchfab '+r.status);const j=await r.json();if(!Array.isArray(j.results))throw Error('Invalid response');return j.results;
  })();
  if(cache.size>=256)cache.delete(cache.keys().next().value);cache.set(key,{until:Date.now()+3600000,promise});
  try{return await promise;}catch(e){cache.delete(key);throw e;}
}
export async function findModel(query){
  const local=await savedMatch(query);if(local)return local;
  const queries=[query.name];if(query.year)queries.push(query.name+' '+query.year);
  const [paths,responses]=await Promise.all([assetPaths().catch(()=>({})),Promise.allSettled(queries.map(candidates))]);
  if(responses.every(r=>r.status==='rejected'))throw Error('Sketchfab недоступен');
  let pool=responses.flatMap(r=>r.status==='fulfilled'?r.value:[]).filter(m=>m.isDownloadable&&(/^(?:CC|Free Standard)/i.test(m.license?.label||'')));
  while(pool.length){const selected=bestModel(pool,query);if(!selected)return null;
    const r=await fetch('https://api.sketchfab.com/v3/models/'+selected.uid,{signal:AbortSignal.timeout(8000)});if(!r.ok)throw Error('Sketchfab недоступен');const m=await r.json();
    if(m.isDownloadable && /^https?:\/\/creativecommons\.org\/(?:licenses|publicdomain)\//.test(m.license?.url||'')){
      const modelPath=paths[selected.uid];
      if(!modelPath) {
        // Search results can be downloadable even when the local Objaverse index
        // does not contain the UID. The model is still useful; glb-store will
        // register an archive path when the public archive is available.
        if(!m.archives?.glb?.url)return null;
        Object.assign(selected,{query,licenseURL:m.license.url,license:m.license.label,fileURL:'/models/'+selected.uid+'.glb',archiveURL:m.archives.glb.url});registerModel(selected,m.archives.glb.url);return selected;
      }
      Object.assign(selected,{query,licenseURL:m.license.url,license:m.license.label,fileURL:'/models/'+selected.uid+'.glb'});registerModel(selected,modelPath);return selected;
    }pool=pool.filter(m=>m.uid!==selected.uid);
  }return null;
}
