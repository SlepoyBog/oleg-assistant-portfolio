import fs from "node:fs/promises";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

if (process.argv.length !== 4) {
  throw new Error("Usage: inspect_workbook.mjs INPUT_XLSX PREVIEW_PNG");
}

const [, , inputPath, previewPath] = process.argv;
const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(inputPath));
const table = await workbook.inspect({
  kind: "table",
  sheetId: "Мой распорядок",
  range: "A1:J73",
  include: "values,formulas",
  tableMaxRows: 73,
  tableMaxCols: 10,
  maxChars: 12000,
});
console.log(table.ndjson);
const errors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",
  options: { useRegex: true, maxResults: 100 },
  summary: "formula error scan",
});
console.log(errors.ndjson);
const preview = await workbook.render({
  sheetName: "Мой распорядок",
  range: "A1:J73",
  scale: 1,
  format: "png",
});
await fs.writeFile(previewPath, new Uint8Array(await preview.arrayBuffer()));
