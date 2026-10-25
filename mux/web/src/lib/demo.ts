// Demo mode: a signed-in user, rooms and a live-looking room served entirely from the browser,
// so every screen can be exercised without Supabase or the backend.
// Enable with NEXT_PUBLIC_DEMO_MODE=true, or the "Try the demo" button on the login page.
//
// There are a few ready-made sample projects, each with its own plan, feed, decisions, files and
// preview. Rooms the user creates start blank and are kept in localStorage.
import type { PlanName, UserPlan, AppEvent, Membership, MessageTo, PlanItem, Room, User } from '@/types';

const DEMO_FLAG_KEY = 'mux_demo';
const CREATED_ROOMS_KEY = 'mux_demo_rooms';

export function isDemoMode(): boolean {
  if (process.env.NEXT_PUBLIC_DEMO_MODE === 'true') return true;
  if (typeof window === 'undefined') return false;
  try {
    return window.localStorage.getItem(DEMO_FLAG_KEY) === '1';
  } catch {
    return false;
  }
}

// True when demo mode was switched on in this browser (an "Open the demo" button), as opposed to a
// whole deployment built with NEXT_PUBLIC_DEMO_MODE. Only the browser flag can be exited.
export function isBrowserDemo(): boolean {
  if (process.env.NEXT_PUBLIC_DEMO_MODE === 'true' || typeof window === 'undefined') return false;
  try {
    return window.localStorage.getItem(DEMO_FLAG_KEY) === '1';
  } catch {
    return false;
  }
}

// Leave the demo and go back to your own rooms (or the sign-in page if you aren't signed in)
export function exitDemo() {
  setDemoMode(false);
  window.location.href = '/dashboard';
}

export function setDemoMode(on: boolean) {
  try {
    if (on) window.localStorage.setItem(DEMO_FLAG_KEY, '1');
    else window.localStorage.removeItem(DEMO_FLAG_KEY);
  } catch {
    // storage unavailable; demo mode just won't persist
  }
}

function makeUser(id: string, name: string, color: string): User {
  const initials = name.split(' ').map(p => p[0]).join('').slice(0, 2).toUpperCase();
  return { id, email: `${id}@demo.mux`, name, initials, color };
}

export const DEMO_USER = makeUser('demo-you', 'Demo User', '#58a6ff');
const PRIYA = makeUser('demo-priya', 'Priya Shah', '#d2a8ff');
const MARCO = makeUser('demo-marco', 'Marco Diaz', '#3fb950');
const DAN = makeUser('demo-dan', 'Dan Okafor', '#9ad0f5');

// Shape of the Supabase user object the pages read from getUser()
export const DEMO_AUTH_USER = {
  id: DEMO_USER.id,
  email: DEMO_USER.email,
  user_metadata: { full_name: DEMO_USER.name, avatar_url: undefined as string | undefined },
};

function membership(roomId: string, user: User, permission: Membership['permission'], domain_role: Membership['domain_role']): Membership {
  return { user_id: user.id, room_id: roomId, permission, domain_role, user };
}

function makeRoom(id: string, title: string, description: string, hoursAgo: number, members: User[] = [PRIYA, MARCO]): Room {
  const created = new Date(Date.now() - hoursAgo * 3600_000).toISOString();
  const roles: Membership['domain_role'][] = ['design', 'eng', 'pm'];
  return {
    id,
    title,
    description,
    owner_id: DEMO_USER.id,
    link_access: 'restricted',
    link_permission: 'editor',
    budget_tokens_cap: 2_000_000,
    budget_runs_cap: 100,
    head_checkpoint_id: null,
    created_at: created,
    updated_at: created,
    members: [membership(id, DEMO_USER, 'owner', 'pm'), ...members.map((u, i) => membership(id, u, 'editor', roles[i % roles.length]))],
  };
}

/* ─────────────── Sample projects ─────────────── */

// What the Preview tab renders for a sample project (stands in for the running app)
export interface DemoPreview {
  brand: string;
  links: string[];
  headline: string;
  tagline: string;
  accent: string; // button color
  background: string;
  ink: string;
  cards: { title: string; meta: string; action: string }[];
  building: string; // placeholder card for the task in progress
}

