import http from 'node:http';
import { URL } from 'node:url';
import { readFile } from 'node:fs/promises';
import { extname, join, normalize } from 'node:path';
import { chromium } from 'playwright';
import {understand,pickTrim,measurements,suitability} from './needs.js';
import {discover,scoreListings} from './semantic.js';
import {webListings} from './web-discovery.js';
import {freshCards,freshDetail} from './fresh.js';
import {activeListing,inactiveText} from './availability.js';
import {assetIdentity,findModel} from './sketchfab.js';
import {localGLB} from './glb-store.js';
import {createReadStream} from 'node:fs';
import {pipeline} from 'node:stream/promises';
import {interview,profile,compareOffers,retrievalText} from './advisor.js';
import {localExplanation} from './planner.js';
import {bodyType,requestedBody} from './body-types.js';

const PORT = Number(process.env.PORT || 3000);
const ROOT = process.cwd();
const MAX_CARDS_PER_SOURCE = 8;
let MAX_CARDS_PER_SOURCE_LOCAL = MAX_CARDS_PER_SOURCE;
const sources = {
  avito: q => `https://www.avito.ru/rossiya/avtomobili?q=${encodeURIComponent(q)}`,
  drom: q => dromSearch(q),
  autoru: q => `https://auto.ru/cars/all/?text=${encodeURIComponent(q)}`
};

const aliases = new Map(Object.entries({део:'daewoo',дэо:'daewoo',дэу:'daewoo',деу:'daewoo',мотиз:'matiz',матиз:'matiz',тойота:'toyota',тойота:'toyota',тойотаа:'toyota',рав4:'rav4',киа:'kia',киаи:'kia',рио:'rio',шкода:'skoda',лада:'lada',мазда:'mazda',форд:'ford',ниссан:'nissan',хонда:'honda'}));
const tokens = text => (text.toLowerCase().replaceAll('ё','е').match(/[а-яa-z0-9-]+/g)||[]).map(w=>aliases.get(w)||w);
const domesticCatalogs = ['lada','uaz','moskvich','gaz'];
const domesticTitle = /^(?:продажа\s+)?(?:новый\s+)?(?:лада|lada|ваз|vaz|уаз|uaz|москвич|moskvich|moskvitch|газ|gaz|иж|izh|тагаз|tagaz|aurus|аурус|sollers|соллерс|evolute|эволют)(?![а-яa-z])/i;
function domesticQuery(q) {
  return !/(?:^|\s)(?:не|кроме|без)\s+(?:российск|отечественн|русск)/i.test(q) && /(?:^|\s)(?:отечественн[а-яё]*|(?:российск|русск)[а-яё]*\s+(?:автопром|авто|машин|марк))/i.test(q);
}
function criteria(q) {
  const parts=tokens(q), normalized=parts.join(' ');
  const pair=normalized.match(/(?:^| )(daewoo|toyota|volkswagen|kia|hyundai|skoda|lada|bmw|audi|ford|nissan|honda|renault|mazda|lexus|mitsubishi) ([a-z][a-z0-9-]*|[0-9]+[a-z][a-z0-9-]*)(?= |$)/);
  const model=pair ? pair.slice(1,3) : parts.includes('matiz') ? ['daewoo','matiz'] : null;
  const limit = q.replace(/до\s*\d+(?:[.,]\d+)?\s*л(?:итр[а-я]*|(?=[\s/]|$))/gi,'').match(/до\s*([\d ]+(?:[.,]\d+)?)\s*(млн|тыс)?/i);
  const amount=limit && Number(limit[1].replaceAll(' ','').replace(',','.')) * ({'млн':1e6,'тыс':1e3}[limit[2]]||1);
  const monetary=limit && (limit[2] || /^(?:₽|руб)/i.test(q.slice(limit.index+limit[0].length).trim()) || (amount>=10000 && !/^(?:км|километр)/i.test(q.slice(limit.index+limit[0].length).trim())));
  return { model, max:monetary?amount:null, year: +(q.match(/(?:от|не старше)\s*(20\d\d)/i)?.[1]||0) };
}
function dromSearch(q, brand) {
  const {model,max,year}=criteria(q);
  const url=new URL(`https://auto.drom.ru/${model ? model.join('/')+'/' : brand ? brand+'/' : ''}`);
  if(max) url.searchParams.set('maxprice',max);
  if(year) url.searchParams.set('minyear',year);
  return url.href;
}

