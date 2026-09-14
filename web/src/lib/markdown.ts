/** The guide's small Markdown subset. Raw HTML is always rendered as text. */
export type Block =
  | { kind: 'heading'; level: number; text: string }
  | { kind: 'paragraph' | 'code'; text: string }
  | { kind: 'list'; ordered: boolean; items: string[] };

export function blocks(markdown: string): Block[] {
  const lines = markdown.replace(/\r\n/g, '\n').split('\n');
  const result: Block[] = [];
  for (let i = 0; i < lines.length;) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }
    if (line.startsWith('```')) {
      const code: string[] = [];
      i++;
      while (i < lines.length && !lines[i].startsWith('```')) code.push(lines[i++]);
      i++;
      result.push({ kind: 'code', text: code.join('\n') });
      continue;
    }
    const heading = /^(#{1,6})\s+(.+)$/.exec(line);
    if (heading) {
      result.push({ kind: 'heading', level: heading[1].length, text: heading[2] });
      i++;
      continue;
    }
    const list = /^(?:[-*]|\d+\.)\s+(.+)$/.exec(line);
    if (list) {
      const ordered = /^\d/.test(line);
      const pattern = ordered ? /^\d+\.\s+(.+)$/ : /^[-*]\s+(.+)$/;
      const items: string[] = [];
      let match: RegExpExecArray | null;
      while (i < lines.length && (match = pattern.exec(lines[i]))) {
        items.push(match[1]); i++;
      }
      result.push({ kind: 'list', ordered, items });
      continue;
    }
    const paragraph = [lines[i++]];
    while (i < lines.length && lines[i].trim() && !/^(?:#|```|[-*] |\d+\. )/.test(lines[i])) {
      paragraph.push(lines[i++]);
    }
    result.push({ kind: 'paragraph', text: paragraph.join(' ') });
  }
  return result;
}

export function inline(text: string): { kind: 'text' | 'code' | 'strong'; text: string }[] {
  return text.split(/(`[^`]+`|\*\*[^*]+\*\*)/g).filter(Boolean).map((part) =>
    part.startsWith('`') ? { kind: 'code', text: part.slice(1, -1) }
      : part.startsWith('**') ? { kind: 'strong', text: part.slice(2, -2) }
        : { kind: 'text', text: part }
  );
}