interface Scenario {
  title: string;
  description: string;
  hoursAgo: number;
  members: User[];
  plan: Omit<PlanItem, 'id'>[];
  messages: { user: User; text: string; label: 'merge' | 'queue' | 'interrupt' | 'conflict' | 'chat'; minutesAgo: number; rationale?: string }[];
  conflict: { task: number; options: string[]; query: string; summary: string; domain: 'ui' | 'architecture' | 'scope' };
  question: { task: number; text: string; options: string[]; default: string };
  budget: { tokens_used: number; runs_used: number };
  preview: DemoPreview;
  files: Record<string, string>;
}

const SCENARIOS: Record<string, Scenario> = {
  'demo-yoga': {
    title: 'Lotus Yoga booking',
    description: 'A booking site for a yoga studio: class schedule, teachers, and booking with Stripe test mode.',
    hoursAgo: 1,
    members: [PRIYA, DAN, MARCO],
    plan: [
      { title: 'Scaffold React + Vite + Tailwind', status: 'done', owner_role: 'eng' },
      { title: 'Navbar with studio logo', status: 'done', owner_role: 'design' },
      { title: 'Hero section', status: 'done', owner_role: 'design' },
      { title: 'Class schedule grid', status: 'doing', owner_role: 'eng', notes: 'coder working · 1 merged note' },
      { title: 'Style booking buttons', status: 'skipped_conflict', owner_role: 'design' },
      { title: 'Checkout flow', status: 'skipped_question', owner_role: 'eng' },
    ],
    messages: [
      { user: DEMO_USER, text: 'Build a booking site for Lotus Yoga with a class schedule.', label: 'merge', minutesAgo: 40 },
      { user: DAN, text: 'Show the teacher name under each class title.', label: 'merge', minutesAgo: 12, rationale: 'Refines the schedule grid.' },
      { user: DAN, text: 'Make all the buttons blue to match the logo.', label: 'conflict', minutesAgo: 9 },
      { user: PRIYA, text: 'Buttons should be green, it feels calmer.', label: 'conflict', minutesAgo: 8 },
      { user: PRIYA, text: 'We also need a login page so members can see their bookings.', label: 'queue', minutesAgo: 3 },
    ],
    conflict: { task: 5, options: ['Blue, match the logo', 'Green, calmer'], query: 'booking button color conversion', summary: 'High-contrast brand-colored CTAs tend to convert best; either works if contrast is AA.', domain: 'ui' },
    question: { task: 6, text: 'Should checkout use Stripe or a fake checkout?', options: ['Stripe test mode', 'Fake checkout'], default: 'Fake checkout' },
    budget: { tokens_used: 1_240_000, runs_used: 38 },
    preview: {
      brand: 'Lotus Yoga',
      links: ['Classes', 'Teachers', 'Pricing', 'Log in'],
      headline: 'Find your class this week',
      tagline: 'Morning flow, evening yin and weekend workshops in Indiranagar. Book a spot in two taps.',
      accent: '#3b82f6',
      background: '#fbf8f4',
      ink: '#2a2521',
      cards: [
        { title: 'Morning Vinyasa', meta: 'Mon 7:00 · 60 min · Anika', action: 'Book' },
        { title: 'Slow Hatha', meta: 'Mon 18:30 · 75 min · Rohan', action: 'Book' },
        { title: 'Yin & Breath', meta: 'Tue 19:00 · 60 min · Meera', action: 'Book' },
      ],
      building: 'Day filter\nbeing built…',
    },
    files: {
      'src/components/ClassCard.tsx': `import type { Yoga } from "../data/classes";

// Edited by coder · task 4 · merged Dan's note
export function ClassCard({ c }: { c: Yoga }) {
  return (
    <div className="rounded-lg border p-4 grid gap-1">
      <h3 className="font-semibold">{c.title}</h3>
      <p className="text-sm text-stone-500">
        {c.day} {c.time} · {c.minutes} min · {c.teacher}
      </p>
      <button className="btn-primary">Book</button>
    </div>
  );
}
`,
    },
  },
  'demo-habit': {
    title: 'Habit tracker',
    description: 'A habit tracker with streaks, reminders and a weekly summary view.',
    hoursAgo: 2,
    members: [PRIYA, MARCO],
    plan: [
      { title: 'Scaffold app shell and routing', status: 'done', owner_role: 'eng' },
      { title: 'Habit list with add / complete', status: 'done', owner_role: 'eng' },
      { title: 'Streak counter and calendar heatmap', status: 'doing', owner_role: 'design' },
      { title: 'Reminder notifications', status: 'todo', owner_role: 'eng' },
      { title: 'Weekly summary view', status: 'todo', owner_role: 'pm' },
      { title: 'Dark mode palette', status: 'todo', owner_role: 'design' },
    ],
    messages: [
      { user: DEMO_USER, text: 'Build a habit tracker with streaks and a weekly summary.', label: 'merge', minutesAgo: 52 },
      { user: MARCO, text: 'Use local storage for now, we can add sync later.', label: 'merge', minutesAgo: 40, rationale: 'Refines the storage approach for t2.' },
      { user: PRIYA, text: 'Streaks should show as a calendar heatmap, not a number.', label: 'conflict', minutesAgo: 20 },
      { user: MARCO, text: 'A plain number is faster to ship — heatmap can come later.', label: 'conflict', minutesAgo: 18 },
      { user: DEMO_USER, text: 'Add a weekly summary email too.', label: 'queue', minutesAgo: 5 },
    ],
    conflict: { task: 3, options: ['Calendar heatmap', 'Streak number'], query: 'habit tracker streak visualization', summary: 'Most popular trackers pair a number with a heatmap; heatmaps improve retention in two studies.', domain: 'ui' },
    question: { task: 4, text: 'Should reminders be push notifications or email?', options: ['Push notifications', 'Email', 'Both'], default: 'Push notifications' },
    budget: { tokens_used: 412_000, runs_used: 14 },
    preview: {
      brand: 'Streaks',
      links: ['Today', 'Habits', 'Summary', 'Settings'],
      headline: 'Small habits, every day',
      tagline: 'Tick off today’s habits and keep your streaks alive. You’re on a 12-day run.',
      accent: '#16a34a',
      background: '#f6faf7',
      ink: '#14281d',
      cards: [
        { title: 'Morning run', meta: '🔥 12-day streak · 6:30', action: 'Done today' },
        { title: 'Read 20 pages', meta: '🔥 5-day streak · evening', action: 'Done today' },
        { title: 'Drink 2L water', meta: '🔥 21-day streak · all day', action: 'Done today' },
      ],
      building: 'Calendar heatmap\nbeing built…',
    },
    files: {
      'src/components/HabitRow.tsx': `import { useState } from "react";

export function HabitRow({ name, streak }: { name: string; streak: number }) {
  const [done, setDone] = useState(false);
  return (
    <li className="flex items-center justify-between rounded-lg border p-3">
      <span>{name} · 🔥 {streak + (done ? 1 : 0)}</span>
      <button onClick={() => setDone(!done)}>{done ? "Undo" : "Done today"}</button>
    </li>
  );
}
`,
    },
  },
  'demo-shop': {
    title: 'NeoCart storefront',
    description: 'A headless storefront with product grid, cart drawer and a fast checkout.',
    hoursAgo: 9,
    members: [DAN, PRIYA, MARCO],
    plan: [
      { title: 'Product catalog data model', status: 'done', owner_role: 'eng' },
      { title: 'Product grid with filters', status: 'done', owner_role: 'design' },
      { title: 'Cart drawer', status: 'doing', owner_role: 'eng' },
      { title: 'Checkout page', status: 'skipped_question', owner_role: 'eng' },
      { title: 'Order confirmation email', status: 'todo', owner_role: 'pm' },
      { title: 'Launch banner & promo codes', status: 'skipped_conflict', owner_role: 'pm' },
    ],
    messages: [
      { user: DEMO_USER, text: 'Build a storefront for NeoCart with a cart and checkout.', label: 'merge', minutesAgo: 300 },
      { user: PRIYA, text: 'Cards need a quick "add to cart" without opening the product.', label: 'merge', minutesAgo: 120 },
      { user: DAN, text: 'Let’s launch with a 20% promo banner.', label: 'conflict', minutesAgo: 60 },
      { user: MARCO, text: 'No promo at launch — keep margins clean.', label: 'conflict', minutesAgo: 58 },
      { user: PRIYA, text: 'Looks great, the grid feels fast.', label: 'chat', minutesAgo: 20 },
    ],
    conflict: { task: 6, options: ['20% launch promo', 'No promo'], query: 'ecommerce launch discount effect', summary: 'Launch promos lift first-week orders but can anchor price expectations.', domain: 'scope' },
    question: { task: 4, text: 'Guest checkout, or require an account?', options: ['Guest checkout', 'Require account'], default: 'Guest checkout' },
    budget: { tokens_used: 880_000, runs_used: 22 },
    preview: {
      brand: 'NeoCart',
      links: ['New in', 'Men', 'Women', 'Cart (2)'],
      headline: 'Spring drop is live',
      tagline: 'Lightweight layers and everyday sneakers. Free shipping over ₹2,000.',
      accent: '#111827',
      background: '#ffffff',
      ink: '#111827',
      cards: [
        { title: 'Aero Runner', meta: '₹4,499 · 5 colours', action: 'Add to cart' },
        { title: 'Linen Overshirt', meta: '₹2,299 · S–XL', action: 'Add to cart' },
        { title: 'Canvas Tote', meta: '₹899 · Organic cotton', action: 'Add to cart' },
      ],
      building: 'Cart drawer\nbeing built…',
    },
    files: {
      'src/components/ProductCard.tsx': `type Product = { name: string; price: number; image: string };

export function ProductCard({ p, onAdd }: { p: Product; onAdd: () => void }) {
  return (
    <article className="rounded-xl border p-3">
      <img src={p.image} alt={p.name} className="aspect-square rounded-lg object-cover" />
      <h3 className="mt-2 font-medium">{p.name}</h3>
      <p className="text-sm">₹{p.price.toLocaleString("en-IN")}</p>
      <button onClick={onAdd}>Add to cart</button>
    </article>
  );
}
`,
    },
  },
  'demo-recipes': {
    title: 'Recipe box',
    description: 'Save recipes from links, tag them, and build a shopping list.',
    hoursAgo: 30,
    members: [PRIYA, MARCO],
    plan: [
      { title: 'Import recipe from a URL', status: 'done', owner_role: 'eng' },
      { title: 'Tags and search', status: 'done', owner_role: 'design' },
      { title: 'Shopping list builder', status: 'doing', owner_role: 'eng' },
      { title: 'Meal planner calendar', status: 'todo', owner_role: 'pm' },
      { title: 'Share a recipe link', status: 'skipped_conflict', owner_role: 'pm' },
      { title: 'Print-friendly view', status: 'skipped_question', owner_role: 'design' },
    ],
    messages: [
      { user: DEMO_USER, text: 'Build a recipe box where I can paste a link and save the recipe.', label: 'merge', minutesAgo: 1800 },
      { user: MARCO, text: 'Merge duplicate ingredients in the shopping list.', label: 'merge', minutesAgo: 1700 },
      { user: PRIYA, text: 'Shared recipes should be public links.', label: 'conflict', minutesAgo: 1650 },
      { user: MARCO, text: 'Shares should need an account to view.', label: 'conflict', minutesAgo: 1648 },
      { user: PRIYA, text: 'Add a meal planner after this.', label: 'queue', minutesAgo: 1600 },
    ],
    conflict: { task: 5, options: ['Public links', 'Account required'], query: 'recipe app sharing public vs private', summary: 'Public links drive growth; private shares suit family plans.', domain: 'scope' },
    question: { task: 6, text: 'Should the print view include photos?', options: ['With photos', 'Text only'], default: 'Text only' },
    budget: { tokens_used: 260_000, runs_used: 9 },
    preview: {
      brand: 'Recipe box',
      links: ['Recipes', 'Tags', 'Shopping list', 'Planner'],
      headline: 'What’s cooking?',
      tagline: '42 saved recipes. Paste a link to add another, or pick a few to build this week’s list.',
      accent: '#ea580c',
      background: '#fffaf5',
      ink: '#3b2414',
      cards: [
        { title: 'Paneer tikka', meta: '#dinner · 35 min', action: 'Add to list' },
        { title: 'Lemon pasta', meta: '#quick · 20 min', action: 'Add to list' },
        { title: 'Masala oats', meta: '#breakfast · 10 min', action: 'Add to list' },
      ],
      building: 'Shopping list\nbeing built…',
    },
    files: {
      'src/lib/shoppingList.ts': `export type Ingredient = { name: string; qty: number; unit: string };

// Merges duplicate ingredients across recipes (Marco's note)
export function buildList(items: Ingredient[]): Ingredient[] {
  const byKey = new Map<string, Ingredient>();
  for (const i of items) {
    const key = i.name.toLowerCase() + "|" + i.unit;
    const prev = byKey.get(key);
    byKey.set(key, prev ? { ...prev, qty: prev.qty + i.qty } : { ...i });
  }
  return [...byKey.values()];
}
`,
    },
  },
};

