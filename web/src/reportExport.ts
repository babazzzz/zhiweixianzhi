/** 维护报告导出：Markdown / Word / 打印（可另存为 PDF）。
 *
 * 报告正文是 Markdown，这里把它转成自包含的 HTML（内联样式），
 * Word 与打印共用同一套渲染，保证三种导出物长得一样。
 */

export interface ExportableReport {
  id: string;
  title: string;
  summary?: string;
  markdown: string;
  generated_at?: string;
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/** 处理行内语法：**粗体**、`代码`。 */
function inline(text: string): string {
  return escapeHtml(text)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/`(.+?)`/g, "<code>$1</code>");
}

/** 把报告 Markdown 转成 HTML 片段（支持标题/表格/列表/引用）。 */
export function markdownToHtml(markdown: string): string {
  const lines = markdown.replace(/\r\n/g, "\n").split("\n");
  const html: string[] = [];
  let index = 0;

  const flushTable = (): void => {
    const rows: string[][] = [];
    while (index < lines.length && lines[index].trim().startsWith("|")) {
      const cells = lines[index].trim().replace(/^\||\|$/g, "").split("|").map((cell) => cell.trim());
      if (!/^-{2,}$/.test(cells[0].replace(/\s/g, ""))) rows.push(cells); // 跳过分隔行
      index += 1;
    }
    if (rows.length) {
      const head = rows[0];
      const body = rows.slice(1);
      html.push("<table><thead><tr>" + head.map((cell) => `<th>${inline(cell)}</th>`).join("") + "</tr></thead><tbody>"
        + body.map((row) => "<tr>" + row.map((cell) => `<td>${inline(cell)}</td>`).join("") + "</tr>").join("")
        + "</tbody></table>");
    }
  };

  while (index < lines.length) {
    const line = lines[index];
    const trimmed = line.trim();
    if (trimmed.startsWith("|")) { flushTable(); continue; }
    if (/^#{1,3}\s/.test(trimmed)) {
      const level = trimmed.match(/^#+/)![0].length;
      html.push(`<h${level}>${inline(trimmed.replace(/^#+\s*/, ""))}</h${level}>`);
    } else if (/^[-*]\s/.test(trimmed)) {
      const items: string[] = [];
      while (index < lines.length && /^\s*[-*]\s/.test(lines[index])) {
        items.push(`<li>${inline(lines[index].trim().replace(/^[-*]\s*/, ""))}</li>`);
        index += 1;
      }
      html.push(`<ul>${items.join("")}</ul>`);
      continue;
    } else if (/^\d+\.\s/.test(trimmed)) {
      const items: string[] = [];
      while (index < lines.length && /^\s*\d+\.\s/.test(lines[index])) {
        items.push(`<li>${inline(lines[index].trim().replace(/^\d+\.\s*/, ""))}</li>`);
        index += 1;
      }
      html.push(`<ol>${items.join("")}</ol>`);
      continue;
    } else if (trimmed.startsWith(">")) {
      html.push(`<blockquote>${inline(trimmed.replace(/^>\s*/, ""))}</blockquote>`);
    } else if (trimmed) {
      html.push(`<p>${inline(trimmed)}</p>`);
    }
    index += 1;
  }
  return html.join("\n");
}

const DOC_STYLE = `
  body { font-family: "Microsoft YaHei", "微软雅黑", sans-serif; color: #1f2328; margin: 32px; line-height: 1.75; }
  h1 { font-size: 22px; border-bottom: 2px solid #1f2328; padding-bottom: 8px; }
  h2 { font-size: 17px; margin-top: 26px; border-left: 4px solid #1f2328; padding-left: 10px; }
  h3 { font-size: 15px; margin-top: 20px; }
  table { border-collapse: collapse; width: 100%; margin: 12px 0; font-size: 13px; }
  th, td { border: 1px solid #d0d7de; padding: 6px 10px; text-align: left; }
  th { background: #f6f8fa; }
  code { background: #f6f8fa; padding: 1px 5px; border-radius: 4px; }
  blockquote { color: #57606a; border-left: 3px solid #d0d7de; margin: 10px 0; padding: 2px 12px; }
  .meta { color: #57606a; font-size: 12px; margin-bottom: 18px; }
`;

/** 生成完整、可独立打开的 HTML 文档。 */
export function reportToHtml(report: ExportableReport): string {
  const meta = [`报告编号：${report.id}`, report.generated_at ? `生成时间：${new Date(report.generated_at).toLocaleString("zh-CN")}` : ""]
    .filter(Boolean)
    .join(" ｜ ");
  return `<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8" /><title>${escapeHtml(report.title)}</title>`
    + `<style>${DOC_STYLE}</style></head><body>`
    + `<h1>${escapeHtml(report.title)}</h1><p class="meta">${escapeHtml(meta)}</p>`
    + markdownToHtml(report.markdown)
    + `</body></html>`;
}

function saveBlob(content: string, filename: string, mime: string): void {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);
  URL.revokeObjectURL(url);
}

/** 下载 Markdown 源文件。 */
export function downloadMarkdown(report: ExportableReport): void {
  saveBlob(report.markdown, `${report.title || report.id}.md`, "text/markdown;charset=utf-8");
}

/** 下载 Word 文档（.doc，Word/WPS 均可直接打开）。 */
export function downloadWord(report: ExportableReport): void {
  saveBlob(reportToHtml(report), `${report.title || report.id}.doc`, "application/msword;charset=utf-8");
}

/** 打开打印视图——在打印对话框里选择"另存为 PDF"即可导出 PDF。 */
export function printReport(report: ExportableReport): void {
  const frame = document.createElement("iframe");
  frame.style.position = "fixed";
  frame.style.right = "0";
  frame.style.bottom = "0";
  frame.style.width = "0";
  frame.style.height = "0";
  frame.style.border = "0";
  document.body.appendChild(frame);
  const doc = frame.contentDocument;
  if (!doc) return;
  doc.open();
  doc.write(reportToHtml(report));
  doc.close();
  frame.onload = () => {
    frame.contentWindow?.focus();
    frame.contentWindow?.print();
    setTimeout(() => document.body.removeChild(frame), 1000);
  };
}
