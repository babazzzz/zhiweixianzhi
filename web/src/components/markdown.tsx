/** 报告正文渲染：把 Markdown 转成 HTML 后用统一的 .markdown 样式排版。
 *
 * 复用 reportExport 里的转换器（已做 HTML 转义），保证页面预览、Word 导出、
 * 打印 PDF 三种输出完全一致——不会出现"页面上是表格、导出后变成一堆竖线"。
 */

import { markdownToHtml } from "../reportExport";

export function MarkdownView({ markdown }: { markdown: string }): JSX.Element {
  return <div className="markdown" dangerouslySetInnerHTML={{ __html: markdownToHtml(markdown) }} />;
}