const sampleRooms: Room[] = Object.entries(SCENARIOS).map(([id, s]) => makeRoom(id, s.title, s.description, s.hoursAgo, s.members));

/* ─────────────── Rooms the user creates (blank, persisted) ─────────────── */

function loadCreated(): Room[] {
  if (typeof window === 'undefined') return [];
  try {
    return JSON.parse(window.localStorage.getItem(CREATED_ROOMS_KEY) || '[]') as Room[];
  } catch {
    return [];
  }
}

function saveCreated(rooms: Room[]) {
  try {
    window.localStorage.setItem(CREATED_ROOMS_KEY, JSON.stringify(rooms));
  } catch {
    // storage unavailable; the room lasts until reload
  }
}

let createdRooms: Room[] | null = null;
function created(): Room[] {
  if (!createdRooms) createdRooms = loadCreated();
  return createdRooms;
}

export function listDemoRooms(): Room[] {
  return [...created(), ...sampleRooms];
}

export function isSampleRoom(id: string): boolean {
  return id in SCENARIOS;
}

// Sample rooms are built in and can't be deleted; rooms made in this browser can
export function deleteDemoRoom(id: string) {
  createdRooms = created().filter(r => r.id !== id);
  saveCreated(createdRooms);
}

export function getDemoRoom(id: string): Room {
  const found = sampleRooms.find(r => r.id === id) || created().find(r => r.id === id);
  if (found) return found;
  const room = makeRoom(id, 'Untitled room', '', 0, []);
  created().unshift(room);
  saveCreated(created());
  return room;
}

