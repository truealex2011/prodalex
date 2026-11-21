// Runs in the listing tab. A recommendation or a cached search snippet is not proof.
export const inactiveText=/(?:объявление|предложение)\s+(?:уже\s+)?(?:снято|удалено|закрыто|неактивно|в архиве|больше не актуально)|снят[оа]?\s+с\s+(?:продажи|публикации)|(?:автомобиль|машина|авто)\s+(?:уже\s+)?продан[ао]?|продавец\s+(?:снял|удалил)\s+объявление|(?:^|\n)\s*(?:продано?|в архиве|архивное объявление)\s*(?:$|\n)/i;
export function activeListing({expected,inactive}){
  const identity=value=>{try{const u=new URL(value,location.href);return u.origin+u.pathname.replace(/\/$/,'');}catch{return '';}};
  if(identity(location.href)!==identity(expected)||!document.querySelector('h1'))return false;
  const canonical=document.querySelector('link[rel="canonical"]')?.href;
  if(canonical && identity(canonical)!==identity(expected))return false;
  const visible=(document.body?.innerText||'').split(/похожие объявления|другие объявления продавца|смотрите также/i)[0];
  if(new RegExp(inactive,'i').test(visible))return false;
  const states=[];
  for(const script of document.querySelectorAll('script[type="application/ld+json"]')){
    let doc;try{doc=JSON.parse(script.textContent)}catch{continue}
    for(const obj of [doc].flat().flatMap(x=>x?.['@graph']||[x])){
      if(!obj)continue;
      const types=[obj['@type']].flat();
      const offers=types.includes('Offer')?[obj]:types.some(x=>['Car','Vehicle','Product'].includes(x))?[obj.offers].flat():[];
      for(const offer of offers){const url=offer?.url||obj.url||obj['@id'];if(!url||identity(url)!==identity(expected)||!offer?.availability)continue;
        states.push(String(offer.availability).split('/').pop());}
    }
  }
  return states.length>0 && states.every(state=>state==='InStock');
}
