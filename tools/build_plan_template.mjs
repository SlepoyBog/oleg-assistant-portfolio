import fs from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const outputPath = fileURLToPath(new URL("../templates/weekly-plan.xlsx", import.meta.url));
const previewPath = fileURLToPath(new URL("../templates/weekly-plan-preview.png", import.meta.url));
const workbook = Workbook.create();
const sheet = workbook.worksheets.add("Мой распорядок");

sheet.showGridLines = false;
sheet.freezePanes.freezeRows(3);

sheet.getRange("A1:K1").merge();
sheet.getRange("A1").values = [["Мой распорядок"]];
sheet.getRange("A1:K1").format = {
  font: { name: "Arial", size: 15, bold: true, color: "#1F2937" },
  verticalAlignment: "center",
};
sheet.getRange("A1:K1").format.rowHeight = 28;

sheet.getRange("A2").values = [["Неделя с"]];
sheet.getRange("G2").values = [[null]];
sheet.getRange("G2").format.numberFormat = "dd.mm.yyyy";
sheet.getRange("A2:G2").format.font = { name: "Arial", size: 10, italic: true, color: "#64748B" };

sheet.getRange("A3:H3").values = [["Начало", "Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]];
sheet.getRange("A3:H3").format = {
  fill: "#1F4E78",
  font: { name: "Arial", size: 10, bold: true, color: "#FFFFFF" },
  horizontalAlignment: "center",
  verticalAlignment: "center",
  borders: { preset: "all", style: "thin", color: "#FFFFFF" },
};
sheet.getRange("A3:H3").format.rowHeight = 24;

const timeRows = [];
for (let minutes = 8 * 60; minutes <= 17 * 60; minutes += 15) {
  timeRows.push([new Date(Date.UTC(1899, 11, 30, Math.floor(minutes / 60), minutes % 60))]);
}
sheet.getRange("A4").write(timeRows);
sheet.getRange("A4:A40").format.numberFormat = "hh:mm";
sheet.getRange("A4:A40").format = {
  fill: "#EAF2F8",
  font: { name: "Arial", size: 9, color: "#334155" },
  horizontalAlignment: "center",
  verticalAlignment: "center",
  borders: { preset: "all", style: "thin", color: "#CBD5E1" },
};
sheet.getRange("B4:H40").format = {
  font: { name: "Arial", size: 9, color: "#1F2937" },
  horizontalAlignment: "center",
  verticalAlignment: "center",
  wrapText: true,
  borders: { preset: "all", style: "thin", color: "#DCE3EA" },
};
sheet.getRange("A4:H40").format.rowHeight = 20;

for (const column of ["B", "C", "D", "E", "F", "G"]) {
  sheet.getRange(`${column}22:${column}25`).merge();
  sheet.getRange(`${column}22`).values = [["Обед"]];
  sheet.getRange(`${column}22:${column}25`).format = {
    fill: "#FFF2CC",
    font: { name: "Arial", size: 10, bold: true, color: "#7C5A00" },
    horizontalAlignment: "center",
    verticalAlignment: "center",
    borders: { preset: "outside", style: "thin", color: "#D6B656" },
  };
}

sheet.getRange("A42:K42").merge();
sheet.getRange("A42").values = [["Как менять план: дата, начало и длительность редактируются вручную. Поставьте «Да» в столбце «Закреплено», чтобы бот не переносил задачу автоматически."]];
sheet.getRange("A42:K42").format = {
  fill: "#FFF7E6",
  font: { name: "Arial", size: 9, italic: true, color: "#7C4A03" },
  wrapText: true,
  verticalAlignment: "center",
};
sheet.getRange("A42:J42").format.rowHeight = 34;

const headers = [["ID", "Приоритет", "Задача / проект", "Дата", "Начало", "Длительность, мин", "Крайний срок", "Закреплено", "Статус", "Категория", "Готово, %"]];
sheet.getRange("A57:K57").values = headers;
sheet.getRange("A57:K57").format = {
  fill: "#1F4E78",
  font: { name: "Arial", size: 10, bold: true, color: "#FFFFFF" },
  horizontalAlignment: "center",
  verticalAlignment: "center",
  wrapText: true,
  borders: { preset: "all", style: "thin", color: "#FFFFFF" },
};
sheet.getRange("A57:K57").format.rowHeight = 30;
sheet.getRange("A58:K73").format = {
  font: { name: "Arial", size: 9, color: "#1F2937" },
  verticalAlignment: "center",
  borders: { preset: "all", style: "thin", color: "#DCE3EA" },
};
sheet.getRange("C58:C73").format.wrapText = true;
sheet.getRange("D58:D73").format.numberFormat = "dd.mm.yyyy";
sheet.getRange("K58:K73").format.numberFormat = "0%";
sheet.getRange("D58:H73").format.fill = "#FFF9DB";
sheet.getRange("H58:H73").dataValidation = { rule: { type: "list", values: ["Да", "Нет"] } };
sheet.getRange("A58:K73").format.rowHeight = 26;

const widths = {
  A: 9, B: 13, C: 34, D: 13, E: 11, F: 18, G: 14, H: 14, I: 18, J: 16, K: 12,
};
for (const [column, width] of Object.entries(widths)) {
  sheet.getRange(`${column}:${column}`).format.columnWidth = width;
}

workbook.recalculate();
const check = await workbook.inspect({
  kind: "table",
  sheetId: sheet.name,
  range: "A1:K73",
  include: "values,formulas",
  tableMaxRows: 73,
  tableMaxCols: 11,
  maxChars: 8000,
});
console.log(check.ndjson);

const errors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",
  options: { useRegex: true, maxResults: 100 },
  summary: "final formula error scan",
});
console.log(errors.ndjson);

const preview = await workbook.render({
  sheetName: "Мой распорядок",
  range: "A1:K73",
  scale: 1,
  format: "png",
});
await fs.mkdir(fileURLToPath(new URL("../templates/", import.meta.url)), { recursive: true });
await fs.writeFile(previewPath, new Uint8Array(await preview.arrayBuffer()));

const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);