export function createDemoRoom(description: string): Room {
  const id = `demo-${Math.random().toString(36).slice(2, 8)}`;
  const firstLine = description.split(/[.,\n]/)[0].trim();
  const title = firstLine.length > 40 ? `${firstLine.slice(0, 40).trimEnd()}…` : firstLine || 'New room';
  const room = makeRoom(id, title.charAt(0).toUpperCase() + title.slice(1), description, 0, []);
  created().unshift(room);
  saveCreated(created());
  return room;
}

export function getDemoPreview(roomId: string): DemoPreview | null {
  return SCENARIOS[roomId]?.preview ?? null;
}

export function getDemoFiles(roomId: string): Record<string, string> {
  return SCENARIOS[roomId]?.files ?? {};
}

/* ─────────────── Room events ─────────────── */

let seqCounter = 0;
function ev<T extends AppEvent['type']>(roomId: string, type: T, payload: unknown, actor = 'coordinator', minutesAgo = 0): AppEvent {
  seqCounter += 1;
  return {
    seq: seqCounter,
    room_id: roomId,
    type,
    actor_id: actor,
    ts: new Date(Date.now() - minutesAgo * 60_000).toISOString(),
    payload,
  } as AppEvent;
}

export function nextDemoSeq(): number {
  seqCounter += 1;
  return seqCounter;
}

