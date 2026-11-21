// Marketplace taxonomy, shared by retrieval and verification. The user's wording
// need not contain a body type: the query planner/translator can supply one.
const taxonomy=[
 ['kabriolet',/кабриолет|родстер|\b(convertible|roadster|cabriolet)s?\b/i],
 ['pikap',/пикап|\bpick[ -]?up(?: truck)?s?\b/i],['miniven',/минив[еэ]н|\bminivans?\b/i],
 ['universal',/универсал(?:а|ы|ом)?(?:\s|$)|\bstation wagons?|\bestate cars?/i],
 ['kupe',/\b(?:coupe|coupé)s?\b|купе/i],['hetchbek',/х[еэ]тчб[еэ]к|\bhatchbacks?\b/i],
 ['sedan',/седан|\bsedans?\b/i],['vnedorozhnik',/внедорожник|кроссовер|\b(?:suv|crossover)s?\b/i]
];
export function bodyType(text){const found=taxonomy.filter(([,re])=>re.test(text));return found.length===1?found[0][0]:null;}

// Distinguish experiencing the air from wanting shelter. A windy destination alone
// says nothing about the desired body type.
export function requestedBody(text,expansion=''){
  const q=text.toLowerCase().replaceAll('ё','е');
  const shelter=/(?:спрят|укры|защит|избеж|бо[юя]сь|не хочу|не дул|не треп|не растреп|не развева|без ветра|без сквозняк|не испортить)/.test(q);
  const air=/вет[её]?р|ветром|бриз|поток воздуха|обдува|развева/.test(q);
  const feeling=/ощущ|почувств|чувств|развева|обдува|волос|шевелюр|локон|трепал/.test(q);
  if(air && shelter)return bodyType(q)==='kabriolet'?null:bodyType(q);
  if(air && feeling && !bodyType(q))return 'kabriolet';
  return bodyType(q)||bodyType(expansion);
}
