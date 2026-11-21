// Match the whole public passenger-car sitemap, then verify availability in each offer.
import {inactiveText} from './availability.js';
let inventory,until=0;
const key=s=>s.toLowerCase().replace(/[^a-z0-9]+/g,'');
async function stock(){
  if(inventory && Date.now()<until)return inventory;
  until=Date.now()+15*60000;
  inventory=(async()=>{const r=await fetch('https://freshauto.ru/sitemap-cars.xml',{signal:AbortSignal.timeout(15000)});if(!r.ok)throw Error('FRESH '+r.status);const xml=await r.text(), map=new Map();let total=0;
    for(const m of xml.matchAll(/<loc>(https:\/\/freshauto\.ru\/cars\/([a-z0-9-]+)-\d+\/)<\/loc>/g)){const k=key(m[2].replace(/^lada-vaz-/,'lada-'));if(!map.has(k))map.set(k,[]);map.get(k).push(m[1]);total++;}return {map,total};
  })().catch(e=>{inventory=null;throw e});return inventory;
}
export async function freshCards(discovery,model,domestic,body){
  const {map,total}=await stock();let matches=[];
  if(model){const urls=map.get(key(model.join(' ')));if(urls)matches.push({urls});}
  else if(domestic){matches=[...map].filter(([k])=>/^(lada|gaz|uaz|moskvich|aurus|sollers|evolute)/.test(k)).map(([,urls])=>({urls}));}
  else if(discovery?.models?.length){
    for(const profile of discovery.models){const urls=map.get(key(profile.path));if(urls && profile.score>=discovery.models[0].score-0.13)matches.push({profile,urls});if(matches.length>=16)break;}
  }
  const items=[];
  if(body && !model){try{const r=await fetch(`https://freshauto.ru/cars/body-${body}/`,{signal:AbortSignal.timeout(10000)});if(r.ok){const html=await r.text();const seen=new Set();for(const m of html.matchAll(/href="(?:https:\/\/freshauto.ru)?(\/cars\/([\w-]+)-\d+\/)"/g)){if(seen.has(m[1]))continue;seen.add(m[1]);items.push({source:'fresh',link:'https://freshauto.ru'+m[1],profile:discovery?.models.find(p=>key(p.path)===key(m[2]))});if(items.length>=20)break;}}}catch{}}
  for(let i=0;i<8;i++)for(const m of matches){if(m.urls[i] && items.length<32)items.push({link:m.urls[i],source:'fresh',profile:m.profile});}
  return {name:'fresh',items,total};
}
export async function freshDetail(item){
  const r=await fetch(item.link,{cache:'no-store',signal:AbortSignal.timeout(12000)});if(!r.ok||r.url!==item.link)return null;
  const html=await r.text();
  const visible=html.replace(/<(script|style)\b[^>]*>[\s\S]*?<\/\1>/gi,'').replace(/<[^>]+>/g,'\n').replace(/&nbsp;/g,' ').split(/похожие объявления|похожие автомобили/i)[0];
  if(inactiveText.test(visible))return null;
  for(const m of html.matchAll(/<script[^>]*type="application\/ld\+json"[^>]*>([\s\S]*?)<\/script>/gi)){
    let car;try{car=JSON.parse(m[1])}catch{continue}
    const offer=car.offers;if(![car['@type']].flat().includes('Car')||offer?.url!==item.link||!String(offer.availability).endsWith('/InStock')||offer.priceCurrency!=='RUB')continue;
    const images=[car.image].flat().filter(u=>typeof u==='string'&&/^https:\/\/[\w-]+\.freshauto\.ru\//.test(u));if(!images.length)continue;
    const eng=car.vehicleEngine, cc=eng?.engineDisplacement?.value;
    const facts={year:car.vehicleModelDate,mileage:car.mileageFromOdometer?.value,body:car.bodyType,gearbox:car.vehicleTransmission,engine:cc?cc+' л':'',power:eng?.enginePower?.value,drive:car.driveWheelConfiguration,fuel:eng?.fuelType,color:car.color};
    const title=[car.brand?.name,car.model,car.vehicleModelDate].filter(Boolean).join(' ');
    return {...item,title,vehicleName:[car.brand?.name,car.model].filter(Boolean).join(' '),images,price:Number(offer.price),facts,description:String(car.description||'').slice(0,1000),collectedAt:new Date().toISOString()};
  }return null;
}