const AGENT = { id: 'agent', email: '', name: 'MUX', initials: 'H', color: '#3b82f6' } as User;

// The initial event dump a real server would send over the room WebSocket
// Messages the user posts in demo mode, kept per room so the chat survives leaving or reloading
const sentKey = (roomId: string) => `mux_demo_sent:${roomId}`;

function loadSent(roomId: string): AppEvent[] {
  try {
    return JSON.parse(window.localStorage.getItem(sentKey(roomId)) || '[]') as AppEvent[];
  } catch {
    return [];
  }
}

function rememberSent(event: AppEvent) {
  try {
    const all = [...loadSent(event.room_id), event].slice(-500);
    window.localStorage.setItem(sentKey(event.room_id), JSON.stringify(all));
  } catch {
    // storage unavailable or full; the message still shows for this session
  }
}

export function rememberDemoEvent(event: AppEvent) {
  rememberSent(event);
}

export function demoRoomEvents(roomId: string): AppEvent[] {
  // History first, then everything the user said in this room before
  const sent = loadSent(roomId);
  const events = baseDemoRoomEvents(roomId);
  return sent.length ? [...events, ...sent.map(e => ({ ...e, seq: nextDemoSeq() }))] : events;
}

function baseDemoRoomEvents(roomId: string): AppEvent[] {
  const at = (m: number) => new Date(Date.now() - m * 60_000).toISOString();
  const scenario = SCENARIOS[roomId];
  const room = getDemoRoom(roomId);
  const presence = [ev(roomId, 'presence.join', { user_id: DEMO_USER.id, user: DEMO_USER, tab: 'feed', active: true, last_seen: at(0) })];

  // A room the user created: empty plan and history, just a welcome from the agent
  if (!scenario) {
    return [
      ...presence,
      ev(roomId, 'message.posted', {
        id: 'welcome', room_id: roomId, user_id: 'agent', label: 'chat', created_at: at(0), user: AGENT,
        text: room.description
          ? `New room ready. I’ll draft a plan for “${room.description}” — add details or say “go” to start.`
          : 'New room ready. Tell me what to build and I’ll draft a plan for the room to approve.',
      }, 'agent', 0),
      ev(roomId, 'budget.updated', { tokens_used: 0, runs_used: 0, tokens_cap: room.budget_tokens_cap, runs_cap: room.budget_runs_cap }),
    ];
  }

  const others = scenario.members.map((u, i) =>
    ev(roomId, 'presence.join', { user_id: u.id, user: u, tab: 'preview', active: i < 2, typing: i === 0, last_seen: at(i * 3) }),
  );
  const plan = scenario.plan.map((item, i) => ev(roomId, 'plan.item_added', { id: `t${i + 1}`, ...item }, 'coordinator', 60 - i));
  const messages = scenario.messages.map((m, i) =>
    ev(roomId, 'message.posted', {
      id: `m${i + 1}`, room_id: roomId, user_id: m.user.id, text: m.text, label: m.label, created_at: at(m.minutesAgo), user: m.user,
      ...(m.rationale ? { rationale: m.rationale } : {}),
    }, m.user.id, m.minutesAgo),
  );
  const c = scenario.conflict;
  const q = scenario.question;

  return [
    ...presence,
    ...others,
    ...plan,
    ...messages,
    ev(roomId, 'conflict.opened', {
      id: 'c1', room_id: roomId, task_id: `t${c.task}`,
      options: c.options,
      evidence: [{ query: c.query, summary: c.summary, citations: ['https://example.com/research'] }],
      domain: c.domain, status: 'voting', created_at: at(8), expires_at: new Date(Date.now() + 10 * 60_000).toISOString(),
      votes: [{ conflict_id: 'c1', user_id: scenario.members[0].id, option: c.options[0], weight: 2 }],
    }, 'coordinator', 8),
    ev(roomId, 'question.opened', {
      id: 'q1', room_id: roomId, task_id: `t${q.task}`,
      text: q.text, options: q.options, default_option: q.default,
      status: 'open', created_at: at(4), expires_at: new Date(Date.now() + 5 * 60_000).toISOString(),
    }, 'coordinator', 4),
    ev(roomId, 'checkpoint.created', { id: 'cp1', room_id: roomId, seq: 1, manifest_id: 'mf1', sandbox_snapshot_uuid: null, plan: [], task_log_id: 'l1', parent_id: null, created_at: at(45) }, 'coordinator', 45),
    ev(roomId, 'checkpoint.created', { id: 'cp2', room_id: roomId, seq: 2, manifest_id: 'mf2', sandbox_snapshot_uuid: null, plan: [], task_log_id: 'l2', parent_id: 'cp1', created_at: at(30) }, 'coordinator', 30),
    ev(roomId, 'checkpoint.created', { id: 'cp3', room_id: roomId, seq: 3, manifest_id: 'mf3', sandbox_snapshot_uuid: null, plan: [], task_log_id: 'l3', parent_id: 'cp2', created_at: at(10) }, 'coordinator', 10),
    ev(roomId, 'budget.updated', { ...scenario.budget, tokens_cap: 2_000_000, runs_cap: 100 }),
  ];
}

