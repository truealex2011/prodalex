// Catalog paths seed discovery; only measurements collected at search time qualify a car.
const rules = [
  ['fuel', /экономич|малолитраж|мал[оы].{0,20}(ест|жрет|кушает|бензин|топлив|расход)|(?:ест|жрет|кушает|расход).{0,18}(мало|небольш|низк|маленьк)|не (?:жрет|прожорлив)|экономи.{0,15}(бензин|топлив)|редко.{0,15}заправ|расход.{0,15}\d/, ['toyota/aqua','daewoo/matiz','honda/fit']],
  ['space', /семь[яеию]|семейн|дет[ьи]|ребен|коляск|багажник|вместитель|много места/, ['skoda/octavia','toyota/wish','lada/largus']],
  ['clearance', /плох.{0,8}дорог|бездорож|на дачу|по грязи|клиренс|высок.{0,8}посадк/, ['renault/duster','lada/4x4_2121_niva','subaru/forester']],
  ['compact', /парков|припарков|компакт|маленьк.{0,10}машин|тесн.{0,8}двор/, ['daewoo/matiz','toyota/vitz','honda/fit']]
];
export function understand(text) {
  const q=text.toLowerCase().replaceAll('ё','е');
  const active=rules.filter(([,pattern])=>pattern.test(q));
  const fuelLimit=q.match(/(?:до|не больше|не более)\s*(\d+(?:[.,]\d+)?)\s*л(?:итр|\s|\/|$)/);
  if(fuelLimit && !active.some(([id])=>id==='fuel')) active.push(rules[0]);
  return {ids:active.map(([id])=>id), paths:[...new Set([0,1,2].flatMap(i=>active.map(r=>r[2][i])))].slice(0,5), fuelLimit:fuelLimit?Number(fuelLimit[1].replace(',','.')):null};
}
const gearbox=s=>/вариатор|cvt/i.test(s)?'cvt':/механик|мкпп/i.test(s)?'mt':/автомат|акпп/i.test(s)?'at':/робот/i.test(s)?'amt':'';
export function pickTrim(rows,f) {
  let group;
  for(const [text,href] of rows) {
    const header=text.match(/(\d+[.,]\d+)\s*л,\s*(\d+)\s*л\.с/i);
    if(header) group={cc:parseFloat(header[1].replace(',','.')),hp:+header[2],box:gearbox(text),four:/4wd|полный привод/i.test(text)};
    if(!group || !href || group.cc!==parseFloat(f.engine.replace(',','.')) || Math.abs(group.hp-Number(f.power))>1 || !group.box || group.box!==gearbox(f.gearbox)) continue;
    if(f.drive && group.four!==/4wd|полный/i.test(f.drive)) continue;
    const period=text.match(/\d{2}\.(\d{4})\s*[-–]\s*(?:\d{2}\.(\d{4})|н\.в)/);
    if(period && +f.year>=+period[1] && +f.year<=+(period[2]||9999)) return href;
  }
}
export function measurements(rows) {
  const val=re=>{const row=rows.find(([label])=>re.test(label));return row && Number(row[1]?.match(/\d+(?:[.,]\d+)?/)?.[0]?.replace(',','.')) || null;};
  return {city:val(/расход топлива в городском/i), mixed:val(/расход топлива в смешанном|расход топлива в комбинированном/i),boot:val(/^объем багажника/i),seats:val(/^число мест/i),clearance:val(/^клиренс/i),length:val(/^габариты кузова|^длина кузова/i)};
}
export function suitability(needs,m={}) {
  let score=0;const reasons=[];
  for(const id of needs.ids){
    if(id==='fuel'){
      const l=m.city||m.mixed;
      if(!l || l>(needs.fuelLimit??(m.city?8:7))) return null;
      reasons.push(`${m.city?'В городе':'В смешанном цикле'}: ${l} л/100 км`);score+=10-l;
    } else if(id==='space'){
      if(!(m.boot>=400 && m.seats>=5)) return null;
      reasons.push(`Багажник ${m.boot} л, мест: ${m.seats}`);score+=Math.min(m.boot/100,10);
    } else if(id==='clearance'){
      if(!(m.clearance>=180)) return null;
      reasons.push(`Клиренс ${m.clearance} мм`);score+=m.clearance/30;
    } else if(id==='compact'){
      if(!(m.length>=2000 && m.length<=4200)) return null;
      reasons.push(`Длина кузова ${m.length} мм`);score+=(4500-m.length)/100;
    }
  }
  return {score,reasons};
}