let browserPromise;
const getBrowser = () => browserPromise ||= chromium.launch({ headless: true });
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const clean = value => String(value || '').replace(/\s+/g, ' ').replace(/&nbsp;/g, ' ').trim();
const first = (...values) => values.map(clean).find(Boolean) || '';
const number = value => { const digits = String(value || '').replace(/[^\d]/g, ''); return digits ? Number(digits) : null; };
const absolute = (value, base) => { try { return new URL(value, base).href; } catch { return ''; } };
const cache=new Map(), cooldown=new Map();
function cached(id){const x=cache.get(id);return x && x.until>Date.now()?x.value:null;}
function remember(id,value){if(cache.size>=1200)cache.delete(cache.keys().next().value);cache.set(id,{value,until:Date.now()+15*60_000});return value;}
function available(url){return !(cooldown.get(new URL(url).hostname)>Date.now());}
async function visit(page,url,timeout=18000){
  if(!available(url))throw Error('Источник временно ограничил запросы');
  const res=await page.goto(url,{waitUntil:'domcontentloaded',timeout});
  const code=res?.status();
  if(code===429 || code===403){const retry=res.headers()['retry-after'];const delay=/^\d+$/.test(retry)?Number(retry)*1000:Date.parse(retry)-Date.now();cooldown.set(new URL(url).hostname,Date.now()+Math.max(180000,delay||0));}
  if(!res || code>=400)throw Error('HTTP '+code);
}

function parseFacts(raw) {
  const text = clean(raw);
  const find = patterns => { for (const pattern of patterns) { const found = text.match(pattern); if (found) return clean(found[1] || found[0]); } return ''; };
  return {
    year: find([/\b((?:19|20)\d{2})\s*г?\b/i]),
    mileage: find([/(\d[\d\s]{1,10})\s*(?:км|тыс\. км)/i]),
    engine: find([/(\d(?:[.,]\d)?\s*л(?:\s*\/\s*\d+\s*л\.с\.)?)/i]),
    power: find([/(\d{2,4})\s*л\.с\.?/i]),
    gearbox: find([/(автомат(?:ическая)?|робот|вариатор|механика|мкпп|акпп)/i]),
    drive: find([/(полный привод|передний привод|задний привод|4WD|2WD)/i]),
    body: find([/(кабриолет|родстер|кроссовер|внедорожник|седан|универсал|хэтчбек|лифтбек|купе|минивэн|пикап)/i]),
    fuel: find([/(бензин|дизель|гибрид|электро)/i]),
    vin: find([/\b([A-HJ-NPR-Z0-9]{17})\b/i])
  };
}