// Plan edits in demo mode: build the same events the real server would broadcast over the
// room socket, so the reducer applies them exactly as it would live. Remembered like messages
// (rememberSent) so plan edits and approvals survive a page reload.
export function demoPlanEditedEvent(roomId: string, items: PlanItem[]): AppEvent {
  return rememberPlanEvent('plan.edited', roomId, { items });
}

export function demoPlanApprovedEvent(roomId: string): AppEvent {
  return rememberPlanEvent('plan.approved', roomId, {});
}

export function demoPlanItemAddedEvent(roomId: string, item: PlanItem): AppEvent {
  return rememberPlanEvent('plan.item_added', roomId, item);
}

export function demoPlanItemUpdatedEvent(roomId: string, id: string, changes: Record<string, unknown>): AppEvent {
  return rememberPlanEvent('plan.item_updated', roomId, { id, changes });
}

export function demoPlanItemRemovedEvent(roomId: string, id: string): AppEvent {
  return rememberPlanEvent('plan.item_removed', roomId, { item_id: id });
}

function rememberPlanEvent(type: AppEvent['type'], roomId: string, payload: unknown): AppEvent {
  const now = new Date().toISOString();
  const event = {
    seq: nextDemoSeq(),
    room_id: roomId,
    type,
    actor_id: DEMO_USER.id,
    ts: now,
    payload,
  } as AppEvent;
  rememberSent(event);
  return event;
}

