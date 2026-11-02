// Untrusted material bytes are parsed as data only. No URLs, macros or code run.
import fs from 'node:fs';
// Keep stdout a single JSON value even when parsers emit diagnostics.
console.log = () => {};
console.warn = () => {};
const MAX = 200000;
const name = process.argv[2] || '';
const raw = fs.readFileSync(0);
const result = {format: name.split('.').pop().toLowerCase(), status: 'archive_only', text: '', truncated: false, warnings: []};
const append = (value) => {
  const remaining = MAX - result.text.length;
  if (value.length > remaining) result.truncated = true;
  result.text += value.slice(0, remaining);
};
try {
  if (raw.length > 20 * 1024 * 1024) throw new Error('file exceeds extraction limit');
  if (result.format === 'pdf') {
    const {getDocument} = await import('pdfjs-dist/legacy/build/pdf.mjs');
    const task = getDocument({data: new Uint8Array(raw), isEvalSupported: false,
      useSystemFonts: false, disableFontFace: true, useWorkerFetch: false,
      stopAtErrors: true, disableAutoFetch: true, disableStream: true,
      maxImageSize: 0, verbosity: 0});
    let document;
    try {
      document = await task.promise;
      for (let page = 1; page <= Math.min(document.numPages, 200) && result.text.length < MAX; page++) {
        const view = await document.getPage(page);
        const content = await view.getTextContent();
        append(`\n[page ${page}]\n` + content.items.map(item => item.str || '').join(' ') + '\n');
        view.cleanup();
      }
      if (document.numPages > 200 || result.text.length >= MAX) result.truncated = true;
      if (!result.text.replace(/\[page \d+\]/g, '').trim()) {
        result.text = ''; result.status = 'archive_only'; result.warnings.push('No selectable PDF text; OCR is not performed.');
      } else result.status = 'extracted';
    } finally { await task.destroy(); }
  } else if (result.format === 'docx') {
    const mammoth = await import('mammoth');
    const extracted = await (mammoth.default || mammoth).extractRawText({buffer: raw});
    append(extracted.value);
    result.warnings.push(...extracted.messages.slice(0, 20).map(item => String(item.message).slice(0, 500)));
    result.status = 'extracted';
  } else {
    result.warnings.push('Unsupported format; original archived without extraction.');
  }
} catch (error) {
  result.status = 'failed'; result.text = '';
  result.warnings.push(`Extraction failed (${String(error?.name || 'Error').slice(0, 80)}); original can still be archived.`);
}
process.stdout.write(JSON.stringify(result));
