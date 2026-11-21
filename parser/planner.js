import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {homedir} from 'node:os';
import {join} from 'node:path';
import {access} from 'node:fs/promises';
const run=promisify(execFile);
const root=join(homedir(),'.cache','avtoradar');
const exe=process.env.AVTORADAR_LLAMA||join(root,'llama',process.platform==='win32'?'llama-completion.exe':'llama-completion');
const cache=new Map();
const profileCache=new Map();
export async function localQuestions(system,user,allowed){
  const downloaded=join(root,'models','planner-ollama.gguf');
  const model=await access(downloaded).then(()=>downloaded,()=>join(root,'models','planner.gguf'));
  const clean=s=>s.replace(/[<>|]/g,' ');
  const prompt=`<|im_start|>system\n${system}<|im_end|>\n<|im_start|>user\n${clean(user)}<|im_end|>\n<|im_start|>assistant\n`;
  const keys=allowed.map(k=>JSON.stringify(JSON.stringify(k))).join(' | ');
  const grammar=String.raw`root ::= "[" ws (item (ws "," ws item)*)? ws "]"
item ::= "{" ws "\"field\"" ws ":" ws (${keys}) ws "," ws "\"label\"" ws ":" ws str ws "," ws "\"text\"" ws ":" ws str ws "," ws "\"reason\"" ws ":" ws str ws "," ws "\"options\"" ws ":" ws "[" ws str (ws "," ws str)+ ws "]" ws "}"
str ::= "\"" [^"\\\n]+ "\""
ws ::= [ \t\n]*`;
  const {stdout}=await run(exe,['-m',model,'-p',prompt,'--grammar',grammar,'--no-display-prompt','--no-conversation','--simple-io','--no-warmup','-t','4','-c','4096','-n','480','--temp','0','--seed','42','-r','<|im_end|>'],{timeout:55000,windowsHide:true,maxBuffer:512000});
  // Retain complete questions even when the generation budget cuts a batch short.
  return [...stdout.matchAll(/\{[^{}]*\}/g)].flatMap(([s])=>{try{return [JSON.parse(s)]}catch{return []}});
}
export async function localExplanation(context){
  const downloaded=join(root,'models','planner-ollama.gguf');
  const model=await access(downloaded).then(()=>downloaded,()=>join(root,'models','planner.gguf'));
  const prompt=`<|im_start|>system
Ты пишешь объяснение рекомендации автомобиля. Отвечай только на русском, 2 коротких предложения, без английских слов, без выдуманных характеристик и без общих рекламных фраз. Первое предложение: почему конкретное объявление подходит под запрос. Второе: что не совпало или что не удалось подтвердить. Если всё совпадает, честно напиши, что обязательные условия подтверждены. Не называй машину идеальной.
<|im_end|>
<|im_start|>user
${JSON.stringify(context).replace(/[<>|]/g,' ')}
<|im_end|>
<|im_start|>assistant
`;
  const {stdout}=await run(exe,['-m',model,'-p',prompt,'--no-display-prompt','--no-conversation','--simple-io','--no-warmup','-t','4','-c','2048','-n','140','--temp','0.1','--seed','42','-r','<|im_end|>'],{timeout:35000,windowsHide:true,maxBuffer:256000});
  return stdout.replace(/\[[^\]]*\]|\{[^}]*\}/g,'').replace(/\[end of text\]/gi,'').replace(/\s+/g,' ').trim().slice(0,500);
}
export async function localProfile(text){
  if(profileCache.has(text))return profileCache.get(text);
  const downloaded=join(root,'models','planner-ollama.gguf');
  const model=await access(downloaded).then(()=>downloaded,()=>join(root,'models','planner.gguf'));
  const prompt=`<|im_start|>system
Ты разбираешь запрос на автомобиль. Извлеки смысл из всей фразы, включая разговорные и образные выражения. Ничего не додумывай. Отделяй обязательное от желательного. Числа, деньги, валюту, сравнения «до», «не дороже», «ровно», «не старше» сохраняй точно. Верни только JSON: {"must":[{"field":"budget|origin|body|seats|year|engine|gearbox|fuel|drive|color|use|detail","value":"...","operator":"...","reason":"..."}],"want":[...],"unknown":["..."],"summary":"..."}. Все строки на русском. Если условие не названо — помести его в unknown.
<|im_end|>
<|im_start|>user
${JSON.stringify(text).replace(/[<>|]/g,' ')}
<|im_end|>
<|im_start|>assistant
`;
  const {stdout}=await run(exe,['-m',model,'-p',prompt,'--no-display-prompt','--no-conversation','--simple-io','--no-warmup','-t','4','-c','3072','-n','480','--temp','0','--seed','42','-r','<|im_end|>'],{timeout:55000,windowsHide:true,maxBuffer:512000});
  const match=stdout.match(/\{[\s\S]*\}/);if(!match)throw Error('profile unavailable');
  const data=JSON.parse(match[0]);if(!Array.isArray(data.must)||!Array.isArray(data.want)||!Array.isArray(data.unknown))throw Error('invalid profile');
  const allowed=new Set(['budget','origin','body','seats','year','engine','gearbox','fuel','drive','color','use','detail']);
  const normalizeItem=item=>{const raw=String(item.field||'').toLowerCase(), reason=String(item.reason||'').toLowerCase(), value=String(item.value||'').toLowerCase(), context=reason+' '+value;let field=[...allowed].find(k=>raw===k||raw.includes(k));
    // The model sometimes puts a whole natural-language clause into the wrong
    // schema slot. Resolve it by meaning before the profile reaches retrieval.
    if(/американ|сша|соединенн[а-я]* штат|американс/.test(context))field='origin';
    if(/ровно\s*(?:в\s*)?\d+\s*(?:человек|пассажир|мест)|вместительн|число мест/.test(context))field='seats';
    if(/руб|тыс|млн|цен|бюджет|не дороже|не более/.test(context))field='budget';
    if(!field)field='detail';
    let operator=item.operator||'equal';if(field==='budget' && /не дороже|не более|до|максимум/.test(context))operator='lte';
    let fixed=String(item.value||'');
    if(field==='origin')fixed='США';
    if(field==='seats'){const n=context.match(/(?:ровно\s*(?:в\s*)?|на\s*)?(\d+)\s*(?:человек|пассажир|мест)/);if(n)fixed=n[1]+' мест';}
    return {...item,field,value:fixed,operator};};
  data.must=data.must.map(normalizeItem);data.want=data.want.map(normalizeItem);profileCache.set(text,data);return data;
}
// The planner suggests search terms only. Prices, specs, photos and links never come from it.
export async function plan(q){
  if(cache.has(q))return cache.get(q);
  try{
    await access(exe);
    const downloaded=join(root,'models','planner-ollama.gguf');
    const model=await access(downloaded).then(()=>downloaded,()=>join(root,'models','planner.gguf'));await access(model);
    const prompt=`<|im_start|>system\nТы подбираешь только легковые машины, не аксессуары. Клиент может шутить или говорить образами. Сначала опиши в goal простыми словами желаемый результат поездки. Не меняй желание на противоположное: испытать ощущение и избежать его — разные задачи. Затем выбери конструкцию автомобиля, которая это позволяет. Для езды под открытым небом — кабриолет со складной крышей; для защиты от непогоды — закрытый салон. Если кузов из желания не следует, не угадывай его. В terms напиши английские автомобильные термины, в models — примеры реальных машин. Только JSON.<|im_end|>\n<|im_start|>user\nна выходных вся моя футбольная команда должна поместиться вместе<|im_end|>\n<|im_start|>assistant\n{"goal":"вместить много пассажиров","terms":"passenger van minibus multiple rows of seats","models":["Ford Transit","Mercedes-Benz Sprinter"]}<|im_end|>\n<|im_start|>user\n${q.slice(0,500).replace(/[<>|]/g,' ')}<|im_end|>\n<|im_start|>assistant\n`;
    const {stdout}=await run(exe,['-m',model,'-p',prompt,'--no-display-prompt','--no-conversation','--simple-io','--no-warmup','-t','4','-c','1024','-n','155','--temp','0.1','--seed','42','-r','<|im_end|>'],{timeout:50000,windowsHide:true,maxBuffer:512000});
    const json=JSON.parse(stdout.match(/\{[^{}]*\}/s)?.[0]||'null');
    if(!json||typeof json.terms!=='string'||!Array.isArray(json.models))return null;
    const result={terms:json.terms.slice(0,250),models:json.models.filter(s=>typeof s==='string'&&s.length<=70).slice(0,6)};
    if(cache.size>=100)cache.delete(cache.keys().next().value);cache.set(q,result);return result;
  }catch{return null;}
}