export function demoMessageEvent(roomId: string, text: string, to: MessageTo = 'agent'): AppEvent {
  const now = new Date().toISOString();
  const event: AppEvent = {
    seq: nextDemoSeq(),
    room_id: roomId,
    type: 'message.posted',
    actor_id: DEMO_USER.id,
    ts: now,
    payload: { id: `m-${Date.now()}`, room_id: roomId, user_id: DEMO_USER.id, text, to,
      ...(to === 'agent' && { label: 'queue', rationale: 'Demo mode: queued locally.' }), created_at: now, user: DEMO_USER },
  } as AppEvent;
  rememberSent(event);
  return event;
}

// Plans in demo mode: the same limits as the server's mux/plans.py, remembered in this browser
const DEMO_PLAN_KEY = 'mux_demo_plan';
const PLAN_LIMITS: Record<PlanName, Omit<UserPlan, 'rooms_owned'>> = {
  free: { plan: 'free', label: 'Free Developer', room_limit: 3, token_cap: 1_000_000, run_cap: 100 },
  pro: { plan: 'pro', label: 'Team Pro', room_limit: null, token_cap: 5_000_000, run_cap: 500 },
  enterprise: { plan: 'enterprise', label: 'Enterprise', room_limit: null, token_cap: 20_000_000, run_cap: 2_000 },
};

export function demoPlan(): UserPlan {
  let name: PlanName = 'free';
  try {
    const saved = window.localStorage.getItem(DEMO_PLAN_KEY);
    if (saved === 'pro' || saved === 'enterprise') name = saved;
  } catch {
    // storage unavailable: Free
  }
  const owned = listDemoRooms().filter(r => r.owner_id === DEMO_USER.id).length;
  return { ...PLAN_LIMITS[name], rooms_owned: owned };
}

export function setDemoPlan(name: PlanName): UserPlan {
  try {
    window.localStorage.setItem(DEMO_PLAN_KEY, name);
  } catch {
    // storage unavailable: the choice lasts until reload
  }
  return demoPlan();
}
