// Line diff for reviewing a held change (components/side/ChangeRequestCard.tsx): longest common subsequence,
// so unchanged lines stay as context. Big files skip the table and show every line as removed then added.
export type DiffLine = { kind: ' ' | '+' | '-'; text: string };

const MAX_CELLS = 400_000;

export function lineDiff(before: string, after: string): DiffLine[] {
  const a = before ? before.split('\n') : [];
  const b = after ? after.split('\n') : [];
  if (a.length * b.length > MAX_CELLS) {
    return [...a.map(text => ({ kind: '-' as const, text })), ...b.map(text => ({ kind: '+' as const, text }))];
  }
  // lcs[i][j]: length of the common subsequence of a[i:] and b[j:]
  const lcs = Array.from({ length: a.length + 1 }, () => new Array<number>(b.length + 1).fill(0));
  for (let i = a.length - 1; i >= 0; i--) {
    for (let j = b.length - 1; j >= 0; j--) {
      lcs[i][j] = a[i] === b[j] ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
    }
  }
  const out: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      out.push({ kind: ' ', text: a[i] });
      i++;
      j++;
    } else if (lcs[i + 1][j] >= lcs[i][j + 1]) {
      out.push({ kind: '-', text: a[i++] });
    } else {
      out.push({ kind: '+', text: b[j++] });
    }
  }
  while (i < a.length) out.push({ kind: '-', text: a[i++] });
  while (j < b.length) out.push({ kind: '+', text: b[j++] });
  return out;
}

// Changed lines with `context` unchanged lines around each; gaps become one "…" line
export function hunks(lines: DiffLine[], context = 2): (DiffLine | null)[] {
  const keep = lines.map((l, i) => l.kind !== ' ' || lines.slice(Math.max(0, i - context), i + context + 1).some(n => n.kind !== ' '));
  const out: (DiffLine | null)[] = [];
  keep.forEach((k, i) => {
    if (k) out.push(lines[i]);
    else if (out[out.length - 1] !== null) out.push(null);
  });
  return out;
}