async function collectSearchCards(context, source, url) {
  const hit=cached(url);if(hit)return hit;if(!available(url))throw Error('Ограничение источника');
  const page = await context.newPage();
  try {
    await visit(page,url);
    await page.waitForTimeout(2200);
    const result=await page.evaluate(({ source, limit }) => {
      const text = node => (node?.textContent || '').replace(/\s+/g, ' ').trim();
      const image = node => node?.currentSrc || node?.src || node?.getAttribute('data-src') || node?.getAttribute('data-lazy') || '';
      const selectors = {
        avito: '[data-marker="item"], [data-marker="item-link"]',
        drom: '[data-ftid="bulls-list_bulletin"], [data-ftid*="bulls-list"]',
        autoru: '[data-mark="OffersSnippet"], [data-mark*="OfferSnippet"]'
      }[source];
      const nodes = [...document.querySelectorAll(selectors || 'article')];
      const seen = new Set();
      return nodes.map(node => {
        const linkNode = node.querySelector('a[href]') || node.closest('a[href]');
        const link = linkNode?.href || '';
        const isListing = source === 'drom' ? /^https:\/\/auto\.drom\.ru\/.+\/\d+\.html(?:$|[?#])/.test(link) : source === 'autoru' ? /^https:\/\/auto\.ru\/cars\/(used|new)\/sale\//.test(link) : /^https:\/\/(?:www\.)?avito\.ru\/[^/]+\/avtomobili\/[^/]+_\d+(?:$|[?#])/.test(link);
        if (!isListing) return null;
        if (!link || seen.has(link)) return null;
        seen.add(link);
        const img = node.querySelector('img');
        const raw = text(node);
        const title = text(node.querySelector('[itemprop="name"], [data-marker*="title"], [data-mark*="Title"], h3, h2')) || text(linkNode);
        const price = text(node.querySelector('[itemprop="price"], [data-marker*="price"], [data-mark*="Price"]')) || raw.match(/[\d\s]+\s*₽/)?.[0] || '';
        return { title, link, image: image(img), price, raw: raw.slice(0, 1800) };
      }).filter(Boolean).slice(0, limit);
    }, { source, limit: MAX_CARDS_PER_SOURCE_LOCAL });
    return result.length?remember(url,result):result;
  } finally { await page.close(); }
}

async function collectDetails(context, item) {
  if(item.source==='fresh'){try{return await freshDetail(item);}catch{return null;}}
  const page = await context.newPage();
  try {
    await visit(page,item.link);
    await page.waitForTimeout(1200);
    if(!await page.evaluate(activeListing,{expected:item.link,inactive:inactiveText.source}))return null;
    const detail = await page.evaluate(() => {
      const clean = value => String(value || '').replace(/\s+/g, ' ').trim();
      const text = node => clean(node?.textContent);
      const cover = document.querySelector('meta[property="og:image"]')?.content;
      const jsonImages = [...document.querySelectorAll('script[type="application/ld+json"]')].flatMap(node => {
        try { const value = JSON.parse(node.textContent); const list = Array.isArray(value) ? value : value['@graph'] || [value]; return list.filter(item=>['Car','Product','Vehicle'].includes(item?.['@type'])).flatMap(item => item?.image || []).map(item => typeof item === 'string' ? item : item?.url).filter(Boolean); } catch { return []; }
      });
      const rows = [...document.querySelectorAll('dt, dd, tr, li, [class*="property"], [class*="spec"]')].map(text).filter(Boolean);
      const raw = clean(document.body?.innerText).slice(0, 12000);
      const description=clean(document.querySelector('[data-ftid="bull_description"],[data-marker="item-view/item-description"],[itemprop="description"],.CardDescription__textInner')?.textContent || document.querySelector('meta[name="description"]')?.content).slice(0,1000);
      const title = clean(document.querySelector('h1, [itemprop="name"]')?.textContent);
      const priceNode = document.querySelector('[itemprop="price"], [data-marker*="price"], [data-mark*="Price"]');
      const generation=[...document.links].map(a=>a.href).find(h=>/^https:\/\/www\.drom\.ru\/catalog\/[\w-]+\/[\w-]+\/g_[\w-]+\/$/.test(h));
      return { title, description,generation, price: clean(priceNode?.getAttribute('content') || priceNode?.textContent), images: [...new Set([...jsonImages,cover].filter(url=>/^https?:\/\//i.test(url||'') && !/logo|placeholder|\.svg/i.test(url)))], rows, raw };
    });
    const facts = parseFacts(`${detail.title} ${detail.rows.join(' ')} ${detail.raw}`);
    facts.color=detail.rows.map(r=>r.match(/^Цвет\s*:?\s+([А-ЯЁа-яё-]{3,24})$/i)?.[1]).find(Boolean)||'';
    return { ...item,description:detail.description,generation:detail.generation,title: first(detail.title, item.title), price: number(first(detail.price, item.price)), images: detail.images, facts, raw: detail.raw.slice(0, 4000), collectedAt: new Date().toISOString() };
  } catch {
    return null;
  } finally { await page.close(); }
}

async function research(context,items){
  const cache=new Map();
  function lookup(url,generation){
    if(!cache.has(url)) cache.set(url,(async()=>{
      const p=await context.newPage();
      try {await p.goto(url,{waitUntil:'domcontentloaded',timeout:12000});
        return await p.locator('tr').evaluateAll((rows,g)=>rows.map(r=>g?[r.innerText,[...r.querySelectorAll('a')].map(a=>a.href).find(h=>/^https:\/\/www\.drom\.ru\/catalog\/[\w-]+\/[\w-]+\/\d+\/$/.test(h))]:[...r.cells].map(c=>c.innerText.trim())).filter(r=>r.length>=2),generation);
      } catch{return [];} finally{await p.close();}
    })());return cache.get(url);
  }
  for(let i=0;i<items.length;i+=4) await Promise.all(items.slice(i,i+4).map(async item=>{
    if(!item.generation) return;
    const trim=pickTrim(await lookup(item.generation,true),item.facts);
    if(trim){item.specURL=trim;item.measurements=measurements(await lookup(trim,false));}
  }));
}
function interleave(lists) {
  return Array.from({length:Math.max(0,...lists.map(l=>l.length))},(_,i)=>lists.map(l=>l[i]).filter(Boolean)).flat();
}
async function catalogCards(context,query,paths,profiles){
  const lists=[];
  for(let i=0;i<paths.length;i+=2){
    const wave=await Promise.allSettled(paths.slice(i,i+2).map(async path=>{
      const cards=await collectSearchCards(context,'drom',dromSearch(query,path));
      return cards.filter(c=>new URL(c.link).pathname.includes('/'+path+'/')).map(c=>({...c,profile:profiles.get(path)}));
    }));
    lists.push(...wave.filter(r=>r.status==='fulfilled').map(r=>r.value));
    if(!available('https://auto.drom.ru') || (lists.filter(l=>l.length).length>=6 && lists.flat().length>=30))break;
  }
  return interleave(lists);
}
function rank(items, query,needs) {
  const words = tokens(query).filter(w=>w.length>2 && /[а-яa-z]/.test(w) && !['машину','машина','автомобиль','нужен','нужна','хочу','ищу','млн','тыс','рублей','года','старше'].includes(w));
  const {model,max,year}=criteria(query);
  const domestic=domesticQuery(query);
  return items.filter(item=>(item.images?.length || /^https?:/.test(item.image)) && (!domestic || domesticTitle.test(item.title)) && (!model || model.every(word=>tokens(item.title).includes(word))) && (!max || (item.price>0 && item.price<=max)) && (!year || +item.facts.year>=year)).map(item => {
    const evidence = new Set(tokens([item.title,item.description,...Object.values(item.facts)].join(' ')));
    const hits = words.reduce((sum, word) => sum + Number(evidence.has(word)), 0);
    const fit=suitability(needs,item.measurements);
    if(!fit || (needs.body && bodyType(String(item.facts.body||''))!==needs.body)) return null;
    if(needs.body)fit.reasons.push('Тип кузова в объявлении: '+item.facts.body);
    if(!needs.body && !item.profile && !needs.ids.length && !model && !domestic && words.length && !hits && !(item.fromWeb && item.listingScore>=0.81)) return null;
    return { ...item, reasons:fit.reasons, match:fit.score + ((item.profile?.score??item.listingScore??0)+(item.listingScore||0))*10 + Math.min(hits,5)*0.1 };
  }).filter(Boolean).sort((a, b) => b.match - a.match);
}

function send(res, status, body, type = 'application/json; charset=utf-8') {
  res.writeHead(status, { 'content-type': type, 'access-control-allow-origin': '*', ...(type.startsWith('application/json')?{'cache-control':'no-store'}:{}) });
  res.end(type.startsWith('application/json') ? JSON.stringify(body) : body);
}

async function staticFile(res, pathname) {
  const requested = pathname === '/' ? '/index.html' : pathname === '/model-viewer.js' ? '/node_modules/@google/model-viewer/dist/model-viewer.min.js' : pathname;
  const file = normalize(join(ROOT, requested));
  if (!file.startsWith(ROOT)) return send(res, 403, 'Forbidden', 'text/plain');
  try { const body = await readFile(file); const types = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8' }; return send(res, 200, body, types[extname(file)] || 'application/octet-stream'); }
  catch { return send(res, 404, 'Not found', 'text/plain'); }
}

const server = http.createServer(async (req, res) => {
  const requestUrl = new URL(req.url, `http://${req.headers.host}`);
  let survey=null;
  if(req.method==='POST' && ['/api/advisor','/api/search'].includes(requestUrl.pathname)){
    try{const chunks=[];let bytes=0;for await(const chunk of req){bytes+=chunk.length;if(bytes>256*1024)throw Error();chunks.push(chunk);}survey=JSON.parse(Buffer.concat(chunks).toString('utf8'));
      if(!['novice','expert'].includes(survey.mode)||typeof survey.description!=='string'||!survey.description.trim()||survey.description.length>600||!Array.isArray(survey.answers)||survey.answers.some(a=>!a||typeof a.field!=='string'||typeof a.value!=='string'||!a.value.trim()||a.value.length>160)||(survey.asked!==undefined&&(!Array.isArray(survey.asked)||survey.asked.some(k=>typeof k!=='string'||k.length>64))))throw Error();
    }catch{return send(res,400,{error:'Проверьте описание и ответы'});}
    if(requestUrl.pathname==='/api/advisor')return send(res,200,await interview(survey));
    if((await interview({...survey,checkOnly:true})).remaining)return send(res,400,{error:'Сначала завершите опрос'});
  }
  const glb=requestUrl.pathname.match(/^\/models\/([a-f0-9]{32})\.glb$/);
  if(glb){try{const saved=await localGLB(glb[1]);if(!saved)return send(res,404,{error:'Файл модели не найден'});
    res.writeHead(200,{'content-type':'model/gltf-binary','content-length':saved.size,'cache-control':'private, max-age=86400'});if(req.method==='HEAD')return res.end();await pipeline(createReadStream(saved.file),res);return;
  }catch{if(!res.headersSent)return send(res,502,{error:'Не удалось получить GLB'});res.destroy();return;}}
  if (requestUrl.pathname === '/health') return send(res, 200, { ok: true, parser: 'playwright' });
  if(requestUrl.pathname==='/api/model3d'){
    const get=k=>(requestUrl.searchParams.get(k)||'').trim();const query={name:get('name'),year:get('year'),body:get('body')};
    if(query.name.length<2||query.name.length>100||query.body.length>40||(query.year&&!/^(?:19|20)\d{2}$/.test(query.year)))return send(res,400,{error:'Некорректная модель'});
    try{return send(res,200,{model:await findModel(query)});}catch{return send(res,503,{error:'Sketchfab временно недоступен'});}
  }
  if (requestUrl.pathname === '/api/search') {
    const prefs=survey?[...profile(survey.description,survey.answers),...(Array.isArray(survey.required)?survey.required:[])].filter((e,i,a)=>a.findIndex(x=>x.field===e.field)===i):[];
    const desired=field=>prefs.find(x=>x.field===field)?.value;
    const query = survey ? retrievalText(survey.description,prefs) : requestUrl.searchParams.get('q')?.trim();
    if (!query) return send(res, 400, { error: 'Введите описание автомобиля' });
    const browser = await getBrowser();
    const context = await browser.newContext({ locale: 'ru-RU', userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36' });
    try {
      const model=desired('model')?.toLowerCase().split(/\s+/)||criteria(query).model;
      const dromPath=(survey?.answers||[]).find(a=>a.field==='drom_path')?.value||'';
      const fast=(survey?.answers||[]).find(a=>a.field==='fast')?.value;
      if(/^\d+$/.test(fast||''))MAX_CARDS_PER_SOURCE_LOCAL=Math.max(4,Math.min(30,Number(fast)));
      const needs=understand(query);
      const discovery=(!model && !domesticQuery(query)) ? await discover(query).catch(()=>({models:[],total:0,mode:'unavailable'})) : null;
      const profiles=new Map((discovery?.models||[]).map(m=>[m.path,m]));
      needs.body=requestedBody(query,discovery?.expanded||'');
      const searchText=model ? model.join(' ') : query;
      const urls=Object.fromEntries(Object.entries(sources).map(([name,makeUrl])=>[name,makeUrl(name==='drom'?query:searchText)]));
      if(dromPath && /^[a-z0-9_-]+(?:\/[a-z0-9_-]+)?$/.test(dromPath))urls.drom='https://auto.drom.ru/'+dromPath+'/';
      const [groups,web,fresh] = await Promise.all([Promise.allSettled(Object.entries(urls).map(async ([name, url]) => {
        const paths=[...profiles.keys()];
        const market=discovery?.models.filter(m=>m.available && m.score>=discovery.models[0].score-0.12).slice(0,12).map(m=>m.path)||[];
        const catalogs=domesticQuery(query)?domesticCatalogs:[...new Set([...interleave([paths.slice(0,4),market.slice(0,4),needs.paths]),...market,...paths])].slice(0,24);
        if(name!=='drom' && discovery?.models.length){
          const leads=discovery.models.slice(0,2);
          const queries=[url,...leads.map(m=>sources[name](m.title))];
          const lists=await Promise.allSettled(queries.map(async (u,i)=>{
            const list=await collectSearchCards(context,name,u);
            if(!i)return list;const m=leads[i-1];
            return list.filter(c=>tokens(m.title).every(t=>tokens(c.title).includes(t))).map(c=>({...c,profile:m}));
          }));
          if(lists.every(l=>l.status==='rejected'))throw Error('Источник недоступен');
          return {name,items:interleave(lists.filter(l=>l.status==='fulfilled').map(l=>l.value))};
        }
        if(name==='drom' && dromPath){
          const cards=await collectSearchCards(context,'drom',urls.drom);
          const wanted=dromPath.split('/');
          const items=cards.filter(c=>wanted.every(w=>new URL(c.link).pathname.toLowerCase().includes('/'+w+'/')));
          return {name,items:items.length?items:cards};
        }
        if(name !== 'drom' || model || !catalogs.length) return {name, items:await collectSearchCards(context,name,url)};
        if(!domesticQuery(query))return {name,items:await catalogCards(context,query,catalogs,profiles)};
        const pages=await Promise.allSettled(catalogs.map(brand=>collectSearchCards(context,name,dromSearch(query,brand))));
        const lists=pages.filter(p=>p.status==='fulfilled').map(p=>p.value);
        if(!lists.length) throw Error('Каталоги Drom недоступны');
        // Alternate makes before applying the existing detail-page limit.
        const items=Array.from({length:MAX_CARDS_PER_SOURCE},(_,i)=>lists.map(list=>list[i]).filter(Boolean)).flat();
        return {name,items};
      })),webListings(context,query),freshCards(discovery,model,domesticQuery(query),needs.body).catch(()=>null)]);
      urls.fresh='https://freshauto.ru/cars/';groups.push(fresh?{status:'fulfilled',value:fresh}:{status:'rejected'});
      const cards = interleave([...groups.map(group => group.status === 'fulfilled' ? group.value.items.map(item => ({ ...item, source: group.value.name })) : []),web]);
      const unique = [...new Map(cards.slice().reverse().map(item => [item.link,item])).values()].reverse();
      // Detail pages are collected after search cards: this supplies real photos and technical data.
      const selected=unique.slice(0,Math.min(12,unique.length)), detailed=[];
      for(let i=0;i<selected.length;i+=6)detailed.push(...(await Promise.all(selected.slice(i,i+6).map(item=>collectDetails(context,item)))).filter(Boolean));
      if(survey && model && !compareOffers(detailed,prefs).some(c=>c.tier===0)){
        // Keep the original requirements; broaden only candidate retrieval.
        try{const similar=await discover([desired('body')||desired('model'),desired('use')||''].join(' '));const extra=await freshCards(similar,null,false,needs.body);const unseen=extra.items.filter(c=>!unique.some(u=>u.link===c.link)).slice(0,10);
          for(let i=0;i<unseen.length;i+=3)detailed.push(...(await Promise.all(unseen.slice(i,i+3).map(c=>collectDetails(context,c)))).filter(Boolean));}catch{}
      }
      if(needs.ids.length||prefs.some(e=>['seats','use','economy'].includes(e.field))) await research(context,detailed);
      if(discovery?.mode==='semantic')await scoreListings(query,detailed).catch(()=>{});
      const base=rank(detailed,query,needs);const ranked=survey?compareOffers(detailed.map(c=>({...c,match:base.find(r=>r.link===c.link)?.match||0})),prefs):base, first=[],rest=[],seen=new Set();
      const exactCount=survey?ranked.filter(c=>c.tier===0).length:ranked.length;
      for(const c of ranked)c.assetQuery=assetIdentity(c);
      const explanationItems=ranked.slice(0,Math.min(5,ranked.length));
      await Promise.all(explanationItems.map(async c=>{
        try{c.aiReason=await localExplanation({запрос:survey?.description||query,обязательные_условия:(survey?prefs:[]).map(e=>`${e.label}: ${e.value}`),автомобиль:c.title,характеристики:c.facts,цена:c.price,проверки:c.checks?.map(e=>`${e.label}: ${e.status}${e.actual?` (${e.actual})`:''}`),расхождения:c.missCount||0});}catch{c.aiReason='';}
      }));
      for(const c of ranked){if(!survey && c.profile && seen.has(c.profile.path))rest.push(c);else first.push(c);if(c.profile)seen.add(c.profile.path);}
      return send(res, 200, { query, urls, inventory:fresh?.total,exactCount,strictConditions:survey?prefs.filter(e=>e.priority==='must').map(e=>({field:e.field,label:e.label,value:e.value})):[],discovery:discovery && {total:discovery.total,mode:discovery.mode,expanded:discovery.expanded,models:discovery.models.slice(0,24).map(({title,path,description,source})=>({title,path,description,source}))},model: model?.join(' '), collected: detailed.length, results: [...first,...rest], sources: groups.map((group, index) => ({ source: Object.keys(urls)[index], ok: group.status === 'fulfilled' && available(Object.values(urls)[index]), count: group.status === 'fulfilled' ? group.value.items.length : 0 })) });
    } catch (error) { return send(res, 502, { error: 'Не удалось собрать объявления', detail: error.message }); }
    finally { await context.close(); }
  }
  return staticFile(res, requestUrl.pathname);
});

server.listen(PORT, () => console.log(`Авторадар: http://localhost:${PORT}`));
process.on('SIGINT', async () => { if (browserPromise) (await browserPromise).close(); process.exit(0); });
