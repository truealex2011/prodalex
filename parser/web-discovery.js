// Only visit car listings on supported marketplaces, never arbitrary search-result URLs.
function listing(href){
  try {
    let u=new URL(href);
    if(u.hostname.endsWith('.bing.com') && u.pathname==='/ck/a'){
      const dest=u.searchParams.get('u');if(!dest?.startsWith('a1'))return;
      u=new URL(Buffer.from(dest.slice(2),'base64url').toString());
    } else if(u.hostname.endsWith('duckduckgo.com') && u.searchParams.has('uddg'))u=new URL(u.searchParams.get('uddg'));
    if(u.protocol!=='https:' || u.port || u.username || u.password)return;
    let source;
    if(u.hostname==='auto.drom.ru' && /^\/[\w-]+\/[\w-]+\/[\w-]+\/\d+\.html$/.test(u.pathname))source='drom';
    if(u.hostname==='auto.ru' && /^\/cars\/(?:used|new)\/sale\/[\w-]+\/[\w-]+\/[\w-]+\/?$/.test(u.pathname))source='autoru';
    if(['www.avito.ru','avito.ru'].includes(u.hostname) && /^\/[^/]+\/avtomobili\/[^/]+_\d+\/?$/.test(u.pathname))source='avito';
    if(source)return {source,link:u.origin+u.pathname,fromWeb:true};
  }catch{}
}
export async function webListings(context,query){
  const q=query+' автомобиль объявление (site:auto.drom.ru OR site:auto.ru OR site:avito.ru)';
  const search=['https://www.bing.com/search?q=','https://html.duckduckgo.com/html/?q='];
  const lists=await Promise.allSettled(search.map(async base=>{
    const page=await context.newPage();try{
      await page.goto(base+encodeURIComponent(q),{waitUntil:'domcontentloaded',timeout:9000});
      const urls=await page.locator('h2 a,.result__a').evaluateAll(a=>a.map(x=>x.href));
      return urls.map(listing).filter(Boolean);
    }finally{await page.close();}
  }));return lists.flatMap(x=>x.status==='fulfilled'?x.value:[]).slice(0,12);
}
