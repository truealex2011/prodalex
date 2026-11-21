import {mkdir,readFile,writeFile,rename,stat,rm,readdir} from 'node:fs/promises';
import {join} from 'node:path';
import {homedir} from 'node:os';
import {gunzipSync} from 'node:zlib';

export const modelDir=join(homedir(),'.cache','avtoradar','3d');
const base='https://huggingface.co/datasets/allenai/objaverse/resolve/main/';
const pending=new Map(),allowed=new Map();let index;
export function assetPaths(){
  return index??=(async()=>{
    await mkdir(modelDir,{recursive:true});const file=join(modelDir,'object-paths.json.gz');
    try{return JSON.parse(gunzipSync(await readFile(file)));}catch{}
    const response=await fetch(base+'object-paths.json.gz',{signal:AbortSignal.timeout(90000)});if(!response.ok)throw Error('Индекс GLB недоступен');
    const gz=Buffer.from(await response.arrayBuffer()), paths=JSON.parse(gunzipSync(gz));await writeFile(file+'.tmp',gz);await rename(file+'.tmp',file);return paths;
  })().catch(e=>{index=null;throw e});
}
export function registerModel(model,path){allowed.set(model.uid,{model,path});}
export async function savedMatch(query){
  for(const name of await readdir(modelDir).catch(()=>[])){
    if(!/^[a-f0-9]{32}\.json$/.test(name))continue;
    try{const m=JSON.parse(await readFile(join(modelDir,name),'utf8'));if(m.query?.name?.toLowerCase()===query.name.toLowerCase() && m.query.year===query.year && m.query.body===query.body && (await stat(join(modelDir,m.uid+'.glb'))).isFile())return m;}catch{}
  }return null;
}
const maxBytes=96*1024*1024;
export function verifyGLB(b){
  if(b.length<20||b.toString('ascii',0,4)!=='glTF'||b.readUInt32LE(4)!==2||b.readUInt32LE(8)!==b.length||b.toString('ascii',16,20)!=='JSON')throw Error('Некорректный GLB');
  const doc=JSON.parse(b.toString('utf8',20,20+b.readUInt32LE(12)));
  if(!doc.meshes?.length||[...(doc.images||[]),...(doc.buffers||[])].some(x=>x.uri&&!x.uri.startsWith('data:')))throw Error('Файл не содержит автономной модели');
}
export async function localGLB(id){
  if(!/^[a-f0-9]{32}$/.test(id))return null;
  const file=join(modelDir,id+'.glb');
  try{const s=await stat(file);if(s.isFile()&&s.size>=20)return {file,size:s.size};}catch{}
  if(pending.has(id))return pending.get(id);
  const entry=allowed.get(id);if(!entry)return null;
  const {path,model}=entry;
  const downloadURL=String(path).startsWith('http')?path:base+path;
  if(!String(path).startsWith('http')&&!new RegExp('^glbs/\\d+-\\d+/'+id+'\\.glb$').test(path))return null;
  const task=(async()=>{
    const r=await fetch(downloadURL,{signal:AbortSignal.timeout(85000)});if(!r.ok||+r.headers.get('content-length')>maxBytes){await r.body?.cancel();throw Error('GLB недоступен');}
    const chunks=[];let size=0;for await(const chunk of r.body){size+=chunk.length;if(size>maxBytes)throw Error('GLB слишком большой');chunks.push(chunk);}
    const bytes=Buffer.concat(chunks);verifyGLB(bytes);
    await writeFile(file+'.part',bytes);await writeFile(join(modelDir,id+'.json'),JSON.stringify({...model,archive:downloadURL},null,2));await rename(file+'.part',file);return {file,size};
  })();pending.set(id,task);try{return await task;}finally{pending.delete(id);await rm(file+'.part',{force:true}).catch(()=>{});}
}
