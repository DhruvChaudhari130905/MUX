// Shared @mention logic. One source of truth so the composer autocomplete, the
// feed highlighting and the notifications all agree on what counts as a mention.

export interface MentionMember {
  id: string;
  name: string;
  email?: string;
}

// The handles one user answers to: their full name, first name and email prefix.
export function handlesFor(user: { name?: string; email?: string }): string[] {
  const out = new Set<string>();
  const name = user.name?.trim();
  if (name) {
    out.add(name.toLowerCase());
    const first = name.split(/\s+/)[0];
    if (first && first.length > 1) out.add(first.toLowerCase());
  }
  const local = user.email?.split('@')[0];
  if (local) out.add(local.toLowerCase());
  return [...out];
}

// Every handle a message could address: each member's handles plus the
// broadcast tokens everyone in a room understands.
export function mentionTokens(members: MentionMember[]): string[] {
  const tokens = new Set<string>();
  for (const m of members) for (const h of handlesFor(m)) tokens.add(h);
  tokens.add('everyone');
  tokens.add('here');
  return [...tokens];
}

// An "@..." token in text: "@sam" or "@Sam Richardson," with trailing
// punctuation stripped, lowercased.
const TOKEN = /@[^\s@]+/g;
const TRAILING = /[.,;:!?)\]]+$/;

function tokensIn(text: string): string[] {
  const out: string[] = [];
  for (const m of text.matchAll(TOKEN)) out.push(m[0].replace(TRAILING, '').slice(1).toLowerCase());
  return out;
}

// True when the text mentions this member (or @everyone / @here). Whole-token
// matching: "@sam" never lights up inside "sample@mux.dev".
export function isMentioned(text: string, user: { name?: string; email?: string }): boolean {
  if (!text) return false;
  const broadcast = tokensIn(text).some(t => t === 'everyone' || t === 'here');
  if (broadcast) return true;
  const handles = new Set(handlesFor(user));
  return tokensIn(text).some(t => handles.has(t));
}

// Splits text around mentions so callers can style them (feed, toasts).
export interface MentionSegment {
  text: string;
  mention: boolean;
}

export function splitMentions(text: string, members: MentionMember[]): MentionSegment[] {
  if (!text) return [];
  const tokens = new Set(mentionTokens(members));
  const parts: MentionSegment[] = [];
  let last = 0;
  TOKEN.lastIndex = 0;
  for (let m = TOKEN.exec(text); m; m = TOKEN.exec(text)) {
    const raw = m[0].replace(TRAILING, '');
    if (tokens.has(raw.slice(1).toLowerCase())) {
      if (m.index > last) parts.push({ text: text.slice(last, m.index), mention: false });
      parts.push({ text: raw, mention: true });
      last = m.index + raw.length;
      TOKEN.lastIndex = last;
    }
  }
  if (last < text.length) parts.push({ text: text.slice(last), mention: false });
  return parts;
}
