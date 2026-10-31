import {readFileSync, writeFileSync} from 'node:fs';
import {spawnSync} from 'node:child_process';
const sources=['forms.cjs','form-ui.cjs','process-graph.cjs','graph-ui.cjs','client-theme.cjs','client-main.cjs'];
for(const source of sources){const result=spawnSync(process.execPath,['--check',source],{stdio:'inherit'});if(result.status)process.exit(result.status);}
writeFileSync('client.js',sources.map(source=>readFileSync(source,'utf8')).join('\n'));
for(const source of ['host.mjs','human-api.mjs']){const result=spawnSync(process.execPath,['--check',source],{stdio:'inherit'});if(result.status)process.exit(result.status);}
for(const language of ['en','zh']) {
  const locale=JSON.parse(readFileSync(`locale/${language}.json`,'utf8'));
  if(!locale.meta?.title || !locale.meta?.description) throw Error(`Missing ${language} plugin metadata`);
}

