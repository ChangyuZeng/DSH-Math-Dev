import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
const raw = readFileSync(process.argv[2], 'utf8');
if (raw && !raw.endsWith('\n')) throw Error('Torn audit journal');
let previous = '0'.repeat(64), seq = 0;
const rows = raw.split('\n').filter(Boolean).map(line => {
  const row = JSON.parse(line), {sha256, ...data} = row;
  const actual = createHash('sha256').update(JSON.stringify(data)).digest('hex');
  if (row.seq !== ++seq || row.previous !== previous || actual !== sha256) throw Error('Audit integrity failure');
  previous = sha256;
  return row;
});
process.stdout.write(JSON.stringify(rows));
