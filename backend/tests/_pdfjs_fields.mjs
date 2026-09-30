// Test helper (B3): `node _pdfjs_fields.mjs <file.pdf> <pdfjs-dist/legacy/build dir>`
// prints {fields: getFieldObjects(), annots: [widget annotations]} as JSON.
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
import { join } from "node:path";

const base = process.argv[3];
const pdfjs = await import(pathToFileURL(join(base, "pdf.mjs")).href);
pdfjs.GlobalWorkerOptions.workerSrc = pathToFileURL(join(base, "pdf.worker.mjs")).href;

const data = new Uint8Array(readFileSync(process.argv[2]));
const doc = await pdfjs.getDocument({ data, verbosity: 0 }).promise;
const objects = (await doc.getFieldObjects()) || {};
const fields = {};
for (const [name, list] of Object.entries(objects)) {
  fields[name] = list.map((o) => ({
    type: o.type, value: o.value, exportValues: o.exportValues, editable: o.editable, rect: o.rect, page: o.page,
  }));
}
const annots = [];
for (let i = 1; i <= doc.numPages; i++) {
  const page = await doc.getPage(i);
  for (const a of await page.getAnnotations()) {
    if (a.subtype === "Widget") {
      annots.push({ page: i, fieldName: a.fieldName, fieldType: a.fieldType, rect: a.rect, fieldValue: a.fieldValue,
        buttonValue: a.buttonValue });
    }
  }
}
process.stdout.write(JSON.stringify({ fields, annots }));
