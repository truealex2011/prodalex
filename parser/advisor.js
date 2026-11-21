import {localQuestions,localProfile} from './planner.js';
import {createHash} from 'node:crypto';
import {requestedBody,bodyType} from './body-types.js';
import {understand,suitability} from './needs.js';

const norm=s=>String(s??'').toLowerCase().replaceAll('ё','е').trim();
const no='Не важно';
const bodies={kabriolet:'Кабриолет',pikap:'Пикап',miniven:'Минивэн',universal:'Универсал',kupe:'Купе',hetchbek:'Хэтчбек',sedan:'Седан',vnedorozhnik:'Кроссовер / внедорожник'};
const colors={'Жёлтый':'желт','Белый':'бел','Чёрный':'черн','Синий':'син','Красный':'красн','Зелёный':'зелен','Серый':'сер','Серебристый':'серебрист','Оранжевый':'оранж','Бежевый':'бежев','Голубой':'голуб','Фиолетовый':'фиолет'};
function color(s){return Object.keys(colors).find(c=>norm(s).split(/[^а-я]+/).some(t=>t.startsWith(colors[c]) && (colors[c]!=='сер'||!t.startsWith('серебрист'))));}
const fields={
  use:['Сценарий','Куда вы будете ездить чаще всего?',['По городу','За город по плохим дорогам','С семьёй и багажом']],
  seats:['Места','Сколько человек должно поместиться вместе с водителем?',['2 места','4 места','5 мест','7 мест']],
  budget:['Бюджет','Сколько вы готовы потратить на покупку?',['До 700 тыс. ₽','До 1,5 млн ₽','До 3 млн ₽','До 5 млн ₽']],
  color:['Цвет','Какой цвет кузова вам нравится?',['Жёлтый','Красный','Белый','Чёрный','Синий']],
  gearbox:['Коробка','Как вам удобнее переключать передачи?',['Автомат','Механика']],
  engine:['Объём двигателя','Какой максимальный рабочий объём двигателя рассматриваете?',['До 1,6 л','До 2,0 л','До 3,0 л']],
  drive:['Привод','Какой привод предпочитаете?',['Полный','Передний','Задний']],
  body:['Кузов','Какой кузов вы представляете?',['Седан','Кабриолет','Кроссовер','Хэтчбек']],
  model:['Модель','Есть конкретная модель?',['Lada Granta','Daewoo Matiz']],
  make:['Марка','Какую марку рассматриваем?',['BMW','Toyota','Lada','Ford','Не важно']],
  year:['Год','Начиная с какого года рассматриваете машины?',['От 2015','От 2019','От 2022']],
  economy:['Расход','Нужен небольшой расход топлива?',['Экономичный']],
  style:['Характер','Какой образ вам ближе?',['Спортивное купе','Уютный семейный универсал','Яркий цвет','Сдержанный цвет']],
  other:['Особое условие','Что ещё важно?',['Не важно']],
  fuel:['Топливо','Какой тип двигателя рассматриваете?',['Бензин','Дизель','Гибрид','Электро']],
  power:['Мощность','Какой минимальной мощности будет достаточно?',['От 100 л.с.','От 150 л.с.','От 200 л.с.']],
  mileage:['Пробег','Какой максимальный пробег допустим?',['До 50 000 км','До 100 000 км','До 150 000 км']],
  origin:['Происхождение','Какая страна происхождения марки нужна?',['США','Европа','Япония','Корея','Россия','Не важно']],
};
const expert={use:'Для какого режима эксплуатации подбираем машину?',budget:'Какой верхний предел цены объявления?',gearbox:'Какие типы трансмиссии допустимы?',color:'Есть предпочтение по заводскому цвету кузова?'};
const originMakes={
  'сша':['Ford','Chevrolet','Dodge','Jeep','Cadillac','Chrysler','Tesla','Lincoln','GMC','Не важно'],
  'европа':['BMW','Mercedes-Benz','Audi','Volkswagen','Volvo','Renault','Skoda','Не важно'],
  'япония':['Toyota','Honda','Nissan','Mazda','Subaru','Mitsubishi','Lexus','Не важно'],
  'корея':['Hyundai','Kia','Genesis','Daewoo','Не важно'],
  'россия':['Lada','UAZ','GAZ','Moskvich','Aurus','Не важно']
};
const money=s=>{const m=norm(s).match(/(\d[\d\s]*(?:[.,]\d+)?)\s*(млн|миллион|тыс)?/);return m?Number(m[1].replace(/\s/g,'').replace(',','.'))*(m[2]?.startsWith('м')?1e6:m[2]?1e3:1):null};
const number=s=>Number(norm(s).match(/\d+(?:[.,]\d+)?/)?.[0]?.replace(',','.'))||null;
const modelText=s=>norm(s).replace(/лад[ауы]/g,'lada').replace(/грант[ауы]?/g,'granta').replace(/(?:дэу|део)/g,'daewoo').replace(/м[ао]тиз/g,'matiz').replace(/тойот[ау]/g,'toyota').replace(/[^\p{L}\p{N}]+/gu,' ').trim();
function parseClause(description){
  const q=norm(description), entries=[];let residue=q;
  const add=(field,value,span)=>{if(value){entries.push({field,value,label:fields[field][0],priority:'must'});if(span)residue=residue.replace(span,' ');}};
  const b=requestedBody(q);add('body',bodies[b],q.match(/(?:кабриолет|родстер|седан|кроссовер|внедорожник|пикап|минив[еэ]н|х[еэ]тчбек|универсал|купе)[а-я]*/)?.[0]);
  const n=modelText(description);const m=n.match(/(?:^| )(lada|toyota|bmw|audi|honda|mazda|hyundai|kia|ford|daewoo|renault|skoda|nissan|volkswagen|subaru|lexus|mitsubishi)\s+([a-z0-9-]+(?:\s+ii(?= |$))?)/i);
  const model=m?`${m[1]} ${m[2]}`:n.includes('granta')?'Lada Granta':n.includes('matiz')?'Daewoo Matiz':'';
  const knownMake=(n.match(/(?:^|\s)(lada|toyota|bmw|audi|honda|mazda|hyundai|kia|ford|daewoo|renault|skoda|nissan|volkswagen|subaru|lexus|mitsubishi)(?=\s|$)/i)?.[1]||'');
  if(knownMake&&!model)entries.push({field:'make',value:knownMake.toUpperCase()==='BMW'?'BMW':knownMake,label:fields.make[0],priority:'must'});
  add('model',model);if(model){residue=residue.replace(/лад[ауы]/g,'lada').replace(/грант[ауы]?/g,'granta').replace(/(?:дэу|део)/g,'daewoo').replace(/м[ао]тиз/g,'matiz').replace(/тойот[ау]/g,'toyota').replace(new RegExp(modelText(model).split(' ').join('[\\s-]+'),'i'),' ').replace(/\bgranta\b|\bmatiz\b/,' ');}
  const price=q.match(/(?:до|не\s+дороже|не\s+более|максимум(?:\s+цена)?|бюджет(?:ом)?)\s*(\d[\d ]*(?:[.,]\d+)?\s*(?:млн|тыс\.?|миллион[а-я]*|₽|руб[а-я]*)|\d[\d ]{4,})/);
  if(price){const value=money(price[1]);if(value)add('budget','До '+value.toLocaleString('ru-RU')+' ₽',price[0]);}
  const year=q.match(/(?:от|не старше)\s*(20\d{2})(?:\s*год[а-я]*)?/);if(year)add('year','От '+year[1],year[0]);
  const col=color(q);add('color',col,col&&new RegExp(colors[col]+'[а-я]*'));
  const box=q.match(/автомат[а-я]*|механик[а-я]*|вариатор[а-я]*|робот[а-я]*|акпп|мкпп/);if(box)add('gearbox',box[0].includes('механ')?'Механика':box[0].startsWith('автомат')?'Автомат':box[0],box[0]);
  const drive=q.match(/полн[а-я]* привод[а-я]*|передн[а-я]* привод[а-я]*|задн[а-я]* привод[а-я]*|4wd|awd/);if(drive)add('drive',drive[0].split(' ')[0],drive[0]);
  const seats=q.match(/(?:ровно\s+в\s+|в\s+|на\s+)?(\d+)\s*(?:мест[а-я]*|человек[а-я]*|пассажир[а-я]*)/);if(seats)add('seats',/ровно/.test(seats[0])?'Ровно '+seats[1]+' мест':seats[1]+' мест',seats[0]);
  const engine=q.match(/(?:двигател[ья]\s*)?(?:до\s*|от\s*)?(\d(?:[.,]\d+)?)\s*л(?:итр[а-я]*)?(?![.\s]*с)/);if(engine&&!/расход/.test(q))add('engine',engine[0].replace(/^двигател[ья]\s*/,''),engine[0]);
  if(understand(q).ids.includes('fuel'))add('economy','Экономичный',/экономич[а-я]*|ест мало бензина/);
  if(/по городу|для города/.test(q))add('use','По городу',/по городу|для города/);else if(/семь[яеию]|с детьми/.test(q))add('use','С семьёй и багажом',/(?:для |с )?семь[а-я]*|с детьми/);else if(/бездорож|плохим дорог/.test(q))add('use','За город по плохим дорогам',/(?:по )?(?:бездорож[а-я]*|плохим дорог[а-я]*)/);
  const fuel=q.match(/дизел[а-я]*|бензин[а-я]*|электромобил[а-я]*|гибрид[а-я]*/);if(fuel&&!entries.some(e=>e.field==='economy'))add('fuel',/дизел/.test(fuel[0])?'Дизель':/бензин/.test(fuel[0])?'Бензин':/электро/.test(fuel[0])?'Электро':'Гибрид',fuel[0]);
  if(/американск|из сша|соединенн[а-я]* штат/i.test(q))add('origin','США',/американск[а-я]*|из сша|соединенн[а-я]* штат/i);
  const power=q.match(/(?:от\s*|до\s*)?\d+\s*л\.?\s*с\.?/);if(power)add('power',power[0],power[0]);
  const mileage=q.match(/(?:пробег[а-я]*\s*)?(?:до|не более)\s*[\d\s]+(?:тыс\.?\s*)?км/);if(mileage){add('mileage',mileage[0],mileage[0]);if(!/₽|руб|бюджет|млн/.test(q)){const i=entries.findIndex(e=>e.field==='budget');if(i>=0)entries.splice(i,1);}}
  return {entries,residue};
}
// Preserve literal requirements that cannot be verified from listing attributes.
// A missing spec is a compromise, not permission to silently drop it.
const topics={color:/цвет|окраск|расцветк|оттенок/i,budget:/бюджет|цен[ауые]|стоимост|потратить/i,gearbox:/коробк|трансмисси|переключать передачи/i,drive:/привод/i,engine:/объ[её]м двигателя|литраж/i,body:/кузов/i,model:/модел[ьи]|марк[ау]/i,seats:/пассажир|сколько мест|число мест|человек/i,fuel:/топлив|тип двигателя/i,power:/мощност/i,mileage:/пробег/i,year:/год выпуска/i,use:/эксплуатаци|цели поездок|куда .*езд/i};
export function initial(text){
  const entries=[];
  for(const clause of text.split(/(?<!\d),(?!\d)|[;!\n]|\s+и\s+|\s+с\s+(?=[а-я])/i).map(s=>s.trim()).filter(Boolean)){
    const {entries:parsed,residue}=parseClause(clause), negative=/(?:^|\s)(?:не(?!\s+(?:старше|более))|без|никак[а-я]+)\s/i.test(clause);
    const plain=norm(clause).replace(/^(?:(?:я|мне|хочу|нуж[а-я]+|ищу|машин[а-я]*|авто(?:мобиль)?|желательно|обязательно|пожалуйста|чтобы|только)\s*)+/,'').trim();
    if(!plain||/не\s*важ[а-я]*|без разниц[а-я]*|без ограничений|не принципиальн[а-я]*/i.test(clause))continue;
    if(!negative)entries.push(...parsed);
    const leftover=residue.split(/[^а-яa-z0-9]+/i).filter(w=>w&&!/^(?:я|для|на|в|с|до|и|то|из|пусть|он|меня|мне|хочу|купить|нуж[а-я]*|ищу|машин[а-я]*|авто(?:мобиль)?|желательно|обязательно|строго|только|цвет[а-я]*|коробк[а-я]*|передач[а-я]*|руб[а-я]*|₽|вместительн[а-я]*|ровно|человек[а-я]*|пассажир[а-я]*)$/.test(w)).join(' ');
    if(negative||!parsed.length||leftover) {
      const detail=leftover||clause;
      entries.push({field:'detail_'+createHash('sha256').update(norm(detail)).digest('hex').slice(0,16),label:'Из описания',value:detail,priority:'must'});
    }
  }
  // Safety net for compound natural-language phrases such as
  // "не дороже 300 тыс. рублей": punctuation in the surrounding phrase must
  // never turn an explicit price into a free-form note.
  if(!entries.some(e=>e.field==='budget')){
    const limit=norm(text).match(/(?:до|не\s+дороже|не\s+более|максимум(?:\s+цена)?|бюджет(?:ом)?)\s*(\d[\d ]*(?:[.,]\d+)?\s*(?:млн|миллион[а-я]*|тыс\.?|руб[а-я]*|₽)|\d[\d ]{4,})/i);
    if(limit){const amount=money(limit[1]);if(amount)entries.unshift({field:'budget',value:'До '+amount.toLocaleString('ru-RU')+' ₽',label:fields.budget[0],priority:'must'});}
  }
  if(/американск[а-я]*/i.test(norm(text))&&!entries.some(e=>e.field==='origin'))entries.push({field:'origin',value:'США',label:fields.origin[0],priority:'must'});
  if(!entries.some(e=>e.field==='seats')){
    const places=norm(text).match(/(?:ровно\s+в\s+|в\s+|на\s+)?(\d+)\s*(?:мест[а-я]*|человек[а-я]*|пассажир[а-я]*)/i);
    if(places)entries.push({field:'seats',value:places[1]+' мест',label:fields.seats[0],priority:'must'});
  }
  const known=/американск[а-я]*|вместительн[а-я]*|ровно\s+в\s+\d+\s+(?:человек|пассажир[а-я]*)|не\s+дороже\s+\d|не\s+более\s+\d/i;
  return entries.filter((e,i)=>!(e.field.startsWith('detail_')&&known.test(e.value))&&!entries.slice(0,i).some(x=>x.field===e.field&&x.value===e.value));
}
const extraField=id=>/^detail_[a-f0-9]{16}$/.test(id);
export function profile(description,answers){const required=initial(description),locked=new Set(required.map(e=>e.field)),map=new Map();for(const a of answers){if(!locked.has(a.field)&&(Object.hasOwn(fields,a.field)||extraField(a.field)))map.set(a.field,{field:a.field,label:fields[a.field]?.[0]||String(a.label||'Дополнительно').slice(0,64),priority:a.priority==='must'?'must':'want',value:String(a.value||'').slice(0,160)});}return [...required,...map.values()].filter(e=>e.value && e.value!==no);}
export function retrievalText(description,entries){
  // Retain descriptive intent but don't reapply an old price, year, colour or model
  // after the customer has explicitly changed or skipped it.
  const values=entries.map(e=>e.value);const original=initial(description);
  const changed=original.some(o=>!entries.some(e=>e.field===o.field&&e.value===o.value));
  return [...new Set([...(changed?[]:[description]),...values])].join(', ')||'легковой автомобиль';
}
export async function interview({description,answers,mode,checkOnly,asked=[],phase='initial'},generate=localQuestions,profileParser=localProfile){
  let aiProfile=null;try{aiProfile=phase==='initial'&&profileParser===localProfile?await profileParser(description):null}catch{}
  const aiEntries=[...(aiProfile?.must||[]).map(x=>({...x,priority:'must'})),...(aiProfile?.want||[]).map(x=>({...x,priority:'want'}))].filter(x=>x&&typeof x.field==='string'&&typeof x.value==='string').map(x=>({field:x.field,value:x.value,label:x.field,priority:x.priority,reason:x.reason,operator:x.operator}));
  const entries=[...profile(description,answers),...aiEntries].filter((e,i,a)=>a.findIndex(x=>x.field===e.field)===i);
  const done=new Set([...entries,...answers].map(a=>a.field));
  const open=entries.some(e=>e.field==='body'&&bodyType(e.value)==='kabriolet');
  const route=mode==='expert'?['make','model','body','year','budget','engine','gearbox','fuel','drive','seats','use','mileage','color']:['make','model','body','year','budget','gearbox','fuel','drive','seats','use','mileage','color','style'];
  const knownText=[description,...answers.map(a=>a.value)].join('; ');
  for(const a of answers)if(a.value!==no)for(const x of initial(a.value))done.add(x.field);
  for(const [field,pattern] of Object.entries(topics))if(pattern.test(knownText) && /не\s*важ|без разниц|люб[а-я]*|не принципиальн/i.test(knownText.split(/[;,]/).find(s=>pattern.test(s))||''))done.add(field);
  const originEntry=entries.find(e=>e.field==='origin');
  const originKey=norm(originEntry?.value||'');
  const allFields=Object.keys(fields).filter(k=>k!=='other' && !(k==='model' && !entries.some(e=>e.field==='make')) && !(k==='model' && originKey && !entries.some(e=>e.field==='make')));
  const remaining=allFields.filter(k=>!done.has(k)&&!asked.includes(k));if(checkOnly)return {entries,remaining:asked.filter(k=>!done.has(k)).length};
  const base=k=>mode==='expert'?(expert[k]||fields[k][1]):fields[k][1];
  const questions=new Map(),seen=new Set(asked);
  const addBase=k=>{let options=fields[k][2];if(k==='make'&&originKey){const group=Object.keys(originMakes).find(key=>originKey.includes(key));if(group)options=originMakes[group];}questions.set(k,{field:k,label:fields[k][0],text:k==='make'&&originKey?`Какая марка из выбранной страны вам ближе?`:base(k),options,priority:['budget','seats'].includes(k)?'must':'want'});};
  const allowed=remaining.filter(k=>!seen.has(k));
  const more=phase==='more';let aiAvailable=true;const answered=answers.filter(a=>a.value!==no).length;
  // The local model chooses only a small contextual batch.
  if(more && answers.length && answers.every(a=>a.value===no))return {entries,required:initial(description),questions:[],aiAvailable:true};
  try{const generated=await generate(`Ты консультант по легковым автомобилям. Режим ${mode==='expert'?'Эксперт: спрашивай технически точно':'Новичок: говори просто, без жаргона'}. Всё из исходного описания обязательно. Реши, достаточно ли данных для подбора: если да, верни []. Если нет — задай только уточнения, которые существенно изменят выбор. Не заполняй анкету ради полноты. ${more?'После ответов обычно уже можно искать.':''} Не спрашивай известное даже другими словами; «Не важно» — тоже ответ. Используй field из списка, для новой темы — detail. Формат: JSON-массив {field,label,text,options}.`,JSON.stringify({описание:description,ответы:answers.map(a=>[a.label||a.field,a.value]).slice(-24),известно:entries.map(e=>e.label+': '+e.value).slice(0,25),темы:allowed.map(k=>k+': '+base(k))}),[...allowed,'detail']);
     for(const g of generated){if(questions.size>=3)break;if(typeof g.label!=='string'||!g.label.trim()||!/[а-я]/i.test(g.text)||!Array.isArray(g.options))continue;
      const field=g.field==='detail'?'detail_'+createHash('sha256').update(norm(g.label)).digest('hex').slice(0,16):g.field;
      if(Object.entries(topics).some(([k,re])=>(done.has(k)||seen.has(k))&&re.test(g.label+' '+g.text)))continue;
      const distinctive=s=>norm(s).split(/[^а-яёa-z0-9]+/).filter(w=>w.length>=3&&!/^(?:хоч|нуж|обязательн|дополнительн|машин|автомоб|налич|желательн|предпоч|како|неко|конкрет|уточн|важ|друг|ваш|чтоб|каки|есть|про|тольк|при|для)/.test(w)).map(w=>w.length>4?w.slice(0,-2):w);
      if(entries.filter(e=>extraField(e.field)).some(e=>distinctive(e.value).some(t=>norm(g.label+' '+g.text).includes(t))))continue;
      if(answers.some(a=>a.label&&norm(a.label)===norm(g.label)))continue;
      if(done.has(field)||seen.has(field)||(!extraField(field)&&!allowed.includes(field)))continue;
       if(!extraField(field)){addBase(field);const item=questions.get(field);if(g.text.endsWith('?')&&g.text.length<180)item.text=g.text;item.reason=String(g.reason||'Это поможет сузить поиск.').slice(0,180);}
       else{const options=[...new Set(g.options.filter(s=>typeof s==='string'&&s.trim()&&s.length<=100&&!/не важно|свой ответ/i.test(s)))];if(options.length<2)continue;questions.set(field,{field,label:g.label.slice(0,64),text:g.text.slice(0,180),reason:String(g.reason||'Это поможет сузить поиск.').slice(0,180),options,priority:'want'});}
      seen.add(field);
    }
  }catch{aiAvailable=false;}
  if(!questions.size&&answered<3){for(const k of ['body','use','budget','year','seats'].filter(k=>allowed.includes(k)).slice(0,2)){addBase(k);questions.get(k).reason='Этот параметр сильно влияет на круг подходящих объявлений.';}}
  return {entries,required:entries,unknown:aiProfile?.unknown||[],summary:aiProfile?.summary||'',questions:[...questions.values()],aiAvailable};
}
function check(c,e){const f=c.facts||{},m=c.measurements||{},s=norm(e.value);let actual,ok;
  if(e.field==='model'){actual=c.vehicleName||c.title;if(actual)ok=(' '+modelText(actual).replace(/lada ваз /,'lada ')+' ').includes(' '+modelText(s)+' ');}
  if(e.field==='origin'){actual=c.title;const us=/\b(?:ford|chevrolet|cadillac|chrysler|dodge|jeep|gmc|buick|lincoln|tesla|pontiac|hummer|ram|shelby|chrysler)\b/i.test(actual);ok=s.includes('сша')?us:undefined;}
  if(e.field==='body'){actual=f.body;if(bodyType(s)&&actual)ok=bodyType(s)===bodyType(actual);}
  if(e.field==='budget'){actual=c.price?Number(c.price).toLocaleString('ru-RU')+' ₽':null;if(actual&&money(s)&&!/^от\s/.test(s))ok=c.price<=money(s);}
  if(e.field==='year'){actual=f.year;if(actual&&number(s))ok=+f.year>=number(s);}
  if(e.field==='engine'){actual=f.engine;if(number(actual)&&/^(?:(?:до|от)\s+)?\d+(?:[.,]\d+)?\s*(?:л(?:итр[а-я]*)?)?$/.test(s))ok=/^от\s/.test(s)?number(actual)>=number(s):/^до\s/.test(s)?number(actual)<=number(s):number(actual)===number(s);}
  if(e.field==='seats'){actual=m.seats||f.seats;if(actual&&number(s))ok=/ровно/.test(s)?+actual===number(s):+actual>=number(s);}
  if(e.field==='color'){actual=f.color;const wanted=color(s),got=color(actual);if(wanted)ok=!!got&&wanted===got;else if(actual)ok=norm(actual)===s;}
  if(e.field==='fuel'){actual=f.fuel;if(actual)ok=norm(actual).includes(s);}
  if(e.field==='power'){actual=f.power;if(actual&&number(s))ok=s.startsWith('до ')?number(actual)<=number(s):number(actual)>=number(s);}
  if(e.field==='mileage'){actual=f.mileage;if(actual&&money(s))ok=money(actual)<=money(s);}
  if(e.field==='style'){if(/купе|универсал/.test(s)){actual=f.body;if(actual)ok=bodyType(s)===bodyType(actual);}else if(/цвет/.test(s)){actual=f.color;if(color(actual))ok=/ярк/.test(s)===['Жёлтый','Красный','Зелёный','Оранжевый','Фиолетовый','Голубой'].includes(color(actual));}}
  if(e.field==='drive'||e.field==='gearbox'){actual=f[e.field];const canonical=t=>norm(t).replace(/4wd|awd|полн[а-я]*/g,'полный').replace(/передн[а-я]*/g,'передний').replace(/задн[а-я]*/g,'задний').replace(/мкпп|механ[а-я]*/g,'механика').replace(/акпп|автомат[а-я]*/g,'автомат');if(actual)ok=canonical(actual).includes(canonical(s));}
  if(e.field==='use'||e.field==='economy'){const needs=understand(e.field==='use'&&s==='по городу'?'компактный':s);if(needs.ids.length){const fit=suitability(needs,m);if(fit){actual=fit.reasons.join(', ');ok=true;}else if(needs.ids.every(k=>({space:m.seats&&m.boot,clearance:m.clearance,compact:m.length,fuel:m.city||m.mixed})[k])){actual='не проходит по каталожным характеристикам';ok=false;}}}
  return {...e,status:typeof ok!=='boolean'?'unknown':ok?'yes':'no',actual:actual?String(actual):null};}
export function compareOffers(cars,entries){return cars.filter(c=>c.images?.length||/^https?:/.test(c.image)).map(c=>{const checks=entries.map(e=>check(c,e)),miss=checks.filter(v=>v.status!=='yes');const tier=miss.some(x=>x.priority==='must')?2:miss.length?1:0;return {...c,checks,tier,missMust:miss.filter(x=>x.priority==='must').length,missCount:miss.length};}).sort((a,b)=>a.tier-b.tier||a.missMust-b.missMust||a.missCount-b.missCount||(b.match||0)-(a.match||0));}
